"""Phase 28 Comprehensive Test Suite for Hey Jev.

Verifies:
1. Creating and reopening the Mission Center without starting a mission.
2. Displaying persisted missions and their correct states.
3. Progress calculated from task and criterion records.
4. Distinction between completed work and verified outcomes.
5. Live updates from mission events.
6. Duplicate event suppression.
7. Reconciliation following missed events or UI refresh.
8. Correct UI-thread dispatch for background updates.
9. Start, pause, resume, cancel controls calling the coordinator.
10. Approval scoped to the exact mission and plan version.
11. Rejection of invalid or stale controls.
12. Prevention of duplicate execution from repeated clicks.
13. Recovery and blocker display when capability unavailable.
14. Secret redaction in displayed information.
15. Correct rendering of unresolved, blocked, partial, unverified states.
16. Persistence across application restart.
17. Cleanup of event subscriptions and UI resources on close.
18. Responsive behaviour under large but bounded mission histories.
19. Compatibility with voice and desktop application lifecycle.
20. Preservation of Phase 22–27.1 mission, workflow, approval, recovery behaviour.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# Offscreen Qt platform for headless CI / automated test runs
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from agent_task import TaskPermission
from assistant_ui import MainWindow
from capability_model import CapabilityType, Reversibility
from capability_registry import CapabilityRegistry
from capability_workflow import WorkflowMode
from event_bus import EventBus, TaskEvent
from memory_model import sanitize_content
from mission_center import (
    AcceptanceCriteriaWidget,
    MissionCenterWindow,
    MissionDiagnosisWidget,
    MissionOverviewWidget,
    MissionTaskGraphWidget,
    MissionTimelineWidget,
)
from mission_coordinator import MissionCoordinator
from mission_create_dialog import MissionCreateDialog
from mission_interpreter import MissionInterpreter
from mission_model import (
    AcceptanceCriterion,
    AcceptanceStatus,
    Mission,
    MissionBudget,
    MissionMode,
    MissionPlan,
    MissionStatus,
    MissionTask,
    MissionTaskStatus,
)
from mission_planner import MissionPlanner
from mission_replanner import MissionReplanner
from mission_store import SqliteMissionStore
from mission_ui_signals import (
    calculate_mission_progress,
    format_criterion_status,
    format_mission_status,
    format_task_status,
)
from resource_governor import ResourceGovernor
from skill_executor import SkillExecutor
from skill_registry import SkillRegistry
from tool_model import RiskLevel
from tool_registry import ToolRegistry
from tool_scanner import ToolScanner
from workflow_engine import WorkflowEngine


@pytest.fixture(scope="session")
def qapp():
    """Ensure QApplication instance exists for GUI tests."""
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def mission_env(qapp):
    """Build an isolated test environment with SQLite store and full registries."""
    tmp_dir = tempfile.mkdtemp(prefix="jev_phase28_test_")
    db_path = os.path.join(tmp_dir, "test_missions.db")

    tool_reg = ToolRegistry()
    ToolScanner(tool_reg).scan_and_register_all()
    skill_reg = SkillRegistry()
    skill_exec = SkillExecutor(tool_registry=tool_reg)
    cap_reg = CapabilityRegistry(
        tool_registry=tool_reg,
        skill_registry=skill_reg,
        skill_executor=skill_exec,
    )
    event_bus = EventBus()
    workflow_engine = WorkflowEngine(registry=cap_reg, event_bus=event_bus)
    mission_store = SqliteMissionStore(db_path=db_path)
    replanner = MissionReplanner(registry=cap_reg)
    governor = ResourceGovernor(foreground_slots=1, background_slots=1)
    coordinator = MissionCoordinator(
        registry=cap_reg,
        workflow_engine=workflow_engine,
        mission_store=mission_store,
        event_bus=event_bus,
        replanner=replanner,
        resource_governor=governor,
    )
    interpreter = MissionInterpreter(registry=cap_reg)
    planner = MissionPlanner(registry=cap_reg, resource_governor=governor)

    window = MissionCenterWindow(
        mission_coordinator=coordinator,
        mission_store=mission_store,
        mission_interpreter=interpreter,
        mission_planner=planner,
        mission_replanner=replanner,
        event_bus=event_bus,
    )

    yield {
        "tmp_dir": tmp_dir,
        "db_path": db_path,
        "tool_reg": tool_reg,
        "cap_reg": cap_reg,
        "event_bus": event_bus,
        "workflow_engine": workflow_engine,
        "mission_store": mission_store,
        "replanner": replanner,
        "governor": governor,
        "coordinator": coordinator,
        "interpreter": interpreter,
        "planner": planner,
        "window": window,
    }

    try:
        window.close()
    except Exception:
        pass
    mission_store.close()


def _make_dummy_mission(
    mission_id: str,
    goal: str = "Test Goal",
    status: MissionStatus = MissionStatus.CREATED,
    mode: MissionMode = MissionMode.MANUAL,
    tasks: List[MissionTask] = None,
    criteria: List[AcceptanceCriterion] = None,
) -> Mission:
    tasks = tasks or []
    criteria = criteria or []
    plan = MissionPlan(
        plan_id=f"plan_{mission_id}",
        plan_version=1,
        tasks=tasks,
    )
    return Mission(
        mission_id=mission_id,
        objective=goal,
        normalized_goal=goal,
        mode=mode,
        status=status,
        plan=plan,
        acceptance_criteria=criteria,
    )


# ---------------------------------------------------------------------------
# Test 1: Creating and reopening Mission Center without starting a mission
# ---------------------------------------------------------------------------
def test_01_create_and_reopen_without_starting_mission(mission_env):
    win = mission_env["window"]
    assert win is not None
    assert win.windowTitle() == "Mission Center — Hey Jev"
    assert win.overview_widget is not None
    assert win.task_graph_widget is not None
    assert win.criteria_widget is not None
    assert win.timeline_widget is not None
    assert win.diag_widget is not None

    # Verify no missions are active or running
    assert len(mission_env["mission_store"].list_missions()) == 0

    # Close and instantiate a fresh window
    win.close()
    win2 = MissionCenterWindow(
        mission_coordinator=mission_env["coordinator"],
        mission_store=mission_env["mission_store"],
        mission_interpreter=mission_env["interpreter"],
        mission_planner=mission_env["planner"],
        event_bus=mission_env["event_bus"],
    )
    assert win2 is not None
    assert len(mission_env["mission_store"].list_missions()) == 0
    win2.close()


# ---------------------------------------------------------------------------
# Test 2: Displaying persisted missions and their correct states
# ---------------------------------------------------------------------------
def test_02_displaying_persisted_missions_and_states(mission_env):
    store = mission_env["mission_store"]
    win = mission_env["window"]

    m1 = _make_dummy_mission("m_created", "Goal 1", status=MissionStatus.CREATED)
    m2 = _make_dummy_mission("m_planning", "Goal 2", status=MissionStatus.PLANNING)
    m3 = _make_dummy_mission("m_ready", "Goal 3", status=MissionStatus.READY)
    m4 = _make_dummy_mission("m_running", "Goal 4", status=MissionStatus.RUNNING)
    m5 = _make_dummy_mission("m_waiting", "Goal 5", status=MissionStatus.WAITING_FOR_APPROVAL)
    m6 = _make_dummy_mission("m_completed", "Goal 6", status=MissionStatus.COMPLETED)
    m7 = _make_dummy_mission("m_failed", "Goal 7", status=MissionStatus.FAILED)

    for m in [m1, m2, m3, m4, m5, m6, m7]:
        store.save_mission(m)

    win.refresh_missions()
    table = win.overview_widget.table
    assert table.rowCount() == 7

    # Check status badges
    statuses_in_table = [table.item(row, 0).text() for row in range(7)]
    assert any("[CREATED]" in s for s in statuses_in_table)
    assert any("[PLANNING]" in s for s in statuses_in_table)
    assert any("[READY]" in s for s in statuses_in_table)
    assert any("[RUNNING]" in s for s in statuses_in_table)
    assert any("[APPROVAL REQUIRED]" in s for s in statuses_in_table)
    assert any("[COMPLETED & VERIFIED]" in s for s in statuses_in_table)
    assert any("[FAILED]" in s for s in statuses_in_table)


# ---------------------------------------------------------------------------
# Test 3: Progress calculated from task and criterion records
# ---------------------------------------------------------------------------
def test_03_progress_calculated_from_task_and_criterion_records():
    # 0 tasks, 0 criteria
    m_empty = _make_dummy_mission("m_empty")
    pct, summary = calculate_mission_progress(m_empty)
    assert pct == 0.0

    # 4 tasks (2 completed, 2 pending), 2 criteria (1 pass, 1 unverified)
    t1 = MissionTask("t1", "Task 1", "Intended 1", capability_id="c1", status=MissionTaskStatus.COMPLETED)
    t2 = MissionTask("t2", "Task 2", "Intended 2", capability_id="c2", status=MissionTaskStatus.COMPLETED)
    t3 = MissionTask("t3", "Task 3", "Intended 3", capability_id="c3", status=MissionTaskStatus.PENDING)
    t4 = MissionTask("t4", "Task 4", "Intended 4", capability_id="c4", status=MissionTaskStatus.PENDING)
    c1 = AcceptanceCriterion("c1", "Crit 1", status=AcceptanceStatus.PASS)
    c2 = AcceptanceCriterion("c2", "Crit 2", status=AcceptanceStatus.UNVERIFIED)

    m = _make_dummy_mission(
        "m_calc",
        status=MissionStatus.RUNNING,
        tasks=[t1, t2, t3, t4],
        criteria=[c1, c2],
    )
    pct, summary = calculate_mission_progress(m)
    # Execution ratio = 2/4 = 0.5. Verification ratio = 1/2 = 0.5. Composite = 0.7*0.5 + 0.3*0.5 = 0.5 (50%)
    assert pct == 50.0
    assert "50% (2/4 tasks completed, 1/2 criteria verified)" in summary

    # Terminal COMPLETED status guarantees 100%
    m.status = MissionStatus.COMPLETED
    pct, summary = calculate_mission_progress(m)
    assert pct == 100.0


# ---------------------------------------------------------------------------
# Test 4: Distinction between completed work and verified outcomes
# ---------------------------------------------------------------------------
def test_04_distinction_between_completed_work_and_verified_outcomes(mission_env):
    store = mission_env["mission_store"]
    win = mission_env["window"]

    # All tasks are COMPLETED, but acceptance criteria are still UNVERIFIED
    t1 = MissionTask("t1", "Task 1", "Intended 1", capability_id="c1", status=MissionTaskStatus.COMPLETED)
    t2 = MissionTask("t2", "Task 2", "Intended 2", capability_id="c2", status=MissionTaskStatus.COMPLETED)
    c1 = AcceptanceCriterion("c1", "Outcome Verified", status=AcceptanceStatus.UNVERIFIED)

    m = _make_dummy_mission(
        "m_distinct",
        status=MissionStatus.VERIFYING,
        tasks=[t1, t2],
        criteria=[c1],
    )
    store.save_mission(m)

    # Progress calculation must NOT be 100%
    pct, summary = calculate_mission_progress(m)
    # Task ratio = 1.0 (70%), Crit ratio = 0.0 (0%) -> 70.0%
    assert pct == 70.0
    assert "2/2 tasks completed" in summary
    assert "0/1 criteria verified" in summary

    # Inspect UI widgets
    win.refresh_missions()
    win.select_mission("m_distinct")
    assert win.progress_bar.value() == 70
    assert "70%" in win.lbl_progress_summary.text()

    # Acceptance criteria widget must show [UNVERIFIED]
    table_crit = win.criteria_widget.table_criteria
    assert table_crit.rowCount() == 1
    assert "[UNVERIFIED]" in table_crit.item(0, 3).text()


# ---------------------------------------------------------------------------
# Test 5: Live updates from mission events
# ---------------------------------------------------------------------------
def test_05_live_updates_from_mission_events(mission_env):
    store = mission_env["mission_store"]
    event_bus = mission_env["event_bus"]
    win = mission_env["window"]

    m = _make_dummy_mission("m_live", "Live Mission", status=MissionStatus.READY)
    store.save_mission(m)
    win.refresh_missions()
    win.select_mission("m_live")

    # State update event
    event = TaskEvent(
        event_type="mission.state_changed",
        task_id="m_live",
        data={"mission_id": "m_live", "to_state": "RUNNING", "reason": "Execution started"},
    )
    event_bus.publish(event)
    QApplication.processEvents()

    # Verify timeline updated
    assert win.timeline_widget.table_events.rowCount() >= 1


# ---------------------------------------------------------------------------
# Test 6: Duplicate event suppression
# ---------------------------------------------------------------------------
def test_06_duplicate_event_suppression(mission_env):
    win = mission_env["window"]
    timeline = win.timeline_widget
    timeline.clear()

    # Emit two identical events with same timestamp / payload in rapid succession
    event1 = TaskEvent(event_type="mission.task_progress", task_id="m_dup", timestamp=100.0, data={"step": 1, "msg": "working"})
    event2 = TaskEvent(event_type="mission.task_progress", task_id="m_dup", timestamp=100.0, data={"step": 1, "msg": "working"})

    assert timeline.append_event(event1) is True
    assert timeline.append_event(event2) is False

    # Second event was suppressed by deduplication filter
    assert timeline.table_events.rowCount() == 1


# ---------------------------------------------------------------------------
# Test 7: Reconciliation following missed events or UI refresh
# ---------------------------------------------------------------------------
def test_07_reconciliation_following_missed_events_or_ui_refresh(mission_env):
    store = mission_env["mission_store"]
    win = mission_env["window"]

    m = _make_dummy_mission("m_reconcile", "Initial State", status=MissionStatus.READY)
    store.save_mission(m)
    win.refresh_missions()
    win.select_mission("m_reconcile")
    assert "[READY]" in win.lbl_status_badge.text()

    # Directly modify store behind the scenes without firing EventBus
    m.status = MissionStatus.WAITING_FOR_APPROVAL
    store.save_mission(m)

    # User clicks refresh
    win.btn_refresh.click()
    QApplication.processEvents()

    # UI must reconcile with database truth
    assert "[APPROVAL REQUIRED]" in win.lbl_status_badge.text()
    assert not win.banner_alert.isHidden()


# ---------------------------------------------------------------------------
# Test 8: Correct UI-thread dispatch for background updates
# ---------------------------------------------------------------------------
def test_08_correct_ui_thread_dispatch(mission_env):
    import threading
    event_bus = mission_env["event_bus"]
    win = mission_env["window"]

    event = TaskEvent(
        event_type="mission.task_progress",
        task_id="m_bg",
        data={"mission_id": "m_bg", "data": "background thread update"},
    )

    def bg_worker():
        event_bus.publish(event)

    t = threading.Thread(target=bg_worker)
    t.start()
    t.join()

    # Process pending events on the Qt GUI thread
    QApplication.processEvents()

    # Signal was received and added to timeline cleanly without Qt thread warnings/crashes
    timeline = win.timeline_widget
    assert timeline.table_events.rowCount() >= 1


# ---------------------------------------------------------------------------
# Test 9: Start, pause, resume, cancel controls calling coordinator
# ---------------------------------------------------------------------------
def test_09_controls_calling_coordinator(mission_env):
    coordinator = mission_env["coordinator"]
    win = mission_env["window"]
    store = mission_env["mission_store"]

    # Mock coordinator control methods
    coordinator.execute_mission = MagicMock(return_value=True)
    coordinator.pause_mission = MagicMock(return_value=True)
    coordinator.resume_mission = MagicMock(return_value=True)
    coordinator.cancel_mission = MagicMock(return_value=True)

    m = _make_dummy_mission("m_ctrl", "Controls Test", status=MissionStatus.READY)
    store.save_mission(m)
    win.refresh_missions()
    win.select_mission("m_ctrl")

    # Start
    win.btn_execute.click()
    time.sleep(0.05)
    QApplication.processEvents()
    assert coordinator.execute_mission.called
    assert coordinator.execute_mission.call_args[0][0] == "m_ctrl"

    # Reset action state for testing Pause
    win._action_in_progress = False
    m.status = MissionStatus.RUNNING
    store.save_mission(m)
    win.select_mission("m_ctrl")
    win.btn_pause.click()
    assert coordinator.pause_mission.called
    assert coordinator.pause_mission.call_args[0][0] == "m_ctrl"

    # Resume
    m.status = MissionStatus.PAUSED
    store.save_mission(m)
    win.select_mission("m_ctrl")
    win.btn_resume.click()
    assert coordinator.resume_mission.called
    assert coordinator.resume_mission.call_args[0][0] == "m_ctrl"

    # Cancel (mock QMessageBox.question to return QMessageBox.Yes)
    m.status = MissionStatus.RUNNING
    store.save_mission(m)
    win.select_mission("m_ctrl")
    with patch("PySide6.QtWidgets.QMessageBox.question", return_value=16384): # QMessageBox.Yes = 16384
        win.btn_cancel.click()
        assert coordinator.cancel_mission.called
        assert coordinator.cancel_mission.call_args[0][0] == "m_ctrl"


# ---------------------------------------------------------------------------
# Test 10: Approval scoped to exact mission and plan version
# ---------------------------------------------------------------------------
def test_10_approval_scoped_to_exact_mission_and_plan_version(mission_env):
    coordinator = mission_env["coordinator"]
    store = mission_env["mission_store"]
    win = mission_env["window"]

    coordinator.approve_plan = MagicMock(return_value=True)

    m = _make_dummy_mission("m_approve", "Approval Test", status=MissionStatus.WAITING_FOR_APPROVAL)
    m.plan.plan_version = 3
    store.save_mission(m)
    win.refresh_missions()
    win.select_mission("m_approve")

    win.btn_approve_plan.click()
    assert coordinator.approve_plan.called
    call_args = coordinator.approve_plan.call_args
    assert call_args[0][0] == "m_approve"
    assert call_args[1]["plan_version"] == 3


# ---------------------------------------------------------------------------
# Test 11: Rejection of invalid or stale controls
# ---------------------------------------------------------------------------
def test_11_rejection_of_invalid_or_stale_controls(mission_env):
    store = mission_env["mission_store"]
    win = mission_env["window"]

    # Mission already in terminal status (SUCCEEDED / COMPLETED)
    m = _make_dummy_mission("m_completed", "Completed Goal", status=MissionStatus.COMPLETED)
    store.save_mission(m)
    win.refresh_missions()
    win.select_mission("m_completed")

    # In COMPLETED status, Start/Execute, Pause, Resume, Approve Plan, and Cancel must be disabled
    assert not win.btn_execute.isEnabled()
    assert not win.btn_pause.isEnabled()
    assert not win.btn_resume.isEnabled()
    assert not win.btn_approve_plan.isEnabled()
    assert not win.btn_cancel.isEnabled()


# ---------------------------------------------------------------------------
# Test 12: Prevention of duplicate execution from repeated clicks
# ---------------------------------------------------------------------------
def test_12_prevention_of_duplicate_execution_from_repeated_clicks(mission_env):
    store = mission_env["mission_store"]
    win = mission_env["window"]

    m = _make_dummy_mission("m_double", "No Double Click", status=MissionStatus.READY)
    store.save_mission(m)
    win.refresh_missions()
    win.select_mission("m_double")

    # Simulate in-progress flag
    win._action_in_progress = True
    win._update_action_buttons_state(m)

    # While action is in progress, action buttons must be disabled
    assert not win.btn_execute.isEnabled()
    assert not win.btn_pause.isEnabled()
    assert not win.btn_resume.isEnabled()
    assert not win.btn_approve_plan.isEnabled()
    assert not win.btn_replan.isEnabled()
    assert not win.btn_cancel.isEnabled()


# ---------------------------------------------------------------------------
# Test 13: Recovery and blocker display when capability unavailable
# ---------------------------------------------------------------------------
def test_13_recovery_and_blocker_display(mission_env):
    store = mission_env["mission_store"]
    win = mission_env["window"]

    t_blocked = MissionTask(
        task_id="t_blocked",
        description="Failed Step",
        intended_result="Failed Step Result",
        capability_id="nonexistent_tool",
        status=MissionTaskStatus.BLOCKED,
        error="Capability 'nonexistent_tool' not registered in environment.",
    )
    m = _make_dummy_mission(
        "m_block",
        "Blocked Mission",
        status=MissionStatus.BLOCKED,
        tasks=[t_blocked],
    )
    m.status_reason = "Capability 'nonexistent_tool' unavailable"
    store.save_mission(m)

    win.refresh_missions()
    win.select_mission("m_block")

    # Banner must alert the blocker
    assert not win.banner_alert.isHidden()
    assert "Blocker" in win.lbl_banner_text.text() or "unavailable" in win.lbl_banner_text.text()

    # Task graph widget must highlight blocked task
    table_tasks = win.task_graph_widget.table_tasks
    assert table_tasks.rowCount() == 1
    assert "[BLOCKED]" in table_tasks.item(0, 5).text()


# ---------------------------------------------------------------------------
# Test 14: Secret redaction in displayed information
# ---------------------------------------------------------------------------
def test_14_secret_redaction_in_displayed_information(mission_env):
    store = mission_env["mission_store"]
    win = mission_env["window"]

    secret_raw = "Deploy with API key sk-proj-1234567890abcdef1234567890abcdef and token ghp_1234567890abcdef1234567890abcdef"
    sanitized = sanitize_content(secret_raw)
    assert "sk-proj-" not in sanitized
    assert "ghp_" not in sanitized
    assert "[REDACTED" in sanitized

    m = _make_dummy_mission("m_secret", secret_raw, status=MissionStatus.READY)
    store.save_mission(m)

    win.refresh_missions()
    win.select_mission("m_secret")

    # Verify no raw secrets in UI text
    overview_text = win.overview_widget.table.item(0, 1).text()
    assert "sk-proj-" not in overview_text
    assert "ghp_" not in overview_text

    title_text = win.lbl_mission_title.text()
    assert "sk-proj-" not in title_text
    assert "ghp_" not in title_text

    meta_text = win.txt_meta_detail.toPlainText()
    assert "sk-proj-" not in meta_text
    assert "ghp_" not in meta_text


# ---------------------------------------------------------------------------
# Test 15: Correct rendering of unresolved, blocked, partial, unverified states
# ---------------------------------------------------------------------------
def test_15_rendering_unresolved_blocked_partial_unverified():
    # Verify accessible text and distinct colors
    lbl, col = format_mission_status(MissionStatus.BLOCKED)
    assert lbl == "[BLOCKED]"
    assert col == "#ef4444"

    lbl, col = format_mission_status(MissionStatus.PARTIALLY_COMPLETED)
    assert lbl == "[PARTIALLY COMPLETED]"
    assert col == "#f59e0b"

    lbl, col = format_task_status(MissionTaskStatus.BLOCKED)
    assert lbl == "[BLOCKED]"
    assert col == "#ef4444"

    lbl, col = format_criterion_status(AcceptanceStatus.UNVERIFIED)
    assert lbl == "[UNVERIFIED]"
    assert col == "#94a3b8"

    lbl, col = format_criterion_status(AcceptanceStatus.BLOCKED)
    assert lbl == "[BLOCKED]"
    assert col == "#f97316"


# ---------------------------------------------------------------------------
# Test 16: Persistence across application restart
# ---------------------------------------------------------------------------
def test_16_persistence_across_restart(mission_env):
    store = mission_env["mission_store"]
    db_path = mission_env["db_path"]

    m = _make_dummy_mission(
        "m_persist",
        "Persistent Goal Across Restart",
        status=MissionStatus.READY,
        mode=MissionMode.COPILOT,
    )
    store.save_mission(m)
    mission_env["window"].close()

    # Re-open a fresh store and fresh window pointing to same db_path
    store2 = SqliteMissionStore(db_path=db_path)
    win2 = MissionCenterWindow(
        mission_coordinator=mission_env["coordinator"],
        mission_store=store2,
        mission_interpreter=mission_env["interpreter"],
        mission_planner=mission_env["planner"],
        event_bus=mission_env["event_bus"],
    )
    win2.refresh_missions()

    assert win2.overview_widget.table.rowCount() == 1
    loaded = store2.get_mission("m_persist")
    assert loaded is not None
    assert loaded.normalized_goal == "Persistent Goal Across Restart"
    assert loaded.mode == MissionMode.COPILOT
    assert loaded.status == MissionStatus.READY

    win2.close()
    store2.close()


# ---------------------------------------------------------------------------
# Test 17: Cleanup of event subscriptions and UI resources on close
# ---------------------------------------------------------------------------
def test_17_cleanup_on_close(mission_env):
    event_bus = mission_env["event_bus"]
    win = MissionCenterWindow(
        mission_coordinator=mission_env["coordinator"],
        mission_store=mission_env["mission_store"],
        mission_interpreter=mission_env["interpreter"],
        mission_planner=mission_env["planner"],
        event_bus=event_bus,
    )

    listener_count_before = len(event_bus._subscribers.get("mission.*", []))
    assert listener_count_before >= 1

    # Close window
    win.close()

    listener_count_after = len(event_bus._subscribers.get("mission.*", []))
    assert listener_count_after == listener_count_before - 1


# ---------------------------------------------------------------------------
# Test 18: Responsive behaviour under large but bounded mission histories
# ---------------------------------------------------------------------------
def test_18_responsive_under_large_history(mission_env):
    store = mission_env["mission_store"]
    win = mission_env["window"]

    # Insert 150 missions
    for i in range(150):
        m = _make_dummy_mission(f"m_batch_{i:03d}", f"Batch Goal {i}", status=MissionStatus.READY)
        store.save_mission(m)

    t0 = time.time()
    win.refresh_missions()
    elapsed = time.time() - t0

    # UI refresh must populate smoothly within bounded time (< 1.5s for 150 rows)
    assert elapsed < 1.5
    assert win.overview_widget.table.rowCount() == 150


# ---------------------------------------------------------------------------
# Test 19: Compatibility with voice and desktop application lifecycle
# ---------------------------------------------------------------------------
def test_19_desktop_main_window_integration(mission_env):
    main_win = MainWindow()

    # 7 tabs invariant strictly preserved
    assert main_win.tabs.count() == 7

    # Autonomous Missions card exists on tab_home
    assert hasattr(main_win, "btn_mission_center")

    # Open Mission Center via MainWindow
    mc_win = main_win.open_mission_center()
    assert mc_win is not None
    assert mc_win.isVisible()

    # Reopening returns the same instance (singleton window per main window)
    mc_win2 = main_win.open_mission_center()
    assert mc_win2 is mc_win

    main_win._quit_application()


# ---------------------------------------------------------------------------
# Test 20: Preservation of Phase 22–27.1 mission, workflow, approval behaviour
# ---------------------------------------------------------------------------
def test_20_preservation_of_mission_and_workflow_behaviour(mission_env):
    interpreter = mission_env["interpreter"]
    planner = mission_env["planner"]
    store = mission_env["mission_store"]

    raw_goal = "Search files for project report and check system status"
    mission, meta = interpreter.interpret_objective(raw_goal)
    assert mission.mission_id is not None
    assert len(mission.acceptance_criteria) >= 1

    plan = planner.create_plan(mission)
    assert plan is not None
    assert plan.plan_version == 1
    assert len(plan.tasks) >= 1

    mission.plan = plan
    mission.status = MissionStatus.READY
    store.save_mission(mission)

    loaded = store.get_mission(mission.mission_id)
    assert loaded is not None
    assert len(loaded.plan.tasks) == len(plan.tasks)
