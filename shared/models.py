"""Shared data models for UNS adapters.

All adapters import from here. Do NOT redefine these classes in adapter code.
"""

from dataclasses import dataclass, field
from enum import Enum


class Quality(Enum):
    """Data quality flags per UNS contract."""
    GOOD = "GOOD"
    BAD = "BAD"
    STALE = "STALE"


class MetricType(Enum):
    """Sparkplug B metric types per UNS contract.

    Only numeric and boolean types are in scope. String-valued metrics
    require a schema amendment (tag_history.value is DOUBLE PRECISION).
    """
    Int32 = "Int32"
    Float = "Float"
    Boolean = "Boolean"


@dataclass
class Metric:
    """A single metric reading.

    Attributes:
        name: Tag name from tag_database.csv (snake_case, exact match).
        value: The reading value, type must match `metric_type`.
        metric_type: Sparkplug B type from the type mapping table.
        quality: Data quality flag.
    """
    name: str
    value: int | float | bool
    metric_type: MetricType
    quality: Quality = Quality.GOOD


@dataclass
class SparkplugMessage:
    """A Sparkplug B DDATA message ready for publishing.

    Attributes:
        topic: Full Sparkplug B topic path.
            Format: spBv1.0/{group_id}/{message_type}/{edge_node_id}/{device_id}
        timestamp_ms: Unix epoch milliseconds, UTC. Never naive.
        metrics: List of metrics in this message.
    """
    topic: str
    timestamp_ms: int
    metrics: list[Metric] = field(default_factory=list)

    def to_payload(self) -> dict:
        """Serialize to the contract-defined JSON payload."""
        return {
            "timestamp_ms": self.timestamp_ms,
            "metrics": [
                {
                    "name": m.name,
                    "value": m.value,
                    "type": m.metric_type.value,
                    "quality": m.quality.value,
                }
                for m in self.metrics
            ],
        }
