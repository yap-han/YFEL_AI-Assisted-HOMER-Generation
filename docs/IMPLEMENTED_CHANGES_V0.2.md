# Implemented software changes — version 0.2

| Requested change | Implementation | Verification |
|---|---|---|
| 1. Hard topical-relevance requirement | Source quality and topical relevance are separate. No paper can be shortlisted without a family-term match. | Off-topic high-quality Oman aquaculture paper is excluded from renewable-resource results. |
| 2. Cross-provider DOI deduplication | DOI is the canonical key; title-year is the fallback. Provider occurrences are merged into one candidate. | Test confirms Crossref and OpenAlex duplicates produce one candidate with two provenance records. |
| 3. Title-and-abstract screening fields | Candidate schema stores automated title/abstract decision, screening status, matched terms and human status. | Relevant academic fixture is shortlisted with pending human review. |
| 4. Inclusion and exclusion reasons | Structured reason arrays are stored for automated and human decisions. | Reports expose both reason columns; screening history retains reviewer comments. |
| 5. Retrieval date and database name | Query log and provenance table store database name, exact query, retrieval timestamp/date and URL. | Exported in `query_log.csv` and `retrieval_provenance.csv`. |
| 6. Citation-chaining records | OpenAlex backward/forward retrieval plus offline fixture; links retain seed, target, direction, source and discovery date. | Automated fixture test stores chain records and exports them. |
| 7. Batch retrieval | Command iterates selected providers × families × locations; defaults cover all profile locations and ontology families. | Offline batch test covers renewable and conventional families across Oman and Qatar; the final demonstration covers four energy-relevant families for Oman. |

Additional structural improvement: a conventional-generation ontology family was added so renewable, grid, storage and dispatchable options can be evaluated within the same evidence framework.

PRISMA-compatible exports were not implemented because they were listed as Step 8 and were outside the requested scope. The current schema retains the records required to add them later.
