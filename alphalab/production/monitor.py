"""Alert generation and deterministic monitoring logic."""

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Alert:
    """Immutable alert triggered by adverse runtime conditions."""
    alert_id: str
    severity: str
    message: str
    timestamp: float
    active: bool = True

def create_alert(severity: str, message: str, timestamp: float) -> Alert:
    return Alert(str(uuid.uuid4()), severity, message, timestamp)