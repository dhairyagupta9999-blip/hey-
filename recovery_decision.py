"""Operational Recovery Decision Engine and Autonomous Maintenance Policy for Hey Jev (Phase 26).

Implements:
- OperationalRecoveryAction: NO_ACTION, RETRY, RECONCILE_STATE, RESTART_COMPONENT,
  ROLLBACK_DEPLOYMENT, PROPOSE_REPAIR, REQUEST_APPROVAL, ESCALATE_TO_USER
- Autonomous Maintenance Allowlist: Low-risk, scoped, idempotent, bounded, verified operations.
- Deterministic Policy Enforcement: LLM recommendations and external inputs can NEVER override
  deterministic policy boundaries. High-risk, irreversible, or unverified operations strictly require approval.
- Anti-Looping & Cycle Protection: Failure fingerprinting, exponential backoff, attempt bounding,
  and automatic escalation on persistent failure patterns.
"""
from __future__ import annotations

import enum
import hashlib
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from event_bus import EventBus
from failure_diagnosis import DiagnosticReport
from memory_model import sanitize_content
from operational_health import HealthObservation, HealthState, SubsystemCategory
from reliability_model import FailureCategory

logger = logging.getLogger(__name__)


class OperationalRecoveryAction(str, enum.Enum):
    """Canonical recovery actions available to Hey Jev."""
    NO_ACTION = "NO_ACTION"
    RETRY = "RETRY"
    RECONCILE_STATE = "RECONCILE_STATE"
    RESTART_COMPONENT = "RESTART_COMPONENT"
    ROLLBACK_DEPLOYMENT = "ROLLBACK_DEPLOYMENT"
    PROPOSE_REPAIR = "PROPOSE_REPAIR"
    REQUEST_APPROVAL = "REQUEST_APPROVAL"
    ESCALATE_TO_USER = "ESCALATE_TO_USER"


# Strictly deterministic allowlist for autonomous (unattended) execution.
# An action on this list can only execute if its specific risk, attempt limits,
# and reversibility invariants are satisfied.
AUTONOMOUS_MAINTENANCE_ALLOWLIST: frozenset[OperationalRecoveryAction] = frozenset({
    OperationalRecoveryAction.NO_ACTION,
    OperationalRecoveryAction.RETRY,
    OperationalRecoveryAction.RECONCILE_STATE,
    OperationalRecoveryAction.RESTART_COMPONENT,
    OperationalRecoveryAction.ROLLBACK_DEPLOYMENT,
})

# Actions that intrinsically modify code, configuration, or security policies and MUST NOT run autonomously.
STRICTLY_GATED_ACTIONS: frozenset[OperationalRecoveryAction] = frozenset({
    OperationalRecoveryAction.PROPOSE_REPAIR,
    OperationalRecoveryAction.REQUEST_APPROVAL,
    OperationalRecoveryAction.ESCALATE_TO_USER,
})

# Privileged or security-critical system targets that can NEVER be autonomously restarted or modified
PRIVILEGED_COMPONENT_PREFIXES: Tuple[str, ...] = (
    "safety_",
    "security_",
    "permission_",
    "auth_",
    "system_core",
    "kernel",
)

PRIVILEGED_COMPONENTS: frozenset[str] = frozenset({
    "safety_engine",
    "security_engine",
    "permission_manager",
    "auth_provider",
    "kernel",
    "system_core",
    "root",
    "security",
    "auth",
})


def is_privileged_target(component_id: str) -> bool:
    """Determine whether a component is a privileged security/system core component."""
    cid = component_id.strip().lower()
    return cid in PRIVILEGED_COMPONENTS or any(cid.startswith(p) for p in PRIVILEGED_COMPONENT_PREFIXES)


@dataclass
class RecoveryPlanV26:
    """A deterministic recovery plan formulated for an observed failure or degradation."""
    plan_id: str
    component_id: str
    action: OperationalRecoveryAction
    reason: str
    risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL
    requires_approval: bool
    autonomous_permitted: bool
    attempt_number: int
    backoff_delay_seconds: float = 0.0
    steps: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "component_id": self.component_id,
            "action": self.action.value,
            "reason": self.reason,
            "risk_level": self.risk_level,
            "requires_approval": self.requires_approval,
            "autonomous_permitted": self.autonomous_permitted,
            "attempt_number": self.attempt_number,
            "backoff_delay_seconds": round(self.backoff_delay_seconds, 2),
            "steps": list(self.steps),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


