"""UNS contract validation utilities.

Used by clients before writing and by the Lead for cross-client review.
"""

import csv
import re
from pathlib import Path

from .models import Reading

SNAKE_CASE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_reading(
    reading: Reading,
    tag_database_path: Path | None = None,
) -> list[str]:
    """Validate a Reading against the UNS contract.

    Checks performed:
    - tag_name is snake_case
    - ts is timezone-aware (naive timestamps are rejected)
    - tag_name exists in tag_database.csv  (when path is supplied)
    - value is compatible with the tag's metric_type (when tag is found)

    Args:
        reading:           The Reading to validate.
        tag_database_path: Path to contracts/tag_database.csv. When None or
                           missing, the tag-existence and type checks are
                           skipped (snake_case and tz checks still run).

    Returns:
        A list of violation strings. Empty list means the reading is valid.
    """
    violations: list[str] = []

    # --- snake_case ---
    if not SNAKE_CASE_PATTERN.match(reading.tag_name):
        violations.append(
            f"tag_name '{reading.tag_name}' is not snake_case"
        )

    # --- timezone-aware timestamp ---
    if (
        reading.ts.tzinfo is None
        or reading.ts.tzinfo.utcoffset(reading.ts) is None
    ):
        violations.append(
            f"ts {reading.ts!r} is naive; clients must attach timezone info "
            "(see CLAUDE.md timestamp rules)"
        )

    # --- tag database checks ---
    if tag_database_path is None or not tag_database_path.exists():
        return violations

    with open(tag_database_path, newline="") as f:
        tags: dict[str, dict] = {row["tag_name"]: row for row in csv.DictReader(f)}

    if reading.tag_name not in tags:
        violations.append(
            f"tag_name '{reading.tag_name}' not found in tag database"
        )
        return violations  # no point checking type if tag unknown

    metric_type = tags[reading.tag_name]["metric_type"]
    value = reading.value

    if metric_type == "Boolean":
        if value not in {0.0, 1.0}:
            violations.append(
                f"value {value!r} is not compatible with Boolean "
                "(expected 0.0 or 1.0)"
            )
    elif metric_type == "Int32":
        if not float(value).is_integer():
            violations.append(
                f"value {value!r} is not compatible with Int32 "
                "(must have no fractional part)"
            )
    # Float: any finite float is valid; no additional check needed.

    return violations
