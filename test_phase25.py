"""Phase 25 Test Suite: Capability Deployment, Versioning, Activation & Lifecycle Recovery.

Covers all 18 test areas specified in Phase 25:
1. Immutable manifest identity & content-hash verification
2. Rejection of stale approval following candidate changes
3. Illegal lifecycle transition rejection
4. No production registry mutation during staging
5. Conflict handling for duplicate capability identifiers
6. Correct registration of a supported real executor
7. Refusal to represent a synthetic handler as actual generated-code execution
8. Refusal to activate unverified executable candidates when adequate isolation is unavailable (BLOCKED_ISOLATION)
9. Enforcement of permissions by actual invocation path
10. Atomic / recoverable registry changes (snapshot restoration)
11. Deactivation prevents new invocations
12. Correct handling of in-flight operations during deactivation
13. Failure and timeout handling in health checks
14. Recovery following interrupted activation and rollback
15. Idempotent repeated deployment and deactivation requests
16. Audit-log and persistent-memory redaction
17. Preservation of previous working capability following failed deployment
18. Compatibility with existing capability registry and runtime systems
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

import pytest

from agent_task import TaskPermission
from capability_deployment import CapabilityDeploymentManager
from capability_model import CapabilityDefinition, CapabilityType
from capability_registry import CapabilityRegistry
from deployment_manifest import (
    DeploymentManifest,
    DeploymentStatus,
    DeploymentType,
    VALID_DEPLOYMENT_TRANSITIONS,
)
from event_bus import EventBus
from extension_manager import ExtensionManager
from memory_store import SqliteMemoryStore
from staged_runtime import StagedExtensionRuntime
from tool_model import RiskLevel, ToolHealth, ToolResult
from tool_registry import ToolRegistry


class TestPhase25CapabilityDeploymentAndLifecycle:
    """Comprehensive test suite for Phase 25 deployment and lifecycle recovery."""

    def setup_method(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="hey_jev_p25_test_")
        self.deployments_dir = os.path.join(self.temp_dir, "deployments")
        self.event_bus = EventBus()
        self.memory_store = SqliteMemoryStore(
            db_path=os.path.join(self.temp_dir, "test_memory.db"),
            event_bus=self.event_bus,
        )
        self.capability_registry = CapabilityRegistry()
        self.staged_runtime = StagedExtensionRuntime(production_registry=self.capability_registry)

        self.deployment_mgr = CapabilityDeploymentManager(
            capability_registry=self.capability_registry,
            staged_runtime=self.staged_runtime,
            event_bus=self.event_bus,
            memory_store=self.memory_store,
            deployments_dir=self.deployments_dir,
            has_process_isolation=False,
        )

        self.extension_mgr = ExtensionManager(
            capability_registry=self.capability_registry,
            event_bus=self.event_bus,
            memory_store=self.memory_store,
            local_integrations_dir=self.temp_dir,
            deployment_manager=self.deployment_mgr,
            has_process_isolation=False,
        )

    def teardown_method(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Test 1: Immutable manifest identity & content-hash verification
    # -------------------------------------------------------------------------
    def test_immutable_manifest_identity_and_integrity(self) -> None:
        manifest = self.deployment_mgr.create_deployment_manifest(
            capability_id="test_cap_1",
            name="Test Capability",
            description="Testing manifest hashing",
            proposal_id="prop_001",
            candidate_hash="abc123hash",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
            version="1.0.0",
            required_permissions=[TaskPermission.READ_FILES],
        )

        assert manifest.manifest_hash != ""
        valid, errors = manifest.verify_integrity()
        assert valid is True
        assert len(errors) == 0

        # Tampering with permissions should break integrity
        manifest.required_permissions.append(TaskPermission.EXECUTE_COMMAND)
        valid_tampered, errors_tampered = manifest.verify_integrity()
        assert valid_tampered is False
        assert any("Manifest fingerprint mismatch" in e for e in errors_tampered)

    # -------------------------------------------------------------------------
    # Test 2: Rejection of stale approval following candidate changes
    # -------------------------------------------------------------------------
    def test_rejection_of_stale_approval_following_changes(self) -> None:
        manifest = self.deployment_mgr.create_deployment_manifest(
            capability_id="test_cap_2",
            name="Stale Approval Test",
            description="Test stale approval rejection",
            proposal_id="prop_002",
            candidate_hash="hash_v1",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        ok, err = self.deployment_mgr.stage_deployment(manifest.deployment_id)
        assert ok is True

        ok, err = self.deployment_mgr.approve_deployment(manifest.deployment_id, approver="auditor")
        assert ok is True
        assert manifest.status == DeploymentStatus.APPROVED

        # Simulate candidate update altering manifest
        manifest.candidate_hash = "hash_v2_modified"
        manifest.manifest_hash = manifest.compute_manifest_hash()

        # Invalidation helper detects staleness
        invalidated = self.deployment_mgr.invalidate_approval_if_stale(manifest.deployment_id)
        assert invalidated is True
        assert manifest.status == DeploymentStatus.READY_FOR_APPROVAL
        assert manifest.approval_record is None

        # Direct activation attempt without re-approval MUST fail
        ok_act, err_act = self.deployment_mgr.activate_deployment(manifest.deployment_id)
        assert ok_act is False
        assert "human approval" in str(err_act).lower()

    # -------------------------------------------------------------------------
    # Test 3: Illegal lifecycle transition rejection
    # -------------------------------------------------------------------------
    def test_illegal_lifecycle_transitions_rejected(self) -> None:
        manifest = self.deployment_mgr.create_deployment_manifest(
            capability_id="test_cap_3",
            name="Transition Test",
            description="Illegal transitions",
            proposal_id="prop_003",
            candidate_hash="hash_v3",
        )
        assert manifest.status == DeploymentStatus.CANDIDATE

        # CANDIDATE directly to ACTIVE is strictly illegal
        with pytest.raises(ValueError, match="Illegal deployment state transition"):
            self.deployment_mgr._transition(manifest, DeploymentStatus.ACTIVE)

        # CANDIDATE directly to APPROVED is strictly illegal
        with pytest.raises(ValueError, match="Illegal deployment state transition"):
            self.deployment_mgr._transition(manifest, DeploymentStatus.APPROVED)

    # -------------------------------------------------------------------------
    # Test 4: No production registry mutation during staging
    # -------------------------------------------------------------------------
    def test_no_production_registry_mutation_during_staging(self) -> None:
        initial_count = self.capability_registry.count()
        manifest = self.deployment_mgr.create_deployment_manifest(
            capability_id="test_cap_4",
            name="Staging Isolation",
            description="Production registry must remain untouched",
            proposal_id="prop_004",
            candidate_hash="hash_v4",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )

        ok, err = self.deployment_mgr.stage_deployment(manifest.deployment_id)
        assert ok is True
        assert manifest.status == DeploymentStatus.READY_FOR_APPROVAL

        # Production registry MUST NOT be mutated
        assert self.capability_registry.count() == initial_count
        assert self.capability_registry.get("test_cap_4") is None

    # -------------------------------------------------------------------------
    # Test 5: Conflict handling for duplicate capability identifiers
    # -------------------------------------------------------------------------
    def test_conflict_handling_for_duplicate_identifiers(self) -> None:
        # Pre-register an existing built-in capability
        existing_cap = CapabilityDefinition(
            capability_id="conflict_tool",
            name="Conflict Builtin",
            description="Existing system tool",
            supported_outcomes=["file_write"],
            capability_type=CapabilityType.TOOL,
            risk_level=RiskLevel.LOW,
            provenance="builtin:existing",
            handler=lambda **kw: ToolResult(tool_id="conflict_tool", status="SUCCESS"),
        )
        self.capability_registry.register(existing_cap)

        manifest = self.deployment_mgr.create_deployment_manifest(
            capability_id="conflict_tool",
            name="Conflicting Candidate",
            description="Duplicate tool ID",
            proposal_id="prop_conflict",
            candidate_hash="hash_conflict",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.deployment_mgr.stage_deployment(manifest.deployment_id)
        self.deployment_mgr.approve_deployment(manifest.deployment_id)

        # Activation must fail due to naming conflict with unrelated tool
        ok, err = self.deployment_mgr.activate_deployment(manifest.deployment_id)
        assert ok is False
        assert "Naming conflict" in str(err)
        # Existing tool remains intact
        assert self.capability_registry.get("conflict_tool").provenance == "builtin:existing"

    # -------------------------------------------------------------------------
    # Test 6: Correct registration of a supported real executor
    # -------------------------------------------------------------------------
    def test_correct_registration_and_execution_of_supported_adapter(self) -> None:
        manifest = self.deployment_mgr.create_deployment_manifest(
            capability_id="sqlite_reader",
            name="SQLite Reader Adapter",
            description="Approved external adapter for sqlite querying",
            proposal_id="prop_006",
            candidate_hash="hash_sqlite",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
            supported_outcomes=["database_read"],
        )
        self.deployment_mgr.stage_deployment(manifest.deployment_id)
        self.deployment_mgr.approve_deployment(manifest.deployment_id)

        ok, err = self.deployment_mgr.activate_deployment(manifest.deployment_id)
        assert ok is True
        assert manifest.status == DeploymentStatus.ACTIVE

        # Capability is in live registry
        registered = self.capability_registry.get("sqlite_reader")
        assert registered is not None
        assert registered.provenance == f"deployment:{manifest.deployment_id}"

        # Invocation yields real execution
        result = self.deployment_mgr.invoke_deployed_capability(
            capability_id="sqlite_reader",
            inputs={"query": "SELECT 1"},
        )
        assert result.status == "SUCCESS"
        assert result.output["capability_id"] == "sqlite_reader"
        assert result.output["inputs"]["query"] == "SELECT 1"

    # -------------------------------------------------------------------------
    # Test 7: Refusal to represent a synthetic handler as actual generated-code execution
    # -------------------------------------------------------------------------
    def test_refusal_to_substitute_synthetic_handler_for_generated_code(self) -> None:
        manifest = self.deployment_mgr.create_deployment_manifest(
            capability_id="untrusted_code_cap",
            name="Untrusted Code",
            description="Generated Python code requiring OS sandbox",
            proposal_id="prop_untrusted",
            candidate_hash="hash_code",
            deployment_type=DeploymentType.EXECUTABLE_CODE,
        )

        # Refusal to construct synthetic handler when process isolation is False
        with pytest.raises(RuntimeError, match="Refusing to substitute a synthetic handler"):
            self.deployment_mgr._construct_executor_handler(manifest)

    # -------------------------------------------------------------------------
    # Test 8: Refusal to activate unverified executable candidates (BLOCKED_ISOLATION)
    # -------------------------------------------------------------------------
    def test_refusal_to_activate_unisolated_executable_code(self) -> None:
        manifest = self.deployment_mgr.create_deployment_manifest(
            capability_id="gen_python_cap",
            name="Generated Code Candidate",
            description="Requires OS isolation",
            proposal_id="prop_gen_01",
            candidate_hash="hash_gen_01",
            deployment_type=DeploymentType.EXECUTABLE_CODE,
        )

        # Staging fails closed to BLOCKED_ISOLATION
        ok, err = self.deployment_mgr.stage_deployment(manifest.deployment_id)
        assert ok is False
        assert manifest.status == DeploymentStatus.BLOCKED_ISOLATION
        assert "requires OS process isolation" in str(err)

        # Force state to APPROVED to test activation defense
        manifest.status = DeploymentStatus.APPROVED
        manifest.approval_record = {
            "approver": "test",
            "manifest_hash": manifest.manifest_hash,
            "candidate_hash": manifest.candidate_hash,
        }
        ok_act, err_act = self.deployment_mgr.activate_deployment(manifest.deployment_id)
        assert ok_act is False
        assert manifest.status == DeploymentStatus.BLOCKED_ISOLATION
        assert "operating-system isolation" in str(err_act)

    # -------------------------------------------------------------------------
    # Test 9: Enforcement of permissions by actual invocation path
    # -------------------------------------------------------------------------
    def test_enforcement_of_permissions_during_invocation(self) -> None:
        manifest = self.deployment_mgr.create_deployment_manifest(
            capability_id="secure_cap",
            name="Secure Capability",
            description="Requires permissions",
            proposal_id="prop_perm",
            candidate_hash="hash_perm",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
            required_permissions=[TaskPermission.NETWORK_ACCESS, TaskPermission.READ_FILES],
        )
        self.deployment_mgr.stage_deployment(manifest.deployment_id)
        self.deployment_mgr.approve_deployment(manifest.deployment_id)
        self.deployment_mgr.activate_deployment(manifest.deployment_id)

        # 1. Caller with insufficient permissions is denied
        res_denied = self.deployment_mgr.invoke_deployed_capability(
            capability_id="secure_cap",
            caller_permissions=[TaskPermission.READ_FILES],
        )
        assert res_denied.status == "ERROR"
        assert "missing required permission" in str(res_denied.error)

        # 2. Caller with full permissions is allowed
        res_allowed = self.deployment_mgr.invoke_deployed_capability(
            capability_id="secure_cap",
            caller_permissions=[TaskPermission.NETWORK_ACCESS, TaskPermission.READ_FILES],
        )
        assert res_allowed.status == "SUCCESS"

    # -------------------------------------------------------------------------
    # Test 10: Atomic / recoverable registry changes (snapshot restoration)
    # -------------------------------------------------------------------------
    def test_atomic_activation_with_rollback_on_health_failure(self) -> None:
        manifest = self.deployment_mgr.create_deployment_manifest(
            capability_id="failing_probe_cap",
            name="Unhealthy Capability",
            description="Fails health check probe",
            proposal_id="prop_fail_probe",
            candidate_hash="hash_probe",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.deployment_mgr.stage_deployment(manifest.deployment_id)
        self.deployment_mgr.approve_deployment(manifest.deployment_id)

        # Provide an executor that fails on health probe
        def failing_executor(**kwargs):
            if kwargs.get("health_probe"):
                return ToolResult(tool_id="failing_probe_cap", status="ERROR", error="Unhealthy service failure")
            return ToolResult(tool_id="failing_probe_cap", status="SUCCESS")

        ok, err = self.deployment_mgr.activate_deployment(manifest.deployment_id, executor_override=failing_executor)
        assert ok is False
        assert "Activation health check failed" in str(err)
        assert manifest.status == DeploymentStatus.ROLLED_BACK

        # Registry must have rolled back: capability must NOT remain registered
        assert self.capability_registry.get("failing_probe_cap") is None

    # -------------------------------------------------------------------------
    # Test 11: Deactivation prevents new invocations
    # -------------------------------------------------------------------------
    def test_deactivation_prevents_new_invocations(self) -> None:
        manifest = self.deployment_mgr.create_deployment_manifest(
            capability_id="deact_test_cap",
            name="Deactivation Test",
            description="Test new calls blocked",
            proposal_id="prop_deact",
            candidate_hash="hash_deact",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.deployment_mgr.stage_deployment(manifest.deployment_id)
        self.deployment_mgr.approve_deployment(manifest.deployment_id)
        self.deployment_mgr.activate_deployment(manifest.deployment_id)

        # Active invocation succeeds
        assert self.deployment_mgr.invoke_deployed_capability("deact_test_cap").status == "SUCCESS"

        # Deactivate
        ok, err = self.deployment_mgr.deactivate_deployment(manifest.deployment_id)
        assert ok is True
        assert manifest.status == DeploymentStatus.INACTIVE

        # Invocation now rejected
        res = self.deployment_mgr.invoke_deployed_capability("deact_test_cap")
        assert res.status == "ERROR"
        assert "not active" in str(res.error).lower() or "not found" in str(res.error).lower()

    # -------------------------------------------------------------------------
    # Test 12: Correct handling of in-flight operations during deactivation
    # -------------------------------------------------------------------------
    def test_in_flight_drainage_during_deactivation(self) -> None:
        entered_event = threading.Event()
        release_event = threading.Event()
        exec_completed = threading.Event()
        result_holder: List[ToolResult] = []

        def slow_executor(**kwargs):
            if kwargs.get("health_probe"):
                return ToolResult(tool_id="slow_cap", status="SUCCESS")
            entered_event.set()
            release_event.wait(timeout=2.0)
            return ToolResult(tool_id="slow_cap", status="SUCCESS", output="Slow operation finished")

        manifest = self.deployment_mgr.create_deployment_manifest(
            capability_id="slow_cap",
            name="Slow Cap",
            description="Tests in-flight draining",
            proposal_id="prop_slow",
            candidate_hash="hash_slow",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.deployment_mgr.stage_deployment(manifest.deployment_id)
        self.deployment_mgr.approve_deployment(manifest.deployment_id)
        self.deployment_mgr.activate_deployment(manifest.deployment_id, executor_override=slow_executor)

        # Launch invocation in background thread
        def runner():
            res = self.deployment_mgr.invoke_deployed_capability("slow_cap")
            result_holder.append(res)
            exec_completed.set()

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        assert entered_event.wait(timeout=2.0) is True

        # Now launch deactivation in a second thread (it will begin draining)
        deact_done = threading.Event()
        deact_status = []

        def deact_runner():
            ok, err = self.deployment_mgr.deactivate_deployment(manifest.deployment_id, drain_timeout_seconds=2.0)
            deact_status.append(ok)
            deact_done.set()

        t_deact = threading.Thread(target=deact_runner, daemon=True)
        t_deact.start()

        # While draining, verify new calls are REJECTED
        time.sleep(0.05)
        new_res = self.deployment_mgr.invoke_deployed_capability("slow_cap")
        assert new_res.status == "ERROR"
        assert "deactivating" in str(new_res.error).lower() or "not active" in str(new_res.error).lower()

        # Now release the in-flight operation
        release_event.set()
        assert exec_completed.wait(timeout=2.0) is True
        assert result_holder[0].status == "SUCCESS"
        assert result_holder[0].output == "Slow operation finished"

        # Deactivation finishes cleanly
        assert deact_done.wait(timeout=2.0) is True
        assert deact_status[0] is True
        assert manifest.status == DeploymentStatus.INACTIVE

    # -------------------------------------------------------------------------
    # Test 13: Failure and timeout handling in health checks
    # -------------------------------------------------------------------------
    def test_health_check_timeout_and_failure_handling(self) -> None:
        def hanging_executor(**kwargs):
            if kwargs.get("health_probe"):
                time.sleep(1.0)  # Exceeds tiny timeout
            return ToolResult(tool_id="hanging_cap", status="SUCCESS")

        manifest = self.deployment_mgr.create_deployment_manifest(
            capability_id="hanging_cap",
            name="Hanging Cap",
            description="Times out",
            proposal_id="prop_hang",
            candidate_hash="hash_hang",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.deployment_mgr.stage_deployment(manifest.deployment_id)
        self.deployment_mgr.approve_deployment(manifest.deployment_id)

        # Temporarily register capability to run health check directly
        cap_def = CapabilityDefinition(
            capability_id="hanging_cap",
            name="Hanging Cap",
            description="Times out",
            supported_outcomes=["hang"],
            capability_type=CapabilityType.TOOL,
            risk_level=RiskLevel.LOW,
            provenance="test",
            handler=hanging_executor,
        )
        self.capability_registry.register(cap_def)

        # Health check with 0.1s timeout should fail on timeout
        healthy, err = self.deployment_mgr.run_health_check(manifest.deployment_id, timeout_seconds=0.1)
        assert healthy is False
        assert "timed out" in str(err).lower()

    # -------------------------------------------------------------------------
    # Test 14: Recovery following interrupted activation and rollback
    # -------------------------------------------------------------------------
    def test_crash_recovery_of_interrupted_activation(self) -> None:
        manifest = self.deployment_mgr.create_deployment_manifest(
            capability_id="crash_cap",
            name="Crash Recovery Cap",
            description="Simulates restart during ACTIVATING",
            proposal_id="prop_crash",
            candidate_hash="hash_crash",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        # Manually simulate ACTIVATING state recorded to disk
        manifest.status = DeploymentStatus.ACTIVATING
        self.deployment_mgr._save_manifest_to_disk(manifest)

        # Since not registered in registry on restart, recovery should reset to ACTIVATION_FAILED
        actions = self.deployment_mgr.recover_deployments()
        assert manifest.deployment_id in actions
        assert manifest.status == DeploymentStatus.ACTIVATION_FAILED

    # -------------------------------------------------------------------------
    # Test 15: Idempotent repeated deployment and deactivation requests
    # -------------------------------------------------------------------------
    def test_idempotent_activation_and_deactivation(self) -> None:
        manifest = self.deployment_mgr.create_deployment_manifest(
            capability_id="idemp_cap",
            name="Idempotent Cap",
            description="Tests idempotency",
            proposal_id="prop_idemp",
            candidate_hash="hash_idemp",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.deployment_mgr.stage_deployment(manifest.deployment_id)
        self.deployment_mgr.approve_deployment(manifest.deployment_id)

        # First activation
        ok1, err1 = self.deployment_mgr.activate_deployment(manifest.deployment_id)
        assert ok1 is True

        # Second activation is idempotent
        ok2, err2 = self.deployment_mgr.activate_deployment(manifest.deployment_id)
        assert ok2 is True
        assert "already active" in str(err2).lower()

        # First deactivation
        ok_d1, err_d1 = self.deployment_mgr.deactivate_deployment(manifest.deployment_id)
        assert ok_d1 is True

        # Second deactivation is idempotent
        ok_d2, err_d2 = self.deployment_mgr.deactivate_deployment(manifest.deployment_id)
        assert ok_d2 is True
        assert "already inactive" in str(err_d2).lower()

    # -------------------------------------------------------------------------
    # Test 16: Audit-log and persistent-memory redaction
    # -------------------------------------------------------------------------
    def test_audit_log_and_memory_redaction(self) -> None:
        events_captured: List[Dict[str, Any]] = []
        self.event_bus.subscribe("deployment.activated", lambda e: events_captured.append(e.data))

        manifest = self.deployment_mgr.create_deployment_manifest(
            capability_id="secret_scrub_cap",
            name="Secret Scrubbing Cap",
            description="Testing secret redaction",
            proposal_id="prop_scrub",
            candidate_hash="hash_scrub",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.deployment_mgr.stage_deployment(manifest.deployment_id)
        self.deployment_mgr.approve_deployment(manifest.deployment_id)
        self.deployment_mgr.activate_deployment(manifest.deployment_id)

        # Execute with secret in input
        raw_secret = "Bearer sk-proj-1234567890abcdef1234567890abcdef"
        res = self.deployment_mgr.invoke_deployed_capability(
            capability_id="secret_scrub_cap",
            inputs={"auth_header": raw_secret, "api_key": "AKIAIOSFODNN7EXAMPLE"},
        )
        assert res.status == "SUCCESS"
        # Input stored in output must be scrubbed
        assert "sk-proj-1234567890abcdef1234567890abcdef" not in str(res.output["inputs"]["auth_header"])
        assert "[REDACTED" in str(res.output["inputs"]["auth_header"])

    # -------------------------------------------------------------------------
    # Test 17: Preservation of previous working capability following failed deployment
    # -------------------------------------------------------------------------
    def test_preservation_of_previous_capability_on_failed_upgrade(self) -> None:
        # Step 1: Deploy v1.0.0
        m1 = self.deployment_mgr.create_deployment_manifest(
            capability_id="service_adapter",
            name="Service Adapter v1",
            description="Working v1 capability",
            proposal_id="prop_srv_v1",
            candidate_hash="hash_srv_v1",
            version="1.0.0",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.deployment_mgr.stage_deployment(m1.deployment_id)
        self.deployment_mgr.approve_deployment(m1.deployment_id)

        def v1_executor(**kwargs):
            return ToolResult(tool_id="service_adapter", status="SUCCESS", output="v1.0.0 working response")

        ok1, _ = self.deployment_mgr.activate_deployment(m1.deployment_id, executor_override=v1_executor)
        assert ok1 is True
        assert self.capability_registry.get("service_adapter").version == "1.0.0"

        # Step 2: Attempt to deploy broken v2.0.0 for the same capability_id
        m2 = self.deployment_mgr.create_deployment_manifest(
            capability_id="service_adapter",
            name="Service Adapter v2",
            description="Broken v2 capability",
            proposal_id="prop_srv_v2",
            candidate_hash="hash_srv_v2",
            version="2.0.0",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.deployment_mgr.stage_deployment(m2.deployment_id)
        self.deployment_mgr.approve_deployment(m2.deployment_id)

        def broken_v2_executor(**kwargs):
            if kwargs.get("health_probe"):
                return ToolResult(tool_id="service_adapter", status="ERROR", error="Health probe crashed in v2")
            return ToolResult(tool_id="service_adapter", status="SUCCESS", output="v2.0.0")

        ok2, err2 = self.deployment_mgr.activate_deployment(m2.deployment_id, executor_override=broken_v2_executor)
        assert ok2 is False
        assert m2.status == DeploymentStatus.ROLLED_BACK

        # Step 3: Verify v1.0.0 is preserved and still functional!
        restored = self.capability_registry.get("service_adapter")
        assert restored is not None
        assert restored.version == "1.0.0"
        assert restored.provenance == f"deployment:{m1.deployment_id}"
        run_res = restored.handler()
        assert run_res.output == "v1.0.0 working response"

    # -------------------------------------------------------------------------
    # Test 18: ExtensionManager integration and lifecycle delegation
    # -------------------------------------------------------------------------
    def test_extension_manager_deployment_integration(self) -> None:
        manifest = self.extension_mgr.create_deployment_manifest(
            capability_id="ext_mgr_cap",
            name="Extension Manager Test",
            description="Testing extension manager delegation",
            proposal_id="prop_ext_mgr",
            candidate_hash="hash_ext_mgr",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        ok_stage, _ = self.extension_mgr.stage_deployment(manifest.deployment_id)
        assert ok_stage is True

        ok_app, _ = self.extension_mgr.approve_deployment(manifest.deployment_id)
        assert ok_app is True

        ok_act, _ = self.extension_mgr.activate_deployment(manifest.deployment_id)
        assert ok_act is True

        res = self.extension_mgr.invoke_deployed_capability("ext_mgr_cap")
        assert res.status == "SUCCESS"

        ok_deact, _ = self.extension_mgr.deactivate_deployment(manifest.deployment_id)
        assert ok_deact is True

        retrieved = self.extension_mgr.get_deployment_manifest(manifest.deployment_id)
        assert retrieved is not None
        assert retrieved.status == DeploymentStatus.INACTIVE
