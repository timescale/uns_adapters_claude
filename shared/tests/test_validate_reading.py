"""Unit tests for shared.validation.validate_reading().

No database connection required — all tests are pure logic against
in-memory Reading objects and a minimal temp CSV fixture.
"""

import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from shared.models import Quality, Reading
from shared.validation import validate_reading


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tag_db(tmp_path: Path) -> Path:
    """Minimal tag_database.csv covering Float, Int32, and Boolean types."""
    path = tmp_path / "tag_database.csv"
    rows = [
        {
            "tag_name": "temperature_bearing_de",
            "cell": "pump_042",
            "area": "area_01",
            "line": "line_03",
            "description": "Drive-end bearing temperature",
            "unit": "degC",
            "metric_type": "Float",
        },
        {
            "tag_name": "agitator_speed",
            "cell": "tank_101",
            "area": "area_01",
            "line": "line_01",
            "description": "Agitator rotational speed",
            "unit": "rpm",
            "metric_type": "Int32",
        },
        {
            "tag_name": "motor_running",
            "cell": "pump_042",
            "area": "area_01",
            "line": "line_03",
            "description": "Motor running status",
            "unit": "bool",
            "metric_type": "Boolean",
        },
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return path


def _ts(naive: bool = False) -> datetime:
    """Helper: returns a tz-aware UTC timestamp, or naive if requested."""
    dt = datetime(2024, 1, 15, 12, 0, 0)
    return dt if naive else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_float_tag_valid(self, tag_db: Path):
        r = Reading("temperature_bearing_de", _ts(), 82.5, Quality.GOOD)
        assert validate_reading(r, tag_db) == []

    def test_int32_tag_whole_number(self, tag_db: Path):
        r = Reading("agitator_speed", _ts(), 120.0, Quality.GOOD)
        assert validate_reading(r, tag_db) == []

    def test_boolean_tag_one(self, tag_db: Path):
        r = Reading("motor_running", _ts(), 1.0, Quality.GOOD)
        assert validate_reading(r, tag_db) == []

    def test_boolean_tag_zero(self, tag_db: Path):
        r = Reading("motor_running", _ts(), 0.0, Quality.GOOD)
        assert validate_reading(r, tag_db) == []

    def test_no_tag_db_skips_tag_checks(self):
        """With no tag_database_path, only snake_case and tz checks run."""
        r = Reading("unknown_tag", _ts(), 99.9, Quality.GOOD)
        assert validate_reading(r, None) == []

    def test_nonexistent_tag_db_path_skips_tag_checks(self, tmp_path: Path):
        r = Reading("some_tag", _ts(), 1.0, Quality.GOOD)
        assert validate_reading(r, tmp_path / "missing.csv") == []

    def test_aware_offset_timestamp_accepted(self, tag_db: Path):
        """Non-UTC tz-aware timestamps should pass the tz check."""
        ts = datetime(2024, 1, 15, 8, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
        r = Reading("temperature_bearing_de", ts, 22.0, Quality.GOOD)
        assert validate_reading(r, tag_db) == []


# ---------------------------------------------------------------------------
# snake_case violations
# ---------------------------------------------------------------------------

class TestSnakeCase:
    def test_camel_case_rejected(self, tag_db: Path):
        r = Reading("TemperatureBearingDe", _ts(), 1.0, Quality.GOOD)
        violations = validate_reading(r, tag_db)
        assert any("snake_case" in v for v in violations)

    def test_leading_digit_rejected(self, tag_db: Path):
        r = Reading("1temperature", _ts(), 1.0, Quality.GOOD)
        violations = validate_reading(r, tag_db)
        assert any("snake_case" in v for v in violations)

    def test_hyphen_rejected(self, tag_db: Path):
        r = Reading("temp-bearing", _ts(), 1.0, Quality.GOOD)
        violations = validate_reading(r, tag_db)
        assert any("snake_case" in v for v in violations)


# ---------------------------------------------------------------------------
# Naive timestamp violations
# ---------------------------------------------------------------------------

class TestNaiveTimestamp:
    def test_naive_ts_rejected(self, tag_db: Path):
        r = Reading("temperature_bearing_de", _ts(naive=True), 22.0, Quality.GOOD)
        violations = validate_reading(r, tag_db)
        assert any("naive" in v for v in violations)

    def test_naive_ts_no_tag_db_still_rejected(self):
        r = Reading("some_tag", _ts(naive=True), 1.0, Quality.GOOD)
        violations = validate_reading(r, None)
        assert any("naive" in v for v in violations)


# ---------------------------------------------------------------------------
# Tag-database violations
# ---------------------------------------------------------------------------

class TestTagDatabase:
    def test_unknown_tag_rejected(self, tag_db: Path):
        r = Reading("nonexistent_tag", _ts(), 1.0, Quality.GOOD)
        violations = validate_reading(r, tag_db)
        assert any("not found in tag database" in v for v in violations)

    def test_int32_fractional_rejected(self, tag_db: Path):
        r = Reading("agitator_speed", _ts(), 120.5, Quality.GOOD)
        violations = validate_reading(r, tag_db)
        assert any("Int32" in v for v in violations)

    def test_boolean_out_of_range_rejected(self, tag_db: Path):
        r = Reading("motor_running", _ts(), 0.5, Quality.GOOD)
        violations = validate_reading(r, tag_db)
        assert any("Boolean" in v for v in violations)

    def test_multiple_violations_reported(self, tag_db: Path):
        """Both snake_case and naive-ts violations should appear together."""
        r = Reading("BadTag", _ts(naive=True), 1.0, Quality.GOOD)
        violations = validate_reading(r, tag_db)
        assert len(violations) >= 2
