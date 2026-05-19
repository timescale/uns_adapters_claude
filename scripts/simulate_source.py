#!/usr/bin/env python3
"""
V7 Source Simulator — stands up three fake industrial sources in-process.

Sources
-------
  Modbus-TCP server  (pymodbus 3.13)  holding registers from contracts/register_map.csv
  OPC UA server      (asyncua 1.1.x)  variable nodes    from contracts/opcua_node_map.csv
  MQTT publisher     (paho-mqtt 2.x)  JSON payloads     to   contracts/mqtt_topic_map.csv

No hardware or extra Docker services required.  The Modbus and OPC UA servers
bind to the addresses in .env (MODBUS_SOURCE_HOST/PORT, OPCUA_SOURCE_HOST/PORT).
The MQTT publisher connects to the Mosquitto broker in docker-compose.yml.

Protocol → area coverage
  modbus : pump_042 (area_01/line_03, 10 tags) + tank_101 (area_01/line_01, 5 tags)
  opcua  : tank_101 (area_01/line_01, 5 tags)  + oven_201 (area_02/line_01, 3 tags)
  mqtt   : conveyor_301 (area_02/line_02, 2 tags)

All sources emit clean, correct UTC-timestamped data.  Failure paths are
injected out-of-band via SQL (see scripts/validate_pipeline.py).

Usage
-----
    uv run scripts/simulate_source.py
    uv run scripts/simulate_source.py --rate 2.0
    uv run scripts/simulate_source.py --rate 1.0 --iterations 10
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import json
import logging
import os
import random
import signal
import struct
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
from asyncua import Server as OpcUaServer, ua
from dotenv import load_dotenv
from pymodbus.server import StartAsyncTcpServer
from pymodbus.simulator.simdata import DataType, SimData
from pymodbus.simulator.simdevice import SimDevice

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("simulator")

# Suppress verbose asyncua internal init messages (add_node / session noise)
logging.getLogger("asyncua").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
TAGS_PATH = ROOT / "contracts" / "tag_database.csv"
REG_MAP_PATH = ROOT / "contracts" / "register_map.csv"
OPCUA_MAP_PATH = ROOT / "contracts" / "opcua_node_map.csv"
MQTT_MAP_PATH = ROOT / "contracts" / "mqtt_topic_map.csv"

# ---------------------------------------------------------------------------
# Source endpoints (from env / .env)
# ---------------------------------------------------------------------------
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MODBUS_HOST = os.getenv("MODBUS_SOURCE_HOST", "localhost")
MODBUS_PORT = int(os.getenv("MODBUS_SOURCE_PORT", "5020"))
OPCUA_HOST = os.getenv("OPCUA_SOURCE_HOST", "localhost")
OPCUA_PORT = int(os.getenv("OPCUA_SOURCE_PORT", "4840"))

OPCUA_NS_URI = "urn:plant-chicago:opcua:simulator"

# OPC UA VariantType mapping from tag_database.csv metric_type / opcua_node_map data_type
_VARIANT_TYPES: dict[str, ua.VariantType] = {
    "Float": ua.VariantType.Float,
    "Int32": ua.VariantType.Int32,
    "Boolean": ua.VariantType.Boolean,
}


# ===========================================================================
# Data loading
# ===========================================================================

def load_tag_db() -> dict[str, dict]:
    """tag_database.csv → {tag_name: row_dict}."""
    with TAGS_PATH.open() as fh:
        return {r["tag_name"]: r for r in csv.DictReader(fh)}


def load_register_map() -> list[dict]:
    with REG_MAP_PATH.open() as fh:
        return list(csv.DictReader(fh))


def load_opcua_map() -> list[dict]:
    with OPCUA_MAP_PATH.open() as fh:
        return list(csv.DictReader(fh))


def load_mqtt_map() -> list[dict]:
    with MQTT_MAP_PATH.open() as fh:
        return list(csv.DictReader(fh))


# ===========================================================================
# Value generation
# ===========================================================================

def fake_value(metric_type: str, tag_name: str = "") -> float | int | bool:
    """Return a plausible physical value for the given metric type."""
    if metric_type == "Boolean":
        return random.random() > 0.1           # ~90 % running / on

    if metric_type == "Int32":
        return random.randint(60, 250)          # agitator rpm range

    # Float — narrow range by tag keyword for realism
    n = tag_name.lower()
    if "temperature" in n:
        return round(random.uniform(60.0, 200.0), 2)
    if "pressure" in n:
        return round(random.uniform(1.5, 8.0), 2)
    if "flow" in n or "speed" in n:
        return round(random.uniform(5.0, 50.0), 2)
    if "current" in n:
        return round(random.uniform(20.0, 80.0), 2)
    if "vibration" in n:
        return round(random.uniform(0.5, 8.0), 2)
    if "level" in n:
        return round(random.uniform(30.0, 90.0), 2)
    if "position" in n:
        return round(random.uniform(20.0, 80.0), 2)
    return round(random.uniform(10.0, 100.0), 2)


def cast_for_vtype(value: float | int | bool, metric_type: str) -> float | int | bool:
    """Cast a fake value to the Python type expected by the OPC UA VariantType."""
    if metric_type == "Boolean":
        return bool(value)
    if metric_type == "Int32":
        return int(value)
    return float(value)


# ===========================================================================
# Modbus helpers  (pymodbus 3.13 SimDevice / SimData API)
# ===========================================================================

def _encode_registers(
    value: float | int | bool,
    data_type: str,
    scale: float,
    offset: float,
) -> list[int]:
    """Convert a physical value to one or two 16-bit Modbus holding-register words.

    Encoding follows register_map.csv conventions:
      raw = (physical - offset) / scale_factor
      int16   → one word, unsigned 16-bit
      float32 → two words, IEEE 754 big-endian, high word first
      bool    → one word, 0 or 1
    """
    if data_type == "bool":
        return [1 if value else 0]

    raw = (float(value) - offset) / scale

    if data_type == "int16":
        word = max(0, min(65535, int(round(raw)))) & 0xFFFF
        return [word]

    if data_type == "float32":
        packed = struct.pack(">f", raw)
        hi = struct.unpack(">H", packed[0:2])[0]
        lo = struct.unpack(">H", packed[2:4])[0]
        return [hi, lo]

    log.warning("Unknown Modbus data_type '%s'; writing zero.", data_type)
    return [0]


def _update_modbus_store(
    store: dict[int, int],
    reg_map: list[dict],
    tag_db: dict,
) -> None:
    """Recompute fresh fake values into the Modbus register store dict.

    store maps wire_address → 16-bit word value.
    Wire address = CSV register_address - 40001  (0-indexed, FC3).
    """
    for row in reg_map:
        tag = tag_db.get(row["tag_name"])
        if tag is None:
            log.warning("[modbus] tag '%s' not in tag_database — skipping", row["tag_name"])
            continue
        wire_addr = int(row["register_address"]) - 40001
        phys = fake_value(tag["metric_type"], row["tag_name"])
        words = _encode_registers(
            phys,
            row["data_type"],
            float(row["scale_factor"]),
            float(row["offset"]),
        )
        for i, w in enumerate(words):
            store[wire_addr + i] = w


def build_modbus_device(
    reg_map: list[dict],
    tag_db: dict,
    store: dict[int, int],
) -> SimDevice:
    """Build a pymodbus SimDevice for the Modbus-TCP server.

    The device exposes a block of holding registers large enough to cover
    all addresses in register_map.csv.  On every read request, the `action`
    callback injects current values from `store`.  The sweep loop updates
    `store` independently; no locking needed (single asyncio event loop).
    """
    max_wire_addr = max(
        int(r["register_address"]) - 40001 + int(r["register_count"])
        for r in reg_map
    )
    block_size = max(max_wire_addr + 10, 200)

    # Pre-populate store with initial values
    _update_modbus_store(store, reg_map, tag_db)

    async def action(
        fc: int,
        start_addr: int,
        address: int,
        count: int,
        current_registers: list[int],
        set_values: list[int] | list[bool] | None,
    ) -> None:
        """Inject current fake values for the requested holding-register range.

        pymodbus 3.13 passes the *entire* register block as current_registers.
        The server reads back the response from current_registers[offset + i]
        where offset = address - start_addr.  We must therefore write to that
        exact position, not to index i.
        """
        if set_values is not None:
            return  # ignore write requests
        offset = address - start_addr   # position of first requested reg in the block
        for i in range(count):
            current_registers[offset + i] = store.get(address + i, 0)

    return SimDevice(
        id=1,
        simdata=[SimData(address=0, count=block_size, values=0, datatype=DataType.REGISTERS)],
        action=action,
    )


# ===========================================================================
# OPC UA helpers  (asyncua 1.1.x)
# ===========================================================================

async def setup_opcua_nodes(
    server: OpcUaServer,
    ns_idx: int,
    opcua_map: list[dict],
    tag_db: dict,
) -> dict[str, tuple[object, ua.VariantType]]:
    """Add Variable nodes under a PlantChicago object; return {tag_name: (node, vtype)}."""
    obj_node = await server.nodes.objects.add_object(ns_idx, "PlantChicago")
    node_vars: dict[str, tuple[object, ua.VariantType]] = {}

    for row in opcua_map:
        tag_name = row["tag_name"]
        tag = tag_db.get(tag_name)
        if tag is None:
            log.warning("[opcua] tag '%s' not in tag_database — skipping", tag_name)
            continue

        vtype = _VARIANT_TYPES.get(row["data_type"], ua.VariantType.Double)
        initial = cast_for_vtype(fake_value(tag["metric_type"], tag_name), tag["metric_type"])

        # Explicit string NodeId from the contract CSV: ns=ns_idx;s=<node_id>
        # Passing ua.NodeId as the first argument forces asyncua to use it verbatim
        # instead of auto-assigning a numeric NodeId (e.g. ns=2;i=1001).
        # Clients address nodes exactly as declared: client.get_node("ns=2;s=TK101.TEMP")
        nodeid = ua.NodeId(row["node_id"], ns_idx)
        bname = ua.QualifiedName(row["node_id"], ns_idx)
        var = await obj_node.add_variable(nodeid, bname, initial, vtype)
        await var.set_writable()
        node_vars[tag_name] = (var, vtype)

    return node_vars


async def update_opcua_nodes(
    node_vars: dict[str, tuple[object, ua.VariantType]],
    tag_db: dict,
) -> None:
    """Write a fresh sweep of fake values to all OPC UA nodes."""
    for tag_name, (var, vtype) in node_vars.items():
        tag = tag_db[tag_name]
        new_val = cast_for_vtype(fake_value(tag["metric_type"], tag_name), tag["metric_type"])
        await var.write_value(ua.DataValue(ua.Variant(new_val, vtype)))


# ===========================================================================
# MQTT helpers  (paho-mqtt 2.x)
# ===========================================================================

def publish_mqtt_messages(
    client: mqtt.Client,
    mqtt_map: list[dict],
    tag_db: dict,
) -> None:
    """Publish one JSON message per topic to the MQTT broker.

    Payload schema:
        {"ts_ms": <epoch-ms UTC>, "tag_name": "...", "value": ..., "quality": "GOOD"}

    ts_ms is always UTC epoch-ms — the mqtt-client needs no timezone conversion
    (source_timezone: UTC in config/mqtt.yaml).
    """
    ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    for row in mqtt_map:
        tag = tag_db.get(row["tag_name"])
        if tag is None:
            continue
        val = fake_value(tag["metric_type"], row["tag_name"])
        # Booleans serialised as int for JSON clarity
        if isinstance(val, bool):
            val = int(val)
        payload = json.dumps({
            "ts_ms": ts_ms,
            "tag_name": row["tag_name"],
            "value": val,
            "quality": "GOOD",
        })
        client.publish(row["mqtt_topic"], payload, qos=0)


# ===========================================================================
# Main async entry point
# ===========================================================================

async def main_async(args: argparse.Namespace) -> None:
    # -----------------------------------------------------------------------
    # 1. Load contract files
    # -----------------------------------------------------------------------
    tag_db = load_tag_db()
    reg_map = load_register_map()
    opcua_map = load_opcua_map()
    mqtt_map = load_mqtt_map()

    # -----------------------------------------------------------------------
    # 2. Modbus: shared store dict + SimDevice with action callback
    # -----------------------------------------------------------------------
    modbus_store: dict[int, int] = {}
    modbus_device = build_modbus_device(reg_map, tag_db, modbus_store)

    # -----------------------------------------------------------------------
    # 3. OPC UA server — init before entering the async-context-manager
    # -----------------------------------------------------------------------
    opcua_server = OpcUaServer()
    await opcua_server.init()
    opcua_server.set_endpoint(f"opc.tcp://{OPCUA_HOST}:{OPCUA_PORT}/")
    ns_idx = await opcua_server.register_namespace(OPCUA_NS_URI)
    opcua_node_vars = await setup_opcua_nodes(opcua_server, ns_idx, opcua_map, tag_db)

    # -----------------------------------------------------------------------
    # 4. MQTT paho client (threaded I/O, driven from asyncio)
    # -----------------------------------------------------------------------
    mqtt_paho = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="uns-simulator-mqtt",
    )
    mqtt_ok = False
    try:
        mqtt_paho.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
        mqtt_paho.loop_start()
        mqtt_ok = True
    except Exception as exc:  # noqa: BLE001
        log.warning("[mqtt] broker unreachable (%s) — MQTT source skipped", exc)

    # -----------------------------------------------------------------------
    # 5. Modbus TCP server as a background asyncio task
    #    pymodbus 3.13: pass SimDevice directly to StartAsyncTcpServer
    # -----------------------------------------------------------------------
    modbus_task = asyncio.create_task(
        StartAsyncTcpServer(context=modbus_device, address=(MODBUS_HOST, MODBUS_PORT))
    )

    # -----------------------------------------------------------------------
    # 6. Signal handling — stop event for clean shutdown
    # -----------------------------------------------------------------------
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    # -----------------------------------------------------------------------
    # 7. Sweep loop inside the OPC UA server context
    # -----------------------------------------------------------------------
    sweep = 0
    try:
        async with opcua_server:
            print("=" * 60)
            print("UNS V7 Source Simulator")
            print("=" * 60)
            print(f"  modbus  tcp://{MODBUS_HOST}:{MODBUS_PORT}"
                  f"  ({len(reg_map)} registers, area_01)")
            print(f"  opcua   opc.tcp://{OPCUA_HOST}:{OPCUA_PORT}/"
                  f"  ({len(opcua_node_vars)} nodes, area_01+area_02)")
            if mqtt_ok:
                print(f"  mqtt    {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}"
                      f"  ({len(mqtt_map)} topics, area_02)")
            else:
                print(f"  mqtt    OFFLINE — start Mosquitto and retry")
            mode = (
                f"{args.iterations} sweeps"
                if args.iterations > 0
                else "continuous (Ctrl+C to stop)"
            )
            print(f"\n  rate={args.rate}s  mode={mode}\n")

            while (
                not stop_event.is_set()
                and (args.iterations == 0 or sweep < args.iterations)
            ):
                # Update all three sources
                _update_modbus_store(modbus_store, reg_map, tag_db)
                await update_opcua_nodes(opcua_node_vars, tag_db)
                if mqtt_ok:
                    publish_mqtt_messages(mqtt_paho, mqtt_map, tag_db)

                sweep += 1
                log.info(
                    "sweep %4d | modbus=%d  opcua=%d  mqtt=%d",
                    sweep,
                    len(reg_map),
                    len(opcua_node_vars),
                    len(mqtt_map) if mqtt_ok else 0,
                )

                # Sleep for rate seconds, but wake early on stop_event
                if args.iterations == 0 or sweep < args.iterations:
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(stop_event.wait(), timeout=args.rate)

    finally:
        modbus_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await modbus_task
        if mqtt_ok:
            mqtt_paho.loop_stop()
            mqtt_paho.disconnect()
        print(f"\nStopped after {sweep} sweep(s).")


# ===========================================================================
# CLI
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "UNS V7 source simulator: Modbus-TCP server + OPC UA server + MQTT publisher, "
            "all in-process. No hardware or extra Docker services required."
        )
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="Seconds between full sweeps across all sources (default: 1.0)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=0,
        metavar="N",
        help="Stop after N sweeps; 0 = run until Ctrl+C (default: 0)",
    )
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
