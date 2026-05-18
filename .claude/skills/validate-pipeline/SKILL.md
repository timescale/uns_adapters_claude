---
name: validate-pipeline
description: Run end-to-end pipeline validation — query TimescaleDB after collection and report
---

# Validate Pipeline

End-to-end validation of the UNS pipeline. Queries TimescaleDB to verify that
readings collected by the clients landed correctly in `tag_history`.

## Usage

- `/validate-pipeline` — query TimescaleDB and report on the data that landed

## Prerequisites

Before running, ensure:
1. Dependencies installed: `uv sync`
2. The schema is applied and `uns_namespace` is seeded
3. The collection clients have run, so `tag_history` holds data

To collect without real sources, start the simulator first (a separate
script). If `tag_history` is empty, the validation script warns and exits.

## Steps

1. Run `uv run scripts/validate_pipeline.py` (no arguments — it validates
   whatever data is already in `tag_history`)
2. Interpret the 6 checks:

| Check | What it validates | Failure means |
|---|---|---|
| Tag coverage | Every tag in tag_database.csv has rows | Client dropped a tag |
| Timestamp sanity | Timestamps are recent, UTC, not future | Timezone bug or clock skew |
| Foreign key integrity | All tag_ids resolve to uns_namespace | Client wrote an unresolved tag |
| Dead letter | No rejected readings | Client emitting invalid data |
| Value plausibility | Booleans are 0/1, temps in range | Scale factor or type error |
| Cross-area drift | Areas have aligned timestamps | Naive-timestamp leak |

3. Report the client health summary (rows per area, latest timestamp)
4. For any FAIL: cross-reference client code and config to diagnose root cause

## Report Format

```
## Pipeline Validation Report

### Checks
- Tag coverage: {N}/{total} — {PASS|FAIL}
- Timestamp sanity: {PASS|FAIL}
- Foreign key integrity: {PASS|FAIL}
- Dead letter: {N} rejected — {PASS|FAIL}
- Value plausibility: {PASS|FAIL}
- Cross-area drift: {PASS|FAIL}

### Client Health
- area_01: {N} tags, {M} rows
- area_02: {N} tags, {M} rows

### Diagnosis
{Root cause analysis for any failures}
```
