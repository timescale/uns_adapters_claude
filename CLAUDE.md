# UNS Data Contract

Every adapter must follow these rules. No exceptions.

## Topic Path Format

```
spBv1.0/{group_id}/DDATA/{area}/{line}/{cell}
```

| Segment | Rule | Maps to | Example |
|---|---|---|---|
| `group_id` | Site name, snake_case | `site` | `plant_chicago` |
| `area` | Area identifier, snake_case | `area` | `area_01` |
| `line` | Line identifier, snake_case | `line` | `line_03` |
| `cell` | Cell/equipment identifier from tag database | `cell` | `pump_042` |

Full example: `spBv1.0/plant_chicago/DDATA/area_01/line_03/pump_042`

The sixth UNS level (`tag_name`) lives in the payload as `metrics[].name`.
`enterprise` is constant per deployment, read from
`contracts/equipment_hierarchy.yaml`.

## Naming Rules

1. **snake_case everywhere.** Topic segments, metric names, equipment IDs.
   `line_01`, not `line-01`. `pump_042`, not `Pump-042`.
2. **Match the tag database exactly.** Metric names must correspond 1:1 with
   `tag_name` in `contracts/tag_database.csv`. No aliases, no abbreviations.
3. **ISA-95 six-level hierarchy is mandatory.** Every UNS path decomposes to
   `enterprise/site/area/line/cell/tag_name`.

## Timestamp Handling

1. **All timestamps MUST be UTC.** Transmit as Unix epoch milliseconds
   (int64). No ISO strings in payloads.
2. **If the source provides naive timestamps** (no timezone info), the
   adapter MUST:
   - Convert using `source_timezone` from `config/{adapter}.yaml`
   - Log a warning: `"Naive timestamp from {source}, assuming {timezone}"`
   - Never silently assume UTC
3. **If the source provides timezone-aware timestamps**, convert to UTC at
   the adapter level. The broker sees only UTC.
4. **Adapters that stamp at read time** (e.g., Modbus, which has no source
   timestamps) must document the clock source in a code comment.
5. **Timestamp field name:** `timestamp_ms` in every payload. Not `ts`, not
   `time`, not `timestamp`.

## Payload Schema

Every DDATA message payload:

```json
{
  "timestamp_ms": 1714003200000,
  "metrics": [
    {
      "name": "temperature_bearing_de",
      "value": 72.4,
      "type": "Float",
      "quality": "GOOD"
    }
  ]
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `timestamp_ms` | int64 | yes | UTC epoch milliseconds |
| `metrics[].name` | string | yes | Must match tag database |
| `metrics[].value` | int, float, or bool | yes | See type mapping |
| `metrics[].type` | string | yes | `Int32`, `Float`, or `Boolean` |
| `metrics[].quality` | string | yes | `GOOD`, `BAD`, or `STALE` |

## Type Mapping

| Source Type | Contract Type | Notes |
|---|---|---|
| Integer registers (16/32-bit) | `Int32` | Raw register value |
| Float registers | `Float` | IEEE 754 single precision |
| Scaled integers | `Float` | Apply scale factor at adapter level |
| Boolean / discrete | `Boolean` | Coerced to 1.0/0.0 in tag_history |

String-valued metrics are out of scope. `tag_history.value` is
`DOUBLE PRECISION`. If a future tag needs string storage, amend the contract
and the schema together.

## Quality Flags

| Flag | When to use |
|---|---|
| `GOOD` | Source reports valid data, communication healthy |
| `BAD` | Source reports error, or read failed |
| `STALE` | No update within `stale_threshold_ms` (per-adapter config) |

If the source protocol has no quality information (e.g., Modbus), default to
`GOOD` on successful read and `BAD` on failure.

## Contract Artifacts — Source of Truth

| File | Purpose |
|---|---|
| `contracts/tag_database.csv` | Canonical tag names, cell/area/line, metric types |
| `contracts/equipment_hierarchy.yaml` | ISA-95 hierarchy (enterprise through cell) |
| `contracts/register_map.csv` | Modbus register-to-tag mapping with scale factors |
| `contracts/historian_tag_map.csv` | Historian proprietary tags to contract tag names |
