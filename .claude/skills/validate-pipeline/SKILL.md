---
name: validate-pipeline
description: Run end-to-end pipeline validation — simulate, ingest, query TimescaleDB, report
---

# Validate Pipeline

End-to-end validation of the UNS pipeline. Runs the simulator, waits for the
ingester to flush, then queries TimescaleDB to verify data landed correctly.

## Usage

- `/validate-pipeline` — validate with data already in the database
- `/validate-pipeline --simulate` — run simulator first, then validate

## Prerequisites

Before running, ensure:
1. MQTT broker is running: `docker compose up -d`
2. Ingester is running in another terminal: `uv run ingester/main.py`
3. Dependencies installed: `uv sync`

If the broker is not reachable, the script will fail fast with a clear message.

## Steps

1. Run `uv run scripts/validate_pipeline.py`
   (add `--simulate --sweeps 5 --wait 15` ONLY if there is no data in
   tag_history yet — never simulate when validating existing data)
2. Interpret the 6 checks:

| Check | What it validates | Failure means |
|---|---|---|
| Tag coverage | Every tag in tag_database.csv has rows | Adapter dropped a tag |
| Timestamp sanity | Timestamps are recent, UTC, not future | Timezone bug or clock skew |
| Foreign key integrity | All tag_ids resolve to uns_namespace | Ingester accepted bad topic |
| Dead letter | No rejected payloads | Adapter emitting invalid data |
| Value plausibility | Booleans are 0/1, temps in range | Scale factor or type error |
| Cross-area drift | Areas have aligned timestamps | Naive-timestamp leak |

3. Report the adapter health summary (rows per area, latest timestamp)
4. For any FAIL: cross-reference adapter code and config to diagnose root cause

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

### Adapter Health
- area_01 (OPC UA + Modbus): {N} tags, {M} rows
- area_02 (Historian): {N} tags, {M} rows

### Diagnosis
{Root cause analysis for any failures}
```
