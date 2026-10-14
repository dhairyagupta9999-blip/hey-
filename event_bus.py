"""Event Bus for Hey Jev Agent Harness.

Dispatches asynchronous/synchronous task lifecycle events:
task.created, task.started, task.progress, task.waiting, task.agent_handoff,
task.failed, task.retrying, task.completed, task.cancelled, task.verification_failed.
"""
from __future__ import annotations

import fnmatch
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class TaskEvent:
    """Represents a broadcast event on the task bus."""
    event_type: str
    task_id: str
    timestamp: float = field(default_factory=time.time)
    agent_name: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "agent_name": self.agent_name,
            "data": self.data,
        }


class EventBus:
    """Thread-safe event bus supporting wildcard subscriptions and event replay."""

    def __init__(self, max_history: int = 1000) -> None:
        self._lock = threading.RLock()
        self._subscribers: Dict[str, List[Callable[[TaskEvent], None]]] = {}
        self._history: List[TaskEvent] = []
        self._max_history = max_history

    def subscribe(self, pattern: str, handler: Callable[[TaskEvent], None]) -> None:
        """Register a handler for event pattern (e.g. 'task.created', 'task.*', '*')."""
        with self._lock:
            if pattern not in self._subscribers:
                self._subscribers[pattern] = []
            if handler not in self._subscribers[pattern]:
                self._subscribers[pattern].append(handler)

    def unsubscribe(self, pattern: str, handler: Callable[[TaskEvent], None]) -> bool:
        """Unregister a handler."""
        with self._lock:
            if pattern in self._subscribers and handler in self._subscribers[pattern]:
                self._subscribers[pattern].remove(handler)
                if not self._subscribers[pattern]:
                    del self._subscribers[pattern]
                return True
        return False

    def publish(self, event: TaskEvent) -> None:
        """Publish a TaskEvent to all matching subscribers."""
        with self._lock:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history.pop(0)

            # Find matching handlers
            handlers_to_call: List[Callable[[TaskEvent], None]] = []
            for pattern, handlers in self._subscribers.items():
                if fnmatch.fnmatch(event.event_type, pattern):
                    handlers_to_call.extend(handlers)

        # Call handlers outside lock to prevent deadlocks
        for handler in handlers_to_call:
            try:
                handler(event)
            except Exception:
                # Isolate handler failure so other handlers and orchestrator continue
                pass

    def emit(
        self,
        event_type: str,
        task_id: str,
        agent_name: str = "",
        data: Optional[Dict[str, Any]] = None,
    ) -> TaskEvent:
        """Convenience method to construct and publish an event."""
        event = TaskEvent(
            event_type=event_type,
            task_id=task_id,
            timestamp=time.time(),
            agent_name=agent_name,
            data=data or {},
        )
        self.publish(event)
        return event

    def get_history(
        self,
        task_id: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> List[TaskEvent]:
        """Query event history, optionally filtered."""
        with self._lock:
            res = list(self._history)

        if task_id:
            res = [e for e in res if e.task_id == task_id]
        if event_type:
            res = [e for e in res if fnmatch.fnmatch(e.event_type, event_type)]
        return res

    def clear(self) -> None:
        """Clear all subscribers and history."""
        with self._lock:
            self._subscribers.clear()
            self._history.clear()
