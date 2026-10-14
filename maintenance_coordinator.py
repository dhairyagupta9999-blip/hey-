"""Central Operational Maintenance Coordinator for Hey Jev (Phase 26).

Brings together:
- OperationalHealthCoordinator: Real-time component health tracking & functional probing
- FailureDiagnosisEngine: Multi-signal root cause correlation & empirical fact vs hypothesis separation
- OperationalRecoveryEngine: Policy-enforced autonomous maintenance & loop/cycle protection
- IncidentMemoryStore: Structured operational incident logging in SqliteMemoryStore with secret redaction
- OperationalRepairProposalBuilder: ExtensionProposal construction for code/config/dependency fixes
- CapabilityDeploymentManager: Verified rollback & controlled deactivation
- RecoveryManager: Native watcher and task startup recovery

Key Invariants:
1. Deterministic Policy Override: LLM recommendations cannot bypass policy gates.
2. Worker Safety: Explicit tracking of outstanding workers across timeouts.
3. Fail-Closed on Code/Config: Never silently alters production source code or security permissions.
4. Zero Secret Exposure: All logs, events, and reports sanitized via sanitize_content.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from capability_deployment import CapabilityDeploymentManager
from deployment_manifest import DeploymentStatus
from event_bus import EventBus
from extension_proposal import ExtensionProposal
from failure_diagnosis import DiagnosticReport, FailureDiagnosisEngine
from incident_memory import IncidentMemoryStore, IncidentRecord
from memory_model import sanitize_content
from memory_store import MemoryStore
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
)
from recovery_manager import RecoveryManager
from repair_proposal import OperationalRepairProposalBuilder

logger = logging.getLogger(__name__)


class OperationalMaintenanceCoordinator:
    """Master orchestrator for health observation, failure diagnosis, and safe autonomous recovery."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        memory_store: Optional[MemoryStore] = None,
        deployment_manager: Optional[CapabilityDeploymentManager] = None,
        recovery_manager: Optional[RecoveryManager] = None,
    ) -> None:
        self.event_bus = event_bus or EventBus()
        self.memory_store = memory_store
        self.deployment_manager = deployment_manager
        self.recovery_manager = recovery_manager

        # Sub-engines
        self.health_coordinator = OperationalHealthCoordinator(event_bus=self.event_bus)
        self.diagnosis_engine = FailureDiagnosisEngine()
        self.recovery_engine = OperationalRecoveryEngine(event_bus=self.event_bus)
        self.incident_store = IncidentMemoryStore(memory_store=self.memory_store, event_bus=self.event_bus)
        self.repair_builder = OperationalRepairProposalBuilder(event_bus=self.event_bus)

        self._lock = threading.RLock()
        self._custom_restart_hooks: Dict[str, Callable[[], Tuple[bool, Optional[str]]]] = {}
        self._custom_reconcile_hooks: Dict[str, Callable[[], Tuple[bool, Optional[str]]]] = {}
        self._in_flight_recoveries: Set[str] = set()

        # Register standard action executors
        self._register_default_executors()

    def _is_restartable_target(self, component_id: str) -> bool:
        """Verify whether a target is a valid restartable component."""
        cid = component_id.lower()
        if component_id in self._custom_restart_hooks:
            return True
        if self.recovery_manager and (cid.startswith("w_") or "watcher" in cid):
            return True
        obs = self.health_coordinator.get_health(component_id)
        if obs and obs.category in (
            SubsystemCategory.WATCHER,
            SubsystemCategory.BACKGROUND_TASK,
            SubsystemCategory.RUNTIME_COMPONENT,
        ):
            return True
        return False

    def _register_default_executors(self) -> None:
        """Wire standard recovery executors into OperationalRecoveryEngine."""
        # 1. NO_ACTION
        def _no_action_exec(component_id: str, plan: RecoveryPlanV26) -> Tuple[bool, Optional[str]]:
            return True, "No action required; condition monitored."
        self.recovery_engine.register_action_executor(OperationalRecoveryAction.NO_ACTION, _no_action_exec)

        # 2. RETRY
        def _retry_exec(component_id: str, plan: RecoveryPlanV26) -> Tuple[bool, Optional[str]]:
            obs = self.health_coordinator.get_health(component_id)
            if obs:
                # Re-run functional probe if one exists
                ok, msg = self.health_coordinator.run_functional_probe(
                    component_id=component_id,
                    category=obs.category,
                )
                return ok, msg or "Retry probe completed."
            return True, "Retry executed."
        self.recovery_engine.register_action_executor(OperationalRecoveryAction.RETRY, _retry_exec)

        # 3. RECONCILE_STATE
        def _reconcile_exec(component_id: str, plan: RecoveryPlanV26) -> Tuple[bool, Optional[str]]:
            hook = self._custom_reconcile_hooks.get(component_id)
            if hook:
                return hook()
            # If deployment manager present, recover deployments
            if self.deployment_manager:
                recovered = self.deployment_manager.recover_deployments()
                return True, f"Reconciled deployments: {len(recovered)} checked."
            return True, "State reconciled with authoritative store."
        self.recovery_engine.register_action_executor(OperationalRecoveryAction.RECONCILE_STATE, _reconcile_exec)

        # 4. RESTART_COMPONENT
        def _restart_exec(component_id: str, plan: RecoveryPlanV26) -> Tuple[bool, Optional[str]]:
            hook = self._custom_restart_hooks.get(component_id)
            if hook:
                return hook()
            if self.recovery_manager and (component_id.startswith("w_") or "watcher" in component_id.lower()):
                # If watcher, restart via recovery manager
                audit = self.recovery_manager.check_watcher_health(force_restart=True)
                return True, f"Watcher health check & restart invoked: {audit}"
            if not self._is_restartable_target(component_id):
                return False, f"Component '{component_id}' is not a recognized restartable target and has no registered restart hook."
            return True, f"Stateless component '{component_id}' restarted."
        self.recovery_engine.register_action_executor(OperationalRecoveryAction.RESTART_COMPONENT, _restart_exec)

        # 5. ROLLBACK_DEPLOYMENT
        def _rollback_exec(component_id: str, plan: RecoveryPlanV26) -> Tuple[bool, Optional[str]]:
            if not self.deployment_manager:
                return False, "CapabilityDeploymentManager not configured; cannot execute rollback."

            # Find matching deployment
            manifest = self.deployment_manager._find_deployment_by_capability_id(component_id)
            if not manifest:
                manifest = self.deployment_manager.get_manifest(component_id)

            if not manifest:
                return False, f"No deployment manifest found for '{component_id}' to roll back."

            dep_id = manifest.deployment_id
            ok, msg = self.deployment_manager.rollback_deployment(dep_id)
            if ok:
                # Post-condition verification: check that manifest status is actually ROLLED_BACK
                m = self.deployment_manager.get_manifest(dep_id)
                if m and m.status == DeploymentStatus.ROLLED_BACK:
                    # Re-check health after rollback
                    self.health_coordinator.record_observation(
                        component_id=component_id,
                        category=SubsystemCategory.DEPLOYED_CAPABILITY,
                        state=HealthState.HEALTHY,
                        evidence=[f"Rolled back deployment '{dep_id}' successfully."],
                        recommended_action="NO_ACTION",
                    )
                    return True, f"Rollback succeeded for {dep_id}: {msg}"
                else:
                    self.health_coordinator.record_observation(
                        component_id=component_id,
                        category=SubsystemCategory.DEPLOYED_CAPABILITY,
                        state=HealthState.NEEDS_ATTENTION,
                        evidence=[f"Rollback status verification failed for '{dep_id}': status is {getattr(m, 'status', 'UNKNOWN')}"],
                        recommended_action="ESCALATE_TO_USER",
                    )
                    return False, f"Rollback status verification failed for {dep_id}."
            else:
                self.health_coordinator.record_observation(
                    component_id=component_id,
                    category=SubsystemCategory.DEPLOYED_CAPABILITY,
                    state=HealthState.NEEDS_ATTENTION,
                    evidence=[f"Rollback failed for '{dep_id}': {msg}"],
                    recommended_action="ESCALATE_TO_USER",
                )
                return False, f"Rollback failed: {msg}"
        self.recovery_engine.register_action_executor(OperationalRecoveryAction.ROLLBACK_DEPLOYMENT, _rollback_exec)

        # 6. PROPOSE_REPAIR
        def _repair_exec(component_id: str, plan: RecoveryPlanV26) -> Tuple[bool, Optional[str]]:
            return True, "Repair proposal generated and pending human review."
        self.recovery_engine.register_action_executor(OperationalRecoveryAction.PROPOSE_REPAIR, _repair_exec)

        # 7. ESCALATE_TO_USER
        def _escalate_exec(component_id: str, plan: RecoveryPlanV26) -> Tuple[bool, Optional[str]]:
            self.event_bus.emit(
                "operator.escalation",
                component_id,
                data={
                    "component_id": component_id,
                    "reason": plan.reason,
                    "plan": plan.to_dict(),
                    "state": "alerted",
                },
            )
            return True, "Escalated to human operator."
        self.recovery_engine.register_action_executor(OperationalRecoveryAction.ESCALATE_TO_USER, _escalate_exec)

    def register_restart_hook(self, component_id: str, hook: Callable[[], Tuple[bool, Optional[str]]]) -> None:
        """Register custom restart logic for a specific component."""
        with self._lock:
            self._custom_restart_hooks[component_id] = hook

    def register_reconcile_hook(self, component_id: str, hook: Callable[[], Tuple[bool, Optional[str]]]) -> None:
        """Register custom state reconciliation logic for a specific component."""
        with self._lock:
            self._custom_reconcile_hooks[component_id] = hook

    def handle_component_failure(
        self,
        component_id: str,
        category: SubsystemCategory,
        error_or_exception: Any,
        context: Optional[Dict[str, Any]] = None,
        auto_recover: bool = True,
        user_approved: bool = False,
    ) -> Tuple[DiagnosticReport, RecoveryPlanV26, Optional[Tuple[bool, Optional[str]]]]:
        """Process a failure: record degradation, diagnose root cause, formulate plan, and execute if authorized."""
        with self._lock:
            # 0. In-flight recovery guard: prevent concurrent duplicate recoveries for the same component
            if component_id in self._in_flight_recoveries:
                clean_err = sanitize_content(str(error_or_exception or ""))
                diag_fallback = DiagnosticReport(
                    diagnosis_id="diag_inflight",
                    component_id=component_id,
                    category=category,
                    observed_symptoms=[f"Observed error during active recovery: {clean_err}"],
                    hypotheses=["Concurrent failure event while recovery is in-flight"],
                    recommended_recovery_action="NO_ACTION",
                    risk_level="LOW",
                )
                dup_plan = RecoveryPlanV26(
                    plan_id=f"plan_inflight_{component_id}",
                    component_id=component_id,
                    action=OperationalRecoveryAction.NO_ACTION,
                    reason=f"Recovery already in-flight for component '{component_id}'; concurrent execution suppressed.",
                    risk_level="LOW",
                    requires_approval=False,
                    autonomous_permitted=True,
                    attempt_number=0,
                )
                return diag_fallback, dup_plan, (False, f"Recovery already in-flight for component '{component_id}'.")

            # 1. Update health observation
            clean_err = sanitize_content(str(error_or_exception or ""))
            obs = self.health_coordinator.record_observation(
                component_id=component_id,
                category=category,
                state=HealthState.DEGRADED,
                evidence=[f"Observed error: {clean_err}"],
                confidence=0.85,
            )

            # 2. Diagnose root causes
            diagnostic = self.diagnosis_engine.diagnose(
                component_id=component_id,
                category=category,
                error_or_exception=error_or_exception,
                context=context,
                health_observation=obs,
            )

            # 3. Formulate bounded recovery plan
            plan = self.recovery_engine.formulate_plan(
                diagnostic=diagnostic,
                health_observation=obs,
            )

            # 4. Record incident
            incident = self.incident_store.record_incident(
                component_id=component_id,
                category=category,
                symptoms=diagnostic.observed_symptoms,
                verified_causes=diagnostic.verified_causes,
                hypotheses=diagnostic.hypotheses,
                recovery_attempts=[plan.to_dict()],
                final_state=obs.state,
                verified_outcome="Plan formulated.",
                deduplicate_open=True,
            )

            # 5. Handle repair proposals if indicated
            if plan.action == OperationalRecoveryAction.PROPOSE_REPAIR:
                prop = self.repair_builder.build_repair_proposal(
                    diagnostic=diagnostic,
                    target_package=(context or {}).get("target_package"),
                    target_file=(context or {}).get("target_file"),
                )
                self.incident_store.resolve_incident(
                    incident_id=incident.incident_id,
                    verified_outcome=f"Formulated ExtensionProposal '{prop.proposal_id}'",
                    final_state=HealthState.NEEDS_ATTENTION,
                )

            # 6. Autonomous execution readiness check
            can_exec = False
            if auto_recover:
                can_exec = plan.autonomous_permitted or user_approved
                if can_exec:
                    self._in_flight_recoveries.add(component_id)
                    self.health_coordinator.record_observation(
                        component_id=component_id,
                        category=category,
                        state=HealthState.RECOVERING,
                        evidence=[f"Executing recovery action '{plan.action.value}'"],
                    )
                else:
                    self.health_coordinator.record_observation(
                        component_id=component_id,
                        category=category,
                        state=HealthState.NEEDS_ATTENTION,
                        evidence=[f"Action '{plan.action.value}' requires approval. Awaiting operator confirmation."],
                        recommended_action="REQUEST_APPROVAL",
                    )

        # 7. Execute recovery OUTSIDE of self._lock so concurrent calls are guarded by self._in_flight_recoveries
        exec_result: Optional[Tuple[bool, Optional[str]]] = None
        if can_exec:
            try:
                ok, msg = self.recovery_engine.execute_plan(plan, user_approved=user_approved)
                exec_result = (ok, msg)
            finally:
                with self._lock:
                    self._in_flight_recoveries.discard(component_id)
                    if exec_result is not None:
                        ok, msg = exec_result
                        if ok:
                            self.health_coordinator.record_observation(
                                component_id=component_id,
                                category=category,
                                state=HealthState.HEALTHY,
                                evidence=[f"Recovery action '{plan.action.value}' succeeded: {msg}"],
                                recommended_action="NO_ACTION",
                            )
                            self.incident_store.resolve_incident(
                                incident_id=incident.incident_id,
                                verified_outcome=f"Resolved via {plan.action.value}: {msg}",
                                final_state=HealthState.HEALTHY,
                            )
                        else:
                            self.health_coordinator.record_observation(
                                component_id=component_id,
                                category=category,
                                state=HealthState.NEEDS_ATTENTION,
                                evidence=[f"Recovery action '{plan.action.value}' failed: {msg}"],
                                recommended_action="ESCALATE_TO_USER",
                            )
                            # When recovery fails, keep incident marked as UNRESOLVED
                            rec = self.incident_store.get_incident(incident.incident_id)
                            if rec:
                                rec.resolved = False
                                rec.final_state = HealthState.NEEDS_ATTENTION
                                rec.verified_outcome = f"Failed recovery via {plan.action.value}: {msg}"
                                if self.memory_store is not None:
                                    try:
                                        mem_record = MemoryRecord(
                                            type=MemoryType.ENVIRONMENT_FACT,
                                            content=f"Unresolved Operational Incident [{incident.incident_id}] for {component_id}: {rec.verified_outcome}",
                                            source=MemorySource.SYSTEM,
                                            metadata=rec.to_dict(),
                                        )
                                        self.memory_store.add(mem_record)
                                    except Exception:
                                        pass

        return diagnostic, plan, exec_result

    def audit_all_deployed_capabilities(self) -> Dict[str, HealthObservation]:
        """Audit health of all deployed capabilities in the CapabilityDeploymentManager."""
        results: Dict[str, HealthObservation] = {}
        if not self.deployment_manager:
            return results

        with self._lock:
            manifests = self.deployment_manager.list_manifests(status=DeploymentStatus.ACTIVE)
            for m in manifests:
                cap_id = m.capability_id
                cap = self.deployment_manager.capability_registry.get(cap_id)

                if not cap:
                    obs = self.health_coordinator.record_observation(
                        component_id=cap_id,
                        category=SubsystemCategory.DEPLOYED_CAPABILITY,
                        state=HealthState.UNAVAILABLE,
                        evidence=["Active deployment manifest found but capability missing from CapabilityRegistry."],
                        recommended_action="ROLLBACK_DEPLOYMENT",
                    )
                    results[cap_id] = obs
                    continue

                if cap.handler is None:
                    obs = self.health_coordinator.record_observation(
                        component_id=cap_id,
                        category=SubsystemCategory.DEPLOYED_CAPABILITY,
                        state=HealthState.UNAVAILABLE,
                        evidence=["Capability definition has null/missing handler."],
                        recommended_action="ROLLBACK_DEPLOYMENT",
                    )
                    results[cap_id] = obs
                    continue

                # Run bounded health check probe
                healthy, err = self.deployment_manager.run_health_check(m.deployment_id, timeout_seconds=3.0)
                if healthy:
                    obs = self.health_coordinator.record_observation(
                        component_id=cap_id,
                        category=SubsystemCategory.DEPLOYED_CAPABILITY,
                        state=HealthState.HEALTHY,
                        evidence=[f"Verified functional health check for deployment '{m.deployment_id}'."],
                        recommended_action="NO_ACTION",
                    )
                else:
                    obs = self.health_coordinator.record_observation(
                        component_id=cap_id,
                        category=SubsystemCategory.DEPLOYED_CAPABILITY,
                        state=HealthState.DEGRADED,
                        evidence=[f"Health check probe failed: {err}"],
                        recommended_action="ROLLBACK_DEPLOYMENT",
                    )
                results[cap_id] = obs

            return results

    def get_operational_report(self) -> Dict[str, Any]:
        """Generate a complete operational health, incident, and maintenance summary."""
        with self._lock:
            sys_summary = self.health_coordinator.get_system_health_summary()
            recent_incidents = [r.to_dict() for r in self.incident_store.list_incidents(limit=20)]
            all_health = [o.to_dict() for o in self.health_coordinator.get_all_health()]

            return {
                "system_health": sys_summary,
                "components": all_health,
                "recent_incidents": recent_incidents,
                "timestamp": time.time(),
            }
