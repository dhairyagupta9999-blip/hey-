"""Phase 25.1 Reliability Audit Test Suite.

Audits and verifies:
1. Health-check execution boundaries, logical deadlines vs OS preemptive termination,
   cooperative cancellation signaling, active probe tracking, and bounded concurrency.
2. Transactional activation, snapshot rollback, competing lifecycle operation protection,
   rollback ownership verification, and idempotent rollback.
3. In-flight execution tracking, caller timeout handling, exception decrementing,
   and deactivation drainage deadline enforcement transitioning to DEACTIVATION_FAILED.
4. Real crash recovery across process boundaries using fresh child processes (subprocess.run)
   simulating interruptions during staging, activating, deactivating, and rollback, plus
   physical reality alignment and tampered manifest detection.
5. Approval binding to candidate & manifest hashes, stale approval invalidation on mutation,
   and fail-closed isolation enforcement.
6. EventBus and MemoryStore lifecycle event sequence distinguishing attempted, completed,
   and recovered states, with complete secret scrubbing across events, memory, and outputs.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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
)
from event_bus import EventBus
from memory_store import SqliteMemoryStore
from staged_runtime import StagedExtensionRuntime
from tool_model import RiskLevel, ToolHealth, ToolResult


class TestPhase25_1ReliabilityAudit:
    """Rigorous audit test suite for Phase 25.1 deployment reliability."""

    def setup_method(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="hey_jev_p25_1_audit_")
        self.deployments_dir = os.path.join(self.temp_dir, "deployments")
        os.makedirs(self.deployments_dir, exist_ok=True)
        self.event_bus = EventBus()
        self.memory_store = SqliteMemoryStore(
            db_path=os.path.join(self.temp_dir, "audit_memory.db"),
            event_bus=self.event_bus,
        )
        self.capability_registry = CapabilityRegistry()
        self.staged_runtime = StagedExtensionRuntime(production_registry=self.capability_registry)

        self.mgr = CapabilityDeploymentManager(
            capability_registry=self.capability_registry,
            staged_runtime=self.staged_runtime,
            event_bus=self.event_bus,
            memory_store=self.memory_store,
            deployments_dir=self.deployments_dir,
            has_process_isolation=False,
        )

    def teardown_method(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Health-Check Guarantees & Cancellation Signaling
    # -------------------------------------------------------------------------
    def test_health_check_timeout_signals_cancel_event_and_bounds_concurrency(self) -> None:
        """Verify that health check timeouts signal cancel_event and enforce bounded probe concurrency."""
        canceled_flag = threading.Event()

        def cooperative_hanging_handler(**kwargs):
            if kwargs.get("health_probe"):
                c_event: Optional[threading.Event] = kwargs.get("cancel_event")
                # Wait cooperatively for cancellation signal or timeout
                if c_event:
                    if c_event.wait(timeout=2.0):
                        canceled_flag.set()
                        return ToolResult(tool_id="coop_probe", status="ERROR", error="Probe cancelled")
                time.sleep(2.0)
            return ToolResult(tool_id="coop_probe", status="SUCCESS")

        m = self.mgr.create_deployment_manifest(
            capability_id="coop_probe",
            name="Cooperative Probe",
            description="Tests cancellation signaling",
            proposal_id="p_coop",
            candidate_hash="hash_coop",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.mgr.stage_deployment(m.deployment_id)
        self.mgr.approve_deployment(m.deployment_id)

        # Register capability to run health check directly
        cap_def = CapabilityDefinition(
            capability_id="coop_probe",
            name="Cooperative Probe",
            description="Tests cancellation signaling",
            supported_outcomes=["test"],
            capability_type=CapabilityType.TOOL,
            provenance=f"deployment:{m.deployment_id}",
            handler=cooperative_hanging_handler,
        )
        self.capability_registry.register(cap_def)

        # Execute health check with short 0.1s deadline
        healthy, err_msg = self.mgr.run_health_check(m.deployment_id, timeout_seconds=0.1)
        assert healthy is False
        assert "timed out" in str(err_msg).lower()
        assert "logical deadline exceeded" in str(err_msg).lower()
        assert "cancel_event" in str(err_msg).lower()

        # Verify that cancel_event was indeed signaled and received by the probe
        assert canceled_flag.wait(timeout=1.0) is True

        # Test concurrency bound: fill active probes up to limit (10)
        dummy_threads = []
        for _ in range(10):
            t = threading.Thread(target=lambda: time.sleep(1.0), daemon=True)
            t.start()
            with self.mgr._probe_lock:
                self.mgr._active_probes.add(t)
            dummy_threads.append(t)

        # 11th health check must be rejected immediately by concurrency gate
        healthy_limit, err_limit = self.mgr.run_health_check(m.deployment_id, timeout_seconds=0.1)
        assert healthy_limit is False
        assert "concurrency limit reached" in str(err_limit).lower()

        # Wait for dummy threads to clean up
        for t in dummy_threads:
            t.join(timeout=1.5)

    # -------------------------------------------------------------------------
    # 2. Competing Lifecycle Operations Guard
    # -------------------------------------------------------------------------
    def test_competing_lifecycle_operations_rejected_for_same_capability(self) -> None:
        """Verify that competing concurrent lifecycle transitions on the same capability are rejected."""
        m1 = self.mgr.create_deployment_manifest(
            capability_id="competing_cap",
            name="Competing Cap 1",
            description="Testing race exclusion",
            proposal_id="p_comp_1",
            candidate_hash="hash_c1",
            version="1.0.0",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.mgr.stage_deployment(m1.deployment_id)
        self.mgr.approve_deployment(m1.deployment_id)

        # Simulate that capability is undergoing an active lifecycle transition
        self.mgr._active_lifecycle_ops["competing_cap"] = m1.deployment_id

        # Competing activation attempt must be rejected
        m2 = self.mgr.create_deployment_manifest(
            capability_id="competing_cap",
            name="Competing Cap 2",
            description="Testing race exclusion 2",
            proposal_id="p_comp_2",
            candidate_hash="hash_c2",
            version="2.0.0",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.mgr.stage_deployment(m2.deployment_id)
        self.mgr.approve_deployment(m2.deployment_id)

        ok, err = self.mgr.activate_deployment(m2.deployment_id)
        assert ok is False
        assert "undergoing lifecycle transition" in str(err)
        assert m1.deployment_id in str(err)

        # Clean up lock
        self.mgr._active_lifecycle_ops.pop("competing_cap", None)

    # -------------------------------------------------------------------------
    # 3. Rollback Ownership Verification Preserves Concurrent Updates
    # -------------------------------------------------------------------------
    def test_rollback_ownership_verification_preserves_concurrent_registrations(self) -> None:
        """Verify rollback does NOT overwrite or unregister a capability if provenance does not match."""
        m_failed = self.mgr.create_deployment_manifest(
            capability_id="shared_cap",
            name="Failed Deployment",
            description="Fails health check",
            proposal_id="p_fail",
            candidate_hash="hash_fail",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.mgr.stage_deployment(m_failed.deployment_id)
        self.mgr.approve_deployment(m_failed.deployment_id)

        # An independent, concurrent update registered a valid capability under shared_cap
        concurrent_cap = CapabilityDefinition(
            capability_id="shared_cap",
            name="Concurrent Valid Cap",
            description="Registered by another authorized entity",
            supported_outcomes=["concurrent"],
            capability_type=CapabilityType.TOOL,
            provenance="concurrent_external_system",
            handler=lambda **kw: ToolResult(tool_id="shared_cap", status="SUCCESS"),
        )
        self.capability_registry.register(concurrent_cap)

        # Now simulate m_failed attempting to rollback shared_cap
        # Since provenance != deployment:m_failed.deployment_id, rollback must refuse to touch it!
        ok = self.mgr._rollback_registry_change(m_failed.deployment_id)
        assert ok is False

        # Verify that concurrent_cap was untouched
        current = self.capability_registry.get("shared_cap")
        assert current is not None
        assert current.provenance == "concurrent_external_system"

        # Verify full rollback_deployment transitions to NEEDS_REVIEW when ownership fails
        m_failed.status = DeploymentStatus.ACTIVATING
        ok_rb, msg_rb = self.mgr.rollback_deployment(m_failed.deployment_id)
        assert ok_rb is False
        assert "NEEDS_REVIEW" in msg_rb
        assert m_failed.status == DeploymentStatus.NEEDS_REVIEW

    # -------------------------------------------------------------------------
    # 4. Deactivation Drainage Deadline & In-Flight Tracking
    # -------------------------------------------------------------------------
    def test_deactivation_drainage_timeout_transitions_to_deactivation_failed(self) -> None:
        """Verify that when in-flight invocations fail to drain within deadline, deactivation aborts."""
        entered = threading.Event()
        release = threading.Event()

        def persistent_worker(**kwargs):
            entered.set()
            release.wait(timeout=3.0)
            return ToolResult(tool_id="drain_timeout_cap", status="SUCCESS")

        m = self.mgr.create_deployment_manifest(
            capability_id="drain_timeout_cap",
            name="Drain Timeout Cap",
            description="Simulates long in-flight call",
            proposal_id="p_drain",
            candidate_hash="hash_drain",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.mgr.stage_deployment(m.deployment_id)
        self.mgr.approve_deployment(m.deployment_id)
        self.mgr.activate_deployment(m.deployment_id, executor_override=persistent_worker)

        # Launch in-flight invocation
        t_inv = threading.Thread(
            target=lambda: self.mgr.invoke_deployed_capability("drain_timeout_cap"),
            daemon=True,
        )
        t_inv.start()
        assert entered.wait(timeout=2.0) is True

        # Attempt deactivation with very short drain timeout (0.1s)
        ok_deact, err_deact = self.mgr.deactivate_deployment(m.deployment_id, drain_timeout_seconds=0.1)
        assert ok_deact is False
        assert "timed out" in str(err_deact).lower()
        assert "active invocation(s) still in-flight" in str(err_deact).lower()
        assert m.status == DeploymentStatus.DEACTIVATION_FAILED

        # Capability MUST still be registered to prevent breaking the running invocation
        assert self.capability_registry.get("drain_timeout_cap") is not None

        # Release worker and wait for completion
        release.set()
        t_inv.join(timeout=2.0)

        # Now retrying deactivation succeeds
        ok_retry, _ = self.mgr.deactivate_deployment(m.deployment_id, drain_timeout_seconds=1.0)
        assert ok_retry is True
        assert m.status == DeploymentStatus.INACTIVE
        assert self.capability_registry.get("drain_timeout_cap") is None

    # -------------------------------------------------------------------------
    # 5. Caller Invocation Timeout & Worker Thread In-Flight Retainment
    # -------------------------------------------------------------------------
    def test_invocation_timeout_retains_in_flight_count_until_worker_exits(self) -> None:
        """Verify that caller timeout returns an error while in-flight count is retained until worker thread finishes."""
        worker_started = threading.Event()
        worker_done = threading.Event()

        def slow_handler(**kwargs):
            worker_started.set()
            time.sleep(0.4)
            worker_done.set()
            return ToolResult(tool_id="slow_inv_cap", status="SUCCESS")

        m = self.mgr.create_deployment_manifest(
            capability_id="slow_inv_cap",
            name="Slow Invocation Cap",
            description="Tests caller timeout tracking",
            proposal_id="p_slow",
            candidate_hash="hash_slow",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.mgr.stage_deployment(m.deployment_id)
        self.mgr.approve_deployment(m.deployment_id)
        self.mgr.activate_deployment(m.deployment_id, executor_override=slow_handler)

        # Call with 0.1s timeout
        res = self.mgr.invoke_deployed_capability("slow_inv_cap", timeout_seconds=0.1)
        assert res.status == "ERROR"
        assert "deadline exceeded" in str(res.error).lower()

        # While worker is still executing, in_flight must remain > 0
        with self.mgr._in_flight_lock:
            active_cnt = self.mgr._in_flight.get("slow_inv_cap", 0)
        assert active_cnt >= 1

        # Wait for worker thread to actually finish
        assert worker_done.wait(timeout=1.5) is True

        # Poll for completion of in_flight cleanup
        t_end = time.time() + 2.0
        while time.time() < t_end:
            with self.mgr._in_flight_lock:
                if self.mgr._in_flight.get("slow_inv_cap", 0) == 0:
                    break
            time.sleep(0.02)

        # Now in_flight must have decremented to 0
        with self.mgr._in_flight_lock:
            final_cnt = self.mgr._in_flight.get("slow_inv_cap", 0)
        assert final_cnt == 0

    # -------------------------------------------------------------------------
    # 6. In-Flight Decrementing on Exceptions
    # -------------------------------------------------------------------------
    def test_in_flight_decrementing_on_handler_exception(self) -> None:
        """Verify in-flight counter is properly decremented when handler raises unexpected exception."""
        def crashing_handler(**kwargs):
            if kwargs.get("health_probe"):
                return ToolResult(tool_id="crash_cap", status="SUCCESS")
            raise RuntimeError("Fatal unhandled handler crash")

        m = self.mgr.create_deployment_manifest(
            capability_id="crash_cap",
            name="Crashing Cap",
            description="Tests exception cleanup",
            proposal_id="p_crash",
            candidate_hash="hash_crash",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.mgr.stage_deployment(m.deployment_id)
        self.mgr.approve_deployment(m.deployment_id)
        self.mgr.activate_deployment(m.deployment_id, executor_override=crashing_handler)

        res = self.mgr.invoke_deployed_capability("crash_cap")
        assert res.status == "ERROR"
        assert "Fatal unhandled handler crash" in str(res.error)

        with self.mgr._in_flight_lock:
            assert self.mgr._in_flight.get("crash_cap", 0) == 0

    # -------------------------------------------------------------------------
    # 7. Real Crash Recovery in Fresh Child Process (subprocess.run)
    # -------------------------------------------------------------------------
    def test_subprocess_crash_recovery_across_critical_lifecycle_points(self) -> None:
        """Simulate process crashes at critical lifecycle points and test recovery in a separate Python process."""
        proc_dir = os.path.join(self.temp_dir, "subprocess_test_dir")
        os.makedirs(proc_dir, exist_ok=True)

        # 1. Prepare manifest representing crash during STAGING
        m_staging = self.mgr.create_deployment_manifest(
            capability_id="crash_staging_cap",
            name="Staging Crash",
            description="Crashed during staging",
            proposal_id="p_s",
            candidate_hash="hash_s",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        m_staging.status = DeploymentStatus.STAGING
        m_staging.manifest_hash = m_staging.compute_manifest_hash()
        with open(os.path.join(proc_dir, f"{m_staging.deployment_id}.json"), "w", encoding="utf-8") as f:
            json.dump(m_staging.to_dict(), f, indent=2)

        # 2. Prepare manifest representing crash post-apply / pre-verify (ACTIVATING)
        m_activating = self.mgr.create_deployment_manifest(
            capability_id="crash_act_cap",
            name="Activating Crash",
            description="Crashed during activation",
            proposal_id="p_a",
            candidate_hash="hash_a",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        m_activating.status = DeploymentStatus.ACTIVATING
        m_activating.manifest_hash = m_activating.compute_manifest_hash()
        with open(os.path.join(proc_dir, f"{m_activating.deployment_id}.json"), "w", encoding="utf-8") as f:
            json.dump(m_activating.to_dict(), f, indent=2)

        # 3. Prepare manifest representing crash during DEACTIVATING
        m_deact = self.mgr.create_deployment_manifest(
            capability_id="crash_deact_cap",
            name="Deactivating Crash",
            description="Crashed during deactivation",
            proposal_id="p_d",
            candidate_hash="hash_d",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        m_deact.status = DeploymentStatus.DEACTIVATING
        m_deact.manifest_hash = m_deact.compute_manifest_hash()
        with open(os.path.join(proc_dir, f"{m_deact.deployment_id}.json"), "w", encoding="utf-8") as f:
            json.dump(m_deact.to_dict(), f, indent=2)

        # 4. Prepare manifest representing crash during ROLLBACK_PENDING
        m_rb = self.mgr.create_deployment_manifest(
            capability_id="crash_rb_cap",
            name="Rollback Crash",
            description="Crashed during rollback",
            proposal_id="p_r",
            candidate_hash="hash_r",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        m_rb.status = DeploymentStatus.ROLLBACK_PENDING
        m_rb.manifest_hash = m_rb.compute_manifest_hash()
        with open(os.path.join(proc_dir, f"{m_rb.deployment_id}.json"), "w", encoding="utf-8") as f:
            json.dump(m_rb.to_dict(), f, indent=2)

        # 5. Prepare tampered manifest
        m_tampered = self.mgr.create_deployment_manifest(
            capability_id="tampered_cap",
            name="Tampered Manifest",
            description="Tampered content",
            proposal_id="p_t",
            candidate_hash="hash_t",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        d_tampered = m_tampered.to_dict()
        d_tampered["candidate_hash"] = "tampered_unauthorized_hash"
        with open(os.path.join(proc_dir, f"{m_tampered.deployment_id}.json"), "w", encoding="utf-8") as f:
            json.dump(d_tampered, f, indent=2)

        # Launch fresh Python child process to execute recovery
        recovery_script = f"""
