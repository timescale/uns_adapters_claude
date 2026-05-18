#!/usr/bin/env python3
"""
MQTT -> TimescaleDB ingester (reference implementation).

Subscribes to Sparkplug B DDATA messages, decomposes each topic into the
six-level UNS path (enterprise/site/area/line/cell/tag_name), resolves
tag_id via the unique constraint on uns_namespace, and batch-inserts into
the tag_history hypertable using COPY + staging + ON CONFLICT DO NOTHING.

This is a REFERENCE — tuned for clarity over throughput. For the full
batching strategy with back-pressure, graceful shutdown under overload,
and benchmarked performance, see the companion article:

    From MQTT to SQL: A Practical Guide to Sensor Data Ingestion
    https://www.tigerdata.com/blog/mqtt-sql-practical-guide-sensor-data-ingestion

Run:
    uv run ingester/main.py
"""

import json
import os
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
import psycopg
from dotenv import load_dotenv

load_dotenv()

BROKER_HOST    = os.getenv("MQTT_BROKER_HOST", "localhost")
BROKER_PORT    = int(os.getenv("MQTT_BROKER_PORT", "1883"))
INGEST_TOPIC   = os.getenv("INGEST_TOPIC", "spBv1.0/#")
FLUSH_INTERVAL = float(os.getenv("FLUSH_INTERVAL_SEC", "5.0"))
DSN            = os.getenv("TIMESCALE_SERVICE_URL")
ENTERPRISE     = os.getenv("UNS_ENTERPRISE", "acme_corp")

if not DSN:
    raise SystemExit("TIMESCALE_SERVICE_URL not set — copy .env.example to .env")

# Thread-safe buffer for batched writes
buffer: list[tuple] = []
buffer_lock = threading.Lock()
shutdown_flag = threading.Event()

accepted = 0
rejected = 0
batch_count = 0


# ---------------------------------------------------------------------------
# Startup: load the full uns_namespace into a dict for O(1) tag_id lookup.
# Avoids a DB round-trip per message at the cost of a restart when the
# namespace changes.
# ---------------------------------------------------------------------------
def load_namespace(conn: psycopg.Connection) -> dict[tuple[str, ...], int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT enterprise, site, area, line, cell, tag_name, id "
            "FROM uns_namespace"
        )
        return {row[:6]: row[6] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Sparkplug topic parsing.
# Format: spBv1.0/{group_id}/{message_type}/{edge_node_id}/{device_id}
# where edge_node_id = "{area}/{line}" and device_id = cell.
# ---------------------------------------------------------------------------
def parse_topic(topic: str) -> tuple[str, str, str, str]:
    """Return (site, area, line, cell) from a Sparkplug DDATA topic.

    Raises ValueError on malformed input.
    """
    parts = topic.split("/")
    if len(parts) != 6 or parts[0] != "spBv1.0":
        raise ValueError(f"Expected spBv1.0/site/DDATA/area/line/cell, got '{topic}'")
    if parts[2] != "DDATA":
        raise ValueError(f"Non-DDATA message: {parts[2]}")
    _, site, _verb, area, line, cell = parts
    return site, area, line, cell


# ---------------------------------------------------------------------------
# Dead-letter write. Direct insert (not batched) because rejections are rare.
# Requires a dead_letter table; if you haven't created one, comment out.
# ---------------------------------------------------------------------------
def dead_letter_write(conn: psycopg.Connection, topic: str,
                      payload: object, reason: str) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO dead_letter (topic, payload, reason) "
                "VALUES (%s, %s, %s)",
                (topic, json.dumps(payload) if payload is not None else None, reason),
            )
        conn.commit()
    except psycopg.errors.UndefinedTable:
        # dead_letter is optional; silently skip if not created yet.
        conn.rollback()
    except Exception as e:
        conn.rollback()
        print(f"  [dead_letter] failed: {e}")


# ---------------------------------------------------------------------------
# Batch flush: COPY into a temp staging table, then INSERT ... ON CONFLICT.
# Same pattern as the MQTT-SQL companion article.
# ---------------------------------------------------------------------------
def flush_buffer(conn: psycopg.Connection) -> None:
    global batch_count
    if shutdown_flag.is_set():
        return

    with buffer_lock:
        if not buffer:
            if not shutdown_flag.is_set():
                threading.Timer(FLUSH_INTERVAL, lambda: flush_buffer(conn)).start()
            return
        batch = buffer[:]
        buffer.clear()

    start = time.time()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TEMP TABLE tmp_ingest "
                "(LIKE tag_history INCLUDING DEFAULTS) ON COMMIT DROP"
            )
            with cur.copy(
                "COPY tmp_ingest (ts, tag_id, value, quality) FROM STDIN"
            ) as copy:
                for row in batch:
                    copy.write_row(row)
            cur.execute(
                """
                INSERT INTO tag_history (ts, tag_id, value, quality)
                SELECT ts, tag_id, value, quality FROM tmp_ingest
                ON CONFLICT (ts, tag_id) DO NOTHING
                """
            )
        conn.commit()
        batch_count += 1
        latency_ms = (time.time() - start) * 1000
        print(f"  Batch {batch_count:3d} | {len(batch):4d} rows | {latency_ms:5.1f}ms | "
              f"accepted: {accepted:,}  rejected: {rejected:,}")
    except Exception as e:
        conn.rollback()
        print(f"  [flush] error: {e}")

    if not shutdown_flag.is_set():
        threading.Timer(FLUSH_INTERVAL, lambda: flush_buffer(conn)).start()


