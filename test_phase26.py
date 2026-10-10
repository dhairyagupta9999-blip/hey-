"""Comprehensive Test Suite for Phase 26: Adaptive Operations, Failure Diagnosis and Safe Autonomous Maintenance.

Covers all 20 required verification dimensions:
1. Canonical health states & structured observations
2. Functional check vs registry lookup distinction
3. Side-effect-aware probes & cooperative cancellation
4. Worker safety & outstanding worker accounting
5. Multi-signal failure diagnosis & classification
6. Verified cause vs working hypothesis separation
7. Dependency availability correlation
8. Deployment version change correlation
9. System resource pressure correlation
10. Autonomous maintenance allowlist enforcement
11. Deterministic policy override (blocking unapproved LLM/caller recommendations)
12. Anti-looping & attempt bounding with backoff
13. Causal cycle breaker preventing infinite recovery loops
14. Deployed capability degradation without silent deletion/permission tampering
15. Deployed capability verified rollback via CapabilityDeploymentManager
16. Persistent incident memory in SqliteMemoryStore
17. Incident recurrence analytics & sliding window counts
18. Secret scrubbing in diagnostics, evidence, and incident memory
19. Reviewable ExtensionProposal generation for code/config repairs
20. End-to-end OperationalMaintenanceCoordinator integration with JevRuntime
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

from agent_task import TaskPermission
from capability_deployment import CapabilityDeploymentManager
from capability_model import CapabilityDefinition, CapabilityType, Reversibility
from capability_registry import CapabilityRegistry
from deployment_manifest import DeploymentManifest, DeploymentStatus, DeploymentType
from event_bus import EventBus
from extension_proposal import ExtensionProposal, ProposalStatus
from failure_diagnosis import DiagnosticReport, FailureDiagnosisEngine
from incident_memory import IncidentMemoryStore, IncidentRecord
from maintenance_coordinator import OperationalMaintenanceCoordinator
from memory_model import MemoryRecord, sanitize_content
from memory_store import SqliteMemoryStore
from operational_health import (
    HealthObservation,
    HealthState,
    OperationalHealthCoordinator,
    SubsystemCategory,
)
from recovery_decision import (
    AUTONOMOUS_MAINTENANCE_ALLOWLIST,
    OperationalRecoveryAction,
    OperationalRecoveryEngine,
    RecoveryPlanV26,
    STRICTLY_GATED_ACTIONS,
)
from reliability_model import FailureCategory
from repair_proposal import OperationalRepairProposalBuilder
from runtime_manager import JevRuntime
from tool_model import ToolResult


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="test_phase26_")
    yield d
    try:
        shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def memory_store(temp_dir):
    db_path = os.path.join(temp_dir, "test_memory.db")
    store = SqliteMemoryStore(db_path=db_path)
    yield store


@pytest.fixture
def capability_registry():
    return CapabilityRegistry()


@pytest.fixture
def deployment_manager(capability_registry, event_bus, temp_dir):
    dep_dir = os.path.join(temp_dir, "deployments")
    return CapabilityDeploymentManager(
        capability_registry=capability_registry,
        event_bus=event_bus,
        deployments_dir=dep_dir,
    )


# ---------------------------------------------------------------------------
# Dimension 1: Canonical Health States & Structured Observation Record
# ---------------------------------------------------------------------------
def test_operational_health_states_and_observation(event_bus):
    coord = OperationalHealthCoordinator(event_bus=event_bus)

    events = []
    event_bus.subscribe("health.state_changed", lambda e: events.append(e))

    obs = coord.record_observation(
        component_id="task_worker_1",
        category=SubsystemCategory.BACKGROUND_TASK,
        state=HealthState.HEALTHY,
        evidence=["Completed 10 tasks with 0 failures."],
        confidence=0.99,
        recommended_action="NO_ACTION",
    )

    assert obs.component_id == "task_worker_1"
    assert obs.category == SubsystemCategory.BACKGROUND_TASK
    assert obs.state == HealthState.HEALTHY
    assert obs.confidence == 0.99
    assert obs.consecutive_failures == 0
    assert obs.failure_count == 0
    assert len(events) == 1

    # Transition to DEGRADED
    obs2 = coord.record_observation(
        component_id="task_worker_1",
        category=SubsystemCategory.BACKGROUND_TASK,
        state=HealthState.DEGRADED,
        evidence=["Transient timeout on task 11."],
        failure_classification=FailureCategory.TIMEOUT,
        confidence=0.85,
        recommended_action="RETRY",
    )
    assert obs2.state == HealthState.DEGRADED
    assert obs2.consecutive_failures == 1
    assert obs2.failure_count == 1
    assert len(events) == 2


# ---------------------------------------------------------------------------
# Dimension 2: Functional Check vs Registry Lookup Distinction
# ---------------------------------------------------------------------------
def test_functional_check_vs_registry_lookup(capability_registry, event_bus):
    coord = OperationalHealthCoordinator(event_bus=event_bus)

    # Register capability without a handler (or broken handler)
    cap = CapabilityDefinition(
        capability_id="test:broken_cap",
        name="Broken Cap",
        description="Exists in registry but fails functional check",
        capability_type=CapabilityType.TOOL,
        handler=None,  # Null handler
    )
    capability_registry.register(cap)

    # In registry: True
    assert capability_registry.is_registered("test:broken_cap")

    # Functional probe: should detect unverified/unavailable because no functional probe succeeds
    ok, err = coord.run_functional_probe(
        component_id="test:broken_cap",
        category=SubsystemCategory.DEPLOYED_CAPABILITY,
    )
    assert not ok
    obs = coord.get_health("test:broken_cap")
    assert obs is not None
    assert obs.state == HealthState.UNVERIFIED  # Unverified functionally


# ---------------------------------------------------------------------------
# Dimension 3: Side-Effect-Aware Probes & Cooperative Cancellation
# ---------------------------------------------------------------------------
def test_side_effect_aware_probes_and_cancellation(event_bus):
    coord = OperationalHealthCoordinator(event_bus=event_bus)

    cancelled_flag = threading.Event()

    def slow_prober(cancel_event=None):
        if cancel_event:
            # Wait for cancel or 1 second
            if cancel_event.wait(timeout=1.0):
                cancelled_flag.set()
                return False, "Cancelled by deadline"
        return True, "Finished"

    ok, msg = coord.run_functional_probe(
        component_id="slow_service",
        category=SubsystemCategory.RUNTIME_COMPONENT,
        prober=slow_prober,
        timeout_seconds=0.1,  # Short timeout triggers cancellation
    )
    assert not ok
    assert "deadline exceeded" in (msg or "").lower()
    # Verify cancel event was signaled to worker
    assert cancelled_flag.wait(timeout=1.5)
    obs = coord.get_health("slow_service")
    assert obs.state == HealthState.UNAVAILABLE


# ---------------------------------------------------------------------------
# Dimension 4: Worker Safety & Outstanding Worker Accounting
# ---------------------------------------------------------------------------
def test_worker_safety_and_outstanding_worker_accounting(event_bus):
    coord = OperationalHealthCoordinator(event_bus=event_bus)

    coord.record_observation(
        component_id="worker_pool",
        category=SubsystemCategory.RUNTIME_COMPONENT,
        state=HealthState.HEALTHY,
    )

    coord.track_worker_start("worker_pool")
    coord.track_worker_start("worker_pool")
    obs = coord.get_health("worker_pool")
    assert obs.outstanding_operations == 2

    coord.track_worker_finish("worker_pool")
    assert obs.outstanding_operations == 1

    coord.track_worker_finish("worker_pool")
    assert obs.outstanding_operations == 0

    # Test concurrency cap on active probe threads
    threads = []
    with coord._probe_lock:
        for i in range(OperationalHealthCoordinator.MAX_CONCURRENT_PROBES):
            t = threading.Thread(target=lambda: time.sleep(1.0), daemon=True)
            t.start()
            threads.append(t)
            coord._active_probe_threads.add(t)

    # Next probe should be rejected immediately due to concurrency cap
    ok, err = coord.run_functional_probe(
        component_id="overloaded_comp",
        category=SubsystemCategory.RUNTIME_COMPONENT,
        prober=lambda: (True, "OK"),
    )
    assert not ok
    assert "concurrency limit reached" in (err or "").lower()

    # Clean up dummy threads
    for t in threads:
        t.join(timeout=1.5)


# ---------------------------------------------------------------------------
# Dimension 5: Multi-Signal Failure Diagnosis & Classification
# ---------------------------------------------------------------------------
def test_multi_signal_failure_diagnosis():
    diag = FailureDiagnosisEngine()

    err = "Connection reset by peer: remote socket forcibly closed"
    report = diag.diagnose(
        component_id="remote_api",
        category=SubsystemCategory.PROVIDER,
        error_or_exception=ConnectionResetError(err),
        context={"target_host": "127.0.0.1", "target_port": 65530},  # Unused port
    )

    assert report.component_id == "remote_api"
    assert report.failure_classification == FailureCategory.NETWORK_FAILURE
    assert any("Error signal:" in s for s in report.observed_symptoms)
    assert report.recommended_recovery_action == "RETRY"


# ---------------------------------------------------------------------------
# Dimension 6: Verified Cause vs Working Hypothesis Separation
# ---------------------------------------------------------------------------
def test_verified_cause_vs_hypothesis_separation():
    diag = FailureDiagnosisEngine()

    with patch("psutil.virtual_memory") as mock_mem, patch("psutil.cpu_percent", return_value=10.0):
        mock_mem.return_value.percent = 50.0

        # 1. Empirically proven missing module
        rep1 = diag.diagnose(
            component_id="ml_toolkit",
            category=SubsystemCategory.DEPLOYED_CAPABILITY,
            error_or_exception=ModuleNotFoundError("No module named 'non_existent_ml_lib_xyz'"),
        )
        assert any("Missing required Python module" in c for c in rep1.verified_causes)
        assert len(rep1.hypotheses) == 0  # Fact, not hypothesis

        # 2. Transient timeout without confirmed root cause
        rep2 = diag.diagnose(
            component_id="db_query",
            category=SubsystemCategory.BACKGROUND_TASK,
            error_or_exception=TimeoutError("Query timed out after 30s"),
        )
        assert len(rep2.verified_causes) == 0
        assert any("Operation exceeded execution deadline" in h for h in rep2.hypotheses)


# ---------------------------------------------------------------------------
# Dimension 7: Dependency Availability Correlation
# ---------------------------------------------------------------------------
def test_dependency_availability_correlation():
    diag = FailureDiagnosisEngine()

    report = diag.diagnose(
        component_id="audio_processor",
        category=SubsystemCategory.DEPLOYED_CAPABILITY,
        error_or_exception="ImportError: No module named 'fake_audio_codec_lib_999'",
    )

    assert report.failure_classification == FailureCategory.TOOL_FAILURE
    assert any("fake_audio_codec_lib_999" in c for c in report.verified_causes)
    assert report.recommended_recovery_action == "PROPOSE_REPAIR"
    assert report.requires_approval is True


# ---------------------------------------------------------------------------
# Dimension 8: Deployment Version Change Correlation
# ---------------------------------------------------------------------------
def test_deployment_version_change_correlation():
    diag = FailureDiagnosisEngine()

    obs = HealthObservation(
        component_id="weather_tool",
        category=SubsystemCategory.DEPLOYED_CAPABILITY,
        state=HealthState.DEGRADED,
        consecutive_failures=3,
    )

    report = diag.diagnose(
        component_id="weather_tool",
        category=SubsystemCategory.DEPLOYED_CAPABILITY,
        error_or_exception="RuntimeError: Schema mismatch on field 'temperature'",
        context={
            "recent_deployment": "dep_weather_v2",
            "deployment_timestamp": time.time() - 60,  # 1 min ago
        },
        health_observation=obs,
    )

    assert report.recommended_recovery_action == "ROLLBACK_DEPLOYMENT"
    assert any("dep_weather_v2" in c for c in report.verified_causes)


# ---------------------------------------------------------------------------
# Dimension 9: System Resource Pressure Correlation
# ---------------------------------------------------------------------------
def test_system_resource_pressure_correlation():
    diag = FailureDiagnosisEngine(resource_cpu_threshold=80.0, resource_mem_threshold=80.0)

    with patch("psutil.cpu_percent", return_value=95.0), patch("psutil.virtual_memory") as mock_mem:
        mock_mem.return_value.percent = 96.0

        report = diag.diagnose(
            component_id="heavy_task",
            category=SubsystemCategory.BACKGROUND_TASK,
            error_or_exception=TimeoutError("Worker task timed out"),
        )

        assert report.failure_classification == FailureCategory.RESOURCE_EXHAUSTED
        assert any("Host hardware resource saturation" in c for c in report.verified_causes)
        assert report.recommended_recovery_action == "RECONCILE_STATE"


# ---------------------------------------------------------------------------
# Dimension 10: Autonomous Maintenance Allowlist Enforcement
# ---------------------------------------------------------------------------
def test_autonomous_maintenance_allowlist():
    rec_engine = OperationalRecoveryEngine()

    # Low-risk action on allowlist: RETRY
    diag_retry = DiagnosticReport(
        diagnosis_id="d1",
        component_id="sync_task",
        category=SubsystemCategory.BACKGROUND_TASK,
        failure_classification=FailureCategory.TRANSIENT,
        recommended_recovery_action="RETRY",
        risk_level="LOW",
        requires_approval=False,
    )
    plan_retry = rec_engine.formulate_plan(diag_retry)
    assert plan_retry.action == OperationalRecoveryAction.RETRY
    assert plan_retry.autonomous_permitted is True
    assert plan_retry.requires_approval is False

    # Action NOT on allowlist: PROPOSE_REPAIR
    diag_repair = DiagnosticReport(
        diagnosis_id="d2",
        component_id="core_tool",
        category=SubsystemCategory.DEPLOYED_CAPABILITY,
        failure_classification=FailureCategory.TOOL_FAILURE,
        recommended_recovery_action="PROPOSE_REPAIR",
        risk_level="MEDIUM",
        requires_approval=True,
    )
    plan_repair = rec_engine.formulate_plan(diag_repair)
    assert plan_repair.action == OperationalRecoveryAction.PROPOSE_REPAIR
    assert plan_repair.autonomous_permitted is False
    assert plan_repair.requires_approval is True


# ---------------------------------------------------------------------------
# Dimension 11: Deterministic Policy Override (Blocking Unapproved LLM Recoms)
# ---------------------------------------------------------------------------
def test_deterministic_policy_override_blocks_unauthorized():
    rec_engine = OperationalRecoveryEngine()

    # Suppose an LLM suggests executing an unauthorized action or security bypass
    diag = DiagnosticReport(
        diagnosis_id="d3",
        component_id="auth_service",
        category=SubsystemCategory.RUNTIME_COMPONENT,
        failure_classification=FailureCategory.PERMISSION_DENIED,
        recommended_recovery_action="RETRY",  # LLM says retry permission denied
        risk_level="LOW",
        requires_approval=False,
    )

    # Policy must override LLM suggestion because FailureCategory is PERMISSION_DENIED
    plan = rec_engine.formulate_plan(diag, suggested_action="RETRY")
    assert plan.action == OperationalRecoveryAction.ESCALATE_TO_USER
    assert plan.requires_approval is True
    assert plan.autonomous_permitted is False
    assert plan.risk_level == "CRITICAL"

    # Execution without approval must be rejected by policy
    ok, err = rec_engine.execute_plan(plan, user_approved=False)
    assert not ok
    assert "requires explicit approval" in err


# ---------------------------------------------------------------------------
# Dimension 12: Anti-Looping & Attempt Bounding with Backoff
# ---------------------------------------------------------------------------
def test_anti_looping_and_attempt_bounding():
    rec_engine = OperationalRecoveryEngine(max_attempts_per_fingerprint=2)

    diag = DiagnosticReport(
        diagnosis_id="d4",
        component_id="flapping_service",
        category=SubsystemCategory.RUNTIME_COMPONENT,
        failure_classification=FailureCategory.TIMEOUT,
        recommended_recovery_action="RETRY",
    )

    # Attempt 1: RETRY allowed
    p1 = rec_engine.formulate_plan(diag)
    assert p1.action == OperationalRecoveryAction.RETRY
    assert p1.attempt_number == 1

    # Attempt 2: RETRY allowed
    p2 = rec_engine.formulate_plan(diag)
    assert p2.action == OperationalRecoveryAction.RETRY
    assert p2.attempt_number == 2

    # Attempt 3: Exceeds max attempts (2) -> Escalated to prevent infinite loop!
    p3 = rec_engine.formulate_plan(diag)
    assert p3.action == OperationalRecoveryAction.ESCALATE_TO_USER
    assert p3.attempt_number == 3
    assert "threshold exceeded" in p3.reason.lower()


# ---------------------------------------------------------------------------
# Dimension 13: Causal Cycle Breaker Preventing Infinite Recovery
# ---------------------------------------------------------------------------
def test_causal_cycle_breaker():
    rec_engine = OperationalRecoveryEngine(max_attempts_per_fingerprint=3)

    diag = DiagnosticReport(
        diagnosis_id="d5",
        component_id="crashing_watcher",
        category=SubsystemCategory.WATCHER,
        failure_classification=FailureCategory.PROCESS_CRASH,
        recommended_recovery_action="RESTART_COMPONENT",
        verified_causes=["Access violation reading 0x00000000"],
    )

    # Consecutive restarts
    p1 = rec_engine.formulate_plan(diag)
    assert p1.action == OperationalRecoveryAction.RESTART_COMPONENT
    p2 = rec_engine.formulate_plan(diag)
    assert p2.action == OperationalRecoveryAction.RESTART_COMPONENT
    p3 = rec_engine.formulate_plan(diag)
    assert p3.action == OperationalRecoveryAction.RESTART_COMPONENT

    # 4th time: loop breaker stops repeated restarts
    p4 = rec_engine.formulate_plan(diag)
    assert p4.action == OperationalRecoveryAction.ESCALATE_TO_USER


# ---------------------------------------------------------------------------
# Dimension 14: Deployed Capability Degradation Without Silent Deletion/Tampering
# ---------------------------------------------------------------------------
def test_deployed_capability_degradation_preserves_registry(deployment_manager, capability_registry):
    manifest = deployment_manager.create_deployment_manifest(
        capability_id="test:degrade_cap",
        name="Degradable Capability",
        description="Testing degradation",
        proposal_id="prop_deg_1",
        candidate_hash="hash123",
        deployment_type=DeploymentType.APPROVED_EXTERNAL,
        required_permissions=[TaskPermission.READ_FILES],
    )
    deployment_manager.stage_deployment(manifest.deployment_id)
    deployment_manager.approve_deployment(manifest.deployment_id, approver="auditor")
    ok, _ = deployment_manager.activate_deployment(manifest.deployment_id)
    assert ok

    coord = OperationalHealthCoordinator()
    # Mark capability as DEGRADED due to intermittent downstream errors
    obs = coord.record_observation(
        component_id="test:degrade_cap",
        category=SubsystemCategory.DEPLOYED_CAPABILITY,
        state=HealthState.DEGRADED,
        evidence=["Downstream API 503 response; falling back to cache."],
    )
    assert obs.state == HealthState.DEGRADED

    # Ensure capability was NOT silently unregistered
    assert capability_registry.is_registered("test:degrade_cap")
    cap = capability_registry.get("test:degrade_cap")
    assert cap is not None
    # Ensure permissions were NOT modified
    assert cap.permissions == [TaskPermission.READ_FILES]


# ---------------------------------------------------------------------------
# Dimension 15: Deployed Capability Verified Rollback
# ---------------------------------------------------------------------------
def test_deployed_capability_verified_rollback(deployment_manager, capability_registry, event_bus):
    coord = OperationalMaintenanceCoordinator(
        event_bus=event_bus,
        deployment_manager=deployment_manager,
    )

    # 1. Register base stable capability
    stable_cap = CapabilityDefinition(
        capability_id="test:rollback_target",
        name="Stable Cap",
        description="Initial stable version",
        capability_type=CapabilityType.TOOL,
        handler=lambda **kwargs: ToolResult("test:rollback_target", status="SUCCESS", output="v1.0"),
        provenance="deployment:dep_prior_v1",
    )
    capability_registry.register(stable_cap)

    # 2. Deploy v2 that replaces it
    manifest = deployment_manager.create_deployment_manifest(
        capability_id="test:rollback_target",
        name="V2 Cap",
        description="Buggy v2",
        proposal_id="prop_v2",
        candidate_hash="hash_v2",
        deployment_type=DeploymentType.APPROVED_EXTERNAL,
        version="2.0.0",
    )
    deployment_manager.stage_deployment(manifest.deployment_id)
    deployment_manager.approve_deployment(manifest.deployment_id, approver="auditor")
    ok, _ = deployment_manager.activate_deployment(manifest.deployment_id)
    assert ok
    assert manifest.status == DeploymentStatus.ACTIVE

    # 3. Simulate failure in v2 and trigger autonomous rollback
    diag, plan, res = coord.handle_component_failure(
        component_id="test:rollback_target",
        category=SubsystemCategory.DEPLOYED_CAPABILITY,
        error_or_exception=RuntimeError("Fatal regression in v2.0.0"),
        context={"recent_deployment": manifest.deployment_id, "deployment_timestamp": time.time()},
        auto_recover=True,
    )

    assert plan.action == OperationalRecoveryAction.ROLLBACK_DEPLOYMENT
    assert res is not None
    success, msg = res
    assert success
    assert "Rollback succeeded" in msg

    # Verify post-condition: restored to previous stable definition
    current = capability_registry.get("test:rollback_target")
    assert current is not None
    assert current.provenance == "deployment:dep_prior_v1"
    assert manifest.status == DeploymentStatus.ROLLED_BACK


# ---------------------------------------------------------------------------
# Dimension 16: Persistent Incident Memory in SqliteMemoryStore
# ---------------------------------------------------------------------------
def test_persistent_incident_memory(memory_store, event_bus):
    inc_store = IncidentMemoryStore(memory_store=memory_store, event_bus=event_bus)

    rec = inc_store.record_incident(
        component_id="db_writer",
        category=SubsystemCategory.RUNTIME_COMPONENT,
        symptoms=["Disk I/O error on commit"],
        verified_causes=["SQLite busy timeout on WAL checkpoint"],
        recovery_attempts=[{"action": "RECONCILE_STATE", "attempt": 1}],
        final_state=HealthState.RECOVERING,
        verified_outcome="Checkpoint flushed.",
        resolved=True,
    )

    assert rec.incident_id.startswith("inc_db_writer_")
    assert rec.resolved is True

    # Check persistence in memory_store
    mems = memory_store.search("Operational Incident")
    assert len(mems) >= 1
    assert "SQLite busy timeout" in mems[0].content


# ---------------------------------------------------------------------------
# Dimension 17: Recurrence Analytics & Sliding Window Counts
# ---------------------------------------------------------------------------
def test_incident_recurrence_analytics(event_bus):
    inc_store = IncidentMemoryStore(event_bus=event_bus)

    comp = "flapping_task"
    # Record 3 incidents
    inc_store.record_incident(comp, SubsystemCategory.BACKGROUND_TASK, symptoms=["Error 1"])
    inc_store.record_incident(comp, SubsystemCategory.BACKGROUND_TASK, symptoms=["Error 2"])
    inc_store.record_incident(comp, SubsystemCategory.BACKGROUND_TASK, symptoms=["Error 3"])

    # Within 300s window -> 3
    count = inc_store.count_recent_failures(comp, window_seconds=300.0)
    assert count == 3

    # Other component -> 0
    assert inc_store.count_recent_failures("other_comp", window_seconds=300.0) == 0


# ---------------------------------------------------------------------------
# Dimension 18: Secret Scrubbing in Diagnostics, Evidence, and Incident Memory
# ---------------------------------------------------------------------------
def test_secret_scrubbing_in_all_operational_data(memory_store, event_bus):
    coord = OperationalMaintenanceCoordinator(event_bus=event_bus, memory_store=memory_store)

    secret_key = "sk-ant-api03-abcdef1234567890abcdef1234567890abcdef"
    error_with_secret = f"Authorization failed with key {secret_key} on endpoint"

    diag, plan, _ = coord.handle_component_failure(
        component_id="provider_llm",
        category=SubsystemCategory.PROVIDER,
        error_or_exception=error_with_secret,
        auto_recover=False,
    )

    # 1. Secret scrubbed from symptoms & evidence
    assert secret_key not in diag.observed_symptoms[0]
    assert "[REDACTED" in diag.observed_symptoms[0]

    # 2. Secret scrubbed from persistent incident memory
    incidents = coord.incident_store.list_incidents(component_id="provider_llm")
    assert len(incidents) == 1
    assert secret_key not in incidents[0].symptoms[0]
    assert "[REDACTED" in incidents[0].symptoms[0]


# ---------------------------------------------------------------------------
# Dimension 19: Reviewable Repair Proposal Generation
# ---------------------------------------------------------------------------
def test_reviewable_repair_proposal_generation(event_bus):
    builder = OperationalRepairProposalBuilder(event_bus=event_bus)

    diag = DiagnosticReport(
        diagnosis_id="diag_mod_err",
        component_id="image_analyzer",
        category=SubsystemCategory.DEPLOYED_CAPABILITY,
        verified_causes=["Missing required Python module: 'pillow'"],
        failure_classification=FailureCategory.TOOL_FAILURE,
        recommended_recovery_action="PROPOSE_REPAIR",
        risk_level="MEDIUM",
        requires_approval=True,
    )

    proposal = builder.build_repair_proposal(
        diagnostic=diag,
        target_package="pillow",
        target_file="image_analyzer.py",
        suggested_code_diff="+ import PIL.Image",
    )

    assert isinstance(proposal, ExtensionProposal)
    assert proposal.status == ProposalStatus.PROPOSED
    assert proposal.approval_required is True
    assert proposal.approval_state == "PENDING"
    assert proposal.candidate.dependencies == ["pillow"]
    assert proposal.candidate_hash != ""
    assert "pillow" in proposal.rationale


# ---------------------------------------------------------------------------
# Dimension 20: End-to-End Operational Maintenance Coordinator with JevRuntime
# ---------------------------------------------------------------------------
def test_end_to_end_operational_coordinator_with_runtime(temp_dir, event_bus):
    appdata = os.path.join(temp_dir, "appdata")
    os.makedirs(appdata, exist_ok=True)

    runtime = JevRuntime(
        appdata_dir=appdata,
        event_bus=event_bus,
        enable_heartbeat=True,
        enable_project_watchers=False,
        enable_native_watchers=False,
    )

    # Start runtime
    runtime.start()
    assert runtime.is_running

    # Check operational report
    report = runtime.get_operational_health_report()
    assert "system_health" in report
    assert "components" in report
    assert "recent_incidents" in report

    # Simulate a component failure through maintenance coordinator
    with patch("psutil.virtual_memory") as mock_mem, patch("psutil.cpu_percent", return_value=10.0):
        mock_mem.return_value.percent = 50.0
        diag, plan, res = runtime.maintenance_coordinator.handle_component_failure(
            component_id="background_task_worker",
            category=SubsystemCategory.BACKGROUND_TASK,
            error_or_exception=TimeoutError("Task execution timed out"),
            auto_recover=True,
        )

    assert diag.failure_classification == FailureCategory.TIMEOUT
    assert plan.action == OperationalRecoveryAction.RETRY

    # Verify updated report reflects incident
    updated_report = runtime.get_operational_health_report()
    assert len(updated_report["recent_incidents"]) >= 1

    # Stop runtime cleanly
    runtime.stop()
    assert not runtime.is_running
