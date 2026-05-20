# Unify Your Plant-Floor Data with Claude Code and TimescaleDB

Companion repository for *"Unify Your Plant-Floor Data with Claude Code and TimescaleDB"*, published on the [TigerData blog](https://www.tigerdata.com/blog).

An Agent Team builds one collection client per protocol — Modbus-TCP, OPC UA, MQTT — each reading from its source and writing directly into a single governed TimescaleDB table. No broker on the data path, no separate ingestion service.

## Agent Team Composition

| Agent | Role |
|---|---|
| Lead — your session | Assigns tasks, enforces `CLAUDE.md`, reviews output |
| `modbus-client` | Builds `clients/modbus/`: polls Modbus-TCP device, writes readings to TimescaleDB |
| `opcua-client` | Builds `clients/opcua/`: subscribes to OPC UA server, writes readings to TimescaleDB |
| `mqtt-client` | Builds `clients/mqtt/`: subscribes to MQTT broker, writes readings to TimescaleDB |
| `contract-reviewer` | Static review of all three clients against `CLAUDE.md` (read-only) |
| `timescale-validator` | End-to-end pipeline check against data landed in TimescaleDB |

## Project Structure

```
.
├── CLAUDE.md                          # UNS data contract (binding for all clients)
├── .claude/
│   ├── settings.json                  # Agent Teams enabled + teammate permissions
│   ├── agents/
│   │   ├── modbus-client.md
│   │   ├── opcua-client.md
│   │   ├── mqtt-client.md
│   │   ├── contract-reviewer.md
│   │   └── timescale-validator.md
│   ├── rules/
│   │   ├── shared-code.md             # Import from shared/, never redefine
│   │   ├── file-ownership.md          # Which agent writes where
│   │   ├── python-conventions.md      # uv, naming, imports
│   │   └── definition-of-done.md      # Client completion criteria
│   └── skills/
│       ├── run-tests/SKILL.md
│       ├── validate-contract/SKILL.md
│       └── validate-pipeline/SKILL.md
├── clients/                           # Built by the Agent Team during the tutorial run
│   ├── modbus/
│   ├── opcua/
│   └── mqtt/
├── shared/
│   ├── models.py                      # Reading, Quality, MetricType
│   ├── validation.py                  # validate_reading()
│   └── db.py                          # DBWriter: tag_id resolution + batch upsert
├── contracts/
│   ├── tag_database.csv               # Canonical tag names, hierarchy, metric types
│   ├── equipment_hierarchy.yaml       # ISA-95 hierarchy (enterprise through cell)
│   ├── register_map.csv               # Modbus register → tag mapping with scale factors
│   ├── opcua_node_map.csv             # OPC UA node IDs → contract tag names
│   └── mqtt_topic_map.csv             # Source MQTT topics → contract tag names
├── config/
│   ├── modbus.yaml
│   ├── opcua.yaml
│   └── mqtt.yaml
├── db/
│   └── schema.sql                     # uns_namespace, tag_history (hypertable), dead_letter
├── scripts/
│   ├── setup.sh                       # One-command environment scaffold
│   ├── seed_namespace.py              # Seeds uns_namespace from contracts/
│   ├── simulate_source.py             # Stands up three fake sources in-process
│   └── validate_pipeline.py           # Six-check end-to-end pipeline validation
├── docker-compose.yml                 # Mosquitto broker on port 1883 (MQTT source)
└── pyproject.toml
```

## Requirements

- [uv](https://docs.astral.sh/uv/) — manages Python and all dependencies; `uv sync` is the only install step (`.python-version` pins 3.13; `uv.lock` makes installs deterministic)
- [Docker](https://www.docker.com/) — for the Mosquitto MQTT broker (one of the three sources)
- `psql` (`postgresql-client`)
- A [Tiger Cloud](https://www.tigerdata.com/) instance (free tier works) — or any TimescaleDB service URL
- Claude Code with Agent Teams enabled
- [tmux](https://github.com/tmux/tmux/wiki) — required for Agent Teams split-pane mode

## Quick Start

### 1. Set up the environment

```bash
bash scripts/setup.sh
```

One command covers everything: checks prerequisites, installs Python dependencies (`uv sync`), creates `.env` from `.env.example` (prompts for your Tiger Data service URL if needed), starts the Mosquitto broker, applies `db/schema.sql` to your TimescaleDB instance, and seeds `uns_namespace` from `contracts/`. Safe to re-run — idempotent throughout.

### 2. Start the simulator

In a dedicated terminal — keep it running while the Agent Team works:

```bash
uv run scripts/simulate_source.py
```

Stands up all three fake sources in-process: a Modbus-TCP server (`pymodbus`), an OPC UA server (`asyncua`), and a publisher to the local Mosquitto broker (`paho-mqtt`). No hardware required.

```bash
# Slower sweep rate — useful when stepping through the tutorial
uv run scripts/simulate_source.py --rate 2.0

# Fixed run — stops after N sweeps
uv run scripts/simulate_source.py --rate 1.0 --iterations 10
```

### 3. Run the Agent Team

In a second terminal, inside tmux:

```bash
claude
```

Paste this prompt to launch the team:

```
Create a new team and build the three collection clients. Spawn modbus-client, opcua-client, and mqtt-client in parallel. When all three complete, spawn contract-reviewer. When review passes, spawn timescale-validator.
```

The Lead is your main Claude Code session — no separate orchestrator agent. The three client teammates build `clients/modbus/`, `clients/opcua/`, and `clients/mqtt/` in parallel, each reading from the simulated sources and writing directly into `tag_history`. `contract-reviewer` follows with a read-only static review against `CLAUDE.md`. `timescale-validator` runs the `/validate-pipeline` skill and interprets the result.

> Validation is also runnable at any time from the main session: `/validate-pipeline`  
> Or manually: `uv run scripts/validate_pipeline.py`

### 4. Verify the UNS

Open the Tiger Cloud SQL Editor and confirm data landed correctly:

```sql
-- Full UNS namespace: six-level ISA-95 hierarchy materialized in Postgres
SELECT enterprise, site, area, line, cell, tag_name, uns_path
FROM uns_namespace
ORDER BY uns_path;
```

```sql
-- Readings per area — confirms all three clients contributed data
SELECT
    ns.area,
    ns.line,
    ns.cell,
    COUNT(*)         AS readings,
    MIN(th.ts)       AS first_reading,
    MAX(th.ts)       AS last_reading
FROM tag_history th
JOIN uns_namespace ns ON ns.id = th.tag_id
GROUP BY ns.area, ns.line, ns.cell
ORDER BY ns.area, ns.line, ns.cell;
```

```sql
-- Latest value per tag — the UNS as a live snapshot
SELECT
    ns.uns_path,
    th.ts,
    th.value,
    th.quality
FROM tag_history th
JOIN uns_namespace ns ON ns.id = th.tag_id
WHERE th.ts = (
    SELECT MAX(ts) FROM tag_history WHERE tag_id = th.tag_id
)
ORDER BY ns.uns_path;
```

If anything looks off, check `dead_letter` for rejected readings:

```sql
SELECT source, reason, count(*) FROM dead_letter GROUP BY 1, 2;
```

### 5. Explore the failure path (optional)

Stay in the same session and inject a naive-timestamp leak via the Tiger Cloud SQL Editor:

```sql
-- Shift area_02 timestamps back 5 hours
-- (simulates naive local time written as UTC)
UPDATE tag_history
SET ts = ts - INTERVAL '5 hours'
WHERE tag_id IN (
    SELECT id FROM uns_namespace WHERE area = 'area_02'
);
```

Re-run validation from your main session: `/validate-pipeline`. Six checks, one fails — `area_02` is five hours behind `area_01`. The Lead picks up the failure and reasons through it.

Revert when done:

```sql
UPDATE tag_history
SET ts = ts + INTERVAL '5 hours'
WHERE tag_id IN (
    SELECT id FROM uns_namespace WHERE area = 'area_02'
);
```
