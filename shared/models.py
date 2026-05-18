"""Shared data models for UNS collection clients.

All clients import from here. Do NOT redefine these classes in client code.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Quality(Enum):
    """Data quality flags per UNS contract."""

    GOOD = "GOOD"
    BAD = "BAD"
    STALE = "STALE"


class MetricType(Enum):
    """Tag metric types per UNS contract.

    Only numeric and boolean types are in scope. String-valued tags
    require a schema amendment (tag_history.value is DOUBLE PRECISION).
    """

    Int32 = "Int32"
    Float = "Float"
    Boolean = "Boolean"


@dataclass
class Reading:
    """A single tag reading, mapping 1:1 to a tag_history row.

    Attributes:
        tag_name: Tag name from tag_database.csv (snake_case, exact match).
        ts:       Timezone-aware timestamp. Naive timestamps are rejected
                  by validate_reading(); clients must attach tzinfo before
                  constructing a Reading.
        value:    The numeric reading. Booleans are stored as 1.0 / 0.0.
        quality:  Data quality flag.
    """

    tag_name: str
    ts: datetime
    value: float
    quality: Quality = Quality.GOOD
