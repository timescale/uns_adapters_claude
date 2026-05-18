---
name: validate-contract
description: Review adapter code against the UNS data contract in CLAUDE.md
---

# Validate Contract

Static review of adapter code against every rule in the UNS data contract.
Reports violations with file paths, line numbers, and required fixes.

## Usage

- `/validate-contract` — review all three adapters
- `/validate-contract opcua` — review the OPC UA adapter only

## Checklist

For each adapter, check every rule from CLAUDE.md:

### Topic Paths
- [ ] Format: `spBv1.0/{group_id}/DDATA/{area}/{line}/{cell}`
- [ ] All segments are snake_case (no hyphens, no camelCase, no uppercase)
- [ ] group_id matches config `group_id` field
- [ ] area/line/cell match values in `contracts/tag_database.csv`

### Naming
- [ ] All metric names are snake_case
- [ ] All metric names exist in `contracts/tag_database.csv` (column `tag_name`)
- [ ] No aliases, abbreviations, or "friendly names"

### Timestamps
- [ ] Field name is `timestamp_ms` (not `ts`, `time`, or `timestamp`)
- [ ] Value is UTC epoch milliseconds (int, not float, not ISO string)
- [ ] Naive timestamp sources log the warning: `"Naive timestamp from {source}, assuming {timezone}"`
- [ ] Timezone conversion uses `source_timezone` from config
- [ ] Adapters that stamp at read time document the clock source

### Payloads
- [ ] JSON structure: `{"timestamp_ms": int, "metrics": [...]}`
- [ ] Each metric has: `name`, `value`, `type`, `quality`
- [ ] Types are from the contract: `Int32`, `Float`, `Boolean` only
- [ ] Quality flags: `GOOD`, `BAD`, `STALE` — no other values

### Shared Code
- [ ] Adapters import from `shared.models` — never redefine `Metric`, `MetricType`, etc.
- [ ] Tests use `shared.validation.validate_message()`
- [ ] No duplicate validation logic inside adapter code

### Tests
- [ ] Each adapter has unit tests
- [ ] Tests mock the protocol client (no live server required)
- [ ] Tests verify contract compliance via `validate_message()`

## Report Format

For each adapter:

```
## {adapter_name} — {PASS | FAIL}

Violations:
- {file}:{line} — {rule violated} — {what's wrong} — Fix: {what to change}

Notes:
- {observations about edge cases or fragility}
```

If an adapter passes all checks, say PASS and move on. Do not pad the report.
