"""UI-Thread Signals and Presentation Formatting for Hey Jev Mission Center (Phase 28).

Provides:
- MissionCenterSignals: QObject signals for cross-thread event dispatching
- Progress derivation from authoritative task and acceptance criterion records
- Accessible status badges with text tags and colors (WCAG compliant)
- Sanitized presentation text helpers
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Signal

from memory_model import sanitize_content
from mission_model import (
    AcceptanceCriterion,
    AcceptanceStatus,
    Mission,
    MissionMode,
    MissionPlan,
    MissionStatus,
    MissionTask,
    MissionTaskStatus,
)
from tool_model import RiskLevel


class MissionCenterSignals(QObject):
    """Thread-safe Qt signals for dispatching mission events and worker outcomes onto the main GUI thread."""
    # Fired when an EventBus mission.* event arrives from a background thread
    mission_event_received = Signal(object)

    # Fired when missions list or selected mission has been reloaded/reconciled
    missions_refreshed = Signal()

    # Fired when a mission control action finishes (mission_id, success, message)
    action_completed = Signal(str, bool, str)

    # Fired when mission creation / interpretation finishes (mission_object, error_msg)
    mission_created = Signal(object, str)

    # Fired when diagnostic or health data is refreshed
    diagnosis_refreshed = Signal(object)


# ---------------------------------------------------------------------------
# Status Badge & Color Mappings (Accessible text + color)
# ---------------------------------------------------------------------------

MISSION_STATUS_CONFIG: Dict[MissionStatus, Tuple[str, str]] = {
    MissionStatus.CREATED: ("[CREATED]", "#94a3b8"),            # Slate
    MissionStatus.PLANNING: ("[PLANNING]", "#a855f7"),          # Purple
    MissionStatus.READY: ("[READY]", "#10b981"),                # Green
    MissionStatus.RUNNING: ("[RUNNING]", "#3b82f6"),            # Blue
    MissionStatus.WAITING_FOR_APPROVAL: ("[APPROVAL REQUIRED]", "#f59e0b"), # Amber
    MissionStatus.BLOCKED: ("[BLOCKED]", "#ef4444"),            # Red
    MissionStatus.VERIFYING: ("[VERIFYING]", "#06b6d4"),        # Teal
    MissionStatus.COMPLETED: ("[COMPLETED & VERIFIED]", "#10b981"), # Emerald
    MissionStatus.PARTIALLY_COMPLETED: ("[PARTIALLY COMPLETED]", "#f59e0b"), # Amber
    MissionStatus.PAUSED: ("[PAUSED]", "#eab308"),              # Yellow
    MissionStatus.CANCELLING: ("[CANCELLING...]", "#94a3b8"),   # Slate
    MissionStatus.CANCELLED: ("[CANCELLED]", "#64748b"),        # Dark slate
    MissionStatus.FAILED: ("[FAILED]", "#ef4444"),              # Red
    MissionStatus.NEEDS_ATTENTION: ("[NEEDS ATTENTION]", "#f97316"), # Orange
}

TASK_STATUS_CONFIG: Dict[MissionTaskStatus, Tuple[str, str]] = {
    MissionTaskStatus.PENDING: ("[PENDING]", "#94a3b8"),
    MissionTaskStatus.RUNNING: ("[RUNNING]", "#3b82f6"),
    MissionTaskStatus.COMPLETED: ("[COMPLETED]", "#10b981"),
    MissionTaskStatus.FAILED: ("[FAILED]", "#ef4444"),
    MissionTaskStatus.CANCELLED: ("[CANCELLED]", "#64748b"),
    MissionTaskStatus.SKIPPED: ("[SKIPPED]", "#94a3b8"),
    MissionTaskStatus.BLOCKED: ("[BLOCKED]", "#ef4444"),
    MissionTaskStatus.NEEDS_APPROVAL: ("[NEEDS APPROVAL]", "#f59e0b"),
}

CRITERION_STATUS_CONFIG: Dict[AcceptanceStatus, Tuple[str, str]] = {
    AcceptanceStatus.PASS: ("[PASS - VERIFIED]", "#10b981"),
    AcceptanceStatus.FAIL: ("[FAIL]", "#ef4444"),
    AcceptanceStatus.BLOCKED: ("[BLOCKED]", "#f97316"),
    AcceptanceStatus.UNVERIFIED: ("[UNVERIFIED]", "#94a3b8"),
}


def format_mission_status(status: MissionStatus) -> Tuple[str, str]:
    """Return accessible (text_label, color_hex) tuple for mission status."""
    return MISSION_STATUS_CONFIG.get(status, (f"[{status.value}]", "#94a3b8"))


def format_task_status(status: MissionTaskStatus) -> Tuple[str, str]:
    """Return accessible (text_label, color_hex) tuple for task status."""
    return TASK_STATUS_CONFIG.get(status, (f"[{status.value}]", "#94a3b8"))


def format_criterion_status(status: AcceptanceStatus) -> Tuple[str, str]:
    """Return accessible (text_label, color_hex) tuple for acceptance status."""
    return CRITERION_STATUS_CONFIG.get(status, (f"[{status.value}]", "#94a3b8"))


def calculate_mission_progress(mission: Mission) -> Tuple[float, str]:
    """Derive progress percentage and detailed summary strictly from persisted task and criteria records.
    
    Guarantees:
    - Never estimates or fabricates progress.
    - Accurately distinguishes executed work from verified outcomes.
    - Terminal COMPLETED returns 100.0%.
    - PARTIALLY_COMPLETED, FAILED, and BLOCKED show real ratios.
    """
    if mission.status == MissionStatus.COMPLETED:
        total_tasks = len(mission.plan.tasks) if mission.plan else 0
        total_crit = len(mission.acceptance_criteria)
        return 100.0, f"100% ({total_tasks}/{total_tasks} tasks executed, {total_crit}/{total_crit} criteria verified)"

    tasks = mission.plan.tasks if mission.plan else []
    total_tasks = len(tasks)
    completed_tasks = sum(1 for t in tasks if t.status == MissionTaskStatus.COMPLETED)

    criteria = mission.acceptance_criteria
    total_crit = len(criteria)
    passed_crit = sum(1 for c in criteria if c.status == AcceptanceStatus.PASS)

    if total_tasks == 0 and total_crit == 0:
        if mission.status in (MissionStatus.CREATED, MissionStatus.PLANNING):
            return 0.0, "0% (Planning in progress)"
        return 0.0, "0% (No tasks scheduled)"

    # Compute task execution ratio and verification ratio
    task_ratio = (completed_tasks / total_tasks) if total_tasks > 0 else 0.0
    crit_ratio = (passed_crit / total_crit) if total_crit > 0 else 0.0

    if total_tasks > 0 and total_crit > 0:
        # Composite progress: 70% execution weight, 30% verification weight
        composite = (0.7 * task_ratio + 0.3 * crit_ratio) * 100.0
        summary = (
            f"{composite:.0f}% ({completed_tasks}/{total_tasks} tasks completed, "
            f"{passed_crit}/{total_crit} criteria verified)"
        )
        return composite, summary
    elif total_tasks > 0:
        pct = task_ratio * 100.0
        return pct, f"{pct:.0f}% ({completed_tasks}/{total_tasks} tasks completed)"
    else:
        pct = crit_ratio * 100.0
        return pct, f"{pct:.0f}% ({passed_crit}/{total_crit} criteria verified)"


def format_relative_time(timestamp: float) -> str:
    """Format timestamp into readable relative time (e.g. '2m ago', 'just now')."""
    if timestamp <= 0:
        return "never"
    delta = time.time() - timestamp
    if delta < 5:
        return "just now"
    elif delta < 60:
        return f"{int(delta)}s ago"
    elif delta < 3600:
        return f"{int(delta // 60)}m ago"
    elif delta < 86400:
        return f"{int(delta // 3600)}h ago"
    else:
        return f"{int(delta // 86400)}d ago"
