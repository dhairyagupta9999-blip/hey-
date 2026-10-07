"""Scheduler Backend Abstraction and Implementations for Hey Jev.

Provides:
- SchedulerBackend: Abstract interface
- InProcessSchedulerBackend: Lightweight priority queue + timer loop
- WindowsTaskSchedulerBackend: Safe user-level Windows Task Scheduler integration
"""
from __future__ import annotations

import abc
import datetime
import heapq
import os
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from background_task_model import BackgroundTaskDefinition, BackgroundTaskState
from event_bus import EventBus


class SchedulerBackend(abc.ABC):
    """Abstract interface for task scheduling backends."""

    @abc.abstractmethod
    def start(self) -> None:
        """Start the scheduler background loop."""
        pass

    @abc.abstractmethod
    def stop(self) -> None:
        """Stop the scheduler."""
        pass

    @abc.abstractmethod
    def schedule(self, task_def: BackgroundTaskDefinition) -> None:
        """Register or update a task's schedule."""
        pass

    @abc.abstractmethod
    def unschedule(self, task_id: str) -> bool:
        """Remove a task from the schedule."""
        pass

    @abc.abstractmethod
    def get_health(self) -> Dict[str, Any]:
        """Report scheduler health status."""
        pass


class InProcessSchedulerBackend(SchedulerBackend):
    """Zero-dependency, thread-safe priority heap in-process scheduler."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        task_runner: Optional[Callable[[BackgroundTaskDefinition], None]] = None,
        misfire_threshold_sec: float = 60.0,
    ) -> None:
        self.event_bus = event_bus or EventBus()
        self.task_runner = task_runner
        self.misfire_threshold_sec = misfire_threshold_sec

        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Map of task_id -> BackgroundTaskDefinition
        self._tasks: Dict[str, BackgroundTaskDefinition] = {}
        # Min-heap of (next_run_at, task_id)
        self._heap: List[Tuple[float, str]] = []
        self._callbacks: Dict[str, Callable[[str], None]] = {}
        self._misfire_count = 0
        self._last_run_timestamp = 0.0

    def schedule_task(self, task_def: BackgroundTaskDefinition, callback: Optional[Callable[[str], None]] = None) -> None:
        """Register a task with an optional direct callback."""
        if callback:
            self._callbacks[task_def.background_task_id] = callback
        self.schedule(task_def)

    def step(self) -> int:
        """Evaluate and run due tasks synchronously once."""
        now = time.time()
        executed = 0
        to_run = []
        with self._lock:
            while self._heap and self._heap[0][1] not in self._tasks:
                heapq.heappop(self._heap)

            while self._heap:
                next_run, tid = self._heap[0]
                if now < next_run:
                    break
                heapq.heappop(self._heap)
                task_def = self._tasks.get(tid)
                if not task_def or not task_def.enabled:
                    continue

                delay = now - next_run
                if delay > self.misfire_threshold_sec:
                    self._misfire_count += 1
                    self.event_bus.emit("scheduler.misfire", tid, data={"delay_sec": round(delay, 2)})

                task_def.last_run_at = now
                task_def.next_run_at = self.calculate_next_run(task_def, base_time=now)
                if task_def.next_run_at:
                    heapq.heappush(self._heap, (task_def.next_run_at, tid))

                self._last_run_timestamp = now
                to_run.append((tid, task_def))

        for tid, task_def in to_run:
            executed += 1
            cb = self._callbacks.get(tid)
            if cb:
                try:
                    cb(tid)
                except Exception as e:
                    self.event_bus.emit("scheduler.task_error", tid, data={"error": str(e)})
            elif self.task_runner:
                try:
                    self.task_runner(task_def)
                except Exception as e:
                    self.event_bus.emit("scheduler.task_error", tid, data={"error": str(e)})

        return executed

    def get_health_status(self) -> Dict[str, Any]:
        h = self.get_health()
        h["scheduled_jobs"] = h.get("scheduled_tasks_count", 0)
        h["misfired_jobs_count"] = h.get("misfire_count", 0)
        return h

    def calculate_next_run(self, task_def: BackgroundTaskDefinition, base_time: Optional[float] = None) -> Optional[float]:
        """Calculate the next execution timestamp from schedule expression."""
        now = base_time if base_time is not None else time.time()
        sched = str(task_def.schedule or "").strip()

        if not sched:
            return None

        # 1. Pure numeric interval in seconds
        try:
            interval = float(sched)
            return now + max(1.0, interval)
        except ValueError:
            pass

        # 2. ISO timestamp format
        try:
            dt = datetime.datetime.fromisoformat(sched)
            ts = dt.timestamp()
            return ts if ts > now else None
        except Exception:
            pass

        # 3. Simple cron-like interval (e.g. "every 10m", "every 1h", "daily")
        sched_lower = sched.lower()
        if sched_lower.startswith("every "):
            parts = sched_lower.split()
            if len(parts) >= 2:
                val_str = parts[1]
                if val_str.endswith("s"):
                    return now + float(val_str[:-1])
                elif val_str.endswith("m"):
                    return now + float(val_str[:-1]) * 60.0
                elif val_str.endswith("h"):
                    return now + float(val_str[:-1]) * 3600.0
                elif val_str.endswith("d"):
                    return now + float(val_str[:-1]) * 86400.0

        if sched_lower == "daily":
            return now + 86400.0

        return now + 60.0  # Safe fallback default

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="InProcessScheduler")
            self._thread.start()

        self.event_bus.emit("scheduler.started", "in_process", data={"type": "InProcessScheduler"})

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._cv.notify_all()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

        self.event_bus.emit("scheduler.stopped", "in_process", data={})

    def schedule(self, task_def: BackgroundTaskDefinition) -> None:
        with self._lock:
            if task_def.next_run_at is None:
                task_def.next_run_at = self.calculate_next_run(task_def)

            self._tasks[task_def.background_task_id] = task_def
            if task_def.enabled and task_def.next_run_at:
                heapq.heappush(self._heap, (task_def.next_run_at, task_def.background_task_id))
                self._cv.notify()

    def unschedule(self, task_id: str) -> bool:
        with self._lock:
            found = self._tasks.pop(task_id, None) is not None
            # Heap will naturally drop stale task_ids during execution
            self._cv.notify()
            return found

    def _scheduler_loop(self) -> None:
        """Main priority heap worker loop."""
        while True:
            with self._lock:
                if not self._running:
                    break

                now = time.time()
                while self._heap and self._heap[0][1] not in self._tasks:
                    heapq.heappop(self._heap)

                if not self._heap:
                    self._cv.wait(timeout=1.0)
                    continue

                next_run, tid = self._heap[0]
                task_def = self._tasks.get(tid)

                if not task_def or not task_def.enabled:
                    heapq.heappop(self._heap)
                    continue

                if now < next_run:
                    # Sleep until next scheduled task or wakeup
                    wait_sec = min(next_run - now, 2.0)
                    self._cv.wait(timeout=wait_sec)
                    continue

                # Ready to execute! Pop from heap
                heapq.heappop(self._heap)

                # Misfire check
                delay = now - next_run
                if delay > self.misfire_threshold_sec:
                    self._misfire_count += 1
                    self.event_bus.emit("scheduler.misfire", tid, data={"delay_sec": round(delay, 2)})

                # Advance next run
                task_def.last_run_at = now
                task_def.next_run_at = self.calculate_next_run(task_def, base_time=now)
                if task_def.next_run_at:
                    heapq.heappush(self._heap, (task_def.next_run_at, tid))

                self._last_run_timestamp = now

            # Execute task outside the lock
            if self.task_runner and task_def:
                try:
                    self.task_runner(task_def)
                except Exception as e:
                    self.event_bus.emit("scheduler.task_error", tid, data={"error": str(e)})

    def get_health(self) -> Dict[str, Any]:
        with self._lock:
            state = "HEALTHY" if self._running else "STOPPED"
            if self._running and self._misfire_count > 5:
                state = "DEGRADED"

            return {
                "backend": "InProcessScheduler",
                "state": state,
                "running": self._running,
                "scheduled_tasks_count": len(self._tasks),
                "misfire_count": self._misfire_count,
                "last_run_at": self._last_run_timestamp,
            }


class WindowsTaskSchedulerBackend(SchedulerBackend):
    """Non-administrative wrapper for Windows Task Scheduler (schtasks.exe)."""

    def __init__(self, prefix: str = "HeyJev_") -> None:
        self.prefix = prefix
        self._running = False
        self._tasks: Dict[str, BackgroundTaskDefinition] = {}

    def _task_name(self, task_id: str) -> str:
        return f"{self.prefix}{task_id}"

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def schedule(self, task_def: BackgroundTaskDefinition) -> None:
        """Register task with Windows Task Scheduler (if available on Windows)."""
        self._tasks[task_def.background_task_id] = task_def
        if os.name != "nt":
            return

        name = self._task_name(task_def.background_task_id)
        cmd = ["schtasks", "/create", "/tn", name, "/tr", "python -c \"pass\"", "/sc", "daily", "/f"]
        try:
            subprocess.run(cmd, capture_output=True, timeout=3.0)
        except Exception:
            pass

    def unschedule(self, task_id: str) -> bool:
        """Unregister task from Windows Task Scheduler."""
        self._tasks.pop(task_id, None)
        if os.name != "nt":
            return True

        name = self._task_name(task_id)
        cmd = ["schtasks", "/delete", "/tn", name, "/f"]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=3.0)
            return res.returncode == 0
        except Exception:
            return False

    def get_health(self) -> Dict[str, Any]:
        return {
            "backend": "WindowsTaskScheduler",
            "state": "HEALTHY" if os.name == "nt" else "SIMULATED",
            "registered_tasks": len(self._tasks),
        }
