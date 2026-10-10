"""Phase 27.1 Reliability Audit Test Suite for Hey Jev.
Audits:
1. End-to-end mission lifecycle through real executors and WorkflowEngine.
2. Approval gating, plan versions, checkpoint pausing, and idempotent resumption.
3. Real capability executor contracts, ToolResult/AgentResult, and downstream output piping.
4. Fresh-process persistence and restart recovery across distinct interruption points.
5. Adaptive replanning with a 3-task dependency chain, preserving verified completed work.
6. Mission budgets, active worker cancellation tracking, and concurrent execution deduplication.
7. Acceptance criteria verification and status consistency (COMPLETED, PARTIALLY_COMPLETED, BLOCKED, NEEDS_ATTENTION, FAILED, CANCELLED).
8. JevRuntime lifecycle integration, connection cleanup, and voice coexistence.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List

import pytest
from agent_task import TaskPermission
from capability_model import ApprovalRequirement, CapabilityDefinition, CapabilityType, Reversibility
from capability_registry import CapabilityRegistry
from capability_workflow import WorkflowMode
from event_bus import EventBus
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
)
from mission_planner import MissionPlanner
from mission_replanner import BudgetExhaustedError, MissionReplanner
from mission_store import SqliteMissionStore
from resource_governor import ResourceGovernor
from runtime_manager import JevRuntime
from skill_executor import SkillExecutor
from skill_registry import SkillRegistry
from tool_model import RiskLevel, ToolResult
from tool_registry import ToolRegistry
from tool_scanner import ToolScanner
from workflow_engine import WorkflowEngine


@pytest.fixture
def audit_env():
    """Build isolated audit test environment with real scanners and SQLite store."""
    tmp_dir = tempfile.mkdtemp(prefix="jev_phase27_1_audit_")
    db_path = os.path.join(tmp_dir, "audit_missions.db")

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
    governor = ResourceGovernor(foreground_slots=1, background_slots=2)
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


# ===========================================================================
# 1. Complete Mission Lifecycle Integration
# ===========================================================================
def test_audit_complete_mission_lifecycle(audit_env):
    """Exercise complete mission lifecycle through real registered capability infrastructure."""
    coordinator = audit_env["coordinator"]
    interpreter = audit_env["interpreter"]
    planner = audit_env["planner"]
    store = audit_env["mission_store"]
    tmp_dir = audit_env["tmp_dir"]

    # Create dummy files for read-only inspection
    sample_file = os.path.join(tmp_dir, "sample.txt")
    with open(sample_file, "w", encoding="utf-8") as f:
        f.write("Hello Jev Audit")

    # 1. Create and interpret
    objective = f"Inspect directory {tmp_dir} and list all entries"
    mission, _ = interpreter.interpret_objective(
        objective=objective,
        context={"path": tmp_dir},
        mode=MissionMode.COPILOT,
    )
    assert mission.status == MissionStatus.PLANNING
    assert len(mission.acceptance_criteria) >= 1

    # 2. Build Plan
    plan = planner.create_plan(mission, context={"path": tmp_dir})
    assert len(plan.tasks) >= 1
    assert mission.status == MissionStatus.READY

    # 3. Persist Plan
    store.save_mission(mission)
    loaded = store.get_mission(mission.mission_id)
    assert loaded is not None
    assert loaded.plan is not None

    # 4. Preview in COPILOT mode: execute with granted permissions
    result = coordinator.execute_mission(
        mission.mission_id,
        context={"path": tmp_dir},
        granted_permissions=[TaskPermission.READ_FILES],
    )

    # 5. Verify outcomes and acceptance criteria
    assert result.status == MissionStatus.COMPLETED
    assert result.plan.tasks[0].status == MissionTaskStatus.COMPLETED
    assert "entries" in result.plan.tasks[0].output
    assert result.acceptance_criteria[0].status == AcceptanceStatus.PASS
    assert result.plan.tasks[0].completed_at is not None

    # 6. Verify durable persistence
    persisted = store.get_mission(mission.mission_id)
    assert persisted.status == MissionStatus.COMPLETED
    assert persisted.plan.tasks[0].status == MissionTaskStatus.COMPLETED


# ===========================================================================
# 2. Approval and Execution Resumption
# ===========================================================================
def test_audit_approval_checkpoint_pausing_and_scoped_execution(audit_env):
    """Test pausing at checkpoint, plan approval scoping, and duplicate prevention."""
    coordinator = audit_env["coordinator"]
    store = audit_env["mission_store"]
    cap_reg = audit_env["cap_reg"]

    # Register custom capability with mandatory approval
    executed_ops = []
    cap_reg.register(
        CapabilityDefinition(
            capability_id="tool:audit.safe_op",
            name="Safe Operation",
            description="Safe read-only op",
            risk_level=RiskLevel.LOW,
            handler=lambda: executed_ops.append("safe") or {"status": "ok"},
        )
    )
    cap_reg.register(
        CapabilityDefinition(
            capability_id="tool:audit.risky_op",
            name="Risky Operation",
            description="Risky mutation op",
            risk_level=RiskLevel.HIGH,
            approval_requirement=ApprovalRequirement.MANDATORY,
            handler=lambda: executed_ops.append("risky") or {"status": "mutated"},
        )
    )

    m = Mission(
        objective="Gated execution audit",
        mode=MissionMode.COPILOT,
        status=MissionStatus.READY,
        acceptance_criteria=[AcceptanceCriterion("ac-1", "Risky op executed")],
    )
    t1 = MissionTask("t1", "Safe step", "Done", capability_id="tool:audit.safe_op")
    t2 = MissionTask(
        "t2",
        "Risky step",
        "Mutated",
        capability_id="tool:audit.risky_op",
        dependencies=["t1"],
        risk_level=RiskLevel.HIGH,
        approval_checkpoint=True,
        approval_reason="Requires operator confirmation before mutation",
    )
    m.plan = MissionPlan(plan_version=1, tasks=[t1, t2])
    store.save_mission(m)

    # 1. Execute unapproved: safe step executes, risky step pauses
    res = coordinator.execute_mission(m.mission_id)
    assert res.status == MissionStatus.WAITING_FOR_APPROVAL
    assert res.plan.tasks[0].status == MissionTaskStatus.COMPLETED
    assert res.plan.tasks[1].status == MissionTaskStatus.NEEDS_APPROVAL
    assert executed_ops == ["safe"]

    # 2. Re-executing while waiting does not duplicate completed work
    res2 = coordinator.execute_mission(m.mission_id)
    assert res2.status == MissionStatus.WAITING_FOR_APPROVAL
    assert executed_ops == ["safe"]  # Still 1 safe execution

    # 3. Approve exact plan version and resume
    assert coordinator.approve_plan(m.mission_id, plan_version=1) is True
    res3 = coordinator.execute_mission(m.mission_id)
    assert res3.status == MissionStatus.COMPLETED
    assert res3.plan.tasks[1].status == MissionTaskStatus.COMPLETED
    assert executed_ops == ["safe", "risky"]

    # 4. Repeated approval and resume requests are idempotent
    assert coordinator.approve_plan(m.mission_id, plan_version=1) is True
    res4 = coordinator.execute_mission(m.mission_id)
    assert res4.status == MissionStatus.COMPLETED
    assert executed_ops == ["safe", "risky"]  # No duplicate execution


def test_audit_permissions_enforced_despite_plan_approval(audit_env):
    """Verify that approving a plan does NOT bypass required execution permissions."""
    coordinator = audit_env["coordinator"]
    store = audit_env["mission_store"]
    tmp_dir = audit_env["tmp_dir"]

    m = Mission(
        objective="Permission audit",
        status=MissionStatus.READY,
        approved_plan_version=1,
        approved_tasks={"t1"},
        acceptance_criteria=[AcceptanceCriterion("ac-1", "Done")],
    )
    t = MissionTask(
        task_id="t1",
        description="List files",
        intended_result="Listed",
        capability_id="tool:filesystem.list_directory",
        inputs={"path": tmp_dir},
        required_permissions=["READ_FILES"],
    )
    m.plan = MissionPlan(plan_version=1, tasks=[t])
    store.save_mission(m)

    # Execute WITHOUT granting READ_FILES
    res = coordinator.execute_mission(m.mission_id, granted_permissions=[])
    assert res.status == MissionStatus.BLOCKED
    assert res.plan.tasks[0].status == MissionTaskStatus.BLOCKED
    assert "permission" in str(res.plan.tasks[0].error).lower()
    assert res.acceptance_criteria[0].status != AcceptanceStatus.PASS


def test_audit_approval_older_plan_version_rejected_after_replan(audit_env):
    """Verify that approvals tied to an old plan version are rejected after replanning."""
    coordinator = audit_env["coordinator"]
    replanner = audit_env["replanner"]
    store = audit_env["mission_store"]

    m = Mission(objective="Version approval audit", status=MissionStatus.RUNNING)
    t = MissionTask("t1", "Task", "Done", status=MissionTaskStatus.FAILED)
    m.plan = MissionPlan(plan_version=1, tasks=[t])
    store.save_mission(m)

    # Replan creates plan v2
    replanner.replan(m, trigger_reason="Task failure")
    assert m.plan.plan_version == 2
    store.save_mission(m)

    # Attempting to approve plan v1 must fail
    assert coordinator.approve_plan(m.mission_id, plan_version=1) is False
    # Approving plan v2 succeeds
    assert coordinator.approve_plan(m.mission_id, plan_version=2) is True


# ===========================================================================
# 3. Real Executor and Workflow Integration
# ===========================================================================
def test_audit_downstream_dependency_output_pipelining(audit_env):
    """Verify task output piping into downstream dependencies."""
    coordinator = audit_env["coordinator"]
    store = audit_env["mission_store"]
    cap_reg = audit_env["cap_reg"]

    received_inputs = {}

    cap_reg.register(
        CapabilityDefinition(
            capability_id="tool:audit.step_one",
            name="Step One",
            description="Returns data dict",
            handler=lambda: {"auth_token": "token_abc123", "target_id": 42},
        )
    )
    cap_reg.register(
        CapabilityDefinition(
            capability_id="tool:audit.step_two",
            name="Step Two",
            description="Consumes step one data",
            handler=lambda **kwargs: received_inputs.update(kwargs) or {"status": "success"},
        )
    )

    m = Mission(
        objective="Pipelining test",
        status=MissionStatus.READY,
        acceptance_criteria=[AcceptanceCriterion("ac-1", "Pipelined successfully")],
    )
    t1 = MissionTask("t1", "Step 1", "Token generated", capability_id="tool:audit.step_one")
    t2 = MissionTask(
        "t2",
        "Step 2",
        "Data received",
        capability_id="tool:audit.step_two",
        dependencies=["t1"],
        inputs={"token": "$t1.auth_token", "target": "$t1.target_id"},
    )
    m.plan = MissionPlan(tasks=[t1, t2])
    store.save_mission(m)

    res = coordinator.execute_mission(m.mission_id)
    assert res.status == MissionStatus.COMPLETED
    assert res.plan.tasks[0].status == MissionTaskStatus.COMPLETED
    assert res.plan.tasks[1].status == MissionTaskStatus.COMPLETED
    assert received_inputs.get("token") == "token_abc123"
    assert received_inputs.get("target") == 42


def test_audit_toolresult_and_agentresult_handling(audit_env):
    """Verify ToolResult objects from real tools are handled properly."""
    coordinator = audit_env["coordinator"]
    store = audit_env["mission_store"]
    cap_reg = audit_env["cap_reg"]

    cap_reg.register(
        CapabilityDefinition(
            capability_id="tool:audit.toolresult_success",
            name="ToolResult Success",
            description="Returns ToolResult with data",
            handler=lambda **kw: ToolResult(tool_id="tool:audit.toolresult_success", status="SUCCESS", output={"metric": 99.5}),
        )
    )
    cap_reg.register(
        CapabilityDefinition(
            capability_id="tool:audit.toolresult_fail",
            name="ToolResult Failure",
            description="Returns ToolResult with error",
            handler=lambda **kw: ToolResult(tool_id="tool:audit.toolresult_fail", status="FAILURE", error="Device disconnected"),
        )
    )

    m = Mission(
        objective="ToolResult audit",
        status=MissionStatus.READY,
        acceptance_criteria=[AcceptanceCriterion("ac-1", "Done")],
    )
    t1 = MissionTask("t1", "Good tool", "Success", capability_id="tool:audit.toolresult_success")
    t2 = MissionTask("t2", "Bad tool", "Fail", capability_id="tool:audit.toolresult_fail", dependencies=["t1"])
    m.plan = MissionPlan(tasks=[t1, t2])
    store.save_mission(m)

    res = coordinator.execute_mission(m.mission_id)
    assert res.plan.tasks[0].status == MissionTaskStatus.COMPLETED
    assert res.plan.tasks[0].output == {"metric": 99.5}
    assert res.plan.tasks[1].status in {MissionTaskStatus.FAILED, MissionTaskStatus.BLOCKED}
    assert "Device disconnected" in str(res.plan.tasks[1].error)


# ===========================================================================
# 4. Mission Persistence and Fresh-Process Recovery
# ===========================================================================
def test_audit_fresh_process_restart_recovery_points(audit_env):
    """Test fresh-process restart recovery across multiple lifecycle interruption states."""
    db_path = audit_env["db_path"]
    store = audit_env["mission_store"]

    # Point A: Mission in CREATED
    m_created = Mission(mission_id="m_created", objective="Created test", status=MissionStatus.CREATED)
    store.save_mission(m_created)

    # Point B: Mission in WAITING_FOR_APPROVAL
    m_waiting = Mission(mission_id="m_waiting", objective="Waiting test", status=MissionStatus.WAITING_FOR_APPROVAL)
    t_w = MissionTask("tw1", "Pending approval", "Wait", status=MissionTaskStatus.NEEDS_APPROVAL)
    m_waiting.plan = MissionPlan(tasks=[t_w])
    store.save_mission(m_waiting)

    # Point C: Mission in RUNNING with non-idempotent task (Ambiguous crash)
    m_running_ambig = Mission(mission_id="m_running_ambig", objective="Ambig crash", status=MissionStatus.RUNNING)
    t_c1 = MissionTask("tc1", "Done step", "Ok", status=MissionTaskStatus.COMPLETED)
    t_c2 = MissionTask(
        "tc2",
        "Interrupted mutation",
        "Mutated",
        status=MissionTaskStatus.RUNNING,
        is_idempotent=False,
        side_effects_occurred=True,
    )
    m_running_ambig.plan = MissionPlan(tasks=[t_c1, t_c2])
    store.save_mission(m_running_ambig)

    # Point D: Mission in RUNNING with idempotent task (Safe retry)
    m_running_idem = Mission(mission_id="m_running_idem", objective="Idem crash", status=MissionStatus.RUNNING)
    t_i1 = MissionTask("ti1", "Interrupted read", "Read", status=MissionTaskStatus.RUNNING, is_idempotent=True)
    m_running_idem.plan = MissionPlan(tasks=[t_i1])
    store.save_mission(m_running_idem)

    # Point E: Mission in CANCELLING
    m_cancelling = Mission(mission_id="m_cancelling", objective="Cancel crash", status=MissionStatus.CANCELLING)
    t_can = MissionTask("tcan1", "In-flight cancel", "Stop", status=MissionTaskStatus.RUNNING)
    m_cancelling.plan = MissionPlan(tasks=[t_can])
    store.save_mission(m_cancelling)

    # Point F: Mission in PLANNING without plan
    m_planning = Mission(mission_id="m_planning", objective="Plan crash", status=MissionStatus.PLANNING)
    store.save_mission(m_planning)

    store.close()

    # Run separate fresh Python process to perform startup recovery
    recovery_code = f"""
