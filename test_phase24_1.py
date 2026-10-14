"""Phase 24.1 Security and Integration Audit Test Suite.

Audits and verifies:
1. Workspace boundary enforcement against NTFS Alternate Data Streams and Windows DOS device names.
2. Path traversal, absolute paths, UNC paths, and drive-relative paths strictly blocked.
3. Directory containment and TOCTOU defense in workspace file creation.
4. Execution boundary: candidate source code is NEVER imported or executed in the live Jev process.
5. Static AST checks openly document their heuristic limitations (not proof of runtime safety).
6. Denial of execution when OS process isolation is unavailable (Stage 7 BLOCKED).
7. Untrusted custom code reports UNVERIFIED unit tests when unisolated.
8. Approval invalidation on code, dependency, or permission mutation.
9. Prevention of premature registration of staged extensions in production CapabilityRegistry.
10. Effective permission enforcement by staged runtime.
11. Workspace cancellation and deterministic cleanup.
12. Secret redaction from staged execution inputs, outputs, and event reports.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional

import pytest

from agent_task import TaskPermission
from builtin_skills import get_builtin_skills
from capability_model import CapabilityDefinition, CapabilityType
from capability_registry import CapabilityRegistry
from dependency_inspector import DependencyInspector, DependencyRiskLevel, InstallationPlan
from event_bus import EventBus
from extension_build_plan import (
    BuildFileSpec,
    BuildPlanValidator,
    BuildResourceLimits,
    ExtensionBuildPlan,
)
from extension_builder import ExtensionBuilder, GeneratedImplementation
from extension_manager import ExtensionManager
from extension_proposal import (
    ExtensionProposal,
    ProposalLifecycleManager,
    ProposalStatus,
)
from extension_workspace import ExtensionWorkspace
from integration_discovery import (
    CandidateExtension,
    CandidateSourceType,
)
from memory_store import SqliteMemoryStore
from skill_executor import SkillExecutor
from skill_registry import SkillRegistry
from staged_runtime import StagedExecutionResult, StagedExtensionRuntime
from staged_validation import ReviewPackage, StageOutcome, StagedValidationPipeline
from tool_model import RiskLevel, ToolHealth, ToolResult
from tool_registry import ToolRegistry
from tool_scanner import ToolScanner


class TestPhase241SecurityAndIsolationAudit:
    """Rigorous security and isolation audit tests for Phase 24."""

    def setup_method(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="hey_jev_p24_1_audit_")
        self.event_bus = EventBus()
        self.memory_store = SqliteMemoryStore(
            db_path=os.path.join(self.temp_dir, "audit_mem.db"),
            event_bus=self.event_bus,
        )

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

        self.ext_manager = ExtensionManager(
            capability_registry=self.capability_registry,
            event_bus=self.event_bus,
            memory_store=self.memory_store,
            local_integrations_dir=os.path.join(self.temp_dir, "integrations"),
            network_available=False,
        )

    def teardown_method(self) -> None:
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_sample_proposal(
        self,
        candidate_id: str = "adapter:audit_sample",
        name: str = "Audit Sample Tool",
        status: ProposalStatus = ProposalStatus.PROPOSED,
    ) -> ExtensionProposal:
        cand = CandidateExtension(
            candidate_id=candidate_id,
            name=name,
            description="Audit testing candidate",
            source_type=CandidateSourceType.SUPPORTED_INTEGRATION,
            source_location="builtin:audit_tool",
            version="1.0.0",
            publisher="hey_jev_core",
            dependencies=["csv"],
            required_permissions=[TaskPermission.READ_FILES, TaskPermission.WRITE_FILES],
            is_trusted=True,
            provided_capabilities=["audit_operation"],
        )
        prop = ExtensionProposal(
            proposal_id=f"prop_{candidate_id.replace(':', '_')}",
            user_goal="Execute audit operation",
            gap_id="gap_audit_test",
            candidate=cand,
            status=status,
        )
        self.ext_manager._proposals[prop.proposal_id] = prop
        return prop

    # -------------------------------------------------------------------------
    # 1. NTFS Alternate Data Streams and Windows DOS Device Names Blocked
    # -------------------------------------------------------------------------
    def test_01_windows_ntfs_ads_and_dos_device_names_blocked(self) -> None:
        """Verify resolve_safe_path rejects NTFS alternate data streams and DOS device names."""
        with ExtensionWorkspace("ws_audit_ads") as ws:
            # NTFS Alternate Data Streams (colon in path)
            for ads_path in ["foo:stream", "script.py:hidden", "sub/data.txt:ads"]:
                with pytest.raises(PermissionError, match="alternate data streams"):
                    ws.resolve_safe_path(ads_path)

            # Windows DOS reserved device names
            for dev_path in ["NUL", "CON", "PRN", "AUX", "COM1", "COM9", "LPT1", "LPT9", "sub/CON.txt", "NUL.dat"]:
                with pytest.raises(PermissionError, match="Reserved Windows device name"):
                    ws.resolve_safe_path(dev_path)

    # -------------------------------------------------------------------------
    # 2. Path Traversal and Boundary Enforcement
    # -------------------------------------------------------------------------
    def test_02_path_traversal_and_outside_boundary_enforcement(self) -> None:
        """Verify directory traversal and outside path patterns are strictly denied."""
        with ExtensionWorkspace("ws_audit_traversal") as ws:
            # Relative traversal
            with pytest.raises(PermissionError, match="traversal"):
                ws.resolve_safe_path("../outside.txt")
            with pytest.raises(PermissionError, match="traversal"):
                ws.resolve_safe_path("sub/../../escape.py")

            # Absolute Windows paths
            with pytest.raises(PermissionError):
                ws.resolve_safe_path("C:/Windows/System32/evil.dll")

            # Extended device namespaces and drive-relative paths
            with pytest.raises(PermissionError):
                ws.resolve_safe_path("\\\\?\\C:\\Windows")
            with pytest.raises(PermissionError):
                ws.resolve_safe_path("D:drive_relative.txt")
            with pytest.raises(PermissionError):
                ws.resolve_safe_path("sub/D:escaped.txt")

    # -------------------------------------------------------------------------
    # 3. Directory Containment and Creation Re-verification
    # -------------------------------------------------------------------------
    def test_03_directory_containment_and_creation_reverification(self) -> None:
        """Verify directory hierarchy creation remains strictly within workspace root."""
        prop = self._create_sample_proposal()
        plan, _ = self.ext_manager.create_build_plan(
            prop.proposal_id,
            permitted_relative_paths=["deep/nested/sub/valid_file.py", "manifest.json"],
        )
        assert plan is not None

        with ExtensionWorkspace("ws_audit_dirs", build_plan=plan) as ws:
            rec = ws.write_file("deep/nested/sub/valid_file.py", "# safe code")
            assert os.path.exists(rec["full_path"])
            assert rec["full_path"].startswith(ws.root_path)

            # Re-verifying canonical directory containment
            dir_real = os.path.realpath(os.path.dirname(rec["full_path"]))
            assert dir_real.startswith(ws.root_path)

    # -------------------------------------------------------------------------
    # 4. Execution Boundary: Candidate Code Never Executed in Live Process
    # -------------------------------------------------------------------------
    def test_04_candidate_code_never_executed_in_live_process(self) -> None:
        """Verify candidate files written to workspace are never imported or executed in Jev."""
        prop = self._create_sample_proposal()
        plan, _ = self.ext_manager.create_build_plan(prop.proposal_id)
        assert plan is not None

        # Build extension
        ok, gen_impl, _ = self.ext_manager.build_extension(plan)
        assert ok is True
        assert gen_impl is not None

        # Check candidate files exist on disk in workspace
        workspace = self.ext_manager._workspaces[plan.plan_id]
        cand_slug = plan.candidate_id.split(":")[-1]
        impl_path = os.path.join(workspace.root_path, f"{cand_slug}.py")
        assert os.path.exists(impl_path)

        # Validate, approve, and stage
        pkg = self.ext_manager.run_staged_validation(plan, workspace, gen_impl)
        ProposalLifecycleManager.grant_approval(prop, approver="security_audit")
        ok, err = self.ext_manager.stage_approved_proposal(prop.proposal_id)
        assert ok is True

        # Staged runtime uses synthetic staged_handler adapter, NOT dynamic file import
        staged_cap = self.ext_manager.staged_runtime.staged_registry.get(prop.candidate.candidate_id)
        assert staged_cap is not None
        assert staged_cap.handler.__name__ == "staged_handler"

        # Production activation also uses dynamic_handler adapter, NOT dynamic file import
        ok, err = self.ext_manager.activate_proposal(prop.proposal_id)
        assert ok is True
        prod_cap = self.capability_registry.get(prop.candidate.candidate_id)
        assert prod_cap is not None
        assert prod_cap.handler.__name__ == "dynamic_handler"

    # -------------------------------------------------------------------------
    # 5. Static AST Checks Report Limitations (Heuristic Check, Not Safety Proof)
    # -------------------------------------------------------------------------
    def test_05_static_ast_heuristics_openly_report_limitations(self) -> None:
        """Verify static scan documents its heuristic nature and does not claim proof of safety."""
        pipeline = StagedValidationPipeline()
        prop = self._create_sample_proposal()
        plan, _ = self.ext_manager.create_build_plan(prop.proposal_id)
        ok, gen_impl, _ = self.ext_manager.build_extension(plan)
        workspace = self.ext_manager._workspaces[plan.plan_id]

        pkg = pipeline.run_pipeline(prop, plan, workspace, gen_impl)
        s5 = next(s for s in pkg.stage_results if s.stage_number == 5)
        assert s5.outcome == StageOutcome.PASS
        assert "heuristic check; not proof of runtime safety" in s5.details
        assert s5.evidence.get("heuristic") is True
        assert s5.evidence.get("os_enforced") is False

    # -------------------------------------------------------------------------
    # 6. Denial of Execution When OS Process Isolation is Unavailable
    # -------------------------------------------------------------------------
    def test_06_denial_of_execution_when_process_isolation_unavailable(self) -> None:
        """Verify integration tests are blocked when OS process isolation is unavailable."""
        pipeline = StagedValidationPipeline()
        prop = self._create_sample_proposal()
        plan, _ = self.ext_manager.create_build_plan(prop.proposal_id)
        ok, gen_impl, _ = self.ext_manager.build_extension(plan)
        workspace = self.ext_manager._workspaces[plan.plan_id]

        # Explicitly run with has_process_isolation=False
        pkg = pipeline.run_pipeline(prop, plan, workspace, gen_impl, has_process_isolation=False)
        assert "isolated_integration_tests" in pkg.blocked_stages
        s7 = next(s for s in pkg.stage_results if s.stage_number == 7)
        assert s7.outcome == StageOutcome.BLOCKED
        assert "process isolation boundary is unavailable" in s7.details.lower()
        assert any("process isolation unavailable" in c.lower() for c in pkg.security_concerns)

    # -------------------------------------------------------------------------
    # 7. Untrusted Custom Code Reports UNVERIFIED Unit Tests When Unisolated
    # -------------------------------------------------------------------------
    def test_07_untrusted_custom_code_reports_unverified_unit_tests(self) -> None:
        """Verify custom untrusted code reports UNVERIFIED unit tests when unisolated."""
        pipeline = StagedValidationPipeline()
        prop = self._create_sample_proposal()
        plan, _ = self.ext_manager.create_build_plan(prop.proposal_id)

        custom_code = (
            'def execute(**kwargs):\n'
            '    return {"status": "SUCCESS", "result": "custom output"}\n'
        )
        ok, gen_impl, _ = self.ext_manager.build_extension(plan, custom_code=custom_code)
        assert ok is True
        assert gen_impl.is_custom_code is True

        workspace = self.ext_manager._workspaces[plan.plan_id]
        pkg = pipeline.run_pipeline(prop, plan, workspace, gen_impl, has_process_isolation=False)

        # Stage 6 must report UNVERIFIED for custom code without process isolation
        assert "synthetic_unit_tests" in pkg.unverified_stages
        s6 = next(s for s in pkg.stage_results if s.stage_number == 6)
        assert s6.outcome == StageOutcome.UNVERIFIED
        assert "OS process isolation boundary is unavailable" in s6.details
        assert any("custom code unit tests unverified" in c.lower() for c in pkg.security_concerns)

    # -------------------------------------------------------------------------
    # 8. Approval Invalidation on Mutation
    # -------------------------------------------------------------------------
    def test_08_approval_invalidation_on_material_mutation(self) -> None:
        """Verify mutating permissions or dependencies invalidates an existing approval grant."""
        prop = self._create_sample_proposal()
        plan, _ = self.ext_manager.create_build_plan(prop.proposal_id)
        ok, gen_impl, _ = self.ext_manager.build_extension(plan)
        workspace = self.ext_manager._workspaces[plan.plan_id]
        pkg = self.ext_manager.run_staged_validation(plan, workspace, gen_impl)

        ProposalLifecycleManager.grant_approval(prop, approver="auditor")
        assert prop.status == ProposalStatus.APPROVED
        assert prop.approval_grant is not None

        # Material change: adding network access permission
        mutated_cand = CandidateExtension(
            candidate_id=prop.candidate.candidate_id,
            name=prop.candidate.name,
            description=prop.candidate.description,
            source_type=prop.candidate.source_type,
            source_location=prop.candidate.source_location,
            version="1.0.1",
            dependencies=prop.candidate.dependencies,
            required_permissions=[TaskPermission.READ_FILES, TaskPermission.NETWORK_ACCESS],
        )

        invalidated = ProposalLifecycleManager.check_and_invalidate_on_material_change(prop, mutated_cand)
        assert invalidated is True
        assert prop.status == ProposalStatus.AWAITING_APPROVAL
        assert prop.approval_state == "PENDING"
        assert prop.approval_grant is None

    # -------------------------------------------------------------------------
    # 9. Prevention of Premature Production Registration from Staged State
    # -------------------------------------------------------------------------
    def test_09_preventing_premature_production_registration_from_staged_state(self) -> None:
        """Verify a staged capability exists only in the staged registry, not production."""
        prop = self._create_sample_proposal()
        plan, _ = self.ext_manager.create_build_plan(prop.proposal_id)
        ok, gen_impl, _ = self.ext_manager.build_extension(plan)
        workspace = self.ext_manager._workspaces[plan.plan_id]
        self.ext_manager.run_staged_validation(plan, workspace, gen_impl)
        ProposalLifecycleManager.grant_approval(prop, approver="auditor")
        self.ext_manager.stage_approved_proposal(prop.proposal_id)

        assert prop.status == ProposalStatus.STAGED
        # Staged runtime has it
        assert self.ext_manager.staged_runtime.is_staged(prop.candidate.candidate_id) is True
        # Production CapabilityRegistry does NOT have it
        assert self.capability_registry.get(prop.candidate.candidate_id) is None

    # -------------------------------------------------------------------------
    # 10. Effective Permission Enforcement in Staged Runtime
    # -------------------------------------------------------------------------
    def test_10_effective_permission_enforcement_in_staged_runtime(self) -> None:
        """Verify staged runtime strictly enforces caller permissions."""
        prop = self._create_sample_proposal()
        plan, _ = self.ext_manager.create_build_plan(prop.proposal_id)
        ok, gen_impl, _ = self.ext_manager.build_extension(plan)
        workspace = self.ext_manager._workspaces[plan.plan_id]
        self.ext_manager.run_staged_validation(plan, workspace, gen_impl)
        ProposalLifecycleManager.grant_approval(prop, approver="auditor")
        self.ext_manager.stage_approved_proposal(prop.proposal_id)

        # Candidate requires READ_FILES and WRITE_FILES
        # Caller provides only READ_FILES -> blocked
        res_denied = self.ext_manager.invoke_staged_capability(
            capability_id=prop.candidate.candidate_id,
            caller_permissions=[TaskPermission.READ_FILES],
            inputs={"query": "test"},
        )
        assert res_denied.status == "ERROR"
        assert "missing required permission" in (res_denied.error or "").lower()

        # Caller provides both -> succeeds
        res_ok = self.ext_manager.invoke_staged_capability(
            capability_id=prop.candidate.candidate_id,
            caller_permissions=[TaskPermission.READ_FILES, TaskPermission.WRITE_FILES],
            inputs={"query": "test"},
        )
        assert res_ok.status == "SUCCESS"

    # -------------------------------------------------------------------------
    # 11. Workspace Cancellation and Cleanup Recovery
    # -------------------------------------------------------------------------
    def test_11_cancellation_and_cleanup_recovery(self) -> None:
        """Verify cancellation stops in-flight workspace operations and cleanup removes files."""
        prop = self._create_sample_proposal()
        plan, _ = self.ext_manager.create_build_plan(prop.proposal_id)
        assert plan is not None

        ws = ExtensionWorkspace("ws_audit_cancel", build_plan=plan)
        ws.write_file("manifest.json", '{"initial": true}')
        root = ws.root_path
        assert os.path.exists(root)

        # Signal cancellation
        ws.cancel()
        assert ws.is_cancelled is True

        with pytest.raises(RuntimeError, match="cancelled"):
            ws.write_file("manifest.json", '{"mutated": true}')

        # Cleanup
        assert ws.cleanup() is True
        assert not os.path.exists(root)

    # -------------------------------------------------------------------------
    # 12. Secret Redaction from Staged Execution Outputs and Reports
    # -------------------------------------------------------------------------
    def test_12_secret_redaction_in_staged_execution(self) -> None:
        """Verify raw secret tokens are scrubbed from inputs, outputs, and staged execution results."""
        prop = self._create_sample_proposal()
        plan, _ = self.ext_manager.create_build_plan(prop.proposal_id)
        ok, gen_impl, _ = self.ext_manager.build_extension(plan)
        workspace = self.ext_manager._workspaces[plan.plan_id]
        self.ext_manager.run_staged_validation(plan, workspace, gen_impl)
        ProposalLifecycleManager.grant_approval(prop, approver="auditor")
        self.ext_manager.stage_approved_proposal(prop.proposal_id)

        raw_api_key = "sk-ant-api03-secrettoken1234567890abcdef"
        raw_bearer = "Bearer ya29.a0AfH6SMD_sensitive_oauth_token"

        res = self.ext_manager.invoke_staged_capability(
            capability_id=prop.candidate.candidate_id,
            caller_permissions=[TaskPermission.READ_FILES, TaskPermission.WRITE_FILES],
            inputs={"token": raw_api_key, "auth_header": raw_bearer},
        )
        assert res.status == "SUCCESS"
        assert raw_api_key not in str(res.output)
        assert raw_bearer not in str(res.output)
        assert "[REDACTED" in str(res.output)
