# UNS Data Contract

Every collection client must follow these rules. No exceptions. A client reads
from one protocol source (Modbus-TCP, OPC UA, or MQTT) and writes readings
directly into the shared TimescaleDB tables defined in `db/schema.sql`.

## Naming Rules

1. **snake_case everywhere.** Tag names, hierarchy identifiers (area, line,
   cell), equipment IDs. `line_01`, not `line-01`. `pump_042`, not `Pump-042`.
2. **Match the tag database exactly.** Tag names must correspond 1:1 with
   `tag_name` in `contracts/tag_database.csv`. No aliases, no abbreviations.
3. **ISA-95 six-level hierarchy is mandatory.** Every UNS path decomposes to
   `enterprise/site/area/line/cell/tag_name`. `enterprise` is constant per
   deployment, read from `contracts/equipment_hierarchy.yaml`.

## Timestamp Handling

Timestamps are the most common source of UNS drift. Every client follows these
rules:

1. All timestamps MUST be stored as UTC. `tag_history.ts` is TIMESTAMPTZ;
   write timezone-aware values only.
2. If the source provides naive timestamps (no timezone info), the client MUST:
   - Convert using `source_timezone` from `config/{client}.yaml`
   - Log a warning: "Naive timestamp from {source}, assuming {timezone}"
   - Never silently assume UTC.
3. Clients that stamp at read time must document the clock source.

## Write Target

Clients write directly into TimescaleDB. There is no MQTT broker on the data
path and no separate ingestion service — each client owns its writes.

A reading maps 1:1 to a `tag_history` row. The `Reading` model in
`shared/models.py` carries `tag_name`, `ts` (timezone-aware `datetime`),
`value`, and `quality`.

1. **Validate first.** Before writing, every reading MUST pass
   `shared.validation.validate_reading()` — tag name known, snake_case,
   `ts` timezone-aware, value type consistent with the tag's `metric_type`.
2. **Resolve the tag.** Look up `tag_id` from `uns_namespace` by `tag_name`.
   A reading whose tag is not in `uns_namespace` is rejected and routed to
   `dead_letter` — never written to `tag_history`.
3. **Insert into `tag_history`.** One row per reading:
   ```sql
   INSERT INTO tag_history (ts, tag_id, value, quality)
   VALUES (%s, %s, %s, %s)
   ON CONFLICT (ts, tag_id) DO NOTHING;
   ```
   `ON CONFLICT (ts, tag_id) DO NOTHING` makes writes idempotent — re-reading
   the same source point never duplicates a row.
4. **Use `DBWriter` from `shared/db.py`.** Do not hand-roll the connection,
   the `tag_id` lookup, or the upsert. The `DBWriter` class resolves `tag_id`
   and batch-upserts; it also routes rejected readings to `dead_letter`.

   ```python
   from shared.db import DBWriter

   writer = DBWriter(dsn, tag_database_path=Path("contracts/tag_database.csv"))
   writer.load_namespace()
   accepted, rejected = writer.write_readings(readings, source="modbus")
   ```

   `source` is a free-text string identifying the originating client; it is
   written to `dead_letter.source` for any rejected reading.

## Type Mapping

| Source Type | Contract Type | Notes |
|---|---|---|
| Integer registers (16/32-bit) | `Int32` | Raw register value |
| Float registers | `Float` | IEEE 754 single precision |
| Scaled integers | `Float` | Apply scale factor at the client level |
| Boolean / discrete | `Boolean` | Coerced to 1.0/0.0 in `tag_history` |

The contract type is the tag's `metric_type` in `uns_namespace`;
`validate_reading()` checks each value against it. String-valued readings are
out of scope — `tag_history.value` is `DOUBLE PRECISION`. If a future tag
needs string storage, amend the contract and the schema together.

## Quality Flags

| Flag | When to use |
|---|---|
| `GOOD` | Source reports valid data, communication healthy |
| `BAD` | Source reports error, or read failed |
| `STALE` | No update within `stale_threshold_ms` (per-client config) |

If the source protocol has no quality information (e.g., Modbus), default to
`GOOD` on successful read and `BAD` on failure. `quality` is written to
`tag_history.quality` as-is.

## Contract Artifacts — Source of Truth

| File | Purpose |
|---|---|
| `contracts/tag_database.csv` | Canonical tag names, cell/area/line, metric types |
| `contracts/equipment_hierarchy.yaml` | ISA-95 hierarchy (enterprise through cell) |
| `contracts/register_map.csv` | Modbus register-to-tag mapping with scale factors |
| `contracts/opcua_node_map.csv` | OPC UA node IDs to contract tag names |
| `contracts/mqtt_topic_map.csv` | Source MQTT topics to contract tag names |
