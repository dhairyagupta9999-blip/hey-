"""Watcher Definition, Types, and Self-Health States for Hey Jev.

Defines:
- WatcherType: FILE, DIRECTORY, GIT, PROCESS, BROWSER, MISSION, PROJECT
- WatcherState: HEALTHY, DEGRADED, FAILED, DISABLED
- WatcherDefinition: Observable configuration with debouncing and bounded restarts
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class WatcherType(str, Enum):
    """Categorization of environmental and system watchers."""
    FILE = "FILE"
    DIRECTORY = "DIRECTORY"
    FILESYSTEM = "FILESYSTEM"
    GIT = "GIT"
    PROCESS = "PROCESS"
    BROWSER = "BROWSER"
    MISSION = "MISSION"
    PROJECT = "PROJECT"


class WatcherState(str, Enum):
    """Operational health state of a watcher."""
    INITIALIZING = "INITIALIZING"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    DISABLED = "DISABLED"


@dataclass
class WatcherDefinition:
    """Normalized definition of an active observer/watcher."""
    watcher_id: str = field(default_factory=lambda: f"watch_{uuid.uuid4().hex[:10]}")
    type: WatcherType = WatcherType.DIRECTORY
    target: str = ""                         # Target folder, file, process name, project_id, etc.
    name: str = ""
    target_path: Optional[str] = None
    poll_interval: Optional[float] = None
    watcher_type: Optional[Any] = None
    filters: List[str] = field(default_factory=list)      # File extensions (e.g. ["*.pdf", "*.py"])
    conditions: Dict[str, Any] = field(default_factory=dict)
    debounce_ms: float = 500.0              # Debounce interval to combine write bursts
    cooldown_sec: float = 5.0               # Minimum interval between trigger fires
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    last_event_at: Optional[float] = None
    last_trigger_at: Optional[float] = None
    task_template: Dict[str, Any] = field(default_factory=dict)
    permission_profile: List[str] = field(default_factory=list)
    state: WatcherState = WatcherState.INITIALIZING
    restart_count: int = 0
    max_restarts: int = 3
    policy_expires_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.watcher_type is not None:
            self.type = self.watcher_type
        if isinstance(self.type, str):
            try:
                self.type = WatcherType(self.type.upper())
            except ValueError:
                self.type = WatcherType.DIRECTORY
        self.watcher_type = self.type

        if self.target_path is not None and not self.target:
            self.target = self.target_path
        elif self.target and self.target_path is None:
            self.target_path = self.target

        if self.poll_interval is not None and "poll_interval" not in self.metadata:
            self.metadata["poll_interval"] = self.poll_interval
        elif "poll_interval" in self.metadata and self.poll_interval is None:
            self.poll_interval = float(self.metadata["poll_interval"])

        if isinstance(self.state, str):
            try:
                self.state = WatcherState(self.state.upper())
            except ValueError:
                self.state = WatcherState.HEALTHY

    def is_policy_expired(self, now: Optional[float] = None) -> bool:
        """Evaluate if watcher authorization window has expired."""
        current_time = now if now is not None else time.time()
        return bool(self.policy_expires_at is not None and current_time >= self.policy_expires_at)

    def can_trigger(self, now: Optional[float] = None) -> bool:
        """Check cooldown constraint."""
        if not self.enabled or self.state in (WatcherState.DISABLED, WatcherState.FAILED):
            return False
        if self.is_policy_expired(now):
            return False
        if self.cooldown_sec <= 0 or self.last_trigger_at is None:
            return True
        current_time = now if now is not None else time.time()
        return (current_time - self.last_trigger_at) >= self.cooldown_sec

    def record_event(self, now: Optional[float] = None) -> None:
        """Record raw incoming event timestamp."""
        self.last_event_at = now if now is not None else time.time()

    def record_trigger(self, now: Optional[float] = None) -> None:
        """Record executed trigger dispatch timestamp."""
        self.last_trigger_at = now if now is not None else time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "watcher_id": self.watcher_id,
            "type": self.type.value,
            "target": self.target,
            "filters": list(self.filters),
            "conditions": dict(self.conditions),
            "debounce_ms": self.debounce_ms,
            "cooldown_sec": self.cooldown_sec,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "last_event_at": self.last_event_at,
            "last_trigger_at": self.last_trigger_at,
            "task_template": dict(self.task_template),
            "permission_profile": list(self.permission_profile),
            "state": self.state.value,
            "restart_count": self.restart_count,
            "max_restarts": self.max_restarts,
            "policy_expires_at": self.policy_expires_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WatcherDefinition:
        d = dict(data)
        return cls(**d)
