#!/usr/bin/env bash
# UNS Collection Clients — One-command environment setup
# Usage: bash scripts/setup.sh
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!!]${NC} $1"; }
fail()  { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

echo "======================================================================"
echo " UNS Collection Clients — Environment Setup"
echo "======================================================================"
echo ""

# ── Prerequisites ──────────────────────────────────────────────────────

echo "Checking prerequisites..."

command -v python3 >/dev/null 2>&1 || fail "Python 3 not found. Install Python 3.11+."
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [ "$(echo "$PYVER < 3.11" | bc -l)" -eq 1 ]; then
    fail "Python $PYVER found, need 3.11+."
fi
info "Python $PYVER"

command -v uv >/dev/null 2>&1 || fail "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
info "uv $(uv --version 2>/dev/null | head -1)"

command -v docker >/dev/null 2>&1 || fail "Docker not found."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 not found."
info "Docker + Compose"

command -v psql >/dev/null 2>&1 || fail "psql not found. Install postgresql-client."
info "psql"

command -v claude >/dev/null 2>&1 || warn "Claude Code not found. Install to run the Agent Team."
if command -v claude >/dev/null 2>&1; then
    CC_VER=$(claude --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1)
    info "Claude Code $CC_VER"
    if [ -n "$CC_VER" ] && [ "$(echo "$CC_VER" | tr '.' '\n' | head -3 | paste -sd'.')" != "" ]; then
        # Warn if not the tested version
        if [ "$CC_VER" != "2.1.110" ]; then
            warn "Tested with 2.1.110. You have $CC_VER — Agent Teams behavior may differ."
        fi
    fi
fi

command -v tmux >/dev/null 2>&1 || warn "tmux not found. Required for Agent Teams split-pane mode."
if command -v tmux >/dev/null 2>&1; then
    info "tmux"
fi

echo ""

# ── Python dependencies ────────────────────────────────────────────────

echo "Installing Python dependencies..."
uv sync --quiet
info "Dependencies installed"

# ── Environment file ───────────────────────────────────────────────────

if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "──────────────────────────────────────────────────────────────"
    echo " .env created from .env.example."
    echo ""
    echo " You need to set TIMESCALE_SERVICE_URL before continuing."
    echo " Open .env and paste your Tiger Data service URL."
    echo ""
    echo " It looks like:"
    echo "   postgres://tsdbadmin:<password>@<host>:<port>/tsdb?sslmode=require"
    echo ""
    echo " Get it from: Tiger Data console → your service → Connection info"
    echo "──────────────────────────────────────────────────────────────"
    echo ""
    read -rp "Press Enter after editing .env (or Ctrl+C to do it later)... "
fi

# Load env
set -a; source .env; set +a

if [ -z "${TIMESCALE_SERVICE_URL:-}" ]; then
    fail "TIMESCALE_SERVICE_URL is empty. Edit .env and re-run this script."
fi
info "Environment loaded"

# ── MQTT broker ────────────────────────────────────────────────────────
# The MQTT broker (Mosquitto) is one of the three data sources the
# mqtt-client collects from. Start it here so it is ready when the
# simulator and Agent Team run.

echo ""
echo "Starting MQTT broker..."
docker rm -f uns-mqtt-broker 2>/dev/null || true
docker compose up -d --quiet-pull 2>/dev/null
sleep 2

if docker ps --format '{{.Names}}' | grep -q mqtt; then
    info "MQTT broker running on port ${MQTT_BROKER_PORT:-1883}"
else
    fail "MQTT broker failed to start. Run: docker compose logs"
fi

# ── Database schema ────────────────────────────────────────────────────

echo ""
echo "Applying database schema..."
psql "$TIMESCALE_SERVICE_URL" -f db/schema.sql -q 2>/dev/null && \
    info "Schema applied (uns_namespace, tag_history, dead_letter)" || \
    warn "Schema may already exist (re-run is safe)"

# ── Seed namespace ─────────────────────────────────────────────────────

echo ""
echo "Seeding UNS namespace..."
uv run scripts/seed_namespace.py 2>/dev/null
TAG_COUNT=$(psql "$TIMESCALE_SERVICE_URL" -t -c "SELECT COUNT(*) FROM uns_namespace;" 2>/dev/null | tr -d ' ')
info "uns_namespace seeded: $TAG_COUNT tags"

# ── Pre-flight verification ────────────────────────────────────────────

echo ""
echo "Running pre-flight checks..."
PREFLIGHT_OK=true

# MQTT broker reachable (required by mqtt-client and simulator)
MQTT_HOST="${MQTT_BROKER_HOST:-localhost}"
MQTT_PORT="${MQTT_BROKER_PORT:-1883}"
if python3 -c "import socket; s=socket.create_connection(('$MQTT_HOST',$MQTT_PORT),2); s.close()" 2>/dev/null; then
    info "MQTT broker reachable ($MQTT_HOST:$MQTT_PORT)"
else
    warn "MQTT broker not reachable — mqtt-client and simulator will fail"
    PREFLIGHT_OK=false
fi

# DB has tags
if [ "${TAG_COUNT:-0}" -gt 0 ] 2>/dev/null; then
    info "Database ready ($TAG_COUNT tags)"
else
    warn "uns_namespace is empty — pipeline validation will fail"
    PREFLIGHT_OK=false
fi

# Python deps importable
if uv run python -c "import psycopg, yaml, pytest" 2>/dev/null; then
    info "All Python packages importable"
else
    warn "Some Python packages failed to import — run: uv sync"
    PREFLIGHT_OK=false
fi

# tmux
if [ -n "${TMUX:-}" ]; then
    info "tmux session active"
else
    warn "Not inside tmux — Agent Teams requires tmux"
    PREFLIGHT_OK=false
fi

# ── Summary ────────────────────────────────────────────────────────────

echo ""
echo "======================================================================"
if [ "$PREFLIGHT_OK" = true ]; then
    echo " Setup complete. Environment ready."
else
    echo " Setup complete with warnings. Fix issues above before running claude."
fi
echo "======================================================================"
echo ""
echo "  MQTT broker:   $MQTT_HOST:$MQTT_PORT"
echo "  Modbus source: ${MODBUS_SOURCE_HOST:-localhost}:${MODBUS_SOURCE_PORT:-5020}  (started by simulator)"
echo "  OPC UA source: ${OPCUA_SOURCE_HOST:-localhost}:${OPCUA_SOURCE_PORT:-4840}  (started by simulator)"
echo "  TimescaleDB:   connected (${TAG_COUNT:-0} tags seeded)"
echo "  Dependencies:  installed"
echo ""
echo "  Next steps:"
echo "    Terminal 1:  uv run scripts/simulate_source.py              # continuous, 1s sweeps (Ctrl+C to stop)"
echo "                 uv run scripts/simulate_source.py --rate 2.0   # slower — 2s between sweeps"
echo "    Terminal 2:  claude                                          # launch the Agent Team"
echo ""
