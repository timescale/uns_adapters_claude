# Building UNS Adapters with Claude Code Agent Teams

Companion repository for *"Building UNS Adapters with Claude Code Agent Teams: A Hybrid Framework for IIoT Integration"*, published on the [TigerData blog](https://www.tigerdata.com/blog).

## Agent Team Composition

| Role | Agent Definition | Model | Purpose |
|---|---|---|---|
| Lead | Your session | opus | Assigns tasks, reviews against contract, catches drift |
| OPC UA Adapter | `.claude/agents/opcua-adapter.md` | sonnet | Builds `adapters/opcua/`: subscriptions, type flattening |
| Modbus Adapter | `.claude/agents/modbus-adapter.md` | sonnet | Builds `adapters/modbus/`: register mapping, scale factors |
| Historian Adapter | `.claude/agents/historian-adapter.md` | sonnet | Builds `adapters/historian/`: naive timestamps, tag translation |
| Contract Reviewer | `.claude/agents/contract-reviewer.md` | sonnet | Reviews all three adapters against CLAUDE.md, read-only |
| TimescaleDB Validator | `.claude/agents/timescale-validator.md` | opus | Validates landed data, IIoT-grounded insights |

## Project Structure

```
.
├── CLAUDE.md                          # The UNS data contract (binding for all adapters)
├── .claude/
│   ├── settings.json                  # Agent Teams enabled + teammate permissions
│   ├── agents/
│   │   ├── opcua-adapter.md           # OPC UA teammate definition
│   │   ├── modbus-adapter.md          # Modbus teammate definition
│   │   ├── historian-adapter.md       # Historian teammate definition
│   │   ├── contract-reviewer.md       # Cross-adapter reviewer definition
│   │   └── timescale-validator.md     # End-to-end pipeline + DB validation
│   ├── rules/
│   │   ├── shared-code.md             # Import from shared/, never redefine
│   │   ├── file-ownership.md          # Which agent writes where
│   │   ├── python-conventions.md      # uv, naming, imports
│   │   └── definition-of-done.md      # Adapter completion criteria
│   └── skills/
│       ├── run-tests/SKILL.md         # Run adapter unit tests
│       ├── validate-contract/SKILL.md # Static review against CLAUDE.md
│       └── validate-pipeline/SKILL.md # End-to-end pipeline validation
├── shared/
│   ├── models.py                      # Metric, Quality, MetricType, SparkplugMessage
│   └── validation.py                  # Contract validation (topic paths, naming, tag lookup)
├── contracts/
│   ├── tag_database.csv               # 20 tags: tag_name, cell, area, line, metric_type
│   ├── equipment_hierarchy.yaml       # ISA-95 hierarchy for acme_corp/plant_chicago
│   ├── register_map.csv               # Modbus register-to-tag mapping (15 registers)
│   └── historian_tag_map.csv          # PI historian tag-to-UNS tag mapping (5 tags)
├── config/
│   ├── opcua.yaml                     # OPC UA adapter config (10 tags, area_01/line_03)
│   ├── modbus.yaml                    # Modbus adapter config (15 tags, area_01)
│   └── historian.yaml                 # Historian adapter config (5 tags, area_02)
├── db/
│   └── schema.sql                     # uns_namespace + tag_history + dead_letter
├── ingester/
│   └── main.py                        # MQTT -> TimescaleDB reference ingester
├── scripts/
│   ├── setup.sh                       # One-command environment scaffold + pre-flight checks
│   ├── seed_namespace.py              # Seeds uns_namespace from contracts
│   ├── simulate_source.py             # Publishes fake Sparkplug DDATA for all 20 tags
│   └── validate_pipeline.py           # End-to-end pipeline validation (6 checks + health)
├── docker-compose.yml                 # Mosquitto broker on port 1883
└── adapters/                          # Created by the Agent Team (not in repo)
    ├── opcua/src/publisher.py
    ├── modbus/src/publisher.py
    └── historian/src/publisher.py
```

## Requirements

- Python 3.11+ with [uv](https://docs.astral.sh/uv/)
- [Docker](https://www.docker.com/) (for the local Mosquitto broker)
- A [Tiger Cloud](https://www.tigerdata.com/) instance (free tier works)
- Claude Code 2.1.110 with Agent Teams enabled (see [Claude Code version](#claude-code-version) below)
- [tmux](https://github.com/tmux/tmux/wiki) (required for Agent Teams split-pane mode)

> **Scope note:** Adapters publish JSON over MQTT, not Sparkplug B protobuf. JSON keeps the contract inspectable for tutorial purposes. For protobuf encoding in production, use [Eclipse Tahu](https://github.com/eclipse/tahu).

### Claude Code version

Pin to **Claude Code 2.1.110**. Versions 2.1.112+ [corrupt the tmux lead pane during Agent Teams approval modals](https://github.com/anthropics/claude-code/issues/52431); 2.1.110 is the last release tested with this tutorial.

Install the pinned version:

```bash
curl -fsSL https://claude.ai/install.sh | bash -s -- --version 2.1.110
```

Disable auto-updates so the pin holds across sessions:

```bash
echo 'export DISABLE_AUTOUPDATER=1' >> ~/.bashrc
export DISABLE_AUTOUPDATER=1
```

Verify:

```bash
claude --version
# 2.1.110 (Claude Code)
```

#### Returning to the latest version

When you're done with the tutorial, undo the pin:

```bash
# Remove the auto-update guard from ~/.bashrc (or zshrc), then unset it for the current shell
unset DISABLE_AUTOUPDATER

# Reinstall the latest release (the installer flips the symlink)
curl -fsSL https://claude.ai/install.sh | bash

claude --version
```

## Quick Start

### 1. Set up the environment

```bash
bash scripts/setup.sh
```

Checks prerequisites, installs dependencies, starts the MQTT broker, applies the schema to your Tiger Data service, and seeds the UNS namespace. Prompts for your Tiger Data service URL if `.env` doesn't exist yet.

### 2. Run the Agent Team

Start Claude Code:

```bash
claude
```

Paste this prompt to launch the team:

```
Create a new team and build the three protocol adapters. Spawn the adapter teammates in parallel, review with contract-reviewer, then validate the pipeline with timescale-validator.
```

> Validation runs as the `/validate-pipeline` skill inside Claude Code, or manually via `uv run scripts/validate_pipeline.py`.

### 3. Verify the UNS in the database

Open the Tiger Cloud SQL Editor and run these queries to confirm the ISA-95 hierarchy and adapter data landed correctly:

```sql
-- Full UNS namespace: six-level ISA-95 hierarchy materialized in Postgres
SELECT enterprise, site, area, line, cell, tag_name, uns_path
FROM uns_namespace
ORDER BY uns_path;
```

```sql
-- Readings per adapter area, confirms all three adapters contributed data
SELECT
    ns.area,
    ns.line,
    ns.cell,
    COUNT(*) AS readings,
    MIN(th.ts) AS first_reading,
    MAX(th.ts) AS last_reading
FROM tag_history th
JOIN uns_namespace ns ON ns.id = th.tag_id
GROUP BY ns.area, ns.line, ns.cell
ORDER BY ns.area, ns.line, ns.cell;
```

```sql
-- Latest value per tag, the UNS as a live snapshot
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

If anything looks off, check `dead_letter` for rejected payloads:

```sql
SELECT reason, count(*) FROM dead_letter GROUP BY 1;
```

### 4. Explore a failure mode (optional)

Simulate a naive-timestamp leak via the Tiger Cloud SQL Editor:

```sql
-- Shift historian timestamps back 5 hours (simulates naive local time as UTC)
UPDATE tag_history
SET ts = ts - INTERVAL '5 hours'
WHERE tag_id IN (
    SELECT id FROM uns_namespace WHERE area = 'area_02'
);
```

Re-run validation. Check 6 will flag `area_02` as ~5 hours behind the other areas.

Revert when done:

```sql
UPDATE tag_history
SET ts = ts + INTERVAL '5 hours'
WHERE tag_id IN (
    SELECT id FROM uns_namespace WHERE area = 'area_02'
);
```
