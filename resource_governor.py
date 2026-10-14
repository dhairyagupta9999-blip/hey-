"""Resource Governor, Concurrency Slots, and Voice Preemption for Hey Jev.

Governs resource allocation on the Intel Core i3-5005U dual-core CPU:
- Separate foreground (1) and background (0 or 1) execution slots
- Immediate background preemption / pause when voice or wake-word is active
- Power status awareness (conservative on battery, pauses nonessential on critical)
- ResourceClass gating (LIGHT, NORMAL, HEAVY)
"""
from __future__ import annotations

import datetime
import threading
import time
from typing import Any, Dict, List, Optional, Set

from dataclasses import dataclass
from background_task_model import ResourceClass


@dataclass
class GovernorDecision:
    allowed: bool
    reason: str = ""


class ResourceGovernor:
    """Thread-safe resource governor enforcing CPU and concurrency boundaries."""

    def __init__(
        self,
        foreground_slots: int = 1,
        background_slots: int = 1,
        quiet_hours_start: int = 23,  # 11:00 PM
        quiet_hours_end: int = 7,     # 7:00 AM
        max_foreground: Optional[int] = None,
        max_background: Optional[int] = None,
    ) -> None:
        self._lock = threading.RLock()
        self.foreground_slots = max_foreground if max_foreground is not None else max(1, foreground_slots)
        self.background_slots = max_background if max_background is not None else max(1, background_slots)
        self.quiet_hours_start = quiet_hours_start
        self.quiet_hours_end = quiet_hours_end

        self._voice_active = False
        self._active_foreground: Set[str] = set()
        self._active_background: Dict[str, ResourceClass] = {}
        self._preempted_tasks: Set[str] = set()

    def acquire_slot(self, task: Any, is_foreground: bool = False) -> GovernorDecision:
        task_id = getattr(task, "background_task_id", None) or getattr(task, "task_id", str(task))
        res_class = getattr(task, "resource_class", ResourceClass.NORMAL)
        with self._lock:
            if not is_foreground and self._voice_active:
                return GovernorDecision(allowed=False, reason="Voice interaction active")
            if is_foreground:
                if len(self._active_foreground) >= self.foreground_slots:
                    return GovernorDecision(allowed=False, reason="Capacity reached: foreground full")
            else:
                if len(self._active_background) >= self.background_slots:
                    return GovernorDecision(allowed=False, reason="Capacity reached: max background slots")
            acq = self.acquire(task_id=task_id, resource_class=res_class, is_foreground=is_foreground)
            if acq:
                return GovernorDecision(allowed=True, reason="Slot acquired")
            return GovernorDecision(allowed=False, reason="Capacity reached")

    def release_slot(self, task_id: str) -> None:
        self.release(task_id)

    def get_system_power_status(self) -> Dict[str, Any]:
        return self.inspect_power_status()

    def set_voice_active(self, active: bool) -> List[str]:
        """Signal user voice or wake-word activity, immediately yielding background work."""
        preempted: List[str] = []
        with self._lock:
            self._voice_active = active
            if active and self._active_background:
                # Mark background tasks as preempted
                for tid in list(self._active_background.keys()):
                    preempted.append(tid)
                    self._preempted_tasks.add(tid)
        return preempted

    def is_voice_active(self) -> bool:
        with self._lock:
            return self._voice_active

    def is_quiet_hours(self, now_dt: Optional[datetime.datetime] = None) -> bool:
        """Check if current time is within configured quiet hours."""
        dt = now_dt or datetime.datetime.now()
        h = dt.hour
        if self.quiet_hours_start > self.quiet_hours_end:
            # Spans midnight (e.g. 23:00 to 07:00)
            return h >= self.quiet_hours_start or h < self.quiet_hours_end
        else:
            return self.quiet_hours_start <= h < self.quiet_hours_end

    def inspect_power_status(self) -> Dict[str, Any]:
        """Query system power and battery status via psutil if available."""
        try:
            import psutil
            battery = psutil.sensors_battery()
            if battery is None:
                return {"has_battery": False, "power_plugged": True, "percent": 100.0, "power_safe": True}
            return {
                "has_battery": True,
                "power_plugged": bool(battery.power_plugged),
                "percent": float(battery.percent),
                "power_safe": bool(battery.power_plugged or battery.percent > 20.0),
            }
        except Exception:
            return {"has_battery": False, "power_plugged": True, "percent": 100.0, "power_safe": True}

    def can_run(
        self,
        resource_class: ResourceClass = ResourceClass.NORMAL,
        is_foreground: bool = False,
    ) -> bool:
        """Evaluate if an additional task can run given active system load."""
        with self._lock:
            if is_foreground:
                return len(self._active_foreground) < self.foreground_slots

            # Background task rules
            # 1. Voice interaction strictly blocks new background allocations
            if self._voice_active:
                return False

            # 2. Check power status (defer HEAVY background work on low battery)
            power = self.inspect_power_status()
            if not power.get("power_safe", True) and resource_class == ResourceClass.HEAVY:
                return False

            # 3. HEAVY background tasks only run if no other background tasks are active
            if resource_class == ResourceClass.HEAVY:
                return len(self._active_background) == 0

            return len(self._active_background) < self.background_slots

    def acquire(
        self,
        task_id: str,
        resource_class: ResourceClass = ResourceClass.NORMAL,
        is_foreground: bool = False,
    ) -> bool:
        """Attempt to acquire a concurrency slot."""
        with self._lock:
            if not self.can_run(resource_class, is_foreground):
                return False

            if is_foreground:
                self._active_foreground.add(task_id)
            else:
                self._active_background[task_id] = resource_class
                self._preempted_tasks.discard(task_id)
            return True

    def release(self, task_id: str) -> None:
        """Release slot held by task."""
        with self._lock:
            self._active_foreground.discard(task_id)
            self._active_background.pop(task_id, None)
            self._preempted_tasks.discard(task_id)

    def is_preempted(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._preempted_tasks

    def get_active_allocations(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "foreground_count": len(self._active_foreground),
                "background_count": len(self._active_background),
                "voice_active": self._voice_active,
                "preempted_tasks": list(self._preempted_tasks),
                "active_background": {k: v.value for k, v in self._active_background.items()},
            }