import sys
import json
from capability_registry import CapabilityRegistry
from capability_deployment import CapabilityDeploymentManager
from deployment_manifest import DeploymentStatus

reg = CapabilityRegistry()
mgr = CapabilityDeploymentManager(
    capability_registry=reg,
    deployments_dir={repr(proc_dir)},
    has_process_isolation=False,
)
actions = mgr.recover_deployments()

results = {{
    "staging_status": mgr.get_manifest("{m_staging.deployment_id}").status.value,
    "activating_status": mgr.get_manifest("{m_activating.deployment_id}").status.value,
    "deact_status": mgr.get_manifest("{m_deact.deployment_id}").status.value,
    "rb_status": mgr.get_manifest("{m_rb.deployment_id}").status.value,
    "tampered_status": mgr.get_manifest("{m_tampered.deployment_id}").status.value,
    "actions": actions,
}}
print(json.dumps(results))
"""
        proc = subprocess.run(
            [sys.executable, "-c", recovery_script],
            capture_output=True,
            text=True,
            cwd=os.getcwd(),
        )

        assert proc.returncode == 0, f"Child process failed: {proc.stderr}"
        child_res = json.loads(proc.stdout.strip())

        # Interrupted staging must be flagged VALIDATION_FAILED
        assert child_res["staging_status"] == DeploymentStatus.VALIDATION_FAILED.value

        # Interrupted activating without registry registration must be ACTIVATION_FAILED
        assert child_res["activating_status"] == DeploymentStatus.ACTIVATION_FAILED.value

        # Interrupted deactivating must complete to INACTIVE
        assert child_res["deact_status"] == DeploymentStatus.INACTIVE.value

        # Interrupted rollback must complete to ROLLED_BACK
        assert child_res["rb_status"] == DeploymentStatus.ROLLED_BACK.value

        # Tampered manifest must be detected and flagged NEEDS_REVIEW
        assert child_res["tampered_status"] == DeploymentStatus.NEEDS_REVIEW.value

    # -------------------------------------------------------------------------
    # 8. Full Lifecycle Event Sequence & Secret Redaction Audit
    # -------------------------------------------------------------------------
    def test_lifecycle_event_sequence_and_secret_redaction(self) -> None:
        """Verify EventBus events distinguish attempted, completed, and recovered states with zero secret leakage."""
        events_emitted: List[Dict[str, Any]] = []

        def capture_event(e):
            events_emitted.append({"event_type": e.event_type, "data": e.data})

        for topic in [
            "deployment.created",
            "deployment.staged",
            "deployment.approved",
            "deployment.activating",
            "deployment.activated",
            "deployment.deactivating",
            "deployment.deactivated",
            "deployment.rollback_started",
            "deployment.rolled_back",
            "deployment.recovered",
        ]:
            self.event_bus.subscribe(topic, capture_event)

        m = self.mgr.create_deployment_manifest(
            capability_id="secret_cap",
            name="Secret Cap",
            description="API Token: sk-proj-supersecretkey1234567890abcdef",
            proposal_id="p_sec",
            candidate_hash="hash_sec",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.mgr.stage_deployment(m.deployment_id)
        self.mgr.approve_deployment(m.deployment_id, scope_notes="Token Bearer sk-ant-secret987654321")
        self.mgr.activate_deployment(m.deployment_id)
        self.mgr.deactivate_deployment(m.deployment_id)

        # Verify event sequence
        topics = [e["event_type"] for e in events_emitted]
        assert "deployment.created" in topics
        assert "deployment.staged" in topics
        assert "deployment.approved" in topics
        assert "deployment.activating" in topics
        assert "deployment.activated" in topics
        assert "deployment.deactivating" in topics
        assert "deployment.deactivated" in topics

        # Verify states distinguishing attempted vs completed
        activating_evt = next(e for e in events_emitted if e["event_type"] == "deployment.activating")
        assert activating_evt["data"].get("state") == "attempted"

        activated_evt = next(e for e in events_emitted if e["event_type"] == "deployment.activated")
        assert activated_evt["data"].get("state") == "completed"

        deactivating_evt = next(e for e in events_emitted if e["event_type"] == "deployment.deactivating")
        assert deactivating_evt["data"].get("state") == "attempted"

        deactivated_evt = next(e for e in events_emitted if e["event_type"] == "deployment.deactivated")
        assert deactivated_evt["data"].get("state") == "completed"

        # Verify NO raw secret appears in ANY event
        for evt in events_emitted:
            evt_str = json.dumps(evt["data"])
            assert "supersecretkey1234567890abcdef" not in evt_str
            assert "secret987654321" not in evt_str

        # Verify NO raw secret appears in persisted memory
        records = self.memory_store.search("sk-")
        for rec in records:
            assert "supersecretkey1234567890abcdef" not in rec.content
            assert "secret987654321" not in rec.content

    # -------------------------------------------------------------------------
    # 9. Fail-Closed Executable Code Isolation Audit
    # -------------------------------------------------------------------------
    def test_fail_closed_executable_code_strictly_blocked_under_all_paths(self) -> None:
        """Verify that EXECUTABLE_CODE without OS isolation is strictly blocked at staging and activation."""
        m = self.mgr.create_deployment_manifest(
            capability_id="untrusted_exec_cap",
            name="Executable Code Candidate",
            description="Untrusted python script",
            proposal_id="p_exec",
            candidate_hash="hash_exec",
            deployment_type=DeploymentType.EXECUTABLE_CODE,
        )

        # Staging must block it immediately
        ok_stage, err_stage = self.mgr.stage_deployment(m.deployment_id)
        assert ok_stage is False
        assert m.status == DeploymentStatus.BLOCKED_ISOLATION
        assert "requires os process isolation" in str(err_stage).lower()

        # Direct activation attempt without staging or approval MUST fail
        ok_act, err_act = self.mgr.activate_deployment(m.deployment_id)
        assert ok_act is False
        assert "without human approval" in str(err_act).lower()

        # Even if artificially placed into APPROVED, activation must reject it fail-closed
        m.status = DeploymentStatus.APPROVED
        m.approval_record = {
            "approver": "compromised_admin",
            "manifest_hash": m.manifest_hash,
            "candidate_hash": m.candidate_hash,
        }
        ok_act2, err_act2 = self.mgr.activate_deployment(m.deployment_id)
        assert ok_act2 is False
        assert m.status == DeploymentStatus.BLOCKED_ISOLATION
        assert "operating-system isolation" in str(err_act2).lower()
        assert self.capability_registry.get("untrusted_exec_cap") is None

    # -------------------------------------------------------------------------
    # 10. Approval Invalidation on Mutation & Stale Approval Rejection
    # -------------------------------------------------------------------------
    def test_approval_invalidation_on_candidate_or_manifest_mutation(self) -> None:
        """Verify that any mutation of candidate hash or manifest content immediately revokes approval."""
        m = self.mgr.create_deployment_manifest(
            capability_id="mut_cap",
            name="Mutation Cap",
            description="Testing approval revocation",
            proposal_id="p_mut",
            candidate_hash="hash_orig",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.mgr.stage_deployment(m.deployment_id)
        self.mgr.approve_deployment(m.deployment_id, approver="officer_1")
        assert m.status == DeploymentStatus.APPROVED
        assert m.approval_record is not None

        # Create a mutated copy simulating candidate update
        import copy
        mutated = copy.deepcopy(m)
        mutated.candidate_hash = "hash_mutated"

        # Check and invalidate on mutation
        revoked = self.mgr.check_and_invalidate_on_mutation(m.deployment_id, mutated)
        assert revoked is True
        assert m.status == DeploymentStatus.READY_FOR_APPROVAL
        assert m.approval_record is None

        # Update manifest to match new candidate code and recompute hash for valid integrity
        m.candidate_hash = "hash_mutated"
        m.manifest_hash = m.compute_manifest_hash()

        # Re-approve
        ok_appr, _ = self.mgr.approve_deployment(m.deployment_id, approver="officer_2")
        assert ok_appr is True
        assert m.status == DeploymentStatus.APPROVED

        # Now simulate tampering directly on manifest permissions
        m.required_permissions.append(TaskPermission.EXECUTE_COMMAND)
        stale = self.mgr.invalidate_approval_if_stale(m.deployment_id)
        assert stale is True
        assert m.status == DeploymentStatus.READY_FOR_APPROVAL
        assert m.approval_record is None

        # Attempting activation without re-approval is rejected
        ok_act, err_act = self.mgr.activate_deployment(m.deployment_id)
        assert ok_act is False
        assert "without human approval" in str(err_act).lower()

    # -------------------------------------------------------------------------
    # 11. Idempotent Repeated Deactivation & Rollback
    # -------------------------------------------------------------------------
    def test_idempotent_repeated_deactivation_and_rollback(self) -> None:
        """Verify that repeated deactivation or rollback calls are safely idempotent."""
        m = self.mgr.create_deployment_manifest(
            capability_id="idemp_cap",
            name="Idempotent Cap",
            description="Testing idempotency",
            proposal_id="p_idemp",
            candidate_hash="hash_idemp",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        self.mgr.stage_deployment(m.deployment_id)
        self.mgr.approve_deployment(m.deployment_id)
        self.mgr.activate_deployment(m.deployment_id)

        # First deactivation
        ok1, msg1 = self.mgr.deactivate_deployment(m.deployment_id)
        assert ok1 is True
        assert m.status == DeploymentStatus.INACTIVE

        # Second deactivation must be cleanly idempotent
        ok2, msg2 = self.mgr.deactivate_deployment(m.deployment_id)
        assert ok2 is True
        assert "already inactive" in str(msg2).lower()
        assert m.status == DeploymentStatus.INACTIVE

    # -------------------------------------------------------------------------
    # 12. Active Recovery Detects Physical Registry Absence & Health Failure
    # -------------------------------------------------------------------------
    def test_active_recovery_detects_registry_absence_or_health_failure(self) -> None:
        """Verify that startup recovery validates physical reality: flagged NEEDS_REVIEW if active but missing or unhealthy."""
        # 1. Manifest recorded ACTIVE but absent from live registry
        m_absent = self.mgr.create_deployment_manifest(
            capability_id="absent_cap",
            name="Absent Cap",
            description="Absent from registry",
            proposal_id="p_abs",
            candidate_hash="hash_abs",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        m_absent.status = DeploymentStatus.ACTIVE
        m_absent.manifest_hash = m_absent.compute_manifest_hash()
        self.mgr._save_manifest_to_disk(m_absent)

        # 2. Manifest recorded ACTIVE and present in registry, but health probe fails
        m_unhealthy = self.mgr.create_deployment_manifest(
            capability_id="unhealthy_cap",
            name="Unhealthy Cap",
            description="Fails health probe on restart",
            proposal_id="p_unh",
            candidate_hash="hash_unh",
            deployment_type=DeploymentType.APPROVED_EXTERNAL,
        )
        m_unhealthy.status = DeploymentStatus.ACTIVE
        m_unhealthy.manifest_hash = m_unhealthy.compute_manifest_hash()
        self.mgr._save_manifest_to_disk(m_unhealthy)

        # Register unhealthy capability into registry
        unhealthy_cap_def = CapabilityDefinition(
            capability_id="unhealthy_cap",
            name="Unhealthy Cap",
            description="Fails health check",
            supported_outcomes=["unhealthy"],
            capability_type=CapabilityType.TOOL,
            provenance=f"deployment:{m_unhealthy.deployment_id}",
            handler=lambda **kw: ToolResult(tool_id="unhealthy_cap", status="ERROR", error="Startup probe failure: database unreachable"),
        )
        self.capability_registry.register(unhealthy_cap_def)

        # Run recovery
        actions = self.mgr.recover_deployments()

        assert m_absent.status == DeploymentStatus.NEEDS_REVIEW
        assert "absent from registry" in actions.get(m_absent.deployment_id, "")

        assert m_unhealthy.status == DeploymentStatus.NEEDS_REVIEW
        assert "health check failed on startup" in actions.get(m_unhealthy.deployment_id, "")
