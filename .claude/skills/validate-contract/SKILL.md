---
name: validate-contract
description: Static review of collection-client code against the UNS data contract in CLAUDE.md
---

# Validate Contract

Static, reasoning-based review of each collection client against the UNS data
contract in `CLAUDE.md`. This is a judgment task, not a script — read the
client code and the contract, then report violations with file paths, line
numbers, and required fixes.

## Usage

- `/validate-contract` — review all three clients
- `/validate-contract modbus` — review the Modbus client only

## Checklist

For each client, check every rule from `CLAUDE.md`:

### Naming
- [ ] All tag names are snake_case (no hyphens, no camelCase, no uppercase)
- [ ] All tag names exist in `contracts/tag_database.csv` (column `tag_name`)
- [ ] No aliases, abbreviations, or "friendly names"
- [ ] Source identifiers resolve to contract tags via the client's map file
      (`register_map.csv` / `opcua_node_map.csv` / `mqtt_topic_map.csv`)

### Timestamps
- [ ] Values written to `tag_history.ts` are timezone-aware UTC
- [ ] Naive timestamp sources convert using `source_timezone` from
      `config/{client}.yaml` and log: `"Naive timestamp from {source}, assuming {timezone}"`
- [ ] Clients that stamp at read time document the clock source
- [ ] No client silently assumes UTC for a naive timestamp

### Write Target
- [ ] Readings are validated with `validate_reading()` before writing
- [ ] Writes go through `DBWriter` — no hand-rolled connections or SQL
- [ ] `tag_id` is resolved from `uns_namespace`; unresolved tags go to `dead_letter`
- [ ] Value types are consistent with the tag's `metric_type` (`Int32`, `Float`, `Boolean`)
- [ ] Quality flags are `GOOD`, `BAD`, or `STALE` — no other values

### Shared Code
- [ ] Clients import `Reading` from `shared.models` — never redefine it
- [ ] No duplicate validation or DB-write logic inside client code

### Tests
- [ ] Each client has unit tests
- [ ] Tests mock the protocol client (no live server required)
- [ ] Tests verify contract compliance via `validate_reading()`

## Report Format

For each client:

```
## {client_name} — {PASS | FAIL}

Violations:
- {file}:{line} — {rule violated} — {what's wrong} — Fix: {what to change}

Notes:
- {observations about edge cases or fragility}
```

If a client passes all checks, say PASS and move on. Do not pad the report.
