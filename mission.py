"""Mission Object, Lifecycle, and Timeline Observability for Hey Jev.

A Mission represents a long-running, multi-agent objective composed of
multiple DAG-decomposed tasks, agent handoffs, review loops, and verification steps.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class MissionStatus(str, Enum):
    """Lifecycle statuses for a multi-agent Mission."""
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PAUSED = "PAUSED"


@dataclass
class TimelineEntry:
    """Structured record in a mission's observable execution timeline."""
    entry_id: str = field(default_factory=lambda: f"time_{uuid.uuid4().hex[:8]}")
    timestamp: float = field(default_factory=time.time)
    event_type: str = ""
    agent_name: str = ""
    task_id: Optional[str] = None
    summary: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TimelineEntry:
        return cls(**data)


@dataclass
class Mission:
    """Long-running user objective composed of multiple specialist agents and tasks."""
    mission_id: str = field(default_factory=lambda: f"mission_{uuid.uuid4().hex[:10]}")
    title: str = ""
    description: str = ""
    status: MissionStatus = MissionStatus.CREATED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    task_ids: List[str] = field(default_factory=list)
    active_agents: List[str] = field(default_factory=list)
    timeline: List[TimelineEntry] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    progress_percent: float = 0.0
    max_agents: int = 5
    pause_reason: Optional[str] = None
    cancel_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            try:
                self.status = MissionStatus(self.status)
            except ValueError:
                self.status = MissionStatus.CREATED

    def transition_to(self, new_status: MissionStatus, reason: str = "") -> None:
        """Explicitly transition mission lifecycle status."""
        old_status = self.status
        self.status = new_status
        self.updated_at = time.time()
        self.record_timeline(
            event_type=f"mission.{new_status.value.lower()}",
            summary=f"Mission transition: {old_status.value} -> {new_status.value}. {reason}".strip(),
            details={"old_status": old_status.value, "new_status": new_status.value, "reason": reason},
        )

    def record_timeline(
        self,
        event_type: str,
        summary: str,
        agent_name: str = "",
        task_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> TimelineEntry:
        """Append an entry to the mission's observability timeline."""
        # Sanitize details to guarantee zero credentials or tokens leaked
        safe_details = {}
        for k, v in (details or {}).items():
            if any(s in k.lower() for s in ["key", "secret", "password", "token", "auth"]):
                safe_details[k] = "[REDACTED]"
            else:
                safe_details[k] = v

        entry = TimelineEntry(
            timestamp=time.time(),
            event_type=event_type,
            agent_name=agent_name,
            task_id=task_id,
            summary=summary,
            details=safe_details,
        )
        self.timeline.append(entry)
        self.updated_at = time.time()
        return entry

    def add_task(self, task_id: str) -> None:
        if task_id not in self.task_ids:
            self.task_ids.append(task_id)
            self.updated_at = time.time()

    def register_agent(self, agent_name: str) -> bool:
        """Register active specialist agent within bounded capacity."""
        if agent_name not in self.active_agents:
            if len(self.active_agents) >= self.max_agents:
                return False
            self.active_agents.append(agent_name)
            self.updated_at = time.time()
        return True

    def pause(self, reason: str = "User requested pause") -> None:
        self.pause_reason = reason
        self.transition_to(MissionStatus.PAUSED, reason=reason)

    def resume(self) -> None:
        self.pause_reason = None
        self.transition_to(MissionStatus.EXECUTING, reason="Mission resumed")

    def cancel(self, reason: str = "User requested cancellation") -> None:
        self.cancel_reason = reason
        self.transition_to(MissionStatus.CANCELLED, reason=reason)

    def update_progress(self, percent: float, summary_update: str = "") -> None:
        self.progress_percent = max(0.0, min(100.0, float(percent)))
        if summary_update:
            self.summary = summary_update
        self.updated_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["timeline"] = [t.to_dict() for t in self.timeline]
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Mission:
        d = dict(data)
        if "timeline" in d:
            d["timeline"] = [TimelineEntry.from_dict(t) if isinstance(t, dict) else t for t in d["timeline"]]
        return cls(**d)
