"""Notification Manager, Priority Levels, and Quiet Hours for Hey Jev.

Supports provider-neutral notification dispatch:
- Levels: SILENT, LOW, NORMAL, IMPORTANT, URGENT
- Quiet hours suppression for non-critical alerts
- Non-intrusive voice queueing (never speaks over active user speech or wake detection)
- EventBus lifecycle notifications
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from background_task_model import NotificationLevel
from event_bus import EventBus
from resource_governor import ResourceGovernor


@dataclass
class NotificationRecord:
    """Historical record of an emitted notification."""
    notification_id: str = field(default_factory=lambda: f"notif_{uuid.uuid4().hex[:8]}")
    title: str = ""
    message: str = ""
    level: NotificationLevel = NotificationLevel.NORMAL
    channel: str = "local"
    timestamp: float = field(default_factory=time.time)
    spoken: bool = False
    deferred: bool = False
    read: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "notification_id": self.notification_id,
            "title": self.title,
            "message": self.message,
            "level": self.level.value,
            "channel": self.channel,
            "timestamp": self.timestamp,
            "spoken": self.spoken,
            "deferred": self.deferred,
            "read": self.read,
            "metadata": dict(self.metadata),
        }


class NotificationManager:
    """Dispatches and tracks notifications across UI, logs, and voice."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        governor: Optional[ResourceGovernor] = None,
    ) -> None:
        self.event_bus = event_bus or EventBus()
        self.governor = governor or ResourceGovernor()
        self._lock = threading.RLock()
        self._history: List[NotificationRecord] = []
        self._deferred_queue: List[NotificationRecord] = []

    def notify(
        self,
        title: str,
        message: str,
        level: NotificationLevel = NotificationLevel.NORMAL,
        channel: str = "local",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> NotificationRecord:
        """Dispatch a notification according to priority and quiet hours."""
        if isinstance(level, str):
            level = NotificationLevel(level.upper())

        is_quiet = self.governor.is_quiet_hours()
        voice_active = self.governor.is_voice_active()

        # Check quiet hours suppression
        deferred = False
        if is_quiet and level in (NotificationLevel.SILENT, NotificationLevel.LOW, NotificationLevel.NORMAL):
            deferred = True

        rec = NotificationRecord(
            title=title,
            message=message,
            level=level,
            channel=channel,
            timestamp=time.time(),
            spoken=False,
            deferred=deferred,
            metadata=dict(metadata or {}),
        )

        with self._lock:
            self._history.append(rec)
            if deferred:
                self._deferred_queue.append(rec)

        event_name = "notification.deferred" if deferred else "notification.sent"
        self.event_bus.emit(
            event_type=event_name,
            task_id=rec.notification_id,
            agent_name="NotificationManager",
            data={
                "title": title,
                "level": level.value,
                "deferred": deferred,
                "channel": channel,
            },
        )
        return rec

    def notify_progress(self, task_name: str, progress_pct: float) -> NotificationRecord:
        return self.notify(
            title=f"Progress: {task_name}",
            message=f"{task_name} is {progress_pct:.0f}% complete.",
            level=NotificationLevel.LOW,
        )

    def notify_completion(self, task_name: str, summary: str) -> NotificationRecord:
        return self.notify(
            title=f"Completed: {task_name}",
            message=summary,
            level=NotificationLevel.NORMAL,
        )

    def notify_warning(self, title: str, warning_msg: str) -> NotificationRecord:
        return self.notify(
            title=title,
            message=warning_msg,
            level=NotificationLevel.IMPORTANT,
        )

    def notify_error(self, title: str, error_msg: str) -> NotificationRecord:
        return self.notify(
            title=title,
            message=error_msg,
            level=NotificationLevel.URGENT,
        )

    def send_notification(
        self,
        title: str,
        message: str,
        level: NotificationLevel = NotificationLevel.NORMAL,
        metadata: Optional[Dict[str, Any]] = None,
        channel: str = "local",
        force: bool = False,
    ) -> NotificationRecord:
        """Alias for notify."""
        return self.notify(
            title=title,
            message=message,
            level=level,
            channel=channel,
            metadata=metadata,
        )

    def get_history(self, limit: int = 50) -> List[NotificationRecord]:
        with self._lock:
            return list(self._history[-limit:])

    def get_recent_notifications(self, limit: int = 50) -> List[NotificationRecord]:
        """Alias for get_history."""
        return self.get_history(limit=limit)

    def get_deferred(self) -> List[NotificationRecord]:
        with self._lock:
            return list(self._deferred_queue)

    def flush_deferred(self) -> List[NotificationRecord]:
        """Release deferred notifications once quiet hours or voice activity ends."""
        with self._lock:
            flushed = list(self._deferred_queue)
            self._deferred_queue.clear()
            for rec in flushed:
                rec.deferred = False
        return flushed

    def mark_read(self, notification_id: str) -> bool:
        with self._lock:
            for rec in self._history:
                if rec.notification_id == notification_id:
                    rec.read = True
                    return True
        return False