# ---------------------------------------------------------------------------
# MQTT callbacks. One Sparkplug DDATA payload may contain multiple metrics;
# each metric becomes one row.
# ---------------------------------------------------------------------------
def make_on_message(namespace: dict[tuple[str, ...], int], conn: psycopg.Connection):
    def on_message(client, userdata, message, properties=None):
        global accepted, rejected
        topic = message.topic
        raw = None
        try:
            raw = json.loads(message.payload.decode())
            site, area, line, cell = parse_topic(topic)

            timestamp_ms = raw.get("timestamp_ms")
            if not isinstance(timestamp_ms, int):
                raise ValueError("missing or non-int timestamp_ms")
            ts = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)

            metrics = raw.get("metrics") or []
            if not metrics:
                raise ValueError("payload has no metrics")

            rows: list[tuple] = []
            for m in metrics:
                key = (ENTERPRISE, site, area, line, cell, m["name"])
                tag_id = namespace.get(key)
                if tag_id is None:
                    raise ValueError(f"unknown tag: {'/'.join(key)}")
                value = m["value"]
                if isinstance(value, bool):
                    value = 1.0 if value else 0.0
                rows.append((ts, tag_id, float(value), m.get("quality", "GOOD")))

            with buffer_lock:
                buffer.extend(rows)
            accepted += len(rows)

        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            rejected += 1
            dead_letter_write(conn, topic, raw, str(e))

    return on_message


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(INGEST_TOPIC)
        print(f"+ subscribed to {INGEST_TOPIC}")
    else:
        print(f"! MQTT connection failed: {reason_code}")


# ---------------------------------------------------------------------------
# Graceful shutdown: flush what's buffered, print a summary.
# ---------------------------------------------------------------------------
def make_signal_handler(conn: psycopg.Connection):
    def handler(signum, frame):
        global batch_count
        print("\n\nshutdown signal - flushing buffer...")
        shutdown_flag.set()
        with buffer_lock:
            if buffer:
                batch = buffer[:]
                buffer.clear()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "CREATE TEMP TABLE tmp_ingest "
                            "(LIKE tag_history INCLUDING DEFAULTS) ON COMMIT DROP"
                        )
                        with cur.copy(
                            "COPY tmp_ingest (ts, tag_id, value, quality) FROM STDIN"
                        ) as copy:
                            for row in batch:
                                copy.write_row(row)
                        cur.execute(
                            """
                            INSERT INTO tag_history (ts, tag_id, value, quality)
                            SELECT ts, tag_id, value, quality FROM tmp_ingest
                            ON CONFLICT (ts, tag_id) DO NOTHING
                            """
                        )
                    conn.commit()
                    batch_count += 1
                    print(f"+ flushed {len(batch)} remaining rows")
                except Exception as e:
                    conn.rollback()
                    print(f"! final flush failed: {e}")
        print(f"\nSummary:\n  accepted: {accepted:,}\n  rejected: {rejected:,}\n  batches:  {batch_count}")
        conn.close()
        os._exit(0)
    return handler


def main() -> None:
    print("=" * 70)
    print("UNS MQTT -> TimescaleDB ingester")
    print("=" * 70)
    print(f"  broker:         {BROKER_HOST}:{BROKER_PORT}")
    print(f"  topic:          {INGEST_TOPIC}")
    print(f"  flush interval: {FLUSH_INTERVAL}s")
    print(f"  enterprise:     {ENTERPRISE}")

    conn = psycopg.connect(DSN)
    namespace = load_namespace(conn)
    print(f"  namespace:      {len(namespace)} tags loaded from uns_namespace\n")

    signal.signal(signal.SIGINT,  make_signal_handler(conn))
    signal.signal(signal.SIGTERM, make_signal_handler(conn))

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="uns-ingester",
    )
    client.on_connect = on_connect
    client.on_message = make_on_message(namespace, conn)
    client.connect(BROKER_HOST, BROKER_PORT, 60)

    threading.Timer(FLUSH_INTERVAL, lambda: flush_buffer(conn)).start()
    print("Ctrl+C to stop\n")
    client.loop_forever()


if __name__ == "__main__":
    main()
