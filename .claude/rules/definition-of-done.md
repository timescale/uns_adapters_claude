# Definition of Done

A collection client is complete when:

1. `uv run pytest clients/{name}/tests/` passes
2. Every reading passes `shared.validation.validate_reading()` before writing
3. Every tag name exists in `contracts/tag_database.csv`
4. All timestamps are timezone-aware UTC
5. Rows land in `tag_history` with a resolved `tag_id`, written via `DBWriter`
6. Config is read from `config/{client}.yaml` — no hardcoded values
