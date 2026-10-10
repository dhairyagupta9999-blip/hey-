"""Phase 26.1 Autonomous Maintenance Safety, Recovery Reliability and Operational Integration Audit Test Suite.

Validates all 15 audit dimensions:
1. Autonomous allowlist enforcement (allowed actions execute, non-allowlist actions blocked).
2. Non-allowlist actions (code edit, config rewrite, privilege escalation) rejected with explicit approval requirement.
3. Target-specific allowlist: arbitrary/unsupported components cannot be restarted or rolled back without approval.
4. Fail-closed on unknown or invalid actions.
5. Recovery loop prevention: same failure fingerprint triggers cycle breaker after N attempts.
6. Multi-failure cycle prevention: alternating failure modes for the same component trigger cycle breaker.
7. Concurrent recovery deduplication: simultaneous failure events for the same component execute at most one recovery.
8. Rollback post-condition verification: rollback failure marks incident unresolved, does not claim recovery.
9. Diagnostic confidence discounting: conflicting evidence reduces diagnostic confidence.
10. Temporal causality vs hypothesis: recent deployment without causal evidence is treated as hypothesis, not verified cause.
11. Resource pressure correlation: high CPU/RAM suppresses false application-bug diagnosis or qualifies it appropriately.
12. Worker safety on timeout: health probe timeout accounts for outstanding worker thread, does not claim thread terminated.
13. Incident memory deduplication: flapping component produces bounded/linked incident records, not unbounded spam.
14. Secret scrubbing in incident memory: tokens, passwords, keys in error messages are scrubbed before persistence.
15. RuntimeManager lifecycle integration: startup, shutdown, and health coordinator integration operate cleanly.
"""
from __future__ import annotations

import os
import shutil
import socket
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

from agent_task import TaskPermission
from capability_deployment import CapabilityDeploymentManager
from capability_model import CapabilityDefinition, CapabilityType
from capability_registry import CapabilityRegistry
from deployment_manifest import DeploymentManifest, DeploymentStatus, DeploymentType
from event_bus import EventBus
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
    is_privileged_target,
)
from reliability_model import FailureCategory
from runtime_manager import JevRuntime


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="test_phase26_1_")
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
# Requirement 1: Autonomous Allowlist Enforcement
# ---------------------------------------------------------------------------
def test_audit_autonomous_allowlist_enforcement():
    engine = OperationalRecoveryEngine()
    executed_actions: List[str] = []

    def mock_executor(component_id: str, plan: RecoveryPlanV26):
        executed_actions.append(plan.action.value)
        return True, "Executed successfully"

    for action in [
        OperationalRecoveryAction.NO_ACTION,
        OperationalRecoveryAction.RETRY,
        OperationalRecoveryAction.RECONCILE_STATE,
        OperationalRecoveryAction.RESTART_COMPONENT,
    ]:
        engine.register_action_executor(action, mock_executor)
        diag = DiagnosticReport(
            diagnosis_id=f"diag_{action.value}",
            component_id="worker_task",
            category=SubsystemCategory.BACKGROUND_TASK,
            failure_classification=FailureCategory.TRANSIENT,
            recommended_recovery_action=action.value,
            risk_level="LOW",
            requires_approval=False,
        )
        plan = engine.formulate_plan(diag)
        assert plan.autonomous_permitted is True
        assert plan.requires_approval is False

        ok, msg = engine.execute_plan(plan, user_approved=False)
        assert ok is True
        assert "Executed successfully" in msg


