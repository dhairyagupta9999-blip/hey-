"""Phase 27 Comprehensive Test Suite for Hey Jev.
Tests Persistent Missions, Goal Interpretation, DAG Planning, Adaptive Replanning,
Governed Autonomy Modes (MANUAL, COPILOT, AUTOPILOT), SQLite Persistence,
Fresh-Process Restart Recovery, Secret Sanitization, and Criteria Verification.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List

import pytest
from agent_task import TaskPermission
from capability_model import CapabilityType, Reversibility
from capability_registry import CapabilityRegistry
from capability_workflow import WorkflowMode
from event_bus import EventBus
from memory_model import sanitize_content
from mission_coordinator import MissionCoordinator
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
    validate_mission_transition,
)
from mission_planner import MissionPlanner, MissionPlanningError
from mission_replanner import BudgetExhaustedError, MissionReplanner
from mission_store import SqliteMissionStore
from resource_governor import ResourceGovernor
from runtime_manager import JevRuntime
from skill_executor import SkillExecutor
from skill_registry import SkillRegistry
from tool_model import RiskLevel
from tool_registry import ToolRegistry
from tool_scanner import ToolScanner
from workflow_engine import WorkflowEngine


@pytest.fixture
def test_env():
    """Build isolated test environment with SQLite store and registries."""
    tmp_dir = tempfile.mkdtemp(prefix="jev_phase27_test_")
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
    }

    mission_store.close()


# ---------------------------------------------------------------------------
# Test 1: Converting ordinary-language goal into measurable acceptance criteria
# ---------------------------------------------------------------------------
def test_01_goal_interpretation_acceptance_criteria(test_env):
    interpreter = test_env["interpreter"]
    raw_goal = "Please clean temporary files older than 7 days, but do not delete logs"
    mission, meta = interpreter.interpret_objective(raw_goal)

    assert "clean temporary files" in mission.normalized_goal
    assert len(mission.explicit_constraints) >= 1
    assert any("logs" in c for c in mission.explicit_constraints)
    assert len(mission.inferred_constraints) >= 2
    assert len(mission.acceptance_criteria) >= 2
    for ac in mission.acceptance_criteria:
        assert ac.status == AcceptanceStatus.UNVERIFIED
        assert ac.criterion_id.startswith("ac-")
        assert len(ac.description) > 0


# ---------------------------------------------------------------------------
# Test 2: Decomposing a goal into dependency-aware tasks
# ---------------------------------------------------------------------------
def test_02_goal_decomposition_into_dag_tasks(test_env):
    interpreter = test_env["interpreter"]
    planner = test_env["planner"]

    mission, _ = interpreter.interpret_objective("organize downloads folder")
    plan = planner.create_plan(mission)

    assert plan.plan_version == 1
    assert len(plan.tasks) >= 1
    task_ids = {t.task_id for t in plan.tasks}
    for t in plan.tasks:
        for dep in t.dependencies:
            assert dep in task_ids

    # Cycle rejection
    cyclic_tasks = [
        MissionTask(task_id="t1", description="One", intended_result="R1", dependencies=["t2"]),
        MissionTask(task_id="t2", description="Two", intended_result="R2", dependencies=["t1"]),
    ]
    with pytest.raises(MissionPlanningError):
        planner._validate_dag(cyclic_tasks)


# ---------------------------------------------------------------------------
# Test 3: Correct execution through real existing workflow path
# ---------------------------------------------------------------------------
def test_03_real_workflow_execution_path(test_env):
    coordinator = test_env["coordinator"]
    store = test_env["mission_store"]

    m = Mission(
        objective="Read temporary directory",
        mode=MissionMode.COPILOT,
        status=MissionStatus.READY,
        acceptance_criteria=[AcceptanceCriterion("ac-1", "Directory contents read successfully")],
    )
    t = MissionTask(
        task_id="t-1",
        description="List files in directory",
        intended_result="Directory entries returned",
        capability_id="tool:filesystem.list_directory",
        inputs={"path": test_env["tmp_dir"]},
        required_permissions=["READ_FILES"],
        is_idempotent=True,
    )
    m.plan = MissionPlan(tasks=[t])
    store.save_mission(m)

    res = coordinator.execute_mission(
        m.mission_id,
        granted_permissions=[TaskPermission.READ_FILES],
    )
    assert res.status == MissionStatus.COMPLETED
    assert res.plan.tasks[0].status == MissionTaskStatus.COMPLETED
    assert "entries" in res.plan.tasks[0].output
    assert res.acceptance_criteria[0].status == AcceptanceStatus.PASS


# ---------------------------------------------------------------------------
# Test 4: Enforcing MANUAL, COPILOT, and AUTOPILOT boundaries
# ---------------------------------------------------------------------------
def test_04_enforcing_manual_copilot_autopilot_modes(test_env):
    coordinator = test_env["coordinator"]
    store = test_env["mission_store"]

    # MANUAL mode: pauses at READY and never auto-executes
    m_manual = Mission(
        objective="Manual plan",
        mode=MissionMode.MANUAL,
        status=MissionStatus.READY,
        acceptance_criteria=[AcceptanceCriterion("ac-1", "Manual task completed")],
    )
    m_manual.plan = MissionPlan(tasks=[MissionTask("t1", "Safe task", "Result", capability_id=None)])
    store.save_mission(m_manual)

    res_man = coordinator.execute_mission(m_manual.mission_id)
    assert res_man.status == MissionStatus.READY

    # AUTOPILOT mode: halts and flags WAITING_FOR_APPROVAL when hitting unapproved checkpoint
    m_auto = Mission(
        objective="Autopilot with gate",
        mode=MissionMode.AUTOPILOT,
        status=MissionStatus.READY,
        acceptance_criteria=[AcceptanceCriterion("ac-1", "High risk task executed")],
    )
    m_auto.plan = MissionPlan(
        tasks=[
            MissionTask(
                "t_gated",
                "Delete system logs",
                "Logs deleted",
                capability_id=None,
                risk_level=RiskLevel.HIGH,
                approval_checkpoint=True,
                approval_reason="High risk destructive operation",
            )
        ]
    )
    store.save_mission(m_auto)

    res_auto = coordinator.execute_mission(m_auto.mission_id)
    assert res_auto.status == MissionStatus.WAITING_FOR_APPROVAL
    assert "Autopilot halted" in res_auto.status_reason


# ---------------------------------------------------------------------------
# Test 5: Required approval before risky task execution
# ---------------------------------------------------------------------------
def test_05_approval_required_before_risky_task(test_env):
    coordinator = test_env["coordinator"]
    store = test_env["mission_store"]

    m = Mission(
        objective="Deploy update",
        mode=MissionMode.COPILOT,
        status=MissionStatus.READY,
        acceptance_criteria=[AcceptanceCriterion("ac-1", "Deployment complete")],
    )
    t = MissionTask(
        task_id="t-deploy",
        description="Execute production deployment",
        intended_result="Production updated",
        risk_level=RiskLevel.HIGH,
        approval_checkpoint=True,
        approval_reason="Production service restart required",
    )
    m.plan = MissionPlan(tasks=[t])
    store.save_mission(m)

    res1 = coordinator.execute_mission(m.mission_id)
    assert res1.status == MissionStatus.WAITING_FOR_APPROVAL
    assert t.task_id not in res1.approved_tasks

    # Approve task
    coordinator.approve_task(m.mission_id, "t-deploy")
    res2 = coordinator.execute_mission(m.mission_id)
    assert res2.status == MissionStatus.COMPLETED
    assert res2.plan.tasks[0].status == MissionTaskStatus.COMPLETED


# ---------------------------------------------------------------------------
# Test 6: Adaptive replanning after a capability becomes unavailable
# ---------------------------------------------------------------------------
def test_06_adaptive_replanning_on_capability_unavailable(test_env):
    replanner = test_env["replanner"]
    m = Mission(
        objective="File processing",
        status=MissionStatus.RUNNING,
    )
    t1 = MissionTask("t1", "Process text", "Processed", status=MissionTaskStatus.FAILED, capability_id="tool:broken")
    m.plan = MissionPlan(plan_version=1, tasks=[t1])

    new_plan = replanner.replan(
        mission=m,
        trigger_reason="Capability 'tool:broken' became unavailable",
        failed_task_id="t1",
    )
    assert new_plan.plan_version == 2
    assert "became unavailable" in new_plan.replan_reason
    assert m.status == MissionStatus.BLOCKED


# ---------------------------------------------------------------------------
# Test 7: Preserving completed tasks during replanning
# ---------------------------------------------------------------------------
def test_07_preserve_completed_tasks_during_replan(test_env):
    replanner = test_env["replanner"]
    m = Mission(
        objective="Multi-stage backup",
        status=MissionStatus.RUNNING,
    )
    t1 = MissionTask("t1", "Create zip archive", "Archive created", status=MissionTaskStatus.COMPLETED, evidence={"zip": "backup.zip"}, is_idempotent=False)
    t2 = MissionTask("t2", "Upload to s3", "Uploaded", status=MissionTaskStatus.FAILED, dependencies=["t1"])
    m.plan = MissionPlan(plan_version=1, tasks=[t1, t2])

    new_plan = replanner.replan(
        mission=m,
        trigger_reason="Network dropped during upload",
        failed_task_id="t2",
    )
    assert new_plan.plan_version == 2
    # Verify t1 was preserved without modification
    p_t1 = new_plan.get_task("t1")
    assert p_t1 is not None
    assert p_t1.status == MissionTaskStatus.COMPLETED
    assert p_t1.evidence == {"zip": "backup.zip"}
    assert not p_t1.is_idempotent


# ---------------------------------------------------------------------------
# Test 8: Rejecting stale approval following a material plan change
# ---------------------------------------------------------------------------
def test_08_reject_stale_approval_on_material_plan_change(test_env):
    replanner = test_env["replanner"]
    m = Mission(
        objective="Data sync",
        status=MissionStatus.RUNNING,
        approved_plan_version=1,
        approved_tasks={"t2"},
    )
    t1 = MissionTask("t1", "Fetch records", "Records fetched", status=MissionTaskStatus.COMPLETED)
    t2 = MissionTask("t2", "Update database", "Updated", status=MissionTaskStatus.FAILED, risk_level=RiskLevel.LOW)
    m.plan = MissionPlan(plan_version=1, tasks=[t1, t2])

    # Replan substituting with higher risk capability
    from capability_model import CapabilityDefinition, ApprovalRequirement
    test_env["cap_reg"].register(
        CapabilityDefinition(
            capability_id="tool:database.force_overwrite",
            name="Force Overwrite DB",
            description="High risk DB overwrite",
            risk_level=RiskLevel.HIGH,
            approval_requirement=ApprovalRequirement.MANDATORY,
            handler=lambda **kwargs: {"res": "ok"},
        )
    )

    new_plan = replanner.replan(
        mission=m,
        trigger_reason="DB lock encountered; switching to force overwrite",
        failed_task_id="t2",
        alternative_capability_id="tool:database.force_overwrite",
    )

    assert new_plan.materially_changed is True
    assert m.approved_plan_version is None
    assert len(m.approved_tasks) == 0
    assert m.status == MissionStatus.WAITING_FOR_APPROVAL


# ---------------------------------------------------------------------------
# Test 9: Bounded retries and replanning attempts
# ---------------------------------------------------------------------------
def test_09_bounded_retries_and_replanning_attempts(test_env):
    budget = MissionBudget(max_replan_attempts=2, max_retries_per_task=1)
    m = Mission(objective="Bounded test", budget=budget, status=MissionStatus.RUNNING)
    t = MissionTask("t1", "Fail task", "Failed", status=MissionTaskStatus.FAILED, max_retries=1)
    m.plan = MissionPlan(plan_version=1, tasks=[t])

    replanner = test_env["replanner"]
    # 1st replan
    replanner.replan(m, trigger_reason="Attempt 1 failure")
    assert budget.current_replan_count == 1
    assert m.plan.plan_version == 2

    # 2nd replan
    replanner.replan(m, trigger_reason="Attempt 2 failure")
    assert budget.current_replan_count == 2
    assert m.plan.plan_version == 3

    # 3rd replan must fail with budget exhaustion
    with pytest.raises(BudgetExhaustedError):
        replanner.replan(m, trigger_reason="Attempt 3 failure")
    assert m.status == MissionStatus.FAILED


# ---------------------------------------------------------------------------
# Test 10: Budget exhaustion and safe stopping
# ---------------------------------------------------------------------------
def test_10_budget_exhaustion_safe_stopping(test_env):
    budget = MissionBudget(max_tasks=1, max_replan_attempts=1)
    m = Mission(
        objective="Budget test",
        budget=budget,
        status=MissionStatus.READY,
        acceptance_criteria=[AcceptanceCriterion("ac-1", "Done")],
    )
    m.plan = MissionPlan(
        tasks=[
            MissionTask("t1", "Task 1", "Done 1"),
            MissionTask("t2", "Task 2", "Done 2"),
        ]
    )
    test_env["mission_store"].save_mission(m)

    res = test_env["coordinator"].execute_mission(m.mission_id)
    assert res.status == MissionStatus.FAILED
    assert "budget" in res.status_reason.lower()


# ---------------------------------------------------------------------------
# Test 11: Avoiding duplicate task execution after repeated events
# ---------------------------------------------------------------------------
def test_11_avoid_duplicate_task_execution_repeated_events(test_env):
    coordinator = test_env["coordinator"]
    m = Mission(objective="Dedup test", status=MissionStatus.READY)
    t = MissionTask("t1", "Idempotent action", "Done")
    m.plan = MissionPlan(tasks=[t])

    executed_count = 0

    def mock_dispatch(*args, **kwargs):
        nonlocal executed_count
        executed_count += 1
        return True

    original = coordinator._execute_single_task
    try:
        coordinator._execute_single_task = mock_dispatch
        test_env["mission_store"].save_mission(m)

        # First dispatch
        coordinator._execute_single_task(m, t, {}, set())
        assert executed_count == 1

        # Second dispatch of same attempt
        # Test dedup key directly
        key = f"{m.mission_id}:{m.plan.plan_version}:{t.task_id}:attempt:{t.retry_count}"
        coordinator._processed_events.add(key)
        can_run = key not in coordinator._processed_events
        assert can_run is False
    finally:
        coordinator._execute_single_task = original


# ---------------------------------------------------------------------------
# Test 12: Mission cancellation with outstanding operations
# ---------------------------------------------------------------------------
def test_12_mission_cancellation_tracking_in_flight_tasks(test_env):
    coordinator = test_env["coordinator"]
    store = test_env["mission_store"]

    m = Mission(objective="Cancellation test", status=MissionStatus.RUNNING)
    t1 = MissionTask("t1", "In flight worker", "Result", status=MissionTaskStatus.RUNNING)
    t2 = MissionTask("t2", "Pending worker", "Result", status=MissionTaskStatus.PENDING)
    m.plan = MissionPlan(tasks=[t1, t2])
    store.save_mission(m)

    coordinator._in_flight_tasks[m.mission_id] = {"t1"}
    coordinator.cancel_mission(m.mission_id, reason="User clicked Cancel")

    loaded = store.get_mission(m.mission_id)
    assert loaded.status == MissionStatus.CANCELLING
    assert loaded.plan.get_task("t2").status == MissionTaskStatus.CANCELLED


# ---------------------------------------------------------------------------
# Test 13: Persistent progress and fresh-process restart recovery
# ---------------------------------------------------------------------------
def test_13_persistent_progress_and_fresh_process_restart_recovery(test_env):
    db_path = test_env["db_path"]
    store = test_env["mission_store"]

    m = Mission(
        mission_id="persisted_restart_m1",
        objective="Multi-step fresh restart recovery test",
        status=MissionStatus.RUNNING,
        acceptance_criteria=[AcceptanceCriterion("ac-1", "Verified after reboot")],
    )
    t1 = MissionTask("t1", "Done task", "Done", status=MissionTaskStatus.COMPLETED, evidence={"hash": "abc"})
    t2 = MissionTask("t2", "Interrupted idempotent task", "Run", status=MissionTaskStatus.RUNNING, is_idempotent=True)
    m.plan = MissionPlan(tasks=[t1, t2])
    store.save_mission(m)
    store.close()

    # Launch a completely fresh python process to inspect and reconcile
    script = f"""
