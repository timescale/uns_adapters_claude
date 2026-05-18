#!/usr/bin/env python3
"""
Source simulator — publish fake Sparkplug B DDATA messages to the MQTT broker.

Lets the reader exercise the ingester without real OPC UA / Modbus / historian
hardware. Generates plausible-looking values for every tag in the tag database,
one message per tag per iteration.

This is NOT one of the adapters — it's a fixture. The real adapters (written
by the Agent Team in adapters/{opcua,modbus,historian}/src/) produce the same
topic + payload shape from real protocol sources.

    uv run scripts/simulate_source.py --rate 1.0
"""

import argparse
import csv
import json
import random
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import yaml
from dotenv import load_dotenv
import os

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
HIERARCHY_PATH = ROOT / "contracts" / "equipment_hierarchy.yaml"
TAGS_PATH = ROOT / "contracts" / "tag_database.csv"

BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))


def load_tags() -> list[dict]:
    with TAGS_PATH.open() as f:
        return list(csv.DictReader(f))


def load_site() -> str:
    with HIERARCHY_PATH.open() as f:
        return yaml.safe_load(f)["site"]


def fake_value(metric_type: str) -> object:
    if metric_type == "Float":
        return round(random.uniform(10.0, 100.0), 2)
    if metric_type == "Int32":
        return random.randint(0, 10000)
    if metric_type == "Boolean":
        return random.choice([True, False])
    return "ok"


def build_topic(site: str, tag: dict) -> str:
    # spBv1.0/{site}/DDATA/{area}/{line}/{cell}
    return f"spBv1.0/{site}/DDATA/{tag['area']}/{tag['line']}/{tag['cell']}"


def build_payload(tag: dict) -> dict:
    return {
        "timestamp_ms": int(time.time() * 1000),
        "metrics": [
            {
                "name": tag["tag_name"],
                "value": fake_value(tag["metric_type"]),
                "type": tag["metric_type"],
                "quality": "GOOD",
            }
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rate", type=float, default=1.0,
                        help="Seconds between full tag sweeps (default: 1.0)")
    parser.add_argument("--iterations", type=int, default=0,
                        help="Stop after N sweeps (0 = run forever)")
    args = parser.parse_args()

    site = load_site()
    tags = load_tags()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="uns-simulator",
    )
    client.connect(BROKER_HOST, BROKER_PORT, 60)
    client.loop_start()
    print(f"Publishing to {BROKER_HOST}:{BROKER_PORT}, {len(tags)} tags, "
          f"rate {args.rate}s/sweep. Ctrl+C to stop.")

    sweep = 0
    try:
        while args.iterations == 0 or sweep < args.iterations:
            for tag in tags:
                topic = build_topic(site, tag)
                payload = build_payload(tag)
                client.publish(topic, json.dumps(payload), qos=0)
            sweep += 1
            print(f"  Sweep {sweep}: {len(tags)} messages published")
            time.sleep(args.rate)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