# ---------------------------------------------------------------------------
# Requirement 2: Non-Allowlist Actions Require Explicit Approval
# ---------------------------------------------------------------------------
def test_audit_non_allowlist_actions_require_approval():
    engine = OperationalRecoveryEngine()

    for gated_action in [
        OperationalRecoveryAction.PROPOSE_REPAIR,
        OperationalRecoveryAction.REQUEST_APPROVAL,
        OperationalRecoveryAction.ESCALATE_TO_USER,
    ]:
        diag = DiagnosticReport(
            diagnosis_id=f"diag_{gated_action.value}",
            component_id="core_subsystem",
            category=SubsystemCategory.RUNTIME_COMPONENT,
            failure_classification=FailureCategory.TOOL_FAILURE,
            recommended_recovery_action=gated_action.value,
            risk_level="MEDIUM",
        )
        plan = engine.formulate_plan(diag)
        assert plan.autonomous_permitted is False
        assert plan.requires_approval is True

        # Unapproved execution MUST be blocked
        ok, err = engine.execute_plan(plan, user_approved=False)
        assert ok is False
        assert "requires explicit approval" in err.lower() or "not on the autonomous allowlist" in err.lower()


# ---------------------------------------------------------------------------
# Requirement 3: Target-Specific Allowlist Checks
# ---------------------------------------------------------------------------
def test_audit_target_specific_allowlist():
    engine = OperationalRecoveryEngine()

    # Case A: Privileged target cannot be restarted autonomously
    privileged_diag = DiagnosticReport(
        diagnosis_id="diag_priv",
        component_id="safety_engine",
        category=SubsystemCategory.RUNTIME_COMPONENT,
        failure_classification=FailureCategory.TRANSIENT,
        recommended_recovery_action="RESTART_COMPONENT",
    )
    plan_priv = engine.formulate_plan(privileged_diag)
    assert plan_priv.action == OperationalRecoveryAction.REQUEST_APPROVAL
    assert plan_priv.requires_approval is True
    assert plan_priv.autonomous_permitted is False
    assert plan_priv.risk_level == "CRITICAL"
    assert "privileged system component" in plan_priv.reason.lower()

    # Case B: Arbitrary component rollback blocked if not a DEPLOYED_CAPABILITY
    non_dep_diag = DiagnosticReport(
        diagnosis_id="diag_non_dep",
        component_id="watcher_proc",
        category=SubsystemCategory.WATCHER,
        failure_classification=FailureCategory.UNKNOWN,
        recommended_recovery_action="ROLLBACK_DEPLOYMENT",
    )
    plan_rollback = engine.formulate_plan(non_dep_diag)
    assert plan_rollback.action == OperationalRecoveryAction.REQUEST_APPROVAL
    assert plan_rollback.requires_approval is True
    assert plan_rollback.autonomous_permitted is False
    assert "not deployed_capability" in plan_rollback.reason.lower()

    # Case C: Unrecognized target restart fails closed in coordinator
    coord = OperationalMaintenanceCoordinator()
    unrec_diag = DiagnosticReport(
        diagnosis_id="diag_unrec",
        component_id="unregistered_mystery_daemon",
        category=SubsystemCategory.RUNTIME_COMPONENT,
        failure_classification=FailureCategory.TRANSIENT,
        recommended_recovery_action="RESTART_COMPONENT",
    )
    plan_unrec = coord.recovery_engine.formulate_plan(unrec_diag)
    ok, err = coord.recovery_engine.execute_plan(plan_unrec, user_approved=True)
    assert ok is False
    assert "not a recognized restartable target" in err.lower()


