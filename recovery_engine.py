"""Failure Classification V2, Recovery Decision Engine, and Circuit Management for Hey Jev.

Provides:
- FailureClassifierV2: Classifies errors into 21 distinct FailureCategory tiers
- RecoveryDecisionEngine: Deterministically plans safe recoveries (RETRY, RESTART, RECONCILE,
  FALLBACK, COMPENSATE, ROLLBACK, REPLAN, ESCALATE, ABORT)
- Loop and cycle prevention using failure fingerprints, cooldowns, and max recovery limits
- CircuitBreakerManager: Protects providers and tools from cascading failures
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from reliability_model import (
    CircuitBreaker,
    CircuitBreakerState,
    FailureCategory,
    RecoveryPlan,
    RecoveryStrategy,
    RetryPolicy,
)


class FailureClassifierV2:
    """Classifies exceptions, errors, and state anomalies into 21 precise categories."""

    PATTERNS = [
        (re.compile(r"429|rate\s*limit|quota\s*exceeded", re.IGNORECASE), FailureCategory.RATE_LIMIT),
        (re.compile(r"timeout|timed\s*out|deadline\s*exceeded", re.IGNORECASE), FailureCategory.TIMEOUT),
        (re.compile(r"unauthorized|401|403|forbidden|api\s*key|auth", re.IGNORECASE), FailureCategory.AUTHENTICATION),
        (re.compile(r"permission\s*denied|access\s*denied|requires\s*elevation", re.IGNORECASE), FailureCategory.PERMISSION_DENIED),
        (re.compile(r"security\s*event|tampering|injection|blocked\s*by\s*policy", re.IGNORECASE), FailureCategory.SECURITY_EVENT),
        (re.compile(r"policy\s*blocked|disallowed\s*path|root\s*drive", re.IGNORECASE), FailureCategory.POLICY_BLOCKED),
        (re.compile(r"no\s*such\s*process|process\s*terminated|crash|segmentation\s*fault", re.IGNORECASE), FailureCategory.PROCESS_CRASH),
        (re.compile(r"connection\s*reset|network|dns|connection\s*refused|socket", re.IGNORECASE), FailureCategory.NETWORK_FAILURE),
        (re.compile(r"memory\s*error|oom|resource\s*exhausted|cpu\s*overload", re.IGNORECASE), FailureCategory.RESOURCE_EXHAUSTED),
        (re.compile(r"browser\s*closed|tab\s*crashed|cdp|playwright", re.IGNORECASE), FailureCategory.BROWSER_FAILURE),
        (re.compile(r"winerror|windows\s*error|registry|dll", re.IGNORECASE), FailureCategory.WINDOWS_FAILURE),
        (re.compile(r"checksum\s*mismatch|verification\s*failed|unverified", re.IGNORECASE), FailureCategory.VERIFICATION_FAILURE),
        (re.compile(r"mismatch|state\s*inconsistent|file\s*missing", re.IGNORECASE), FailureCategory.STATE_MISMATCH),
        (re.compile(r"unknown\s*state|unreachable\s*status", re.IGNORECASE), FailureCategory.UNKNOWN_STATE),
        (re.compile(r"captcha|human\s*verification|user\s*prompt", re.IGNORECASE), FailureCategory.USER_REQUIRED),
        (re.compile(r"transient|temporary\s*failure|retryable", re.IGNORECASE), FailureCategory.TRANSIENT),
    ]

    @classmethod
    def classify(cls, error_or_exception: Any, context: Optional[Dict[str, Any]] = None) -> FailureCategory:
        """Classify error string or exception into a standard FailureCategory."""
        err_msg = str(error_or_exception or "").strip()
        if not err_msg:
            return FailureCategory.UNKNOWN

        for pattern, cat in cls.PATTERNS:
            if pattern.search(err_msg):
                return cat

        if isinstance(error_or_exception, TimeoutError):
            return FailureCategory.TIMEOUT
        if isinstance(error_or_exception, PermissionError):
            return FailureCategory.PERMISSION_DENIED
        if isinstance(error_or_exception, ConnectionError):
            return FailureCategory.NETWORK_FAILURE

        return FailureCategory.TOOL_FAILURE


class RecoveryDecisionEngine:
    """Evaluates failures and synthesizes safe, bounded RecoveryPlans."""

    def __init__(
        self,
        retry_policy: Optional[RetryPolicy] = None,
        max_recovery_attempts: int = 3,
    ) -> None:
        self.retry_policy = retry_policy or RetryPolicy()
        self.max_recovery_attempts = max_recovery_attempts
        self._recovery_history: Dict[str, List[RecoveryPlan]] = {}  # target_key -> plans

    def plan_recovery(
        self,
        failure: FailureCategory,
        target_id: str,
        attempt_count: int = 1,
        context: Optional[Dict[str, Any]] = None,
    ) -> RecoveryPlan:
        """Deterministically decide recovery strategy and actions."""
        ctx = dict(context or {})
        fingerprint = self._compute_fingerprint(failure, target_id, ctx)

        # Loop detection: check if same fingerprint exceeded bounds
        prior_plans = self._recovery_history.get(fingerprint, [])
        if len(prior_plans) >= self.max_recovery_attempts:
            # Escalation to prevent infinite recovery cycle
            plan = RecoveryPlan(
                failure_id=target_id,
                strategy=RecoveryStrategy.ESCALATE,
                risk="HIGH",
                requires_approval=True,
                steps=[{"action": "escalate_to_user", "reason": "Max recovery attempts exceeded for this failure pattern"}],
            )
            prior_plans.append(plan)
            return plan

        strategy = self._map_category_to_strategy(failure, attempt_count, ctx)

        steps: List[Dict[str, Any]] = []
        requires_approval = False
        risk = "LOW"

        if strategy == RecoveryStrategy.RETRY:
            delay = self.retry_policy.compute_delay(attempt_count)
            steps.append({"action": "sleep_backoff", "delay_seconds": round(delay, 2)})
            steps.append({"action": "retry_operation", "attempt": attempt_count + 1})
        elif strategy == RecoveryStrategy.RECONCILE:
            steps.append({"action": "inspect_external_state", "target": target_id})
            steps.append({"action": "decide_already_applied_or_retry"})
        elif strategy == RecoveryStrategy.FALLBACK:
            fallback_target = ctx.get("fallback_target", "secondary_provider")
            steps.append({"action": "route_to_fallback", "fallback_target": fallback_target})
            risk = "MEDIUM"
        elif strategy == RecoveryStrategy.COMPENSATE:
            steps.append({"action": "execute_compensation", "revert_metadata": ctx.get("revert_metadata", {})})
            risk = "MEDIUM"
        elif strategy == RecoveryStrategy.RESTART:
            steps.append({"action": "restart_subsystem", "subsystem": ctx.get("subsystem", target_id)})
            steps.append({"action": "verify_health"})
        elif strategy == RecoveryStrategy.ESCALATE:
            steps.append({"action": "notify_user", "reason": f"Unrecoverable failure: {failure.value}"})
            requires_approval = True
            risk = "HIGH"
        elif strategy == RecoveryStrategy.ABORT:
            steps.append({"action": "halt_execution", "reason": f"Fatal security or permission block: {failure.value}"})
            risk = "CRITICAL"

        plan = RecoveryPlan(
            failure_id=target_id,
            strategy=strategy,
            steps=steps,
            risk=risk,
            requires_approval=requires_approval,
        )

        prior_plans.append(plan)
        self._recovery_history[fingerprint] = prior_plans
        return plan

    def _map_category_to_strategy(
        self, failure: FailureCategory, attempt_count: int, ctx: Dict[str, Any]
    ) -> RecoveryStrategy:
        """Deterministic mapping from FailureCategory to Strategy."""
        if failure in (FailureCategory.SECURITY_EVENT, FailureCategory.POLICY_BLOCKED):
            return RecoveryStrategy.ABORT

        if failure in (FailureCategory.AUTHENTICATION, FailureCategory.USER_REQUIRED):
            return RecoveryStrategy.ESCALATE

        if failure == FailureCategory.UNKNOWN_STATE:
            return RecoveryStrategy.RECONCILE

        if failure in (FailureCategory.PROCESS_CRASH, FailureCategory.BROWSER_FAILURE):
            return RecoveryStrategy.RESTART if attempt_count <= 2 else RecoveryStrategy.FALLBACK

        if failure in (FailureCategory.RATE_LIMIT, FailureCategory.TIMEOUT):
            if attempt_count <= self.retry_policy.max_attempts:
                return RecoveryStrategy.RETRY
            return RecoveryStrategy.FALLBACK

        if failure == FailureCategory.VERIFICATION_FAILURE:
            if ctx.get("can_compensate"):
                return RecoveryStrategy.COMPENSATE
            return RecoveryStrategy.REPLAN

        if failure == FailureCategory.CAPABILITY_MISMATCH:
            return RecoveryStrategy.FALLBACK

        # Default retry if policy permits
        if self.retry_policy.can_retry(failure, attempt_count):
            return RecoveryStrategy.RETRY

        return RecoveryStrategy.ESCALATE

    def _compute_fingerprint(self, failure: FailureCategory, target_id: str, ctx: Dict[str, Any]) -> str:
        raw = f"{failure.value}|{target_id}|{ctx.get('error', '')}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class CircuitBreakerManager:
    """Maintains and coordinates named circuit breakers across tools, providers, and services."""

    def __init__(self) -> None:
        self._breakers: Dict[str, CircuitBreaker] = {}

    def get_or_create(
        self,
        name: str,
        failure_threshold: int = 3,
        cooldown_seconds: float = 5.0,
    ) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                cooldown_seconds=cooldown_seconds,
            )
        return self._breakers[name]

    def can_execute(self, name: str) -> bool:
        cb = self._breakers.get(name)
        if not cb:
            return True
        return cb.can_execute()

    def record_success(self, name: str) -> None:
        cb = self._breakers.get(name)
        if cb:
            cb.record_success()

    def record_failure(self, name: str) -> None:
        cb = self.get_or_create(name)
        cb.record_failure()

    def get_open_circuits(self) -> List[str]:
        return [name for name, cb in self._breakers.items() if cb.state == CircuitBreakerState.OPEN]
