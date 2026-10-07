"""Trigger Model and Definition for Event-Driven Hey Jev.

Defines:
- TriggerType: Supported event triggers
- TriggerDefinition: Structured schema for time-based and reactive event triggers
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class TriggerType(str, Enum):
    """Supported trigger types for background tasks and automation."""
    TIME = "TIME"                        # One-shot epoch or ISO timestamp
    INTERVAL = "INTERVAL"                # Periodic execution every N seconds
    CRON = "CRON"                        # Standard cron schedule format
    STARTUP = "STARTUP"                  # Triggered when Jev initializes
    LOGIN = "LOGIN"                      # Triggered when Windows user logs in
    FILE_CHANGE = "FILE_CHANGE"          # Specific file modified/created/deleted
    DIRECTORY_CHANGE = "DIRECTORY_CHANGE"# Directory contents change
    PROCESS_START = "PROCESS_START"      # Target application or PID launches
    PROCESS_EXIT = "PROCESS_EXIT"        # Target application or PID terminates
    GIT_CHANGE = "GIT_CHANGE"            # Repository commit, branch, or dirty state change
    MISSION_EVENT = "MISSION_EVENT"      # Mission lifecycle event (completed, failed)
    TASK_EVENT = "TASK_EVENT"            # Task lifecycle event
    BROWSER_EVENT = "BROWSER_EVENT"      # Browser URL, title, or navigation event
    MANUAL = "MANUAL"                    # Triggered explicitly on user demand


@dataclass
class TriggerDefinition:
    """Normalized configuration for an event or temporal trigger."""
    trigger_id: str = field(default_factory=lambda: f"trig_{uuid.uuid4().hex[:10]}")
    type: TriggerType = TriggerType.TIME
    target: Optional[str] = None          # File path, URL, process name, or event pattern
    expression: Optional[str] = None      # Interval in seconds, time string, or cron spec
    trigger_type: Optional[Any] = None   # Alias for type
    interval_seconds: Optional[float] = None # Alias for expression
    conditions: Dict[str, Any] = field(default_factory=dict)
    debounce_ms: float = 500.0           # Combine rapid successive signals into 1 event
    cooldown_sec: float = 0.0            # Minimum spacing between trigger executions
    last_triggered_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.trigger_type is not None:
            self.type = self.trigger_type
        if isinstance(self.type, str):
            try:
                self.type = TriggerType(self.type.upper())
            except ValueError:
                self.type = TriggerType.MANUAL
        self.trigger_type = self.type

        if self.interval_seconds is not None and self.expression is None:
            self.expression = str(self.interval_seconds)
        elif self.expression is not None and self.interval_seconds is None:
            try:
                self.interval_seconds = float(self.expression)
            except (ValueError, TypeError):
                pass

    def can_trigger(self, now: Optional[float] = None) -> bool:
        """Check if cooldown period has passed."""
        if self.cooldown_sec <= 0 or self.last_triggered_at is None:
            return True
        current_time = now if now is not None else time.time()
        return (current_time - self.last_triggered_at) >= self.cooldown_sec

    def record_trigger(self, now: Optional[float] = None) -> None:
        """Update last triggered timestamp."""
        self.last_triggered_at = now if now is not None else time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "type": self.type.value,
            "target": self.target,
            "expression": self.expression,
            "conditions": dict(self.conditions),
            "debounce_ms": self.debounce_ms,
            "cooldown_sec": self.cooldown_sec,
            "last_triggered_at": self.last_triggered_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TriggerDefinition:
        d = dict(data)
        return cls(**d)
