"""Provider-neutral Universal Task Model for Hey Jev Agent Harness.

Defines AgentTask, explicit state machine transitions, priorities,
permissions, and lifecycle tracking.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Union


class TaskStatus(str, Enum):
    """Explicit lifecycle statuses for an AgentTask."""
    QUEUED = "QUEUED"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskPriority(str, Enum):
    """Execution priority levels for tasks."""
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TaskPermission(str, Enum):
    """Task-level permission scopes."""
    READ_FILES = "READ_FILES"
    WRITE_FILES = "WRITE_FILES"
    EXECUTE_COMMAND = "EXECUTE_COMMAND"
    CONTROL_BROWSER = "CONTROL_BROWSER"
    SEND_EMAIL = "SEND_EMAIL"
    CHANGE_SETTINGS = "CHANGE_SETTINGS"
    INSTALL_SOFTWARE = "INSTALL_SOFTWARE"
    NETWORK_ACCESS = "NETWORK_ACCESS"


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal status transition is attempted."""
    pass


# Valid explicit state transition graph
VALID_TRANSITIONS: Dict[TaskStatus, Set[TaskStatus]] = {
    TaskStatus.QUEUED: {
        TaskStatus.PLANNING,
        TaskStatus.RUNNING,
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.PLANNING: {
        TaskStatus.RUNNING,
        TaskStatus.WAITING,
        TaskStatus.BLOCKED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.RUNNING: {
        TaskStatus.WAITING,
        TaskStatus.BLOCKED,
        TaskStatus.VERIFYING,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.WAITING: {
        TaskStatus.RUNNING,
        TaskStatus.BLOCKED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.BLOCKED: {
        TaskStatus.QUEUED,
        TaskStatus.PLANNING,
        TaskStatus.RUNNING,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.VERIFYING: {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.RUNNING,  # re-run if verification needed adjustment
        TaskStatus.CANCELLED,
    },
    TaskStatus.COMPLETED: {
        TaskStatus.QUEUED,  # Allowed only on explicit retry / re-open
    },
    TaskStatus.FAILED: {
        TaskStatus.QUEUED,  # Allowed on retry
        TaskStatus.PLANNING,
    },
    TaskStatus.CANCELLED: set(),  # Terminal state
}


@dataclass
class AgentTask:
    """Universal provider-neutral task model."""
    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:12]}")
    parent_task_id: Optional[str] = None
    goal: str = ""
    description: str = ""
    status: TaskStatus = TaskStatus.QUEUED
    priority: TaskPriority = TaskPriority.NORMAL
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    owner_agent: str = "jev_orchestrator"
    current_agent: Optional[str] = None
    assigned_agents: List[str] = field(default_factory=list)
    delegated_agents: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    next_action: Optional[str] = None
    confidence: float = 1.0
    requires_approval: bool = False
    approval_state: str = "NONE"  # "NONE", "PENDING", "APPROVED", "REJECTED"
    retry_count: int = 0
    max_retries: int = 3
    timestamps: Dict[str, float] = field(default_factory=dict)
    cancellation_state: Dict[str, Any] = field(default_factory=dict)
    permissions: List[TaskPermission] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = TaskStatus(self.status)
        if isinstance(self.priority, str):
            self.priority = TaskPriority(self.priority)
        # Normalize permissions to TaskPermission enums
        normalized_perms: List[TaskPermission] = []
        for p in self.permissions:
            if isinstance(p, str):
                try:
                    normalized_perms.append(TaskPermission(p))
                except ValueError:
                    pass
            elif isinstance(p, TaskPermission):
                normalized_perms.append(p)
        self.permissions = normalized_perms

        # Ensure created_at in timestamps
        if "created" not in self.timestamps:
            self.timestamps["created"] = self.created_at
        if self.status.value not in self.timestamps:
            self.timestamps[self.status.value] = self.created_at

    def transition_to(self, new_status: Union[TaskStatus, str], reason: str = "") -> None:
        """Explicitly transition to a new status with validation."""
        if isinstance(new_status, str):
            new_status = TaskStatus(new_status)

        if new_status == self.status:
            return

        valid_targets = VALID_TRANSITIONS.get(self.status, set())
        if new_status not in valid_targets:
            raise InvalidStateTransitionError(
                f"Cannot transition task '{self.task_id}' from {self.status.value} to {new_status.value} "
                f"(valid targets: {[s.value for s in valid_targets]})"
            )

        self.status = new_status
        now = time.time()
        self.updated_at = now
        self.timestamps[new_status.value] = now
        if reason:
            self.context.setdefault("status_reasons", []).append({
                "from": self.status.value,
                "to": new_status.value,
                "reason": reason,
                "timestamp": now,
            })

    def request_cancellation(self, reason: str = "User requested cancellation") -> None:
        """Mark task as cancellation requested and transition if allowed."""
        now = time.time()
        self.cancellation_state = {
            "requested": True,
            "reason": reason,
            "timestamp": now,
        }
        self.updated_at = now
        # If task can transition to CANCELLED right away, do so
        if self.status in VALID_TRANSITIONS and TaskStatus.CANCELLED in VALID_TRANSITIONS[self.status]:
            self.transition_to(TaskStatus.CANCELLED, reason=reason)

    def is_cancelled(self) -> bool:
        """Check if task is in CANCELLED status or cancellation has been requested."""
        return self.status == TaskStatus.CANCELLED or self.cancellation_state.get("requested", False)

    def add_error(self, error_message: str) -> None:
        """Record an error string."""
        self.errors.append(error_message)
        self.updated_at = time.time()

    def add_artifact(self, artifact_type: str, path_or_uri: str, description: str = "") -> None:
        """Attach an output artifact metadata dict."""
        self.artifacts.append({
            "type": artifact_type,
            "uri": path_or_uri,
            "description": description,
            "created_at": time.time(),
        })
        self.updated_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize task to a plain dictionary."""
        return {
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "goal": self.goal,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "owner_agent": self.owner_agent,
            "current_agent": self.current_agent,
            "assigned_agents": list(self.assigned_agents),
            "delegated_agents": list(self.delegated_agents),
            "context": self.context,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "artifacts": self.artifacts,
            "errors": self.errors,
            "recommendations": self.recommendations,
            "next_action": self.next_action,
            "confidence": self.confidence,
            "requires_approval": self.requires_approval,
            "approval_state": self.approval_state,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "timestamps": self.timestamps,
            "cancellation_state": self.cancellation_state,
            "permissions": [p.value for p in self.permissions],
            "dependencies": list(self.dependencies),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentTask:
        """Deserialize from a plain dictionary."""
        d = dict(data)
        if "status" in d:
            d["status"] = TaskStatus(d["status"])
        if "priority" in d:
            d["priority"] = TaskPriority(d["priority"])
        if "permissions" in d:
            d["permissions"] = [
                TaskPermission(p) if isinstance(p, str) else p
                for p in d["permissions"]
            ]
        return cls(**d)