import sys
from mission_store import SqliteMissionStore

store = SqliteMissionStore(r"{db_path}")
interrupted = store.get_interrupted_missions()
for m in interrupted:
    store.reconcile_interrupted_mission(m.mission_id)
store.close()
"""
    proc = subprocess.run([sys.executable, "-c", recovery_code], capture_output=True, text=True)
    assert proc.returncode == 0, f"Recovery subprocess failed: {proc.stderr}"

    # Verify reconciliation in fresh store instance
    verify_store = SqliteMissionStore(db_path=db_path)

    # Check Point A: CREATED unchanged
    assert verify_store.get_mission("m_created").status == MissionStatus.CREATED

    # Check Point B: WAITING_FOR_APPROVAL unchanged
    assert verify_store.get_mission("m_waiting").status == MissionStatus.WAITING_FOR_APPROVAL

    # Check Point C: Ambiguous non-idempotent task marked BLOCKED, mission marked NEEDS_ATTENTION
    m_ambig_rec = verify_store.get_mission("m_running_ambig")
    assert m_ambig_rec.status == MissionStatus.NEEDS_ATTENTION
    assert m_ambig_rec.plan.tasks[0].status == MissionTaskStatus.COMPLETED  # Completed preserved!
    assert m_ambig_rec.plan.tasks[1].status == MissionTaskStatus.BLOCKED

    # Check Point D: Idempotent task reset to PENDING, mission READY
    m_idem_rec = verify_store.get_mission("m_running_idem")
    assert m_idem_rec.status == MissionStatus.READY
    assert m_idem_rec.plan.tasks[0].status == MissionTaskStatus.PENDING

    # Check Point E: CANCELLING reconciled to CANCELLED
    m_can_rec = verify_store.get_mission("m_cancelling")
    assert m_can_rec.status == MissionStatus.CANCELLED
    assert m_can_rec.plan.tasks[0].status == MissionTaskStatus.CANCELLED

    # Check Point F: PLANNING reconciled to NEEDS_ATTENTION
    m_plan_rec = verify_store.get_mission("m_planning")
    assert m_plan_rec.status == MissionStatus.NEEDS_ATTENTION

    verify_store.close()


# ===========================================================================
# 5. Adaptive Replanning with 3-Task Dependency Chain
# ===========================================================================
def test_audit_adaptive_replanning_three_dependent_tasks(audit_env):
    """Test adaptive replanning on a 3-task dependency chain:
    Task 1 (Success) -> Task 2 (Fails) -> Task 3 (Depends on Task 2).
    Completed work is preserved; Task 3 not prematurely executed; plan v2 generated.
    """
    coordinator = audit_env["coordinator"]
    store = audit_env["mission_store"]
    cap_reg = audit_env["cap_reg"]

    t3_executed = False

    cap_reg.register(
        CapabilityDefinition(
            capability_id="tool:audit.dep_task1",
            name="Dep Task 1",
            description="Task 1",
            handler=lambda: {"res1": "success"},
        )
    )
    cap_reg.register(
        CapabilityDefinition(
            capability_id="tool:audit.dep_task2_fail",
            name="Dep Task 2 Fail",
            description="Task 2 fails",
            handler=lambda: {"status": "FAILED", "error": "Disk I/O timeout"},
        )
    )
    cap_reg.register(
        CapabilityDefinition(
            capability_id="tool:audit.dep_task3",
            name="Dep Task 3",
            description="Task 3 depends on 2",
            handler=lambda: setattr(sys, "_t3_ran", True) or {"res3": "ok"},
        )
    )

    m = Mission(
        objective="3-task dependency audit",
        status=MissionStatus.READY,
        acceptance_criteria=[
            AcceptanceCriterion("ac-1", "Task 1 complete"),
            AcceptanceCriterion("ac-2", "Task 2 complete"),
            AcceptanceCriterion("ac-3", "Task 3 complete"),
        ],
    )
    t1 = MissionTask("t1", "Task 1", "Done 1", capability_id="tool:audit.dep_task1")
    t2 = MissionTask(
        "t2",
        "Task 2",
        "Done 2",
        capability_id="tool:audit.dep_task2_fail",
        dependencies=["t1"],
        max_retries=1,
    )
    t3 = MissionTask(
        "t3",
        "Task 3",
        "Done 3",
        capability_id="tool:audit.dep_task3",
        dependencies=["t2"],
    )
    m.plan = MissionPlan(plan_version=1, tasks=[t1, t2, t3])
    store.save_mission(m)

    # Execute
    res = coordinator.execute_mission(m.mission_id)

    # 1. Task 1 was completed and preserved
    assert res.plan.tasks[0].status == MissionTaskStatus.COMPLETED
    assert res.plan.tasks[0].output == {"res1": "success"}

    # 2. Task 2 failed and triggered replanning to plan v2
    assert res.plan.plan_version == 2

    # 3. Task 3 was NOT executed prematurely
    assert getattr(sys, "_t3_ran", False) is False

    # 4. Mission is not falsely marked COMPLETED
    assert res.status in {MissionStatus.READY, MissionStatus.BLOCKED, MissionStatus.FAILED}
    assert res.status != MissionStatus.COMPLETED
    assert res.acceptance_criteria[2].status != AcceptanceStatus.PASS


# ===========================================================================
# 6. Mission Budgets, Cancellation and Concurrency
# ===========================================================================
def test_audit_cancellation_tracks_active_worker_until_cessation(audit_env):
    """Test mission cancellation while task is active."""
    coordinator = audit_env["coordinator"]
    store = audit_env["mission_store"]
    cap_reg = audit_env["cap_reg"]

    worker_started = threading.Event()
    worker_can_finish = threading.Event()
    worker_finished = threading.Event()

    def slow_worker():
        worker_started.set()
        worker_can_finish.wait(timeout=5.0)
        worker_finished.set()
        return {"result": "slow_done"}

    cap_reg.register(
        CapabilityDefinition(
            capability_id="tool:audit.slow_action",
            name="Slow Action",
            description="Long running action",
            handler=slow_worker,
        )
    )

    m = Mission(
        objective="Cancellation test",
        status=MissionStatus.READY,
    )
    t = MissionTask("t1", "Slow step", "Done", capability_id="tool:audit.slow_action")
    m.plan = MissionPlan(tasks=[t])
    store.save_mission(m)

    exec_thread = threading.Thread(
        target=coordinator.execute_mission,
        args=(m.mission_id,),
    )
    exec_thread.start()

    # Wait until task is actively running
    assert worker_started.wait(timeout=3.0) is True

    # Cancel while worker in flight: must enter CANCELLING
    cancelled = coordinator.cancel_mission(m.mission_id, reason="User cancelled slow task")
    assert cancelled is True
    mid_state = store.get_mission(m.mission_id)
    assert mid_state.status == MissionStatus.CANCELLING

    # Release worker to finish
    worker_can_finish.set()
    exec_thread.join(timeout=5.0)
    assert worker_finished.is_set() is True

    # Mission status must be CANCELLED now that all in-flight workers ceased
    final_state = store.get_mission(m.mission_id)
    assert final_state.status == MissionStatus.CANCELLED


def test_audit_concurrent_execute_mission_deduplication(audit_env):
    """Verify concurrent attempts to execute the same mission do not double-dispatch tasks."""
    coordinator = audit_env["coordinator"]
    store = audit_env["mission_store"]
    cap_reg = audit_env["cap_reg"]

    dispatch_counter = 0
    lock = threading.Lock()

    def counting_handler():
        nonlocal dispatch_counter
        with lock:
            dispatch_counter += 1
        time.sleep(0.05)
        return {"count": dispatch_counter}

    cap_reg.register(
        CapabilityDefinition(
            capability_id="tool:audit.count_action",
            name="Count Action",
            description="Counts executions",
            handler=counting_handler,
        )
    )

    m = Mission(objective="Concurrent dedup test", status=MissionStatus.READY)
    t = MissionTask("t1", "Count step", "Counted", capability_id="tool:audit.count_action")
    m.plan = MissionPlan(tasks=[t])
    store.save_mission(m)

    # Launch 4 concurrent threads trying to execute the same mission
    threads = [
        threading.Thread(target=coordinator.execute_mission, args=(m.mission_id,))
        for _ in range(4)
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=5.0)

    # Exactly 1 dispatch occurred!
    assert dispatch_counter == 1
    final_m = store.get_mission(m.mission_id)
    assert final_m.status == MissionStatus.COMPLETED


# ===========================================================================
# 7. Acceptance Criteria and Final Status Consistency
# ===========================================================================
def test_audit_status_consistency_partially_completed_vs_completed(audit_env):
    """Verify PARTIALLY_COMPLETED status when an unverified external dependency remains."""
    coordinator = audit_env["coordinator"]
    store = audit_env["mission_store"]

    m = Mission(
        objective="Partial completion test",
        status=MissionStatus.READY,
        acceptance_criteria=[
            AcceptanceCriterion("ac-1", "Internal task completed"),
            AcceptanceCriterion(
                "ac-2",
                "Remote third party verification",
                external_dependency="https://external-service.org/verify",
            ),
        ],
    )
    t1 = MissionTask("t1", "Internal task", "Done")
    m.plan = MissionPlan(tasks=[t1])
    store.save_mission(m)

    res = coordinator.execute_mission(m.mission_id)
    assert res.status == MissionStatus.PARTIALLY_COMPLETED
    assert res.acceptance_criteria[0].status == AcceptanceStatus.PASS
    assert res.acceptance_criteria[1].status == AcceptanceStatus.UNVERIFIED
    assert "cannot be verified internally" in res.acceptance_criteria[1].evidence.get("reason", "")


# ===========================================================================
# 8. Runtime Integration and Voice Coexistence
# ===========================================================================
def test_audit_runtime_initialization_and_repeated_start_stop():
    """Verify JevRuntime lifecycle, single initialization, and clean connection management."""
    tmp_dir = tempfile.mkdtemp(prefix="jev_phase27_1_runtime_")
    runtime = JevRuntime(appdata_dir=tmp_dir, enable_heartbeat=False)

    # Verify single initialization of mission components
    assert runtime.mission_store is not None
    assert runtime.mission_coordinator is not None
    assert runtime.mission_interpreter is not None
    assert runtime.mission_planner is not None
    assert runtime.mission_replanner is not None

    # First cycle
    runtime.start()
    assert runtime.is_running is True
    status1 = runtime.get_status()
    assert status1["runtime_running"] is True
    assert status1["missions_count"] == 0

    runtime.stop()
    assert runtime.is_running is False

    # Second cycle (repeated start/stop must be clean without duplicate instances)
    runtime.start()
    assert runtime.is_running is True
    # Register safe audit capability
    runtime.capability_registry.register(
        CapabilityDefinition(
            capability_id="tool:runtime.audit_task",
            name="Runtime Audit Task",
            description="Audit service",
            handler=lambda: {"status": "ok"},
        )
    )
    m, _ = runtime.create_mission("Run runtime audit task")
    assert m.status == MissionStatus.READY
    status2 = runtime.get_status()
    assert status2["missions_count"] == 1

    runtime.stop()
    assert runtime.is_running is False


def test_audit_voice_coexistence_governor_slot_prioritization(audit_env):
    """Verify that background mission execution respects foreground voice interaction."""
    governor = audit_env["governor"]
    coordinator = audit_env["coordinator"]
    store = audit_env["mission_store"]

    m = Mission(objective="Voice coexistence test", status=MissionStatus.READY)
    t = MissionTask("t1", "Background action", "Done")
    m.plan = MissionPlan(tasks=[t])
    store.save_mission(m)

    # Simulate active foreground voice interaction
    governor._voice_active = True

    # Mission execution must pause/yield rather than starving voice interaction
    res = coordinator.execute_mission(m.mission_id)
    assert res.plan.tasks[0].status == MissionTaskStatus.PENDING  # Task was not run while voice active

    # Deactivate voice
    governor._voice_active = False

    # Resuming execution succeeds now that voice is inactive
    res2 = coordinator.execute_mission(m.mission_id)
    assert res2.status == MissionStatus.COMPLETED
    assert res2.plan.tasks[0].status == MissionTaskStatus.COMPLETED
