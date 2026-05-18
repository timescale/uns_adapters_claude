#!/usr/bin/env python3
"""
Seed uns_namespace from the contract artifacts.

Reads contracts/equipment_hierarchy.yaml and contracts/tag_database.csv and
inserts one row per (enterprise, site, area, line, cell, tag_name) tuple into
uns_namespace. Idempotent via ON CONFLICT DO NOTHING.

Run after docker compose up + psql -f db/schema.sql against Tiger Cloud.

    uv run scripts/seed_namespace.py
"""

import csv
import os
from pathlib import Path

import psycopg
import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
HIERARCHY_PATH = ROOT / "contracts" / "equipment_hierarchy.yaml"
TAGS_PATH = ROOT / "contracts" / "tag_database.csv"
DSN = os.getenv("TIMESCALE_SERVICE_URL")

if not DSN:
    raise SystemExit("TIMESCALE_SERVICE_URL not set — copy .env.example to .env")


def load_hierarchy() -> dict:
    with HIERARCHY_PATH.open() as f:
        return yaml.safe_load(f)


def load_tags() -> list[dict]:
    with TAGS_PATH.open() as f:
        return list(csv.DictReader(f))


def build_rows(hierarchy: dict, tags: list[dict]) -> list[tuple]:
    """Assemble uns_namespace rows from the hierarchy + tag database.

    The hierarchy supplies enterprise + site; per-tag rows supply area, line,
    cell, tag_name (plus metric_type and unit for metadata).
    """
    enterprise = hierarchy["enterprise"]
    site = hierarchy["site"]

    rows: list[tuple] = []
    for tag in tags:
        rows.append(
            (
                enterprise,
                site,
                tag["area"],
                tag["line"],
                tag["cell"],
                tag["tag_name"],
                tag.get("description"),
                tag["metric_type"],
                tag.get("unit"),
            )
        )
    return rows


def main() -> None:
    hierarchy = load_hierarchy()
    tags = load_tags()
    rows = build_rows(hierarchy, tags)

    print(f"Seeding {len(rows)} uns_namespace rows into {DSN.split('@')[-1]} ...")

    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO uns_namespace
                    (enterprise, site, area, line, cell, tag_name,
                     description, metric_type, unit)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (enterprise, site, area, line, cell, tag_name) DO NOTHING
                """,
                rows,
            )
            conn.commit()
            cur.execute("SELECT count(*) FROM uns_namespace")
            total = cur.fetchone()[0]

    print(f"Done. uns_namespace now has {total} rows.")


if __name__ == "__main__":
    main()
