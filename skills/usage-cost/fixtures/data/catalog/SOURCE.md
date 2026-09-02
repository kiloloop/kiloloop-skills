# Catalog slice

The files under `providers/` and `models/` are copied verbatim from the
models.dev repository (<https://github.com/anomalyco/models.dev>, MIT license)
at commit `f7af17dfaf28e3e2fb7dbf6ca72ba73e8436d59e`: the `anthropic`
provider's model files, and the base-model files those inherit display names
from.

They pin what the catalog said at that commit so `test_refresh_rates.py` runs
offline and deterministically. They are not a rate source for the report, which
reads only `scripts/rates.json`.