# ---------------------------------------------------------------------------
# Requirement 4: Fail-Closed on Unknown or Invalid Actions
# ---------------------------------------------------------------------------
def test_audit_fail_closed_on_unknown_or_invalid_actions():
    engine = OperationalRecoveryEngine()

    diag = DiagnosticReport(
        diagnosis_id="diag_unknown",
        component_id="worker_task",
        category=SubsystemCategory.BACKGROUND_TASK,
        failure_classification=FailureCategory.UNKNOWN,
        recommended_recovery_action="COMPLETELY_INVALID_ACTION_XYZ",
    )
    # Unknown string maps to fallback with bounded approval
    plan = engine.formulate_plan(diag)
    assert plan.action in (OperationalRecoveryAction.RETRY, OperationalRecoveryAction.REQUEST_APPROVAL)
    if plan.action == OperationalRecoveryAction.REQUEST_APPROVAL:
        assert plan.requires_approval is True
        assert plan.autonomous_permitted is False

    # Executing an unmapped executor action fails closed
    unmapped_plan = RecoveryPlanV26(
        plan_id="plan_unmapped",
        component_id="worker_task",
        action=OperationalRecoveryAction.REQUEST_APPROVAL,
        reason="Testing unmapped",
        risk_level="HIGH",
        requires_approval=False,
        autonomous_permitted=True,
        attempt_number=1,
    )
    ok, err = engine.execute_plan(unmapped_plan, user_approved=True)
    assert ok is False
    assert "no concrete executor registered" in err.lower()


# ---------------------------------------------------------------------------
# Requirement 5: Recovery Loop Prevention (Same Failure Fingerprint)
# ---------------------------------------------------------------------------
def test_audit_recovery_loop_prevention_same_fingerprint():
    engine = OperationalRecoveryEngine(max_attempts_per_fingerprint=2)

    diag = DiagnosticReport(
        diagnosis_id="diag_flapper",
        component_id="task_worker_1",
        category=SubsystemCategory.BACKGROUND_TASK,
        failure_classification=FailureCategory.TIMEOUT,
        recommended_recovery_action="RETRY",
        verified_causes=["Socket timeout"],
    )

    # Attempt 1 -> RETRY
    p1 = engine.formulate_plan(diag)
    assert p1.action == OperationalRecoveryAction.RETRY
    assert p1.attempt_number == 1

    # Attempt 2 -> RETRY
    p2 = engine.formulate_plan(diag)
    assert p2.action == OperationalRecoveryAction.RETRY
    assert p2.attempt_number == 2

    # Attempt 3 -> Cycle breaker triggered!
    p3 = engine.formulate_plan(diag)
    assert p3.action == OperationalRecoveryAction.ESCALATE_TO_USER
    assert p3.attempt_number == 3
    assert p3.requires_approval is True
    assert p3.autonomous_permitted is False
    assert "threshold exceeded" in p3.reason.lower()


# ---------------------------------------------------------------------------
# Requirement 6: Multi-Failure Cycle Prevention (Alternating Failures)
# ---------------------------------------------------------------------------
def test_audit_multi_failure_alternating_cycle_prevention():
    engine = OperationalRecoveryEngine(max_attempts_per_fingerprint=5, max_component_cycles=4)

    # Component oscillates between TIMEOUT and RESOURCE_EXHAUSTED
    diag_timeout = DiagnosticReport(
        diagnosis_id="diag_t",
        component_id="oscillating_service",
        category=SubsystemCategory.RUNTIME_COMPONENT,
        failure_classification=FailureCategory.TIMEOUT,
        recommended_recovery_action="RETRY",
    )
    diag_resource = DiagnosticReport(
        diagnosis_id="diag_r",
        component_id="oscillating_service",
        category=SubsystemCategory.RUNTIME_COMPONENT,
        failure_classification=FailureCategory.RESOURCE_EXHAUSTED,
        recommended_recovery_action="RECONCILE_STATE",
    )

    p1 = engine.formulate_plan(diag_timeout)
    assert p1.action == OperationalRecoveryAction.RETRY

    p2 = engine.formulate_plan(diag_resource)
    assert p2.action == OperationalRecoveryAction.RECONCILE_STATE

    p3 = engine.formulate_plan(diag_timeout)
    assert p3.action == OperationalRecoveryAction.RETRY

    # 4th alternating failure triggers multi-failure cycle breaker
    p4 = engine.formulate_plan(diag_resource)
    assert p4.action == OperationalRecoveryAction.ESCALATE_TO_USER
    assert "alternating cycle detected" in p4.reason.lower()
    assert p4.requires_approval is True
    assert p4.autonomous_permitted is False