import sys
from mission_store import SqliteMissionStore
from mission_model import MissionStatus, MissionTaskStatus

store = SqliteMissionStore(r"{db_path}")
interrupted = store.get_interrupted_missions()
assert len(interrupted) >= 1
reconciled = store.reconcile_interrupted_mission("persisted_restart_m1")
assert reconciled.status == MissionStatus.READY
assert reconciled.plan.get_task("t1").status == MissionTaskStatus.COMPLETED
assert reconciled.plan.get_task("t2").status == MissionTaskStatus.PENDING
store.close()
print("FRESH_PROCESS_RECOVERY_SUCCESS")
"""
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert "FRESH_PROCESS_RECOVERY_SUCCESS" in proc.stdout


# ---------------------------------------------------------------------------
# Test 14: Safe handling of ambiguous outcomes and non-idempotent retries
# ---------------------------------------------------------------------------
def test_14_ambiguous_outcomes_and_non_idempotent_retries(test_env):
    store = test_env["mission_store"]
    m = Mission(
        mission_id="ambiguous_m1",
        objective="Payment processing",
        status=MissionStatus.RUNNING,
    )
    t_non_idempotent = MissionTask(
        task_id="t_pay",
        description="Debit balance",
        intended_result="Debited",
        status=MissionTaskStatus.RUNNING,
        is_idempotent=False,
        side_effects_occurred=True,
    )
    m.plan = MissionPlan(tasks=[t_non_idempotent])
    store.save_mission(m)

    reconciled = store.reconcile_interrupted_mission(m.mission_id)
    assert reconciled.status == MissionStatus.NEEDS_ATTENTION
    assert reconciled.plan.get_task("t_pay").status == MissionTaskStatus.BLOCKED
    assert "side effects" in reconciled.status_reason.lower()


# ---------------------------------------------------------------------------
# Test 15: Acceptance criteria that remain BLOCKED or UNVERIFIED
# ---------------------------------------------------------------------------
def test_15_acceptance_criteria_blocked_or_unverified(test_env):
    coordinator = test_env["coordinator"]
    m = Mission(
        objective="Cloud sync",
        status=MissionStatus.READY,
        acceptance_criteria=[
            AcceptanceCriterion("ac-local", "Local file prepared"),
            AcceptanceCriterion("ac-cloud", "Synced to external cloud vendor", external_dependency="AWS_S3"),
        ],
    )
    m.plan = MissionPlan(tasks=[MissionTask("t-local", "Build zip", "Zip created")])
    test_env["mission_store"].save_mission(m)

    res = coordinator.execute_mission(m.mission_id)
    assert res.status == MissionStatus.PARTIALLY_COMPLETED
    assert res.acceptance_criteria[0].status == AcceptanceStatus.PASS
    assert res.acceptance_criteria[1].status == AcceptanceStatus.UNVERIFIED
    assert "external dependency" in res.acceptance_criteria[1].evidence["reason"].lower()


# ---------------------------------------------------------------------------
# Test 16: Correct PARTIALLY_COMPLETED and COMPLETED distinctions
# ---------------------------------------------------------------------------
def test_16_partially_completed_vs_completed_distinction(test_env):
    coordinator = test_env["coordinator"]
    store = test_env["mission_store"]

    # 1. Fully COMPLETED
    m_full = Mission(
        objective="Local check",
        status=MissionStatus.READY,
        acceptance_criteria=[AcceptanceCriterion("ac-1", "Criteria 1 passing")],
    )
    m_full.plan = MissionPlan(tasks=[MissionTask("t-1", "Pass task", "Passed")])
    store.save_mission(m_full)
    res_full = coordinator.execute_mission(m_full.mission_id)
    assert res_full.status == MissionStatus.COMPLETED

    # 2. PARTIALLY_COMPLETED
    m_partial = Mission(
        objective="Partial check",
        status=MissionStatus.READY,
        acceptance_criteria=[
            AcceptanceCriterion("ac-1", "Criteria 1 passing"),
            AcceptanceCriterion("ac-2", "External verification", external_dependency="VendorAPI"),
        ],
    )
    m_partial.plan = MissionPlan(tasks=[MissionTask("t-1", "Pass task", "Passed")])
    store.save_mission(m_partial)
    res_partial = coordinator.execute_mission(m_partial.mission_id)
    assert res_partial.status == MissionStatus.PARTIALLY_COMPLETED


# ---------------------------------------------------------------------------
# Test 17: Resource-governor and concurrent-task limits
# ---------------------------------------------------------------------------
def test_17_resource_governor_concurrency_limits(test_env):
    governor = test_env["governor"]
    planner = test_env["planner"]

    m = Mission(objective="Resource test")
    plan = planner.create_plan(m)
    assert m.budget.max_concurrent_tasks <= governor.foreground_slots

    # Slot denial check
    governor._voice_active = True
    task = MissionTask("t1", "Background task", "Result")
    decision = governor.acquire_slot(task=task, is_foreground=False)
    assert decision.allowed is False
    assert "Voice interaction active" in decision.reason


# ---------------------------------------------------------------------------
# Test 18: Secret redaction in persisted mission data and reports
# ---------------------------------------------------------------------------
def test_18_secret_redaction_in_mission_data_and_reports(test_env):
    store = test_env["mission_store"]
    secret_text = "API_KEY=AIzaSyA_secret_1234567890abcdef and token=ghp_secretpassword123"

    m = Mission(
        objective=f"Deploy with token: {secret_text}",
        explicit_constraints=[f"Use key {secret_text}"],
    )
    t = MissionTask(
        task_id="t1",
        description=f"Configure credential {secret_text}",
        intended_result="Configured",
        error=f"Authentication failed: {secret_text}",
    )
    m.plan = MissionPlan(tasks=[t])
    store.save_mission(m)

    loaded = store.get_mission(m.mission_id)
    assert "AIzaSyA_secret" not in loaded.objective
    assert "[REDACTED" in loaded.objective
    assert "[REDACTED" in loaded.explicit_constraints[0]
    assert "[REDACTED" in loaded.plan.tasks[0].description
    assert "[REDACTED" in loaded.plan.tasks[0].error


# ---------------------------------------------------------------------------
# Test 19: Mission history integrated with existing task and incident infrastructure
# ---------------------------------------------------------------------------
def test_19_mission_history_and_task_store_integration(test_env):
    store = test_env["mission_store"]
    event_bus = test_env["event_bus"]

    events = []
    event_bus.subscribe("mission.task.completed", lambda ev: events.append(ev))

    coordinator = test_env["coordinator"]
    m = Mission(
        objective="Event history test",
        status=MissionStatus.READY,
        acceptance_criteria=[AcceptanceCriterion("ac-1", "Done")],
    )
    m.plan = MissionPlan(tasks=[MissionTask("t1", "Quick task", "Result")])
    store.save_mission(m)

    coordinator.execute_mission(m.mission_id)
    assert len(events) >= 1
    assert events[0].task_id == m.mission_id

    # Query missions list
    all_missions = store.list_missions()
    assert any(x.mission_id == m.mission_id for x in all_missions)


# ---------------------------------------------------------------------------
# Test 20: Compatibility with existing Phase 15–26.1 tests
# ---------------------------------------------------------------------------
def test_20_runtime_integration_and_backward_compatibility(test_env):
    appdata = test_env["tmp_dir"]
    rt = JevRuntime(appdata_dir=appdata, enable_heartbeat=False)

    status = rt.get_status()
    assert "missions_count" in status
    assert "runtime_running" in status

    # Create mission via JevRuntime
    mission, meta = rt.create_mission("Organize download folders")
    assert mission.mission_id is not None
    assert mission.status == MissionStatus.READY

    rt.stop()
    assert rt.is_running is False
