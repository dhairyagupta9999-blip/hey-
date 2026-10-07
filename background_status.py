"""Background Status API and Human-Readable Work Summary for Hey Jev.

Produces:
- BackgroundStatus: Consolidated snapshot of tasks, watchers, schedules, and resources
- get_active_work_summary: Transparent summary for the user or dashboard
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from background_task_model import BackgroundTaskDefinition, BackgroundTaskState
from resource_governor import ResourceGovernor
from watcher_model import WatcherDefinition, WatcherState


@dataclass
class BackgroundStatusSnapshot:
    """Structured status payload for dashboard and agent introspection."""
    timestamp: float = field(default_factory=time.time)
    running_tasks: List[Dict[str, Any]] = field(default_factory=list)
    queued_tasks: List[Dict[str, Any]] = field(default_factory=list)
    paused_tasks: List[Dict[str, Any]] = field(default_factory=list)
    failed_tasks: List[Dict[str, Any]] = field(default_factory=list)
    active_watchers: List[Dict[str, Any]] = field(default_factory=list)
    next_scheduled: List[Dict[str, Any]] = field(default_factory=list)
    voice_active: bool = False
    quiet_hours: bool = False
    summary: str = ""

    @property
    def total_tasks(self) -> int:
        return len(self.running_tasks) + len(self.queued_tasks) + len(self.paused_tasks) + len(self.failed_tasks)

    @property
    def summary_text(self) -> str:
        return self.summary

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BackgroundStatus:
    """Aggregates active work across BackgroundTaskManager and ResourceGovernor."""

    def __init__(self, manager: Optional[Any] = None) -> None:
        self.manager = manager

    def get_status_snapshot(self) -> BackgroundStatusSnapshot:
        if self.manager:
            return self.create_snapshot(
                tasks=self.manager.list_tasks(),
                watchers=self.manager.list_watchers(),
                governor=self.manager.governor,
            )
        return self.create_snapshot([], [])

    def get_active_work_summary(self) -> str:
        snap = self.get_status_snapshot()
        return f"Active Background Work: {snap.summary}"

    @staticmethod
    def create_snapshot(
        tasks: List[BackgroundTaskDefinition],
        watchers: List[WatcherDefinition],
        governor: Optional[ResourceGovernor] = None,
    ) -> BackgroundStatusSnapshot:
        running: List[Dict[str, Any]] = []
        queued: List[Dict[str, Any]] = []
        paused: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        scheduled: List[Dict[str, Any]] = []

        for t in tasks:
            d = {"task_id": t.background_task_id, "name": t.name, "priority": t.priority.value}
            if t.state == BackgroundTaskState.RUNNING:
                running.append(d)
            elif t.state in (BackgroundTaskState.READY, BackgroundTaskState.WAITING):
                queued.append(d)
            elif t.state == BackgroundTaskState.PAUSED:
                paused.append(d)
            elif t.state == BackgroundTaskState.FAILED:
                failed.append(d)

            if t.enabled and t.next_run_at:
                scheduled.append({
                    "task_id": t.background_task_id,
                    "name": t.name,
                    "next_run_at": t.next_run_at,
                    "schedule": t.schedule,
                })

        active_w = [
            {"watcher_id": w.watcher_id, "type": w.type.value, "target": w.target, "state": w.state.value}
            for w in watchers if w.enabled and w.state != WatcherState.DISABLED
        ]

        voice_act = governor.is_voice_active() if governor else False
        quiet = governor.is_quiet_hours() if governor else False

        # Generate concise summary
        lines: List[str] = []
        if voice_act:
            lines.append("Background tasks are currently paused due to active voice interaction.")
        elif not running and not queued and not active_w:
            lines.append("No background tasks are currently running.")
        else:
            task_cnt = len(running)
            watch_cnt = len(active_w)
            parts = []
            if task_cnt > 0:
                parts.append(f"{task_cnt} task{'s' if task_cnt > 1 else ''} running")
            if watch_cnt > 0:
                parts.append(f"{watch_cnt} active watcher{'s' if watch_cnt > 1 else ''}")
            lines.append(f"Background automation active: {', '.join(parts)}.")

            if failed:
                lines.append(f"Notice: {len(failed)} background task(s) reported failures.")
            else:
                lines.append("Nothing requires your immediate attention.")

        summary_text = " ".join(lines)

        return BackgroundStatusSnapshot(
            running_tasks=running,
            queued_tasks=queued,
            paused_tasks=paused,
            failed_tasks=failed,
            active_watchers=active_w,
            next_scheduled=sorted(scheduled, key=lambda s: s["next_run_at"])[:5],
            voice_active=voice_act,
            quiet_hours=quiet,
            summary=summary_text,
        )