# ---------------------------------------------------------------------------
# Requirement 7: Concurrent Recovery Deduplication
# ---------------------------------------------------------------------------
def test_audit_concurrent_recovery_deduplication():
    coord = OperationalMaintenanceCoordinator()
    execution_count = 0
    lock = threading.Lock()

    def slow_restart():
        nonlocal execution_count
        with lock:
            execution_count += 1
        time.sleep(0.15)
        return True, "Slow restart completed"

    coord.register_restart_hook("concurrent_worker", slow_restart)

    results: List[Any] = []

    def trigger_failure():
        res = coord.handle_component_failure(
            component_id="concurrent_worker",
            category=SubsystemCategory.RUNTIME_COMPONENT,
            error_or_exception=RuntimeError("Simulated crash"),
            auto_recover=True,
        )
        results.append(res)

    threads = [threading.Thread(target=trigger_failure) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3.0)

    # Exactly 1 thread executes the recovery action; other concurrent threads are deduplicated
    assert execution_count == 1
    # At least one result was suppressed with in-flight message
    suppressed = [r for r in results if r[2] and "already in-flight" in str(r[2][1])]
    assert len(suppressed) >= 1


# ---------------------------------------------------------------------------
# Requirement 8: Rollback Post-Condition Verification
# ---------------------------------------------------------------------------
def test_audit_rollback_post_condition_verification(deployment_manager, capability_registry, event_bus):
    coord = OperationalMaintenanceCoordinator(
        event_bus=event_bus,
        deployment_manager=deployment_manager,
    )

    # Deploy test capability
    manifest = deployment_manager.create_deployment_manifest(
        capability_id="test:verify_rollback",
        name="Cap",
        description="Desc",
        proposal_id="prop_v1",
        candidate_hash="hash_1",
        deployment_type=DeploymentType.APPROVED_EXTERNAL,
        version="1.0.0",
    )
    deployment_manager.stage_deployment(manifest.deployment_id)
    deployment_manager.approve_deployment(manifest.deployment_id, approver="auditor")
    deployment_manager.activate_deployment(manifest.deployment_id)

    # Simulate rollback failure from DeploymentManager
    with patch.object(deployment_manager, "rollback_deployment", return_value=(False, "Database locked on rollback")):
        diag, plan, res = coord.handle_component_failure(
            component_id="test:verify_rollback",
            category=SubsystemCategory.DEPLOYED_CAPABILITY,
            error_or_exception=RuntimeError("Regression in deployment"),
            context={"recent_deployment": manifest.deployment_id},
            auto_recover=True,
        )

        assert res is not None
        ok, msg = res
        assert ok is False
        assert "Rollback failed" in msg

        # Post-condition verification: incident is NOT marked resolved
        incidents = coord.incident_store.list_incidents(component_id="test:verify_rollback")
        assert len(incidents) == 1
        assert incidents[0].resolved is False
        assert incidents[0].final_state == HealthState.NEEDS_ATTENTION

        # Component health remains NEEDS_ATTENTION
        health = coord.health_coordinator.get_health("test:verify_rollback")
        assert health is not None
        assert health.state == HealthState.NEEDS_ATTENTION


# ---------------------------------------------------------------------------
# Requirement 9: Diagnostic Confidence Discounting on Contradicting Evidence
# ---------------------------------------------------------------------------
def test_audit_diagnostic_confidence_discounting():
    diag = FailureDiagnosisEngine()

    # Network failure where direct socket probe to localhost succeeded -> contradicting evidence
    with patch("socket.create_connection") as mock_conn:
        mock_conn.return_value = MagicMock()  # Socket connection succeeds

        report = diag.diagnose(
            component_id="api_client",
            category=SubsystemCategory.PROVIDER,
            error_or_exception=ConnectionResetError("Socket reset by peer"),
            context={"target_host": "127.0.0.1", "target_port": 80},
        )

        assert len(report.contradicting_evidence) >= 1
        assert any("Socket connection to 127.0.0.1:80 succeeded" in e for e in report.contradicting_evidence)
        # Confidence must be discounted from default 0.8
        assert report.confidence < 0.8


