"""Site-data layer: weather, load synthesis, site register and HOMER export.

The site layer supplies the inputs that the evidence pipeline declares as
``site_data_required`` in ``config/minimum_homer_scenario.json``: chronological
load, solar and temperature series, tariffs, outages, fuel price and the
financial assumptions. Every value it produces is a candidate for human
review; nothing here is approved evidence.
"""

SITE_LAYER_VERSION = "site_layer_v0.7"

__all__ = ["SITE_LAYER_VERSION"]
