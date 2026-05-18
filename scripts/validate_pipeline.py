"""End-to-end pipeline validation for UNS adapters.

Runs the simulator, waits for the ingester to flush, then queries TimescaleDB
to verify data landed correctly. Designed to be run by the TimescaleDB
Validator agent or by a human after an Agent Team build.

Usage:
    # With ingester already running in another terminal:
    uv run scripts/validate_pipeline.py

    # Full pipeline (starts simulator, waits, then validates):
    uv run scripts/validate_pipeline.py --simulate --sweeps 5 --wait 15
"""

import argparse
import csv
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import psycopg
except ImportError:
    print("psycopg not installed. Run: uv add psycopg[binary]")
    sys.exit(1)

from dotenv import load_dotenv

load_dotenv()

TIMESCALE_URL = os.getenv("TIMESCALE_SERVICE_URL", "")
TAG_DB_PATH = Path("contracts/tag_database.csv")
HIERARCHY_PATH = Path("contracts/equipment_hierarchy.yaml")
ENTERPRISE = os.getenv("UNS_ENTERPRISE", "acme_corp")


def get_connection():
    if not TIMESCALE_URL:
        print("FAIL: TIMESCALE_SERVICE_URL not set. Run: source .env")
        sys.exit(1)
    return psycopg.connect(TIMESCALE_URL)


def load_expected_tags() -> set[str]:
    """Load canonical tag names from the contract."""
    with open(TAG_DB_PATH) as f:
        reader = csv.DictReader(f)
        return {row["tag_name"] for row in reader}


def check_tag_coverage(conn, expected_tags: set[str]) -> dict:
    """Check that every expected tag has at least one row in tag_history."""
    cur = conn.execute("""
        SELECT ns.tag_name, COUNT(th.ts) AS readings
        FROM uns_namespace ns
        LEFT JOIN tag_history th ON th.tag_id = ns.id
        GROUP BY ns.tag_name
        ORDER BY ns.tag_name
    """)
    rows = cur.fetchall()

    covered = set()
    missing = set()
    counts = {}
    for tag_name, readings in rows:
        counts[tag_name] = readings
        if readings > 0:
            covered.add(tag_name)
        else:
            missing.add(tag_name)

    # Tags in contract but not in uns_namespace at all
    db_tags = {tag_name for tag_name, _ in rows}
    not_seeded = expected_tags - db_tags

    return {
        "total": len(expected_tags),
        "covered": len(covered),
        "missing": missing,
        "not_seeded": not_seeded,
        "counts": counts,
        "pass": len(missing) == 0 and len(not_seeded) == 0,
    }


def check_timestamp_sanity(conn) -> dict:
    """Verify timestamps are recent, UTC, and not in the future."""
    cur = conn.execute("""
        SELECT
            MIN(ts) AS earliest,
            MAX(ts) AS latest,
            NOW() AS server_now,
            COUNT(*) FILTER (WHERE ts > NOW() + INTERVAL '1 minute') AS future_rows,
            COUNT(*) AS total_rows
        FROM tag_history
    """)
    row = cur.fetchone()
    if not row or row[4] == 0:
        return {"pass": False, "reason": "No rows in tag_history", "total_rows": 0}

    earliest, latest, server_now, future_rows, total_rows = row
    return {
        "earliest": str(earliest),
        "latest": str(latest),
        "server_now": str(server_now),
        "future_rows": future_rows,
        "total_rows": total_rows,
        "pass": future_rows == 0,
    }


def check_foreign_keys(conn) -> dict:
    """Check for orphan tag_history rows with no matching uns_namespace."""
    cur = conn.execute("""
        SELECT COUNT(*) FROM tag_history th
        LEFT JOIN uns_namespace ns ON ns.id = th.tag_id
        WHERE ns.id IS NULL
    """)
    orphans = cur.fetchone()[0]
    return {"orphan_rows": orphans, "pass": orphans == 0}


def check_dead_letter(conn) -> dict:
    """Check dead_letter table for rejected payloads."""
    cur = conn.execute("""
        SELECT reason, COUNT(*) AS cnt
        FROM dead_letter
        GROUP BY reason
        ORDER BY cnt DESC
    """)
    rows = cur.fetchall()
    reasons = {reason: cnt for reason, cnt in rows}
    total = sum(reasons.values())
    return {"total": total, "reasons": reasons, "pass": total == 0}


