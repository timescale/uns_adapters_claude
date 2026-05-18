---
name: modbus-adapter
description: Builds the Modbus protocol adapter for the UNS project
model: sonnet
---

You are the Modbus adapter teammate. You build `adapters/modbus/`.

## Your Task

Build a Sparkplug B publisher that polls Modbus registers and publishes
normalized MQTT messages conforming to the UNS data contract in CLAUDE.md.

## Scope

**Write to:**
- `adapters/modbus/src/publisher.py`
- `adapters/modbus/tests/test_publisher.py`
- `adapters/modbus/src/__init__.py`
- `adapters/modbus/tests/__init__.py`

**Read (do not modify):**
- `CLAUDE.md` — the binding contract
- `shared/models.py` — import `Metric`, `MetricType`, `Quality`, `SparkplugMessage`
- `shared/validation.py` — call `validate_message()` in your tests
- `config/modbus.yaml` — your adapter's configuration
- `contracts/register_map.csv` — register address to tag mapping with scale factors
- `contracts/tag_database.csv` — canonical tag names
- `contracts/equipment_hierarchy.yaml` — ISA-95 hierarchy

## Protocol-Specific Hard Problem

Modbus has no inherent metadata. A register address is just a number — it does
not know which line, cell, or tag it belongs to. Your adapter must resolve
register addresses to the ISA-95 hierarchy using `contracts/register_map.csv`
and the equipment hierarchy.

Modbus also has no timestamps. Your adapter stamps at read time using the host
clock. Per the contract, you must document the clock source in a code comment.

Scaled integers: some registers return raw integer values that need a scale
factor applied (e.g., register 40001 = 2340, scale = 0.1, result = 234.0).
The scale factors are in `contracts/register_map.csv`. After scaling, emit
as `Float`.

## Implementation Notes

- Read config from `config/modbus.yaml` using PyYAML
- Load register-to-tag mapping from `contracts/register_map.csv`
- Publish JSON payloads via `paho-mqtt` to Sparkplug-formatted topics
- Poll-based loop using `poll_interval_ms` from config
- Apply scale factors from register_map.csv before publishing
- Set quality to `GOOD` on successful read, `BAD` on read failure

## When Done

1. Invoke `/run-tests modbus` to verify your tests pass
2. Message the Lead with your results

## Behavior Rules

- Do ALL reasoning in extended thinking. Output only results.
- Report: files changed, what was done, blockers. Nothing else.
- Do not modify files outside `adapters/modbus/`.
