# Shared Code — Import, Never Redefine

Clients import from `shared/`. Never duplicate these in client code:

- `shared.models` — `Reading`, `Quality`, `MetricType`
- `shared.validation` — `validate_reading()`
- `shared.db` — `DBWriter` (resolves `tag_id`, batch-upserts to `tag_history`)

If you need something that doesn't exist in `shared/`, message the Lead.
