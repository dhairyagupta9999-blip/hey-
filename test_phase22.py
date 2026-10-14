"""Focused Verification and Acceptance Tests for Hey Jev Phase 22.

Covers all 14 required capabilities:
1. Discovery of a capability from an outcome-oriented description.
2. Correct rejection of unavailable or inapplicable tools.
3. Ranking of candidates using capability metadata.
4. Missing input and ambiguous-plan handling.
5. Construction of a multi-step dependency-aware workflow.
6. Correct passing and validation of step outputs.
7. Enforcement of approval gates for risky steps (COPILOT mode).
8. Refusal to auto-execute untrusted or gated capabilities (AUTOPILOT mode).
9. Bounded retries and resource constraints.
10. Failure propagation and rollback behavior.
11. Verification that distinguishes a real successful outcome from a tool call that merely returned.
12. Logging of selections, execution results, and audit events.
13. Graceful operation when an LLM provider is unavailable.
14. Regression compatibility with existing tools, skills, runtime, and memory.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
from agent_task import TaskPermission
from capability_discovery import CapabilityDiscoveryEngine, DiscoveryResult
from capability_model import (
    ApprovalRequirement,
    CapabilityDefinition,
    CapabilityType,
    Reversibility,
)
from capability_registry import CapabilityRegistry
from capability_workflow import (
    StepStatus,
    WorkflowComposer,
    WorkflowMode,
    WorkflowPlan,
    WorkflowStep,
)
from event_bus import EventBus
from memory_model import MemoryRecord, MemoryType
from memory_store import SqliteMemoryStore
from skill_model import SkillDefinition, SkillTrustLevel
from skill_registry import SkillRegistry
from tool_model import RiskLevel, ToolDefinition, ToolHealth, ToolResult
from tool_registry import ToolRegistry
from workflow_engine import (
    WorkflowEngine,
    WorkflowExecutionReport,
    WorkflowExecutionStatus,
)


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="heyjev_test_p22_")
    yield d
    try:
        shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


@pytest.fixture
def populated_registry():
    """Build a populated CapabilityRegistry with sample tools and skills."""
    t_reg = ToolRegistry()
    s_reg = SkillRegistry()

    # Tool 1: list_directory
    def handler_list_dir(path: str = ".", **kwargs):
        if not os.path.exists(path):
            return ToolResult(tool_id="filesystem.list_directory", status="FAILURE", error=f"Path '{path}' not found")
        files = os.listdir(path)
        entries = [{"name": f, "path": os.path.join(path, f)} for f in files]
        return ToolResult(tool_id="filesystem.list_directory", status="SUCCESS", output={"entries": entries, "count": len(entries)})

    t_list = ToolDefinition(
        tool_id="filesystem.list_directory",
        name="List Directory",
        description="Inspect folder contents and list files in a directory",
        capabilities=["directory_listing", "file_discovery", "picture_discovery"],
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        output_schema={"type": "object", "properties": {"entries": {"type": "array"}}},
        permissions=[TaskPermission.READ_FILES],
        risk_level=RiskLevel.LOW,
        supports_verification=True,
    )
    t_reg.register(t_list, handler=handler_list_dir)

    # Tool 2: move_file
    def handler_move(source: str, destination: str, **kwargs):
        if not os.path.exists(source):
            return ToolResult(tool_id="filesystem.move_file", status="FAILURE", error="Source file missing")
        os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
        shutil.move(source, destination)
        return ToolResult(
            tool_id="filesystem.move_file",
            status="SUCCESS",
            output={"source": source, "destination": destination, "moved": True},
            rollback_metadata={"undo_action": "move_file", "source": source, "destination": destination},
        )

    t_move = ToolDefinition(
        tool_id="filesystem.move_file",
        name="Move File",
        description="Move or relocate a file to a new destination folder",
        capabilities=["file_moving", "file_organization"],
        input_schema={"type": "object", "properties": {"source": {"type": "string"}, "destination": {"type": "string"}}, "required": ["source", "destination"]},
        output_schema={"type": "object", "properties": {"moved": {"type": "boolean"}}},
        permissions=[TaskPermission.READ_FILES, TaskPermission.WRITE_FILES],
        risk_level=RiskLevel.MEDIUM,
        supports_rollback=True,
        supports_verification=True,
    )
    t_reg.register(t_move, handler=handler_move)

    # Tool 3: set_wallpaper
    def handler_wallpaper(path: str, **kwargs):
        return ToolResult(
            tool_id="windows.set_wallpaper",
            status="SUCCESS",
            output={"wallpaper_path": path, "applied": True},
        )

    t_wall = ToolDefinition(
        tool_id="windows.set_wallpaper",
        name="Set Desktop Wallpaper",
        description="Change desktop background picture using an image file",
        capabilities=["wallpaper", "desktop_background"],
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        output_schema={"type": "object", "properties": {"applied": {"type": "boolean"}}},
        permissions=[TaskPermission.CHANGE_SETTINGS],
        risk_level=RiskLevel.MEDIUM,
        supports_verification=True,
    )
    t_reg.register(t_wall, handler=handler_wallpaper)

    # Skill: downloads.organization
    s_org = SkillDefinition(
        skill_id="downloads.organization",
        name="Organize Downloads Folder",
        description="Scan directory for disorganized items and categorize them",
        required_tools=["filesystem.list_directory", "filesystem.move_file"],
        inputs={"path": {"type": "string", "required": True}},
        outputs={"organized": {"type": "boolean"}},
        permissions=[TaskPermission.READ_FILES, TaskPermission.WRITE_FILES],
        risk_level=RiskLevel.MEDIUM,
        tags=["organize", "downloads", "cleanup", "sort"],
    )
    s_reg.register(s_org)

    cap_reg = CapabilityRegistry(tool_registry=t_reg, skill_registry=s_reg)
    return cap_reg


# ==============================================================================
# TEST 1: Outcome-Oriented Capability Discovery
# ==============================================================================
def test_01_discovery_from_outcome_oriented_description(populated_registry):
    engine = CapabilityDiscoveryEngine(registry=populated_registry)
    # Natural outcome goal without mentioning tool names
    res = engine.discover("Find and list my downloaded photo files")
    assert res.best_match is not None
    assert res.best_match.capability.capability_id == "filesystem.list_directory"
    assert res.confidence > 0.50
    assert "matches outcome" in res.best_match.rationale or "semantic relevance" in res.best_match.rationale


# ==============================================================================
# TEST 2: Rejection of Unavailable or Inapplicable Tools
# ==============================================================================
def test_02_rejection_of_unavailable_or_inapplicable_tools(populated_registry):
    engine = CapabilityDiscoveryEngine(registry=populated_registry)

    # 1. Completely impossible / unmapped capability
    res1 = engine.discover("Teleport my physical laptop to Paris")
    assert res1.best_match is None or res1.confidence < 0.25
    assert res1.unsupported_reason is not None

    # 2. Tool marked BROKEN / UNAVAILABLE
    broken_tool = ToolDefinition(
        tool_id="broken.nuclear_control",
        name="Nuclear Control",
        description="Operate reactor core",
        capabilities=["reactor"],
        health=ToolHealth.BROKEN,
    )
    populated_registry.tool_registry.register(broken_tool)
    populated_registry.sync_from_registries()

    res2 = engine.discover("Operate reactor core")
    assert res2.best_match is None or res2.best_match.capability.capability_id != "broken.nuclear_control"


# ==============================================================================
# TEST 3: Ranking Candidates Using Capability Metadata
# ==============================================================================
def test_03_ranking_candidates_using_metadata(populated_registry):
    # Register two competing tools for file inspection
    safe_cap = CapabilityDefinition(
        capability_id="safe.reader",
        name="Safe File Inspector",
        description="Inspect files in folder with zero risk",
        supported_outcomes=["inspect_folder"],
        risk_level=RiskLevel.LOW,
        reversibility=Reversibility.READ_ONLY,
        health=ToolHealth.HEALTHY,
        permissions=[TaskPermission.READ_FILES],
    )
    risky_cap = CapabilityDefinition(
        capability_id="risky.reader",
        name="Risky File Inspector",
        description="Inspect files in folder with high risk mutation",
        supported_outcomes=["inspect_folder"],
        risk_level=RiskLevel.HIGH,
        reversibility=Reversibility.IRREVERSIBLE,
        health=ToolHealth.DEGRADED,
        permissions=[TaskPermission.READ_FILES, TaskPermission.EXECUTE_COMMAND],
    )
    populated_registry.register(safe_cap)
    populated_registry.register(risky_cap)

    engine = CapabilityDiscoveryEngine(registry=populated_registry)
    res = engine.discover("Inspect files in folder", prefer_read_only=True)

    assert len(res.candidates) >= 2
    # Safe tool must rank higher due to health bonus, lower risk penalty, and read-only bonus
    assert res.candidates[0].capability.capability_id == "safe.reader"
    assert res.candidates[0].score > res.candidates[1].score


# ==============================================================================
# TEST 4: Missing Input and Ambiguous-Plan Handling
# ==============================================================================
def test_04_missing_input_and_ambiguous_plan_handling(populated_registry):
    engine = CapabilityDiscoveryEngine(registry=populated_registry)

    # 1. Missing input detection: move_file requires destination argument
    res = engine.discover("Relocate file to destination", context={})
    best = res.best_match
    assert best is not None
    if best.capability.capability_id == "filesystem.move_file":
        assert "destination" in best.missing_arguments

    # 2. Ambiguity handling: goal matches two similar capabilities equally
    cap1 = CapabilityDefinition(
        capability_id="photo.view1",
        name="Photo Viewer One",
        description="View pictures and images",
        supported_outcomes=["view_pictures"],
    )
    cap2 = CapabilityDefinition(
        capability_id="photo.view2",
        name="Photo Viewer Two",
        description="View pictures and images",
        supported_outcomes=["view_pictures"],
    )
    populated_registry.register(cap1)
    populated_registry.register(cap2)

    res_amb = engine.discover("View pictures")
    assert res_amb.is_ambiguous is True
    assert len(res_amb.ambiguity_candidates) >= 2

    # Composer must refuse to guess an ambiguous plan without clarification
    composer = WorkflowComposer(registry=populated_registry, discovery_engine=engine)
    plan, err = composer.compose_workflow("View pictures")
    assert plan is None
    assert "Ambiguous goal matches multiple competing plans" in err


# ==============================================================================
# TEST 5: Multi-Step Dependency-Aware Workflow Construction
# ==============================================================================
def test_05_construction_of_multi_step_dependency_aware_workflow(populated_registry):
    composer = WorkflowComposer(registry=populated_registry)
    goal = "Find newest picture in Downloads and set as desktop wallpaper"

    plan, err = composer.compose_workflow(goal=goal, context={"path": "C:\\MockDownloads", "photo_index": 4})
    assert err is None
    assert plan is not None
    assert len(plan.steps) == 2

    # Step 1: list directory, Step 2: apply wallpaper depending on step 1
    assert plan.steps[0].capability_id == "filesystem.list_directory"
    assert plan.steps[1].capability_id == "windows.set_wallpaper"
    assert "step_list_photos" in plan.steps[1].depends_on

    # Topological sorting check
    order = plan.get_topological_order()
    assert order == ["step_list_photos", "step_apply_wallpaper"]
    assert "WORKFLOW PREVIEW" in plan.get_readable_preview()


# ==============================================================================
# TEST 6: Correct Passing and Validation of Step Outputs (Data-Pipelining)
# ==============================================================================
def test_06_passing_and_validation_of_step_outputs(temp_dir, populated_registry):
    # Create test files in temp_dir
    os.makedirs(temp_dir, exist_ok=True)
    f1 = os.path.join(temp_dir, "img1.png")
    f2 = os.path.join(temp_dir, "img2.png")
    f3 = os.path.join(temp_dir, "img3.png")
    f4 = os.path.join(temp_dir, "img4.png")
    for f in [f1, f2, f3, f4]:
        with open(f, "w") as fp:
            fp.write("dummy image")

    composer = WorkflowComposer(registry=populated_registry)
    plan, _ = composer.compose_workflow(
        "Find picture in folder and set wallpaper",
        context={"path": temp_dir, "photo_index": 4},
        mode=WorkflowMode.COPILOT,
    )

    engine = WorkflowEngine(registry=populated_registry)
    # Pre-approve checkpoint for step 2
    engine.approve_checkpoint(plan.workflow_id, plan.steps[1].step_id)

    report = engine.execute_workflow(
        plan=plan,
        granted_permissions=[TaskPermission.READ_FILES, TaskPermission.CHANGE_SETTINGS],
    )

    assert report.status == WorkflowExecutionStatus.COMPLETED
    assert report.steps_completed == 2

    # Verify input of step 2 was interpolated from step 1 output
    step2_result = report.step_results["step_apply_wallpaper"]
    assert step2_result["status"] == "COMPLETED"
    applied_path = plan.steps[1].output.get("wallpaper_path")
    assert applied_path == f4  # Exactly the 4th item!


# ==============================================================================
# TEST 7: Enforcement of Approval Gates for Risky Steps (COPILOT Mode)
# ==============================================================================
def test_07_enforcement_of_approval_gates_for_risky_steps(temp_dir, populated_registry):
    os.makedirs(temp_dir, exist_ok=True)
    for i in range(1, 5):
        with open(os.path.join(temp_dir, f"img{i}.png"), "w") as fp:
            fp.write("dummy image")

    composer = WorkflowComposer(registry=populated_registry)
    plan, _ = composer.compose_workflow(
        "Find picture in folder and set wallpaper",
        context={"path": temp_dir},
        mode=WorkflowMode.COPILOT,
    )

    bus = EventBus()
    approval_events = []
    bus.subscribe("workflow.approval_requested", lambda e: approval_events.append(e))

    engine = WorkflowEngine(registry=populated_registry, event_bus=bus)

    # Initial run without pre-approval: must pause at step 2 approval checkpoint
    report = engine.execute_workflow(
        plan=plan,
        granted_permissions=[TaskPermission.READ_FILES, TaskPermission.CHANGE_SETTINGS],
    )

    assert report.status == WorkflowExecutionStatus.PAUSED_FOR_APPROVAL
    assert report.waiting_on_step == "step_apply_wallpaper"
    assert len(approval_events) == 1
    assert approval_events[0].data["step_id"] == "step_apply_wallpaper"

    # Human provides approval
    engine.approve_checkpoint(plan.workflow_id, "step_apply_wallpaper")

    # Resuming execution succeeds
    report2 = engine.execute_workflow(
        plan=plan,
        granted_permissions=[TaskPermission.READ_FILES, TaskPermission.CHANGE_SETTINGS],
    )
    assert report2.status == WorkflowExecutionStatus.COMPLETED
    assert report2.steps_completed == 2


# ==============================================================================
# TEST 8: Refusal to Auto-Execute Untrusted or Gated Capabilities (AUTOPILOT Mode)
# ==============================================================================
def test_08_refusal_to_auto_execute_untrusted_or_unverified_capabilities(temp_dir, populated_registry):
    composer = WorkflowComposer(registry=populated_registry)
    plan, _ = composer.compose_workflow(
        "Find picture in folder and set wallpaper",
        context={"path": temp_dir},
        mode=WorkflowMode.AUTOPILOT,
    )

    engine = WorkflowEngine(registry=populated_registry)

    # In AUTOPILOT mode, attempting a gated action strictly aborts with policy violation
    report = engine.execute_workflow(
        plan=plan,
        granted_permissions=[TaskPermission.READ_FILES, TaskPermission.CHANGE_SETTINGS],
    )

    assert report.status == WorkflowExecutionStatus.ABORTED_POLICY
    assert "Autopilot policy violation" in report.error
    assert report.steps_completed == 0


# ==============================================================================
# TEST 9: Bounded Retries and Resource Constraints
# ==============================================================================
def test_09_bounded_retries_and_resource_constraints(populated_registry):
    call_count = {"n": 0}

    def flaky_handler(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("Temporary transient network glitch")
        return {"result": "recovered"}

    flaky_cap = CapabilityDefinition(
        capability_id="flaky.action",
        name="Flaky Action",
        description="Performs action with one transient retry",
        handler=flaky_handler,
    )
    populated_registry.register(flaky_cap)

    plan = WorkflowPlan(
        workflow_id="wf_flaky",
        goal="Run flaky action",
        desired_outcome="recovered",
        mode=WorkflowMode.COPILOT,
        steps=[
            WorkflowStep(
                step_id="s1",
                capability_id="flaky.action",
                description="Call flaky",
                retry_count=0,
                max_retries=2,
            )
        ],
    )

    engine = WorkflowEngine(registry=populated_registry)
    report = engine.execute_workflow(plan)

    assert report.status == WorkflowExecutionStatus.COMPLETED
    assert call_count["n"] == 2  # Retried once and succeeded!
    assert plan.steps[0].retry_count == 1

    # Resource constraint check: when CPU > max_cpu_percent
    engine.max_cpu_percent = 85.0
    with patch("psutil.cpu_percent", return_value=99.0):
        plan2 = WorkflowPlan(
            workflow_id="wf_overload",
            goal="Overload test",
            desired_outcome="ok",
            steps=[WorkflowStep(step_id="s_load", capability_id="flaky.action", description="load")],
        )
        report2 = engine.execute_workflow(plan2)
        assert report2.status == WorkflowExecutionStatus.FAILED
        assert "CPU limit exceeded" in report2.error


# ==============================================================================
# TEST 10: Failure Propagation and Recovery / Rollback Behavior
# ==============================================================================
def test_10_failure_propagation_and_recovery_behavior(temp_dir, populated_registry):
    src_file = os.path.join(temp_dir, "original.txt")
    dest_file = os.path.join(temp_dir, "moved.txt")
    with open(src_file, "w") as fp:
        fp.write("content to move")

    def failing_step_handler(**kwargs):
        raise RuntimeError("Fatal downstream processing fault")

    populated_registry.register(
        CapabilityDefinition(
            capability_id="fatal.step",
            name="Fatal Step",
            description="Always crashes",
            handler=failing_step_handler,
        )
    )

    plan = WorkflowPlan(
        workflow_id="wf_rollback",
        goal="Move file then crash",
        desired_outcome="done",
        steps=[
            WorkflowStep(
                step_id="step_move",
                capability_id="filesystem.move_file",
                description="Move file",
                inputs={"source": src_file, "destination": dest_file},
            ),
            WorkflowStep(
                step_id="step_fail",
                capability_id="fatal.step",
                description="Crash",
                depends_on=["step_move"],
            ),
        ],
    )

    engine = WorkflowEngine(registry=populated_registry)
    report = engine.execute_workflow(
        plan=plan,
        granted_permissions=[TaskPermission.READ_FILES, TaskPermission.WRITE_FILES],
    )

    assert report.status == WorkflowExecutionStatus.FAILED
    assert "Fatal downstream processing fault" in report.error
    assert report.rolled_back is True

    # Assert rollback inverted the move: original.txt restored, moved.txt gone!
    assert os.path.exists(src_file)
    assert not os.path.exists(dest_file)


# ==============================================================================
# TEST 11: Verification Distinguishes Real Outcome from Mere Return
# ==============================================================================
def test_11_verification_distinguishes_real_outcome_from_mere_return(temp_dir, populated_registry):
    # A deceptive handler that returns {"status": "SUCCESS"} but did NOT actually move the file
    def deceptive_handler(source: str, destination: str, **kwargs):
        return {"status": "SUCCESS", "message": "I claim I moved it!"}

    populated_registry.register(
        CapabilityDefinition(
            capability_id="deceptive.mover",
            name="Deceptive Mover",
            description="Claims to move file but does nothing",
            handler=deceptive_handler,
        )
    )

    src = os.path.join(temp_dir, "real_src.txt")
    dest = os.path.join(temp_dir, "fake_dest.txt")
    with open(src, "w") as fp:
        fp.write("test")

    plan = WorkflowPlan(
        workflow_id="wf_deceptive",
        goal="Deceptive mover test",
        desired_outcome="file_moved",
        steps=[
            WorkflowStep(
                step_id="s_fake",
                capability_id="deceptive.mover",
                description="Deceptive move",
                inputs={"source": src, "destination": dest},
                verification_criteria=["file_moved_to_destination"],  # Requires physical existence of destination
            )
        ],
    )

    engine = WorkflowEngine(registry=populated_registry)
    report = engine.execute_workflow(plan)

    # Must FAIL verification because destination file was NOT physically created on disk!
    assert report.status == WorkflowExecutionStatus.FAILED
    assert "Outcome verification failed" in report.error
    assert plan.steps[0].verified is False


# ==============================================================================
# TEST 12: Logging and Event Auditing
# ==============================================================================
def test_12_logging_and_event_auditing(populated_registry):
    bus = EventBus()
    events_captured = []
    bus.subscribe("workflow.started", lambda e: events_captured.append(e))
    bus.subscribe("workflow.step_completed", lambda e: events_captured.append(e))
    bus.subscribe("workflow.completed", lambda e: events_captured.append(e))

    plan = WorkflowPlan(
        workflow_id="wf_audit",
        goal="Audit trail verification",
        desired_outcome="done",
        steps=[
            WorkflowStep(
                step_id="s1",
                capability_id="filesystem.list_directory",
                description="List directory",
                inputs={"path": "."},
                verification_criteria=["entries_is_list"],
            )
        ],
    )

    engine = WorkflowEngine(registry=populated_registry, event_bus=bus)
    report = engine.execute_workflow(plan, granted_permissions=[TaskPermission.READ_FILES])

    assert report.status == WorkflowExecutionStatus.COMPLETED
    event_types = [e.event_type for e in events_captured]
    assert "workflow.started" in event_types
    assert "workflow.step_completed" in event_types
    assert "workflow.completed" in event_types


# ==============================================================================
# TEST 13: Graceful Operation When LLM Provider is Unavailable
# ==============================================================================
def test_13_graceful_operation_when_llm_unavailable(populated_registry):
    # Ensure discovery and workflow composer operate 100% deterministically on CPU
    # even when external LLM gateways return 403 or network exceptions
    composer = WorkflowComposer(registry=populated_registry)

    with patch("answer_provider.ask_answer", side_effect=RuntimeError("Offline LLM")):
        plan, err = composer.compose_workflow("Find picture in Downloads and set wallpaper")
        assert err is None
        assert plan is not None
        assert len(plan.steps) == 2
        assert plan.steps[0].capability_id == "filesystem.list_directory"
        assert plan.steps[1].capability_id == "windows.set_wallpaper"


# ==============================================================================
# TEST 14: Regression Compatibility with Existing Subsystems & Memory
# ==============================================================================
def test_14_regression_compatibility_with_existing_subsystems(temp_dir):
    from runtime_manager import JevRuntime

    bg_db = os.path.join(temp_dir, "test_bg.db")
    proj_db = os.path.join(temp_dir, "test_proj.db")
    mem_db = os.path.join(temp_dir, "test_mem.db")

    mem_store = SqliteMemoryStore(db_path=mem_db)

    runtime = JevRuntime(
        appdata_dir=temp_dir,
        background_db_path=bg_db,
        project_db_path=proj_db,
        enable_heartbeat=False,
        enable_project_watchers=False,
    )

    # 1. Verify Phase 22 attributes exist on JevRuntime
    assert hasattr(runtime, "capability_registry")
    assert hasattr(runtime, "capability_discovery")
    assert hasattr(runtime, "workflow_composer")
    assert hasattr(runtime, "workflow_engine")

    # Wire memory store into engine
    runtime.workflow_engine.memory_store = mem_store

    # 2. Plan and execute workflow via JevRuntime
    plan, report, err = runtime.plan_and_execute_workflow(
        goal="List files in current directory",
        context={"path": temp_dir},
        granted_permissions=[TaskPermission.READ_FILES],
    )

    assert err is None
    assert plan is not None
    assert report is not None
    assert report.status == WorkflowExecutionStatus.COMPLETED

    # 3. Verify outcome was recorded into persistent SQLite memory store
    assert mem_store.count() == 1
    mems = mem_store.list()
    assert "Workflow successfully executed" in mems[0].content