class OperationalRecoveryEngine:
    """Evaluates diagnostic findings and enforces deterministic autonomous maintenance policies."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        max_attempts_per_fingerprint: int = 3,
        max_component_cycles: int = 4,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
    ) -> None:
        self.event_bus = event_bus or EventBus()
        self.max_attempts = max_attempts_per_fingerprint
        self.max_component_cycles = max_component_cycles
        self.base_backoff = base_backoff_seconds
        self.max_backoff = max_backoff_seconds
        self._lock = threading.RLock()
        self._action_history: Dict[str, List[RecoveryPlanV26]] = {}  # fingerprint -> prior plans
        self._component_history: Dict[str, List[RecoveryPlanV26]] = {}  # component_id -> prior plans
        self._action_executors: Dict[OperationalRecoveryAction, Callable[..., Tuple[bool, Optional[str]]]] = {}

    def register_action_executor(
        self,
        action: OperationalRecoveryAction,
        executor: Callable[..., Tuple[bool, Optional[str]]],
    ) -> None:
        """Register a concrete execution handler for a recovery action."""
        with self._lock:
            self._action_executors[action] = executor

    def compute_fingerprint(
        self,
        component_id: str,
        category: FailureCategory,
        action: OperationalRecoveryAction,
        verified_cause: str = "",
    ) -> str:
        """Compute deterministic failure and recovery fingerprint."""
        raw = f"{component_id}:{category.value}:{action.value}:{verified_cause}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def compute_backoff(self, attempt: int) -> float:
        """Compute exponential backoff with bounded jitter."""
        factor = min(self.max_backoff, self.base_backoff * (2 ** max(0, attempt - 1)))
        jitter = random.uniform(0.8, 1.2)
        return min(self.max_backoff, factor * jitter)

    def formulate_plan(
        self,
        diagnostic: DiagnosticReport,
        health_observation: Optional[HealthObservation] = None,
        suggested_action: Optional[str] = None,
    ) -> RecoveryPlanV26:
        """Deterministically formulate a RecoveryPlan based on diagnostic evidence and safety policy."""
        with self._lock:
            comp_id = diagnostic.component_id
            classification = diagnostic.failure_classification
            verified_cause = diagnostic.verified_causes[0] if diagnostic.verified_causes else ""

            # 1. Determine raw intended action
            raw_action_str = suggested_action or diagnostic.recommended_recovery_action
            try:
                intended_action = OperationalRecoveryAction(raw_action_str)
            except ValueError:
                intended_action = OperationalRecoveryAction.RETRY

            # 2. Compute fingerprint and check loop / cycle limits
            fingerprint = self.compute_fingerprint(comp_id, classification, intended_action, verified_cause)
            prior_plans = self._action_history.get(fingerprint, [])
            current_attempt = len(prior_plans) + 1

            # Cycle Breaker 1: Same fingerprint repeated attempts threshold
            if current_attempt > self.max_attempts:
                plan = RecoveryPlanV26(
                    plan_id=f"plan_esc_{fingerprint}_{current_attempt}",
                    component_id=comp_id,
                    action=OperationalRecoveryAction.ESCALATE_TO_USER,
                    reason=(
                        f"Autonomous retry threshold exceeded ({self.max_attempts} attempts). "
                        "Loop breaker triggered; escalating to human operator."
                    ),
                    risk_level="HIGH",
                    requires_approval=True,
                    autonomous_permitted=False,
                    attempt_number=current_attempt,
                    steps=[{"step": "notify_operator", "fingerprint": fingerprint}],
                    metadata={
                        "fingerprint": fingerprint,
                        "original_action": intended_action.value,
                        "failure_category": classification.value,
                    },
                )
                prior_plans.append(plan)
                self._action_history[fingerprint] = prior_plans
                comp_plans = self._component_history.setdefault(comp_id, [])
                comp_plans.append(plan)
                return plan

            # Cycle Breaker 2: Multi-failure alternating cycle protection for the same component
            now = time.time()
            comp_prior_plans = self._component_history.get(comp_id, [])
            recent_comp_plans = [p for p in comp_prior_plans if (now - p.created_at) <= 600.0]
            distinct_categories = {
                p.metadata.get("failure_category")
                for p in recent_comp_plans
                if p.metadata.get("failure_category")
            }
            distinct_categories.add(classification.value)

            if len(recent_comp_plans) >= self.max_component_cycles or (
                len(distinct_categories) >= 2 and len(recent_comp_plans) >= 3
            ):
                plan = RecoveryPlanV26(
                    plan_id=f"plan_cycle_{comp_id}_{len(recent_comp_plans) + 1}",
                    component_id=comp_id,
                    action=OperationalRecoveryAction.ESCALATE_TO_USER,
                    reason=(
                        f"Multi-failure alternating cycle detected for component '{comp_id}' "
                        f"({len(recent_comp_plans)} recovery attempts across {len(distinct_categories)} failure categories). "
                        "Escalating to human operator to prevent oscillating recovery loop."
                    ),
                    risk_level="HIGH",
                    requires_approval=True,
                    autonomous_permitted=False,
                    attempt_number=len(recent_comp_plans) + 1,
                    steps=[{"step": "notify_operator", "component_id": comp_id}],
                    metadata={
                        "component_cycle_breaker": True,
                        "failure_category": classification.value,
                        "distinct_categories": list(distinct_categories),
                    },
                )
                comp_prior_plans.append(plan)
                self._component_history[comp_id] = comp_prior_plans
                return plan

            # 3. Deterministic Policy Enforcement:
            # Rule 3A: Security, authentication, or permission boundary blocks strictly require human escalation
            if classification in (
                FailureCategory.PERMISSION_DENIED,
                FailureCategory.POLICY_BLOCKED,
                FailureCategory.AUTHENTICATION,
                FailureCategory.SECURITY_EVENT,
            ):
                intended_action = OperationalRecoveryAction.ESCALATE_TO_USER
                requires_approval = True
                autonomous_permitted = False
                risk_level = "CRITICAL"
                reason = f"Security or permission boundary block: {classification.value}."

            # Rule 3B: Privileged / security critical components CANNOT be modified or restarted autonomously
            elif is_privileged_target(comp_id):
                intended_action = OperationalRecoveryAction.REQUEST_APPROVAL
                requires_approval = True
                autonomous_permitted = False
                risk_level = "CRITICAL"
                reason = f"Target '{comp_id}' is a privileged system component; autonomous modification/restart disallowed without operator approval."

            elif intended_action == OperationalRecoveryAction.PROPOSE_REPAIR:
                # Code/config repair must go through proposal lifecycle, not direct unattended execution
                requires_approval = True
                autonomous_permitted = False
                risk_level = "MEDIUM"
                reason = "Repair proposal requires review before code/configuration modification."

            elif intended_action in STRICTLY_GATED_ACTIONS:
                requires_approval = True
                autonomous_permitted = False
                risk_level = diagnostic.risk_level
                reason = "Action is classified in STRICTLY_GATED_ACTIONS; human authorization required."

            # Rule 3C: Target-specific allowlist check for ROLLBACK_DEPLOYMENT
            elif intended_action == OperationalRecoveryAction.ROLLBACK_DEPLOYMENT and diagnostic.category != SubsystemCategory.DEPLOYED_CAPABILITY:
                intended_action = OperationalRecoveryAction.REQUEST_APPROVAL
                requires_approval = True
                autonomous_permitted = False
                risk_level = "HIGH"
                reason = f"Component '{comp_id}' is category {diagnostic.category.value}, not DEPLOYED_CAPABILITY; rollback requires human authorization."

            # Rule 3D: Target-specific allowlist check for RESTART_COMPONENT
            elif intended_action == OperationalRecoveryAction.RESTART_COMPONENT and diagnostic.category not in (
                SubsystemCategory.WATCHER,
                SubsystemCategory.BACKGROUND_TASK,
                SubsystemCategory.RUNTIME_COMPONENT,
            ):
                intended_action = OperationalRecoveryAction.REQUEST_APPROVAL
                requires_approval = True
                autonomous_permitted = False
                risk_level = "HIGH"
                reason = f"Component '{comp_id}' of category {diagnostic.category.value} is not an eligible autonomous restart target."

            elif intended_action in AUTONOMOUS_MAINTENANCE_ALLOWLIST:
                # Validate low-risk invariants
                requires_approval = False
                autonomous_permitted = True
                risk_level = diagnostic.risk_level or "LOW"
                reason = f"Autonomous maintenance allowed for {intended_action.value} under bounded policy."

            else:
                # Fail-closed fallback: unlisted actions require approval
                intended_action = OperationalRecoveryAction.REQUEST_APPROVAL
                requires_approval = True
                autonomous_permitted = False
                risk_level = "HIGH"
                reason = "Unlisted recovery action failed closed; approval required."

            backoff = self.compute_backoff(current_attempt) if intended_action == OperationalRecoveryAction.RETRY else 0.0

            steps: List[Dict[str, Any]] = []
            if intended_action == OperationalRecoveryAction.RETRY:
                steps.append({"step": "backoff_sleep", "delay_seconds": round(backoff, 2)})
                steps.append({"step": "re_execute_probe_or_task", "component_id": comp_id})
            elif intended_action == OperationalRecoveryAction.RECONCILE_STATE:
                steps.append({"step": "inspect_storage_truth", "component_id": comp_id})
                steps.append({"step": "realign_in_memory_state", "component_id": comp_id})
            elif intended_action == OperationalRecoveryAction.RESTART_COMPONENT:
                steps.append({"step": "stop_component", "component_id": comp_id})
                steps.append({"step": "start_component", "component_id": comp_id})
                steps.append({"step": "verify_component_health", "component_id": comp_id})
            elif intended_action == OperationalRecoveryAction.ROLLBACK_DEPLOYMENT:
                steps.append({"step": "request_deployment_rollback", "component_id": comp_id})
                steps.append({"step": "verify_restored_state", "component_id": comp_id})
            elif intended_action == OperationalRecoveryAction.PROPOSE_REPAIR:
                steps.append({"step": "generate_extension_repair_proposal", "component_id": comp_id})
            elif intended_action == OperationalRecoveryAction.ESCALATE_TO_USER:
                steps.append({"step": "emit_critical_operator_alert", "component_id": comp_id})

            plan = RecoveryPlanV26(
                plan_id=f"plan_{fingerprint}_{current_attempt}",
                component_id=comp_id,
                action=intended_action,
                reason=reason,
                risk_level=risk_level,
                requires_approval=requires_approval,
                autonomous_permitted=autonomous_permitted,
                attempt_number=current_attempt,
                backoff_delay_seconds=backoff,
                steps=steps,
                metadata={
                    "fingerprint": fingerprint,
                    "verified_cause": verified_cause,
                    "failure_category": classification.value,
                },
            )

            prior_plans.append(plan)
            self._action_history[fingerprint] = prior_plans
            comp_prior_plans = self._component_history.setdefault(comp_id, [])
            comp_prior_plans.append(plan)

            self.event_bus.emit(
                "recovery.plan_formulated",
                comp_id,
                data=plan.to_dict(),
            )
            return plan

    def execute_plan(
        self,
        plan: RecoveryPlanV26,
        user_approved: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """Safely execute a recovery plan, strictly enforcing authorization gates."""
        with self._lock:
            # 1. Gate Check
            if plan.requires_approval and not user_approved:
                err = f"Execution blocked: Plan '{plan.plan_id}' requires explicit approval but none was granted."
                logger.warning(err)
                return False, err

            if not plan.autonomous_permitted and not user_approved:
                err = f"Execution blocked: Action '{plan.action.value}' is not on the autonomous allowlist."
                logger.warning(err)
                return False, err

            # 2. Sleep backoff if specified
            if plan.backoff_delay_seconds > 0:
                time.sleep(min(plan.backoff_delay_seconds, 2.0))  # Cap sleep in tests/local execution

            # 3. Dispatch to registered executor
            executor = self._action_executors.get(plan.action)
            if not executor:
                err = f"No concrete executor registered for recovery action '{plan.action.value}'."
                logger.error(err)
                return False, err

            self.event_bus.emit(
                "recovery.execution_started",
                plan.component_id,
                data=plan.to_dict(),
            )

            try:
                ok, msg = executor(component_id=plan.component_id, plan=plan)
                clean_msg = sanitize_content(msg or "")

                self.event_bus.emit(
                    "recovery.execution_finished",
                    plan.component_id,
                    data={
                        "plan_id": plan.plan_id,
                        "action": plan.action.value,
                        "success": ok,
                        "message": clean_msg,
                    },
                )
                return ok, clean_msg
            except Exception as e:
                err_msg = f"Recovery executor error: {sanitize_content(str(e))}"
                logger.exception(err_msg)
                self.event_bus.emit(
                    "recovery.execution_finished",
                    plan.component_id,
                    data={
                        "plan_id": plan.plan_id,
                        "action": plan.action.value,
                        "success": False,
                        "message": err_msg,
                    },
                )
                return False, err_msg
