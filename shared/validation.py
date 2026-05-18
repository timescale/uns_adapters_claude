"""UNS contract validation utilities.

Used by adapters in tests and by the Lead for cross-adapter review.
"""

import csv
import re
from pathlib import Path

from .models import SparkplugMessage

# Sparkplug B topic pattern
TOPIC_PATTERN = re.compile(
    r"^spBv1\.0/[a-z][a-z0-9_]*/(?:DDATA|DBIRTH|DDEATH)/[a-z][a-z0-9_/]*/[a-z][a-z0-9_]*$"
)

SNAKE_CASE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_topic_path(topic: str) -> list[str]:
    """Validate a Sparkplug B topic path against the UNS contract.

    Returns a list of violations (empty = valid).
    """
    violations = []

    if not TOPIC_PATTERN.match(topic):
        violations.append(f"Topic path does not match Sparkplug B format: {topic}")

    segments = topic.split("/")
    for i, segment in enumerate(segments):
        if i == 0:  # "spBv1.0" — skip
            continue
        if i == 2:  # message_type — uppercase is expected
            continue
        if not SNAKE_CASE_PATTERN.match(segment):
            violations.append(f"Segment '{segment}' is not snake_case in topic: {topic}")

    return violations


def validate_message(msg: SparkplugMessage, tag_database_path: Path | None = None) -> list[str]:
    """Validate a SparkplugMessage against the UNS contract.

    Returns a list of violations (empty = valid).
    """
    violations = []

    # Topic
    violations.extend(validate_topic_path(msg.topic))

    # Timestamp
    if msg.timestamp_ms <= 0:
        violations.append("timestamp_ms must be positive")
    if msg.timestamp_ms < 1_000_000_000_000:
        violations.append("timestamp_ms appears to be seconds, not milliseconds")

    # Metrics
    if not msg.metrics:
        violations.append("Message has no metrics")

    valid_tags = None
    if tag_database_path and tag_database_path.exists():
        with open(tag_database_path) as f:
            reader = csv.DictReader(f)
            valid_tags = {row["tag_name"] for row in reader}

    for m in msg.metrics:
        if not SNAKE_CASE_PATTERN.match(m.name):
            violations.append(f"Metric name '{m.name}' is not snake_case")
        if valid_tags and m.name not in valid_tags:
            violations.append(f"Metric '{m.name}' not found in tag database")

    return violations
