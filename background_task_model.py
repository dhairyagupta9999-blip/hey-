"""Background Task Definition, States, and Resource Classes for Hey Jev.

Defines:
- BackgroundTaskState: Explicit lifecycle states
- BackgroundTaskPriority: Priority levels (CRITICAL, HIGH, NORMAL, LOW)
- ResourceClass: LIGHT, NORMAL, HEAVY
- NotificationPolicy: Notification levels and routing
- BackgroundTaskDefinition: Normalized persistent schema
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class BackgroundTaskState(str, Enum):
    """Lifecycle states of a background task."""
    DRAFT = "DRAFT"
    DISABLED = "DISABLED"
    SCHEDULED = "SCHEDULED"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class BackgroundTaskPriority(str, Enum):
    """Execution priority levels for background tasks."""
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


class ResourceClass(str, Enum):
    """Resource footprint classification for scheduling and concurrency."""
    LIGHT = "LIGHT"      # Git inspection, file watching, health query
    NORMAL = "NORMAL"    # Web page check, file download, research query
    HEAVY = "HEAVY"      # Local LLM, image processing, full directory index


class NotificationLevel(str, Enum):
    """Urgency level governing user notifications."""
    SILENT = "SILENT"
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    IMPORTANT = "IMPORTANT"
    URGENT = "URGENT"
    CRITICAL = "CRITICAL"


@dataclass
class BackgroundTaskDefinition:
    """Normalized definition of a persistent background task."""
    background_task_id: str = field(default_factory=lambda: f"bg_{uuid.uuid4().hex[:10]}")
    name: str = ""
    description: str = ""
    mission_id: Optional[str] = None
    skill_id: Optional[str] = None
    task_template: Dict[str, Any] = field(default_factory=dict)
    trigger_type: str = "TIME"
    schedule: Optional[str] = None  # Interval in seconds, ISO timestamp, or cron string
    conditions: Dict[str, Any] = field(default_factory=dict)
    state: BackgroundTaskState = BackgroundTaskState.READY
    priority: BackgroundTaskPriority = BackgroundTaskPriority.NORMAL
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    next_run_at: Optional[float] = None
    last_run_at: Optional[float] = None
    last_result: Optional[Dict[str, Any]] = None
    failure_count: int = 0
    max_retries: int = 3
    retry_policy: Dict[str, Any] = field(default_factory=lambda: {"backoff_sec": 30, "max_backoff_sec": 300})
    resource_class: ResourceClass = ResourceClass.NORMAL
    permission_profile: List[str] = field(default_factory=list)
    notification_policy: NotificationLevel = NotificationLevel.NORMAL
    is_interactive: bool = False  # Headless (background) vs Interactive (requires user desktop session)
    task_id: Optional[str] = None
    action_type: Optional[str] = None
    action_payload: Optional[Dict[str, Any]] = None
    trigger: Optional[Any] = None
    notification_level: Optional[Any] = None
    policy_expires_at: Optional[float] = None  # Temporary authorization boundary
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.task_id is not None:
            self.background_task_id = self.task_id
        else:
            self.task_id = self.background_task_id

        if self.action_type is not None:
            self.task_template["action_type"] = self.action_type
        elif "action_type" in self.task_template and self.action_type is None:
            self.action_type = self.task_template["action_type"]

        if self.action_payload is not None:
            self.task_template["payload"] = self.action_payload
        elif "payload" in self.task_template and self.action_payload is None:
            self.action_payload = self.task_template["payload"]

        if self.trigger is not None:
            if hasattr(self.trigger, "type"):
                self.trigger_type = self.trigger.type.value if hasattr(self.trigger.type, "value") else str(self.trigger.type)
            if hasattr(self.trigger, "expression") and self.trigger.expression is not None:
                self.schedule = str(self.trigger.expression)

        if self.notification_level is not None:
            self.notification_policy = self.notification_level

        if isinstance(self.state, str):
            try:
                self.state = BackgroundTaskState(self.state.upper())
            except ValueError:
                self.state = BackgroundTaskState.READY
        if isinstance(self.priority, str):
            try:
                self.priority = BackgroundTaskPriority(self.priority.upper())
            except ValueError:
                self.priority = BackgroundTaskPriority.NORMAL
        if isinstance(self.resource_class, str):
            try:
                self.resource_class = ResourceClass(self.resource_class.upper())
            except ValueError:
                self.resource_class = ResourceClass.NORMAL
        if isinstance(self.notification_policy, str):
            try:
                self.notification_policy = NotificationLevel(self.notification_policy.upper())
            except ValueError:
                self.notification_policy = NotificationLevel.NORMAL

    def is_policy_expired(self, now: Optional[float] = None) -> bool:
        """Check if temporary authorization has expired."""
        current_time = now if now is not None else time.time()
        return bool(self.policy_expires_at is not None and current_time >= self.policy_expires_at)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize definition to JSON-compatible dictionary."""
        return {
            "background_task_id": self.background_task_id,
            "name": self.name,
            "description": self.description,
            "mission_id": self.mission_id,
            "skill_id": self.skill_id,
            "task_template": dict(self.task_template),
            "trigger_type": self.trigger_type,
            "schedule": self.schedule,
            "conditions": dict(self.conditions),
            "state": self.state.value,
            "priority": self.priority.value,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "next_run_at": self.next_run_at,
            "last_run_at": self.last_run_at,
            "last_result": self.last_result,
            "failure_count": self.failure_count,
            "max_retries": self.max_retries,
            "retry_policy": dict(self.retry_policy),
            "resource_class": self.resource_class.value,
            "permission_profile": list(self.permission_profile),
            "notification_policy": self.notification_policy.value,
            "is_interactive": self.is_interactive,
            "policy_expires_at": self.policy_expires_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BackgroundTaskDefinition:
        """Construct definition from dictionary."""
        d = dict(data)
        return cls(**d)
