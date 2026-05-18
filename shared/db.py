"""DB-write helper for UNS collection clients.

Every client imports DBWriter from here. Handles:

- uns_namespace → tag_id resolution (loaded once at startup, O(1) lookup).
  Keyed by tag_name; a restart is required if the namespace changes.

- Batched INSERT INTO tag_history (ts, tag_id, value, quality)
  ON CONFLICT (ts, tag_id) DO NOTHING
  via COPY into a temp staging table (same pattern as the retired ingester).

- Readings that fail validate_reading() or have no matching tag_id are
  routed to the dead_letter table (source column, not topic — the schema
  uses `source TEXT` since there are no MQTT topics in V7).

Usage:
    from shared.db import DBWriter

    with DBWriter(dsn, tag_database_path=Path("contracts/tag_database.csv")) as writer:
        writer.load_namespace()
        accepted, rejected = writer.write_readings(readings, source="modbus")
"""

import json
from pathlib import Path

import psycopg

from .models import Reading
from .validation import validate_reading


class DBWriter:
    """Write helper bound to a single psycopg connection.

    Public API
    ----------
    load_namespace() -> int
        Load uns_namespace tag_name → id mapping into memory.
        Returns the number of tags loaded.
        Call once after connecting; call again to refresh if the namespace
        has changed.

    write_readings(readings, source="") -> tuple[int, int]
        Validate, resolve tag_id, and batch-upsert a list of Reading objects.
        Returns (accepted_count, rejected_count).
        Rejected readings go to dead_letter with a reason string.

    close()
        Close the underlying connection. Also called automatically by the
        context-manager __exit__.
    """

    def __init__(
        self,
        dsn: str,
        tag_database_path: Path | None = None,
    ) -> None:
        """
        Args:
            dsn:               libpq connection string or DSN URL.
            tag_database_path: Path to contracts/tag_database.csv.
                               Passed through to validate_reading().
                               None disables tag-existence and type checks
                               (snake_case and tz-aware checks still run).
        """
        self.conn: psycopg.Connection = psycopg.connect(dsn)
        self.tag_database_path = tag_database_path
        self._namespace: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Namespace loader
    # ------------------------------------------------------------------

    def load_namespace(self) -> int:
        """Load uns_namespace into memory.

        Executes: SELECT tag_name, id FROM uns_namespace
        Builds a dict keyed by tag_name for O(1) lookup in write_readings.

        Returns:
            Number of tags loaded.
        """
        with self.conn.cursor() as cur:
            cur.execute("SELECT tag_name, id FROM uns_namespace")
            self._namespace = {row[0]: row[1] for row in cur.fetchall()}
        return len(self._namespace)

    # ------------------------------------------------------------------
    # Main write path
    # ------------------------------------------------------------------

    def write_readings(
        self,
        readings: list[Reading],
        source: str = "",
    ) -> tuple[int, int]:
        """Validate, resolve tag_id, and batch-upsert readings.

        Each reading goes through validate_reading() first. Readings that
        pass validation are resolved against the in-memory namespace map;
        if the tag_name has no matching row in uns_namespace, the reading
        is also rejected.

        Valid rows are collected and flushed as one batch. Rejected rows
        are written individually to dead_letter.

        Args:
            readings: List of Reading objects to write.
            source:   Free-text identifier for the originating client
                      (e.g. "modbus", "opcua", "mqtt"). Stored in
                      dead_letter.source for triage.

        Returns:
            (accepted, rejected) — row counts.
        """
        good: list[tuple] = []
        rejected = 0

        for r in readings:
            violations = validate_reading(r, self.tag_database_path)
            if violations:
                self._dead_letter(source, r, "; ".join(violations))
                rejected += 1
                continue

            tag_id = self._namespace.get(r.tag_name)
            if tag_id is None:
                self._dead_letter(
                    source,
                    r,
                    f"tag_name '{r.tag_name}' not found in uns_namespace "
                    "(namespace may need a refresh — call load_namespace())",
                )
                rejected += 1
                continue

            good.append((r.ts, tag_id, r.value, r.quality.value))

        if good:
            self._batch_upsert(good)

        return len(good), rejected

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _batch_upsert(self, rows: list[tuple]) -> None:
        """COPY rows into a temp staging table, then INSERT … ON CONFLICT.

        The temp table is dropped automatically on COMMIT (ON COMMIT DROP).
        Any exception rolls back the transaction and re-raises so the caller
        can decide whether to retry or surface the error.
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    "CREATE TEMP TABLE tmp_ingest "
                    "(LIKE tag_history INCLUDING DEFAULTS) ON COMMIT DROP"
                )
                with cur.copy(
                    "COPY tmp_ingest (ts, tag_id, value, quality) FROM STDIN"
                ) as copy:
                    for row in rows:
                        copy.write_row(row)
                cur.execute(
                    """
                    INSERT INTO tag_history (ts, tag_id, value, quality)
                    SELECT ts, tag_id, value, quality FROM tmp_ingest
                    ON CONFLICT (ts, tag_id) DO NOTHING
                    """
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _dead_letter(
        self,
        source: str,
        reading: Reading,
        reason: str,
    ) -> None:
        """Route a rejected reading to dead_letter.

        Uses the `source` column (TEXT) introduced in V7. The payload is
        stored as JSONB for later triage. Silently skips if the dead_letter
        table does not exist yet (e.g. schema not yet applied).
        """
        payload = {
            "tag_name": reading.tag_name,
            "ts": reading.ts.isoformat(),
            "value": reading.value,
            "quality": reading.quality.value,
        }
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO dead_letter (source, payload, reason) "
                    "VALUES (%s, %s, %s)",
                    (source, json.dumps(payload), reason),
                )
            self.conn.commit()
        except psycopg.errors.UndefinedTable:
            self.conn.rollback()
        except psycopg.errors.UndefinedColumn:
            # Schema not yet migrated (topic → source); skip gracefully.
            self.conn.rollback()
        except Exception:
            self.conn.rollback()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying database connection."""
        self.conn.close()

    def __enter__(self) -> "DBWriter":
        return self

    def __exit__(self, *_) -> None:
        self.close()
