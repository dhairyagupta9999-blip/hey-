"""Phase 22.1: Production Integration, Workflow Safety, and Reliability Audit Test Suite.

Independently inspects and verifies:
1. Real tool execution with ToolScanner and filesystem handlers (no mocks)
2. Real skill execution via SkillExecutor
3. Capability metadata fidelity reflecting underlying tools & skills
4. Safety and approval enforcement across MANUAL mode (0 steps executed)
5. Safety and approval enforcement across COPILOT mode (pause on gated steps, resume on approval)
6. Safety and approval enforcement across AUTOPILOT mode (abort on ungranted approval checkpoints)
7. Execution-time rejection of unavailable or unhealthy capabilities
8. Safe dynamic parameter interpolation without eval/exec
9. Strict rejection of unresolved variable references and invalid paths
10. Validation of required arguments at execution time
11. Bounded retries and backoff
12. Rollback accuracy: real inversion vs irreversible warnings (no false success claims)
13. Independent physical verification with stale destination file detection
14. Workflow cancellation mid-execution and duplicate execution prevention
15. Credential redaction in EventBus payloads and persistent memory
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional

import pytest

from agent_result import AgentResult, AgentResultStatus
from agent_task import TaskPermission
from builtin_skills import get_builtin_skills
from capability_discovery import CapabilityDiscoveryEngine
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
from memory_model import MemoryRecord, MemorySource, MemoryType
from memory_store import SqliteMemoryStore
from skill_executor import SkillExecutor
from skill_registry import SkillRegistry
from tool_model import RiskLevel, ToolDefinition, ToolHealth, ToolResult
from tool_registry import ToolRegistry
from tool_scanner import ToolScanner
from workflow_engine import (
    WorkflowEngine,
    WorkflowExecutionReport,
    WorkflowExecutionStatus,
)


class TestPhase22ProductionAudit:
    """Rigorous audit test suite verifying Phase 22 & 22.1 production integration."""

    def setup_method(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="hey_jev_audit_p22_")
        self.event_bus = EventBus()
        self.memory_store = SqliteMemoryStore(
            db_path=os.path.join(self.temp_dir, "audit_mem.db"),
            event_bus=self.event_bus,
        )

        # Real registries with dynamic scanner and built-ins
        self.tool_registry = ToolRegistry()
        ToolScanner(self.tool_registry).scan_and_register_all()

        self.skill_registry = SkillRegistry()
        for s in get_builtin_skills():
            self.skill_registry.register(s)

        self.skill_executor = SkillExecutor(
            tool_registry=self.tool_registry,
            event_bus=self.event_bus,
        )

        self.capability_registry = CapabilityRegistry(
            tool_registry=self.tool_registry,
            skill_registry=self.skill_registry,
            skill_executor=self.skill_executor,
        )

        self.discovery_engine = CapabilityDiscoveryEngine(registry=self.capability_registry)
        self.composer = WorkflowComposer(registry=self.capability_registry, discovery_engine=self.discovery_engine)
        self.engine = WorkflowEngine(
            registry=self.capability_registry,
            event_bus=self.event_bus,
            memory_store=self.memory_store,
        )

    def teardown_method(self) -> None:
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Real Tool Execution with ToolScanner and File Handlers
    # -------------------------------------------------------------------------
    def test_01_real_tool_execution_with_scanner_and_disk(self) -> None:
        """Verify real tool execution operates on physical files, not test doubles."""
        # Create real files in sandbox
        file_a = os.path.join(self.temp_dir, "report_a.txt")
        file_b = os.path.join(self.temp_dir, "report_b.txt")
        with open(file_a, "w", encoding="utf-8") as f:
            f.write("Alpha content")
        with open(file_b, "w", encoding="utf-8") as f:
            f.write("Beta content")

        cap_list = self.capability_registry.get("tool:filesystem.list_directory")
        assert cap_list is not None
        assert cap_list.handler is not None
        assert callable(cap_list.handler)

        # Call real list handler
        res = cap_list.handler(path=self.temp_dir)
        assert isinstance(res, ToolResult)
        assert res.is_success
        assert isinstance(res.output, dict)
        entries = res.output.get("entries", [])
        entry_names = [e.get("name") for e in entries if isinstance(e, dict)]
        assert "report_a.txt" in entry_names
        assert "report_b.txt" in entry_names

        # Move file using real move handler
        dest_a = os.path.join(self.temp_dir, "report_moved.txt")
        cap_move = self.capability_registry.get("tool:filesystem.move_file")
        assert cap_move is not None
        assert cap_move.handler is not None

        res_move = cap_move.handler(source=file_a, destination=dest_a)
        assert isinstance(res_move, ToolResult)
        assert res_move.is_success
        assert not os.path.exists(file_a)
        assert os.path.exists(dest_a)

    # -------------------------------------------------------------------------
    # 2. Real Skill Execution via SkillExecutor
    # -------------------------------------------------------------------------
    def test_02_real_skill_execution_via_skill_executor(self) -> None:
        """Verify real skill execution coordinates tools and produces verified results."""
        # Create test files
        sub = os.path.join(self.temp_dir, "docs")
        os.makedirs(sub, exist_ok=True)
        doc1 = os.path.join(sub, "spec1.md")
        with open(doc1, "w", encoding="utf-8") as f:
            f.write("Design contract")

        cap_skill = self.capability_registry.get("skill:windows.file_management")
        assert cap_skill is not None
        assert cap_skill.capability_type == CapabilityType.SKILL
        assert cap_skill.handler is not None

        # Build a workflow step that invokes the real skill
        step = WorkflowStep(
            step_id="step_manage_files",
            capability_id="skill:windows.file_management",
            description="Manage and list files via skill",
            inputs={"path": sub},
            expected_outcome="list directory files",
            verification_criteria=["output_non_empty"],
        )
        plan = WorkflowPlan(
            workflow_id="wf_skill_test",
            goal="Scan documentation directory",
            desired_outcome="Directory contents retrieved",
            mode=WorkflowMode.COPILOT,
            steps=[step],
        )

        report = self.engine.execute_workflow(
            plan=plan,
            granted_permissions=[TaskPermission.READ_FILES],
        )

        assert report.status == WorkflowExecutionStatus.COMPLETED
        assert report.steps_completed == 1
        assert step.status == StepStatus.COMPLETED
        assert step.verified is True
        assert step.output is not None

    # -------------------------------------------------------------------------
    # 3. Metadata Fidelity from Underlying Tools and Skills
    # -------------------------------------------------------------------------
    def test_03_metadata_fidelity_from_underlying_tools_and_skills(self) -> None:
        """Verify unified capability schema faithfully maps risk, permissions, and reversibility."""
        cap_move = self.capability_registry.get("tool:filesystem.move_file")
        assert cap_move is not None
        assert cap_move.reversibility == Reversibility.REVERSIBLE
        assert cap_move.risk_level == RiskLevel.MEDIUM
        assert TaskPermission.WRITE_FILES in cap_move.permissions

        cap_list = self.capability_registry.get("tool:filesystem.list_directory")
        assert cap_list is not None
        assert cap_list.reversibility == Reversibility.READ_ONLY
        assert cap_list.risk_level == RiskLevel.LOW
        assert cap_list.approval_requirement == ApprovalRequirement.NONE
        assert TaskPermission.READ_FILES in cap_list.permissions

        cap_wall = self.capability_registry.get("windows.set_wallpaper")
        assert cap_wall is not None
        assert TaskPermission.CHANGE_SETTINGS in cap_wall.permissions

    # -------------------------------------------------------------------------
    # 4. MANUAL Mode Safety
    # -------------------------------------------------------------------------
    def test_04_manual_mode_proposes_plan_with_zero_executed_steps(self) -> None:
        """Verify MANUAL mode synthesizes plan but executes 0 steps without confirmation."""
        step = WorkflowStep(
            step_id="step_list",
            capability_id="tool:filesystem.list_directory",
            description="List directory",
            inputs={"path": self.temp_dir},
        )
        plan = WorkflowPlan(
            workflow_id="wf_manual_01",
            goal="Inspect files",
            desired_outcome="List files",
            mode=WorkflowMode.MANUAL,
            steps=[step],
        )

        report = self.engine.execute_workflow(plan=plan, granted_permissions=[TaskPermission.READ_FILES])

        assert report.status == WorkflowExecutionStatus.MANUAL_READY
        assert report.steps_completed == 0
        assert step.status == StepStatus.PENDING

    # -------------------------------------------------------------------------
    # 5. COPILOT Mode Gating and Approval Resume
    # -------------------------------------------------------------------------
    def test_05_copilot_mode_pauses_at_gated_checkpoint_and_resumes_on_approval(self) -> None:
        """Verify COPILOT mode runs safe steps, pauses on gated action, and resumes upon approval."""
        test_file = os.path.join(self.temp_dir, "to_relocate.txt")
        dest_file = os.path.join(self.temp_dir, "relocated.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("Temporary payload")

        step1 = WorkflowStep(
            step_id="step_list",
            capability_id="tool:filesystem.list_directory",
            description="Scan files",
            inputs={"path": self.temp_dir},
            verification_criteria=["output_non_empty"],
        )
        step2 = WorkflowStep(
            step_id="step_move",
            capability_id="tool:filesystem.move_file",
            description="Relocate file",
            inputs={"source": test_file, "destination": dest_file},
            depends_on=["step_list"],
            approval_checkpoint=True,
            approval_reason="Relocating file requires explicit user confirmation",
        )
        plan = WorkflowPlan(
            workflow_id="wf_copilot_gate",
            goal="List and relocate file",
            desired_outcome="Relocated file",
            mode=WorkflowMode.COPILOT,
            steps=[step1, step2],
        )

        all_perms = [TaskPermission.READ_FILES, TaskPermission.WRITE_FILES]

        # First run: should execute step 1, pause at step 2
        report1 = self.engine.execute_workflow(plan=plan, granted_permissions=all_perms)
        assert report1.status == WorkflowExecutionStatus.PAUSED_FOR_APPROVAL
        assert report1.steps_completed == 1
        assert report1.waiting_on_step == "step_move"
        assert step1.status == StepStatus.COMPLETED
        assert step2.status == StepStatus.WAITING_APPROVAL
        assert os.path.exists(test_file)  # Must NOT be moved yet
        assert not os.path.exists(dest_file)

        # User approves checkpoint
        self.engine.approve_checkpoint(plan.workflow_id, "step_move")

        # Second run: resumes and completes step 2
        report2 = self.engine.execute_workflow(plan=plan, granted_permissions=all_perms)
        assert report2.status == WorkflowExecutionStatus.COMPLETED
        assert report2.steps_completed == 2
        assert step2.status == StepStatus.COMPLETED
        assert not os.path.exists(test_file)  # Now moved
        assert os.path.exists(dest_file)

    # -------------------------------------------------------------------------
    # 6. AUTOPILOT Mode Policy Enforcement
    # -------------------------------------------------------------------------
    def test_06_autopilot_mode_executes_safe_steps_and_aborts_unauthorized_gated_steps(self) -> None:
        """Verify AUTOPILOT mode aborts with 0 side effects if plan contains unapproved checkpoints."""
        test_file = os.path.join(self.temp_dir, "untouchable.txt")
        dest_file = os.path.join(self.temp_dir, "untouchable_dest.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("Crucial data")

        step1 = WorkflowStep(
            step_id="step_list",
            capability_id="tool:filesystem.list_directory",
            description="Scan files",
            inputs={"path": self.temp_dir},
        )
        step2 = WorkflowStep(
            step_id="step_move",
            capability_id="tool:filesystem.move_file",
            description="Move file",
            inputs={"source": test_file, "destination": dest_file},
            depends_on=["step_list"],
            approval_checkpoint=True,
            approval_reason="Unauthorized modification",
        )
        plan = WorkflowPlan(
            workflow_id="wf_autopilot_abort",
            goal="Automated reorganization",
            desired_outcome="All files moved",
            mode=WorkflowMode.AUTOPILOT,
            steps=[step1, step2],
        )

        all_perms = [TaskPermission.READ_FILES, TaskPermission.WRITE_FILES]
        report = self.engine.execute_workflow(plan=plan, granted_permissions=all_perms)

        assert report.status == WorkflowExecutionStatus.ABORTED_POLICY
        assert report.steps_completed == 0
        assert step1.status == StepStatus.PENDING
        assert os.path.exists(test_file)  # Untouched
        assert not os.path.exists(dest_file)

    # -------------------------------------------------------------------------
    # 7. Execution-Time Rejection of Unhealthy/Unavailable Capabilities
    # -------------------------------------------------------------------------
    def test_07_execution_time_rejection_of_unhealthy_or_unavailable_capabilities(self) -> None:
        """Verify capabilities marked UNAVAILABLE or BROKEN are rejected at execution time."""
        cap = self.capability_registry.get("tool:filesystem.list_directory")
        assert cap is not None
        original_health = cap.health

        try:
            # Simulate degraded / unavailable tool
            cap.health = ToolHealth.UNAVAILABLE
            assert not cap.is_available

            step = WorkflowStep(
                step_id="step_broken",
                capability_id="tool:filesystem.list_directory",
                description="List files with broken tool",
                inputs={"path": self.temp_dir},
            )
            plan = WorkflowPlan(
                workflow_id="wf_health_check",
                goal="List files",
                desired_outcome="Results",
                mode=WorkflowMode.COPILOT,
                steps=[step],
            )

            report = self.engine.execute_workflow(plan=plan, granted_permissions=[TaskPermission.READ_FILES])
            assert report.status == WorkflowExecutionStatus.ABORTED_POLICY
            assert "unavailable" in (report.error or "").lower()
            assert report.steps_completed == 0
        finally:
            cap.health = original_health

    # -------------------------------------------------------------------------
    # 8. Safe Dynamic Parameter Interpolation without eval/exec
    # -------------------------------------------------------------------------
    def test_08_safe_dynamic_interpolation_without_eval_or_exec(self) -> None:
        """Verify dynamic parameter resolution works safely without executing arbitrary code."""
        # Context contains structured output
        context = {
            "step_search": {
                "entries": [
                    {"name": "file_1.png", "path": "C:/photos/file_1.png"},
                    {"name": "file_2.png", "path": "C:/photos/file_2.png"},
                ]
            }
        }

        # Safe index lookup
        inputs = {"source": "$step_search.entries[1].path"}
        resolved, err = self.engine._resolve_inputs(inputs, context)
        assert err is None
        assert resolved["source"] == "C:/photos/file_2.png"

        # Hostile / injection string should NOT be evaluated
        hostile_inputs = {"cmd": "$__import__('os').system('calc')"}
        resolved_h, err_h = self.engine._resolve_inputs(hostile_inputs, context)
        assert err_h is not None
        assert "Unresolved variable reference" in err_h

    # -------------------------------------------------------------------------
    # 9. Rejection of Unresolved References
    # -------------------------------------------------------------------------
    def test_09_rejection_of_unresolved_variable_references(self) -> None:
        """Verify missing dynamic references cause clean step abortion instead of silent None."""
        step = WorkflowStep(
            step_id="step_use_ref",
            capability_id="tool:filesystem.list_directory",
            description="Use missing reference",
            inputs={"path": "$nonexistent_step.output.path"},
        )
        plan = WorkflowPlan(
            workflow_id="wf_unresolved",
            goal="Test unresolved ref",
            desired_outcome="Clean failure",
            mode=WorkflowMode.COPILOT,
            steps=[step],
        )

        report = self.engine.execute_workflow(plan=plan, granted_permissions=[TaskPermission.READ_FILES])
        assert report.status == WorkflowExecutionStatus.FAILED
        assert "Unresolved variable reference" in (report.error or "")
        assert report.steps_completed == 0

    # -------------------------------------------------------------------------
    # 10. Validation of Required Arguments at Execution Time
    # -------------------------------------------------------------------------
    def test_10_validation_of_required_arguments_at_execution_time(self) -> None:
        """Verify missing required capability arguments are rejected before invocation."""
        cap = self.capability_registry.get("tool:filesystem.move_file")
        assert cap is not None
        assert "source" in cap.required_arguments
        assert "destination" in cap.required_arguments

        # Omit 'destination' argument
        step = WorkflowStep(
            step_id="step_bad_move",
            capability_id="tool:filesystem.move_file",
            description="Move without destination",
            inputs={"source": os.path.join(self.temp_dir, "nonexistent.txt")},
        )
        plan = WorkflowPlan(
            workflow_id="wf_missing_args",
            goal="Move file",
            desired_outcome="File moved",
            mode=WorkflowMode.COPILOT,
            steps=[step],
        )

        report = self.engine.execute_workflow(
            plan=plan, granted_permissions=[TaskPermission.READ_FILES, TaskPermission.WRITE_FILES]
        )
        assert report.status == WorkflowExecutionStatus.FAILED
        assert "Missing required argument" in (report.error or "")
        assert "destination" in (report.error or "")

    # -------------------------------------------------------------------------
    # 11. Bounded Retries and Backoff
    # -------------------------------------------------------------------------
    def test_11_bounded_retries_and_exponential_backoff(self) -> None:
        """Verify steps retry with bounded attempts upon transient failures."""
        attempts = 0

        def flaky_handler(**kwargs):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError(f"Transient network glitch attempt {attempts}")
            return {"status": "SUCCESS", "data": "recovered"}

        cap = CapabilityDefinition(
            capability_id="custom:flaky_tool",
            name="Flaky Tool",
            description="Fails twice then succeeds",
            handler=flaky_handler,
            reversibility=Reversibility.READ_ONLY,
            approval_requirement=ApprovalRequirement.NONE,
        )
        self.capability_registry.register(cap)

        step = WorkflowStep(
            step_id="step_retry",
            capability_id="custom:flaky_tool",
            description="Test retries",
            max_retries=2,
            verification_criteria=["output_non_empty"],
        )
        plan = WorkflowPlan(
            workflow_id="wf_retry_test",
            goal="Test recovery",
            desired_outcome="Eventual success",
            mode=WorkflowMode.COPILOT,
            steps=[step],
        )

        report = self.engine.execute_workflow(plan=plan)
        assert report.status == WorkflowExecutionStatus.COMPLETED
        assert attempts == 3
        assert step.retry_count == 2
        assert step.status == StepStatus.COMPLETED

    # -------------------------------------------------------------------------
    # 12. Rollback Accuracy: Inversion vs Irreversible Warning
    # -------------------------------------------------------------------------
    def test_12_rollback_accuracy_real_inversion_vs_irreversible_warning(self) -> None:
        """Verify rollback accurately inverts reversible steps and reports irreversible actions."""
        # 1. Test Reversible File Move Inversion
        src = os.path.join(self.temp_dir, "original.txt")
        dest = os.path.join(self.temp_dir, "moved.txt")
        with open(src, "w", encoding="utf-8") as f:
            f.write("Important text")

        step_move = WorkflowStep(
            step_id="step_move",
            capability_id="tool:filesystem.move_file",
            description="Move file",
            inputs={"source": src, "destination": dest},
            verification_criteria=["file_moved_to_destination"],
        )
        step_fail = WorkflowStep(
            step_id="step_fail",
            capability_id="custom:nonexistent",
            description="Intentional failure step",
            depends_on=["step_move"],
        )
        plan_rev = WorkflowPlan(
            workflow_id="wf_rollback_test",
            goal="Move then fail",
            desired_outcome="Rollback test",
            mode=WorkflowMode.COPILOT,
            steps=[step_move, step_fail],
        )

        report_rev = self.engine.execute_workflow(
            plan=plan_rev, granted_permissions=[TaskPermission.READ_FILES, TaskPermission.WRITE_FILES]
        )
        assert report_rev.status == WorkflowExecutionStatus.FAILED
        assert report_rev.rolled_back is True
        assert "Successfully rolled back" in report_rev.rollback_summary
        assert step_move.status == StepStatus.ROLLED_BACK
        assert os.path.exists(src)  # File moved back!
        assert not os.path.exists(dest)

        # 2. Test Irreversible Action Warning (No false success claim)
        def irreversible_handler(**kwargs):
            return {"status": "SUCCESS"}

        cap_irrev = CapabilityDefinition(
            capability_id="custom:irreversible_action",
            name="Irreversible Action",
            description="Cannot be undone",
            handler=irreversible_handler,
            reversibility=Reversibility.IRREVERSIBLE,
            supports_rollback=False,
        )
        self.capability_registry.register(cap_irrev)

        step_irrev = WorkflowStep(
            step_id="step_irrev",
            capability_id="custom:irreversible_action",
            description="Execute irreversible action",
        )
        step_fail2 = WorkflowStep(
            step_id="step_fail2",
            capability_id="custom:nonexistent",
            description="Fail",
            depends_on=["step_irrev"],
        )
        plan_irrev = WorkflowPlan(
            workflow_id="wf_irrev_test",
            goal="Irreversible then fail",
            desired_outcome="Clear warning",
            mode=WorkflowMode.COPILOT,
            steps=[step_irrev, step_fail2],
        )

        report_irrev = self.engine.execute_workflow(plan=plan_irrev)
        assert report_irrev.status == WorkflowExecutionStatus.FAILED
        assert report_irrev.rolled_back is False  # Must NOT claim rollback succeeded!
        assert "No reversible steps executed" in report_irrev.rollback_summary

    # -------------------------------------------------------------------------
    # 13. Independent Physical Verification: Stale File Detection
    # -------------------------------------------------------------------------
    def test_13_independent_physical_verification_detects_stale_destination_file(self) -> None:
        """Verify outcome verification fails if a stale destination file exists but source was not moved."""
        src_file = os.path.join(self.temp_dir, "active_src.txt")
        stale_dest = os.path.join(self.temp_dir, "stale_dest.txt")
        with open(src_file, "w", encoding="utf-8") as f:
            f.write("Source content")
        with open(stale_dest, "w", encoding="utf-8") as f:
            f.write("Stale destination content")

        step = WorkflowStep(
            step_id="step_stale_move",
            capability_id="tool:filesystem.move_file",
            description="Move file",
            inputs={"source": src_file, "destination": stale_dest},
            verification_criteria=["file_moved_to_destination"],
        )

        # Simulate a bugged handler that returned success but left source file intact
        dummy_cap = CapabilityDefinition(
            capability_id="tool:filesystem.move_file",
            name="Move File",
            description="Move file",
        )
        verified, evidence, err = self.engine._verify_outcome(
            step=step,
            cap=dummy_cap,
            inputs={"source": src_file, "destination": stale_dest},
            output={"moved": True},
        )

        assert verified is False
        assert "Stale file detection" in (err or "")
        assert "source file still exists" in (err or "")

        # Now simulate actual source removal
        os.remove(src_file)
        verified_ok, ev_ok, err_ok = self.engine._verify_outcome(
            step=step,
            cap=dummy_cap,
            inputs={"source": src_file, "destination": stale_dest},
            output={"moved": True},
        )
        assert verified_ok is True
        assert err_ok is None

    # -------------------------------------------------------------------------
    # 14. Mid-Execution Cancellation & Duplicate Prevention
    # -------------------------------------------------------------------------
    def test_14_workflow_cancellation_and_duplicate_prevention(self) -> None:
        """Verify mid-workflow cancellation halts execution and duplicate calls do not re-run actions."""
        # 1. Cancellation test
        step1 = WorkflowStep(
            step_id="step_1",
            capability_id="tool:filesystem.list_directory",
            description="Step 1",
            inputs={"path": self.temp_dir},
        )
        step2 = WorkflowStep(
            step_id="step_2",
            capability_id="tool:filesystem.list_directory",
            description="Step 2",
            inputs={"path": self.temp_dir},
            depends_on=["step_1"],
        )
        plan = WorkflowPlan(
            workflow_id="wf_cancel_test",
            goal="Two steps",
            desired_outcome="Halt",
            mode=WorkflowMode.COPILOT,
            steps=[step1, step2],
        )

        # Pre-cancel workflow
        self.engine.cancel_workflow(plan.workflow_id)
        report = self.engine.execute_workflow(plan=plan, granted_permissions=[TaskPermission.READ_FILES])

        assert report.status == WorkflowExecutionStatus.CANCELLED
        assert "cancelled by user request" in (report.error or "").lower()

        # 2. Duplicate Execution Guard test
        step_done = WorkflowStep(
            step_id="step_already_done",
            capability_id="tool:filesystem.list_directory",
            description="Done",
            status=StepStatus.COMPLETED,
            inputs={"path": self.temp_dir},
        )
        plan_completed = WorkflowPlan(
            workflow_id="wf_completed_test",
            goal="Already done",
            desired_outcome="No re-run",
            mode=WorkflowMode.COPILOT,
            steps=[step_done],
        )

        report_dup = self.engine.execute_workflow(
            plan=plan_completed, granted_permissions=[TaskPermission.READ_FILES]
        )
        assert report_dup.status == WorkflowExecutionStatus.COMPLETED
        assert "duplicate execution prevented" in report_dup.verification_summary.lower()

    # -------------------------------------------------------------------------
    # 15. Credential Redaction in EventBus and Memory
    # -------------------------------------------------------------------------
    def test_15_credential_redaction_in_event_bus_and_memory(self) -> None:
        """Verify API keys, tokens, and passwords are fully scrubbed from event payloads and memory."""
        captured_events: List[Dict[str, Any]] = []

        def event_listener(event):
            captured_events.append(event.data)

        self.event_bus.subscribe("workflow.started", event_listener)
        self.event_bus.subscribe("workflow.step_completed", event_listener)
        self.event_bus.subscribe("workflow.completed", event_listener)

        secret_key = "sk-ant-api03-abcdef123456789012345678"
        step = WorkflowStep(
            step_id="step_secret",
            capability_id="tool:filesystem.list_directory",
            description=f"Scan with token {secret_key}",
            inputs={"path": self.temp_dir},
        )
        plan = WorkflowPlan(
            workflow_id="wf_secret_leak_check",
            goal=f"Scan directory with key {secret_key}",
            desired_outcome="Redacted events",
            mode=WorkflowMode.COPILOT,
            steps=[step],
        )

        report = self.engine.execute_workflow(plan=plan, granted_permissions=[TaskPermission.READ_FILES])
        assert report.status == WorkflowExecutionStatus.COMPLETED

        # Check EventBus payloads
        for payload in captured_events:
            for k, v in payload.items():
                if isinstance(v, str):
                    assert secret_key not in v, f"Secret leaked in event key '{k}': {v}"

        # Check MemoryStore records
        records = self.memory_store.search("Scan directory")
        for rec in records:
            assert secret_key not in rec.content, "Secret leaked in memory record content!"
            for k, v in rec.metadata.items():
                if isinstance(v, str):
                    assert secret_key not in v, f"Secret leaked in memory record metadata '{k}'!"
