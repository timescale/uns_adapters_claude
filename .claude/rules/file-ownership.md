# File Ownership

Teammate definitions live in `.claude/agents/`. Each agent writes only to
its designated directory:

| Agent | Writes to |
|---|---|
| `opcua-adapter` | `adapters/opcua/` |
| `modbus-adapter` | `adapters/modbus/` |
| `historian-adapter` | `adapters/historian/` |
| `contract-reviewer` | nothing (read-only) |
| `timescale-validator` | `scripts/`, `reports/` |

Two teammates must NEVER write to the same file. Never modify `shared/`,
`contracts/`, `config/`, `db/`, `ingester/`, or `CLAUDE.md`.

## Team Lifecycle

When shutting down, send `shutdown_request` to each teammate individually.
Broadcast (`to: "*"`) is not supported — it will error.
