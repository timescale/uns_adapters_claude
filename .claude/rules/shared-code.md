# Shared Code — Import, Never Redefine

Adapters import from `shared/`. Never duplicate these in adapter code:

- `shared.models` — `Metric`, `MetricType`, `Quality`, `SparkplugMessage`
- `shared.validation` — `validate_message()`, `validate_topic_path()`

If you need something that doesn't exist in `shared/`, message the Lead.
