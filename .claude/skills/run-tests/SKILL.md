---
name: run-tests
description: Run unit tests for one or all adapters and report results
---

# Run Tests

Run adapter unit tests and report results. Accepts an optional adapter name.

## Usage

- `/run-tests` — run tests for all adapters
- `/run-tests opcua` — run tests for the OPC UA adapter only
- `/run-tests modbus` — run tests for the Modbus adapter only
- `/run-tests historian` — run tests for the Historian adapter only

## Steps

1. Run `uv sync` to ensure dependencies are installed.
2. Determine the scope:
   - If an adapter name is provided, run `uv run pytest adapters/{name}/tests/ -v`
   - If no name, run `uv run pytest adapters/ -v`
3. Report results:
   - Number of tests passed / failed / errors
   - For failures: test name, file:line, and the assertion that failed
   - Do not dump raw pytest output — summarize

## What Tests Must Cover

Per the Definition of Done (`.claude/rules/definition-of-done.md`):

1. Topic paths match contract format (snake_case, correct ISA-95 hierarchy)
2. All metric names exist in `contracts/tag_database.csv`
3. Timestamps are UTC epoch milliseconds
4. `shared.validation.validate_message()` returns no violations
5. Protocol client is mocked — no live server required