# ---------------------------------------------------------------------------
# Requirement 10: Temporal Causality vs Hypothesis for Recent Deployments
# ---------------------------------------------------------------------------
def test_audit_temporal_causality_vs_hypothesis():
    diag = FailureDiagnosisEngine()

    # Scenario A: Unrelated background task fails with network error shortly after a deployment
    rep_hypo = diag.diagnose(
        component_id="background_indexer",
        category=SubsystemCategory.BACKGROUND_TASK,
        error_or_exception=ConnectionResetError("Network transport error"),
        context={
            "recent_deployment": "dep_unrelated_tool_v3",
            "deployment_timestamp": time.time() - 30,  # 30 seconds ago
        },
    )
    # Lacks causal error link: Must be treated as hypothesis, NOT verified cause
    assert not any("dep_unrelated_tool_v3" in c for c in rep_hypo.verified_causes)
    assert any("dep_unrelated_tool_v3" in h for h in rep_hypo.hypotheses)
    assert rep_hypo.confidence <= 0.65

    # Scenario B: Deployed capability fails with schema mismatch referencing deployment
    rep_cause = diag.diagnose(
        component_id="dep_unrelated_tool_v3",
        category=SubsystemCategory.DEPLOYED_CAPABILITY,
        error_or_exception=RuntimeError("Schema mismatch in deployment dep_unrelated_tool_v3"),
        context={
            "recent_deployment": "dep_unrelated_tool_v3",
            "deployment_timestamp": time.time() - 30,
        },
    )
    # Direct causal link: Verified cause
    assert any("dep_unrelated_tool_v3" in c for c in rep_cause.verified_causes)
    assert rep_cause.confidence >= 0.9


# ---------------------------------------------------------------------------
# Requirement 11: System Resource Pressure Correlation
# ---------------------------------------------------------------------------
def test_audit_resource_pressure_correlation():
    diag = FailureDiagnosisEngine(resource_cpu_threshold=80.0, resource_mem_threshold=80.0)

    # High host CPU/RAM during an application bug (e.g. ValueError)
    with patch("psutil.cpu_percent", return_value=96.0), patch("psutil.virtual_memory") as mock_mem:
        mock_mem.return_value.percent = 98.0

        report = diag.diagnose(
            component_id="json_parser",
            category=SubsystemCategory.RUNTIME_COMPONENT,
            error_or_exception=ValueError("Invalid JSON token at position 4"),
        )

        # High host CPU/RAM must NOT mask the application error as hardware exhaustion!
        assert report.failure_classification != FailureCategory.RESOURCE_EXHAUSTED
        assert any("High host resource pressure" in h for h in report.hypotheses)


# ---------------------------------------------------------------------------
# Requirement 12: Worker Safety on Probe Timeout
# ---------------------------------------------------------------------------
def test_audit_worker_safety_on_timeout():
    coord = OperationalHealthCoordinator()

    def hung_probe(**kwargs):
        time.sleep(0.5)
        return True, "Finished"

    coord.register_functional_prober("hung_subsystem", hung_probe)

    ok, msg = coord.run_functional_probe(
        component_id="hung_subsystem",
        category=SubsystemCategory.RUNTIME_COMPONENT,
        timeout_seconds=0.05,
    )

    assert ok is False
    assert "Probe deadline exceeded" in msg

    obs = coord.get_health("hung_subsystem")
    assert obs is not None
    assert obs.state == HealthState.UNAVAILABLE
    assert obs.failure_classification == FailureCategory.TIMEOUT
    # Worker safety: Thread is not assumed killed, worker accounting acknowledges outstanding thread
    assert any("Worker cancellation signaled" in e for e in obs.evidence)


