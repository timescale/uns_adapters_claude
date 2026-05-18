"""Unit tests for DBWriter._dead_letter() warning path.

No real DB connection required — psycopg is monkeypatched so the bare
except branch (unexpected DB error) can be exercised in isolation.
"""

import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from shared.db import DBWriter
from shared.models import Quality, Reading


def _make_writer() -> DBWriter:
    """Return a DBWriter with a mocked psycopg connection (no real DB)."""
    with patch("shared.db.psycopg.connect") as mock_connect:
        mock_connect.return_value = MagicMock()
        writer = DBWriter("postgresql://fake/db")
    return writer


def _reading() -> Reading:
    return Reading("motor_running", datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc), 1.0, Quality.GOOD)


class TestDeadLetterWarning:
    def test_unexpected_db_error_emits_warning(self, caplog):
        """A non-UndefinedTable/UndefinedColumn exception must log a WARNING
        rather than silently swallowing the error."""
        writer = _make_writer()
        # Make cursor().__enter__ raise a generic RuntimeError — simulates
        # any unexpected DB error that isn't a schema-missing case.
        writer.conn.cursor.return_value.__enter__.side_effect = RuntimeError("boom")
        writer.conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with caplog.at_level(logging.WARNING, logger="shared.db"):
            writer._dead_letter("modbus", _reading(), "test reason")

        assert any(
            "dead_letter write failed" in r.message and "motor_running" in r.message
            for r in caplog.records
        ), f"Expected warning not found. Records: {[r.message for r in caplog.records]}"

    def test_warning_names_source_and_tag(self, caplog):
        """The warning message must include both source and tag_name so the
        operator can trace which client dropped what reading."""
        writer = _make_writer()
        writer.conn.cursor.return_value.__enter__.side_effect = RuntimeError("connection reset")

        with caplog.at_level(logging.WARNING, logger="shared.db"):
            writer._dead_letter("opcua", _reading(), "some reason")

        warning_text = " ".join(r.message for r in caplog.records)
        assert "opcua" in warning_text
        assert "motor_running" in warning_text

    def test_undefined_table_does_not_warn(self, caplog):
        """UndefinedTable (schema not applied) must stay silent — it is an
        expected transient state, not an operator-actionable error."""
        import psycopg.errors

        writer = _make_writer()
        writer.conn.cursor.return_value.__enter__.side_effect = (
            psycopg.errors.UndefinedTable()
        )

        with caplog.at_level(logging.WARNING, logger="shared.db"):
            writer._dead_letter("mqtt", _reading(), "reason")

        assert not any("dead_letter write failed" in r.message for r in caplog.records)
