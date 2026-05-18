# Definition of Done

An adapter is complete when:

1. `uv run pytest adapters/{name}/tests/` passes
2. All published messages pass `shared.validation.validate_message()`
3. Every metric name exists in `contracts/tag_database.csv`
4. All timestamps are UTC epoch milliseconds
5. Topic paths are snake_case and follow the six-level ISA-95 hierarchy
6. Config is read from `config/{adapter}.yaml` — no hardcoded values
