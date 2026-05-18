---
name: run-tests
description: Run unit tests for one or all collection clients and report results
---

# Run Tests

Run client unit tests and report results. Accepts an optional client name.

## Usage

- `/run-tests` — run tests for all clients
- `/run-tests modbus` — run tests for the Modbus client only
- `/run-tests opcua` — run tests for the OPC UA client only
- `/run-tests mqtt` — run tests for the MQTT client only

## Steps

1. Run `uv sync` to ensure dependencies are installed.
2. Determine the scope:
   - If a client name is provided, run `uv run pytest clients/{name}/tests/ -v`
   - If no name, run `uv run pytest clients/ -v`
3. Report results:
   - Number of tests passed / failed / errors
   - For failures: test name, file:line, and the assertion that failed
   - Do not dump raw pytest output — summarize

## What Tests Must Cover

Per the Definition of Done (`.claude/rules/definition-of-done.md`):

1. All tag names exist in `contracts/tag_database.csv`
2. Timestamps are timezone-aware UTC
3. `shared.validation.validate_reading()` returns no violations
4. The protocol client (Modbus / OPC UA / MQTT) is mocked — no live server required
5. The `DBWriter` is mocked or pointed at a test database — never production data