def check_value_plausibility(conn) -> dict:
    """Spot-check value ranges for known tag types."""
    issues = []

    # Boolean tags should have values 0.0 or 1.0 only
    cur = conn.execute("""
        SELECT ns.tag_name, th.value
        FROM tag_history th
        JOIN uns_namespace ns ON ns.id = th.tag_id
        WHERE ns.tag_name IN ('motor_running', 'agitator_running', 'conveyor_running')
          AND th.value NOT IN (0.0, 1.0)
        LIMIT 5
    """)
    bad_bools = cur.fetchall()
    if bad_bools:
        issues.append(f"Boolean tags with non-0/1 values: {bad_bools}")

    # Temperature tags: plausible range -50 to 500 degC
    cur = conn.execute("""
        SELECT ns.tag_name, MIN(th.value), MAX(th.value)
        FROM tag_history th
        JOIN uns_namespace ns ON ns.id = th.tag_id
        WHERE ns.tag_name LIKE 'temperature_%'
        GROUP BY ns.tag_name
        HAVING MIN(th.value) < -50 OR MAX(th.value) > 500
    """)
    bad_temps = cur.fetchall()
    if bad_temps:
        issues.append(f"Temperature out of plausible range: {bad_temps}")

    # Pressure tags: plausible range 0 to 100 bar
    cur = conn.execute("""
        SELECT ns.tag_name, MIN(th.value), MAX(th.value)
        FROM tag_history th
        JOIN uns_namespace ns ON ns.id = th.tag_id
        WHERE ns.tag_name LIKE 'pressure_%'
        GROUP BY ns.tag_name
        HAVING MIN(th.value) < 0 OR MAX(th.value) > 100
    """)
    bad_pressure = cur.fetchall()
    if bad_pressure:
        issues.append(f"Pressure out of plausible range: {bad_pressure}")

    return {"issues": issues, "pass": len(issues) == 0}


def check_cross_area_timestamp_drift(conn) -> dict:
    """Detect timestamp offset between areas — catches naive-timezone bugs.

    Compares the average latest timestamp per area. If one area is offset
    from the others by a round number of hours (1-12h), it's almost certainly
    a naive-timestamp leak: the adapter published local time as if it were UTC.
    """
    # Use a wide window (24h) to catch data that drifted far from the expected time.
    # A naive-timestamp leak can shift data by many hours — a 1h window would
    # exclude the drifted rows entirely, hiding the bug.
    cur = conn.execute("""
        SELECT
            ns.area,
            AVG(EXTRACT(EPOCH FROM th.ts)) AS avg_epoch,
            MAX(th.ts) AS latest
        FROM tag_history th
        JOIN uns_namespace ns ON ns.id = th.tag_id
        WHERE th.ts > NOW() - INTERVAL '24 hours'
        GROUP BY ns.area
        ORDER BY ns.area
    """)
    rows = cur.fetchall()
    if len(rows) < 2:
        return {"pass": True, "areas_checked": len(rows), "detail": "Need 2+ areas to compare"}

    area_epochs = {area: avg_epoch for area, avg_epoch, _ in rows}
    area_latest = {area: str(latest) for area, _, latest in rows}

    # Compare each pair — flag if offset is a round number of hours (±10 min tolerance)
    drifted = []
    areas = list(area_epochs.keys())
    for i, a1 in enumerate(areas):
        for a2 in areas[i + 1:]:
            diff_sec = abs(area_epochs[a1] - area_epochs[a2])
            diff_hours = diff_sec / 3600
            nearest_hour = round(diff_hours)
            if nearest_hour >= 1 and abs(diff_hours - nearest_hour) < 0.17:
                # Within ~10 min of a round hour offset — suspicious
                behind = a1 if area_epochs[a1] < area_epochs[a2] else a2
                drifted.append({
                    "areas": (a1, a2),
                    "offset_hours": round(diff_hours, 1),
                    "behind": behind,
                    "diagnosis": (
                        f"{behind} timestamps are ~{nearest_hour}h behind — "
                        f"probable naive-timestamp leak (local time published as UTC)"
                    ),
                })

    return {
        "pass": len(drifted) == 0,
        "areas_checked": len(areas),
        "area_latest": area_latest,
        "drifted": drifted,
    }


def adapter_health(conn) -> dict:
    """Per-adapter health: row counts and latest reading by area."""
    cur = conn.execute("""
        SELECT
            ns.area,
            COUNT(*) AS rows,
            COUNT(DISTINCT ns.tag_name) AS tags,
            MAX(th.ts) AS last_reading
        FROM tag_history th
        JOIN uns_namespace ns ON ns.id = th.tag_id
        GROUP BY ns.area
        ORDER BY ns.area
    """)
    rows = cur.fetchall()
    adapters = {}
    for area, row_count, tag_count, last_reading in rows:
        adapters[area] = {
            "rows": row_count,
            "tags": tag_count,
            "last_reading": str(last_reading),
        }
    return adapters


def check_broker():
    """Verify the MQTT broker is reachable."""
    import socket
    host = os.getenv("MQTT_BROKER_HOST", "localhost")
    port = int(os.getenv("MQTT_BROKER_PORT", "1883"))
    try:
        sock = socket.create_connection((host, port), timeout=3)
        sock.close()
        return True
    except (ConnectionRefusedError, OSError):
        return False