# ---------------------------------------------------------------------------
# Requirement 13: Incident Memory Deduplication for Flapping Components
# ---------------------------------------------------------------------------
def test_audit_incident_memory_deduplication(memory_store, event_bus):
    store = IncidentMemoryStore(memory_store=memory_store, event_bus=event_bus)

    comp = "flapping_service_x"

    # Simulate 5 rapid degradation events for the same component
    for i in range(5):
        store.record_incident(
            component_id=comp,
            category=SubsystemCategory.RUNTIME_COMPONENT,
            symptoms=[f"Error burst iteration {i}"],
            verified_causes=["Transient connection reset"],
            deduplicate_open=True,
        )

    # Deduplication links into 1 open incident record instead of 5 unbounded records
    open_incidents = store.list_incidents(component_id=comp, resolved=False)
    assert len(open_incidents) == 1
    assert open_incidents[0].occurrence_count == 5

    # Total failure count accurately reflects all 5 occurrences
    assert store.count_recent_failures(comp, window_seconds=300.0) == 5

    # Persistence across reload: A new store instance hydrades from SqliteMemoryStore
    fresh_store = IncidentMemoryStore(memory_store=memory_store, event_bus=event_bus)
    loaded_incidents = fresh_store.list_incidents(component_id=comp, resolved=False)
    assert len(loaded_incidents) >= 1
    assert loaded_incidents[0].occurrence_count == 5


# ---------------------------------------------------------------------------
# Requirement 14: Secret Scrubbing in Incident Memory
# ---------------------------------------------------------------------------
def test_audit_secret_scrubbing_in_incident_memory(memory_store, event_bus):
    coord = OperationalMaintenanceCoordinator(event_bus=event_bus, memory_store=memory_store)

    secret_auth_token = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.super_secret_payload"
    secret_key = "sk-ant-api03-abcdef1234567890abcdef1234567890abcdef"
    raw_error = f"Authentication rejected using token={secret_auth_token} and apiKey={secret_key}"

    diag, plan, _ = coord.handle_component_failure(
        component_id="api_gateway",
        category=SubsystemCategory.PROVIDER,
        error_or_exception=raw_error,
        auto_recover=False,
    )

    # 1. Scrubbed in DiagnosticReport
    assert secret_auth_token not in diag.observed_symptoms[0]
    assert secret_key not in diag.observed_symptoms[0]
    assert "[REDACTED_CREDENTIAL]" in diag.observed_symptoms[0]

    # 2. Scrubbed in IncidentRecord
    inc = coord.incident_store.list_incidents(component_id="api_gateway")[0]
    assert secret_auth_token not in inc.symptoms[0]
    assert secret_key not in inc.symptoms[0]
    assert "[REDACTED_CREDENTIAL]" in inc.symptoms[0]

    # 3. Scrubbed in SQLite MemoryStore records
    persisted_memories = memory_store.search("api_gateway")
    for m in persisted_memories:
        assert secret_auth_token not in m.content
        assert secret_key not in m.content


# ---------------------------------------------------------------------------
# Requirement 15: RuntimeManager Lifecycle Integration
# ---------------------------------------------------------------------------
def test_audit_runtimemanager_lifecycle_integration(temp_dir, event_bus):
    appdata = os.path.join(temp_dir, "appdata_rt")
    os.makedirs(appdata, exist_ok=True)

    runtime = JevRuntime(
        appdata_dir=appdata,
        event_bus=event_bus,
        enable_heartbeat=True,
        enable_project_watchers=False,
        enable_native_watchers=False,
    )

    # Idempotent startup
    runtime.start()
    assert runtime.is_running is True
    # Duplicate start does not crash or spawn duplicate loops
    runtime.start()
    assert runtime.is_running is True

    # Operational health report is accessible
    report = runtime.get_operational_health_report()
    assert "system_health" in report
    assert "components" in report

    # Clean shutdown
    runtime.stop()
    assert runtime.is_running is False
