from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class ProviderConfigurationError(RuntimeError):
    """Raised before a paid request when a provider cannot be used safely."""


class ProviderRequestError(RuntimeError):
    """Raised when a configured provider rejects or cannot complete a request."""


@dataclass
class ProviderCall:
    provider: str
    model: str
    operation: str
    prompt_version: str
    request_sha256: str
    response_id: str | None
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_seconds: float
    estimated_cost_usd: float
    pricing_snapshot: dict[str, float]
    attempt: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StructuredResult:
    payload: dict[str, Any]
    raw_text: str
    call: ProviderCall


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _post_json(endpoint: str, api_key: str, body: dict[str, Any], timeout: float) -> tuple[dict[str, Any], float]:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise ProviderRequestError(f"Provider returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderRequestError(f"Provider request failed: {exc}") from exc
    latency = time.perf_counter() - started
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderRequestError("Provider returned a non-JSON response") from exc
    if not isinstance(payload, dict):
        raise ProviderRequestError("Provider response root is not an object")
    return payload, latency


def _required_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a strict Structured Outputs schema without changing stored evidence."""

    clone = json.loads(json.dumps(schema))

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if node.get("type") == "object" and isinstance(properties, dict):
                node["additionalProperties"] = False
                node["required"] = list(properties)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(clone)
    clone.pop("$schema", None)
    return clone


class OpenAIResponsesConnector:
    """Strict JSON-schema connector for the OpenAI Responses API."""

    provider_name = "openai_responses"

    def __init__(self, config: dict[str, Any]) -> None:
        self.model_name = str(config.get("model") or "").strip()
        if not self.model_name:
            raise ProviderConfigurationError("OpenAI connector requires a model")
        key_env = str(config.get("api_key_env") or "OPENAI_API_KEY")
        self.api_key = os.environ.get(key_env)
        if not self.api_key:
            raise ProviderConfigurationError(
                f"{key_env} is missing. Create an OpenAI API key with billing/model access, "
                "set it in the shell environment, and rerun. No fixture fallback was used."
            )
        self.endpoint = str(config.get("endpoint") or "https://api.openai.com/v1/responses")
        self.timeout_seconds = float(config.get("timeout_seconds", 180))
        self.reasoning_effort = str(config.get("reasoning_effort") or "low")
        pricing = config.get("pricing_usd_per_million_tokens") or {}
        self.pricing = {
            "input": float(pricing.get("input", 0)),
            "cached_input": float(pricing.get("cached_input", pricing.get("input", 0))),
            "output": float(pricing.get("output", 0)),
        }

    @staticmethod
    def _output_text(response: dict[str, Any]) -> str:
        chunks: list[str] = []
        for item in response.get("output") or []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("type") == "output_text":
                    chunks.append(str(content.get("text") or ""))
        if not chunks and isinstance(response.get("output_text"), str):
            chunks.append(response["output_text"])
        if not chunks:
            raise ProviderRequestError("Responses API result did not contain output_text")
        return "".join(chunks)

    def structured(
        self,
        *,
        operation: str,
        prompt_version: str,
        system_prompt: str,
        evidence_payload: dict[str, Any],
        output_schema: dict[str, Any],
        schema_name: str,
        attempt: int = 1,
        correction_feedback: list[str] | None = None,
    ) -> StructuredResult:
        immutable_evidence = _canonical_json(evidence_payload)
        correction = ""
        if correction_feedback:
            correction = (
                "\nThe prior output failed deterministic validation for these reasons: "
                + " | ".join(correction_feedback)
                + ". Correct only the output structure. The evidence below is unchanged."
            )
        body = {
            "model": self.model_name,
            "store": False,
            "reasoning": {"effort": self.reasoning_effort},
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": correction + "\n" + immutable_evidence}],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": _required_schema(output_schema),
                }
            },
        }
        response, latency = _post_json(self.endpoint, self.api_key, body, self.timeout_seconds)
        raw_text = self._output_text(response)
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ProviderRequestError("Structured output was not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ProviderRequestError("Structured output root was not an object")
        usage = response.get("usage") or {}
        input_details = usage.get("input_tokens_details") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        cached = int(input_details.get("cached_tokens") or 0)
        uncached = max(input_tokens - cached, 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        estimated_cost = (
            uncached * self.pricing["input"]
            + cached * self.pricing["cached_input"]
            + output_tokens * self.pricing["output"]
        ) / 1_000_000
        call = ProviderCall(
            provider=self.provider_name,
            model=self.model_name,
            operation=operation,
            prompt_version=prompt_version,
            request_sha256=_sha256({"system": system_prompt, "evidence": evidence_payload, "schema": output_schema}),
            response_id=str(response.get("id")) if response.get("id") else None,
            input_tokens=input_tokens,
            cached_input_tokens=cached,
            output_tokens=output_tokens,
            total_tokens=int(usage.get("total_tokens") or input_tokens + output_tokens),
            latency_seconds=latency,
            estimated_cost_usd=estimated_cost,
            pricing_snapshot=dict(self.pricing),
            attempt=attempt,
        )
        return StructuredResult(payload=payload, raw_text=raw_text, call=call)


class MistralOCRConnector:
    """Mistral OCR 4.1 connector retaining page, table, block, and confidence data."""

    provider_name = "mistral_ocr"

    def __init__(self, config: dict[str, Any]) -> None:
        self.model_name = str(config.get("model") or "mistral-ocr-4-1")
        key_env = str(config.get("api_key_env") or "MISTRAL_API_KEY")
        self.api_key = os.environ.get(key_env)
        if not self.api_key:
            raise ProviderConfigurationError(
                f"{key_env} is missing. Create a Mistral API key with OCR access, set it in "
                "the shell environment, and rerun. OCR was not replaced with local text parsing."
            )
        self.endpoint = str(config.get("endpoint") or "https://api.mistral.ai/v1/ocr")
        self.timeout_seconds = float(config.get("timeout_seconds", 300))
        self.price_per_1000_pages = float(config.get("price_usd_per_1000_pages", 0))

    def process_pdf(self, path: str | Path, *, attempt: int = 1) -> tuple[dict[str, Any], ProviderCall]:
        pdf = Path(path)
        if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
            raise ProviderConfigurationError(f"OCR requires an original PDF file: {pdf}")
        encoded = base64.b64encode(pdf.read_bytes()).decode("ascii")
        body = {
            "model": self.model_name,
            "document": {
                "type": "document_url",
                "document_url": f"data:application/pdf;base64,{encoded}",
            },
            "table_format": "markdown",
            "include_blocks": True,
            "confidence_scores_granularity": "block",
            "include_image_base64": False,
            "extract_header": True,
            "extract_footer": True,
        }
        response, latency = _post_json(self.endpoint, self.api_key, body, self.timeout_seconds)
        pages = response.get("pages") or []
        if not isinstance(pages, list) or not pages:
            raise ProviderRequestError(f"OCR returned no pages for {pdf.name}")
        page_count = len(pages)
        usage = response.get("usage_info") or {}
        processed = int(usage.get("pages_processed") or usage.get("num_pages") or page_count)
        cost = processed * self.price_per_1000_pages / 1000
        call = ProviderCall(
            provider=self.provider_name,
            model=str(response.get("model") or self.model_name),
            operation="ocr_and_table_extraction",
            prompt_version="ocr_contract_v0.6",
            request_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
            response_id=None,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            latency_seconds=latency,
            estimated_cost_usd=cost,
            pricing_snapshot={"per_1000_pages": self.price_per_1000_pages},
            attempt=attempt,
        )
        return response, call