def run_simulation(sweeps: int, wait: int):
    """Run the simulator and wait for ingester to flush."""
    if not check_broker():
        host = os.getenv("MQTT_BROKER_HOST", "localhost")
        port = os.getenv("MQTT_BROKER_PORT", "1883")
        print(f"\nFAIL: MQTT broker not reachable at {host}:{port}")
        print("  Run: docker compose up -d")
        sys.exit(1)
    print(f"\nRunning simulator: {sweeps} sweeps...")
    subprocess.run(
        ["uv", "run", "scripts/simulate_source.py", "--rate", "0.5",
         "--iterations", str(sweeps)],
        check=True,
    )
    print(f"Waiting {wait}s for ingester to flush...")
    time.sleep(wait)


def main():
    parser = argparse.ArgumentParser(description="Validate UNS pipeline end-to-end")
    parser.add_argument("--simulate", action="store_true",
                        help="Run simulator before validating")
    parser.add_argument("--sweeps", type=int, default=5,
                        help="Number of simulator sweeps (default: 5)")
    parser.add_argument("--wait", type=int, default=15,
                        help="Seconds to wait after simulation for flush (default: 15)")
    args = parser.parse_args()

    print("=" * 70)
    print(" UNS Pipeline Validation")
    print("=" * 70)

    conn = get_connection()

    # Auto-detect: if tag_history is empty and --simulate not explicitly passed,
    # warn the user. If --simulate is passed, run it.
    cur = conn.execute("SELECT COUNT(*) FROM tag_history")
    row_count = cur.fetchone()[0]

    if args.simulate:
        run_simulation(args.sweeps, args.wait)
        # Refresh connection after simulation to see new data
        conn.close()
        conn = get_connection()
    elif row_count == 0:
        print("\n  tag_history is empty. Run with --simulate to populate,")
        print("  or ensure the ingester + adapters/simulator have run first.")
        print("  Usage: uv run scripts/validate_pipeline.py --simulate --sweeps 5 --wait 15")
        conn.close()
        sys.exit(1)
    expected_tags = load_expected_tags()
    all_pass = True

    # 1. Tag coverage
    print("\n[1/6] Tag coverage...")
    result = check_tag_coverage(conn, expected_tags)
    status = "PASS" if result["pass"] else "FAIL"
    print(f"  {status}: {result['covered']}/{result['total']} tags have data")
    if result["missing"]:
        print(f"  Missing: {sorted(result['missing'])}")
    if result["not_seeded"]:
        print(f"  Not in uns_namespace: {sorted(result['not_seeded'])}")
    all_pass &= result["pass"]

    # 2. Timestamp sanity
    print("\n[2/6] Timestamp sanity...")
    result = check_timestamp_sanity(conn)
    status = "PASS" if result["pass"] else "FAIL"
    if result["total_rows"] == 0:
        print(f"  FAIL: No rows in tag_history")
    else:
        print(f"  {status}: {result['total_rows']} rows, range {result['earliest']} to {result['latest']}")
        if result["future_rows"] > 0:
            print(f"  Future timestamps: {result['future_rows']} rows (timezone bug?)")
    all_pass &= result["pass"]

    # 3. Foreign key integrity
    print("\n[3/6] Foreign key integrity...")
    result = check_foreign_keys(conn)
    status = "PASS" if result["pass"] else "FAIL"
    print(f"  {status}: {result['orphan_rows']} orphan rows")
    all_pass &= result["pass"]

    # 4. Dead letter
    print("\n[4/6] Dead letter analysis...")
    result = check_dead_letter(conn)
    status = "PASS" if result["pass"] else "FAIL"
    print(f"  {status}: {result['total']} rejected payloads")
    if result["reasons"]:
        for reason, cnt in result["reasons"].items():
            print(f"    {reason}: {cnt}")
    all_pass &= result["pass"]

    # 5. Value plausibility
    print("\n[5/6] Value plausibility...")
    result = check_value_plausibility(conn)
    status = "PASS" if result["pass"] else "FAIL"
    print(f"  {status}")
    for issue in result["issues"]:
        print(f"    {issue}")
    all_pass &= result["pass"]

    # 6. Cross-area timestamp drift
    print("\n[6/6] Cross-area timestamp drift...")
    result = check_cross_area_timestamp_drift(conn)
    status = "PASS" if result["pass"] else "FAIL"
    print(f"  {status}: {result['areas_checked']} areas compared")
    if result.get("drifted"):
        for d in result["drifted"]:
            print(f"    {d['diagnosis']}")
    if result.get("area_latest"):
        for area, ts in result["area_latest"].items():
            print(f"    {area}: latest = {ts}")
    all_pass &= result["pass"]

    # Adapter health summary
    print("\n" + "-" * 70)
    print("Adapter health (by area):")
    adapters = adapter_health(conn)
    for area, data in adapters.items():
        print(f"  {area}: {data['tags']} tags, {data['rows']} rows, last: {data['last_reading']}")

    if not adapters:
        print("  No data in tag_history.")

    conn.close()

    # Verdict
    print("\n" + "=" * 70)
    if all_pass:
        print(" PIPELINE VALIDATION: PASS")
    else:
        print(" PIPELINE VALIDATION: FAIL — see details above")
    print("=" * 70)

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
