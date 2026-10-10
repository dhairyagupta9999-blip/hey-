"""Unified Operational Health Model and Coordinator for Hey Jev (Phase 26).

Provides:
- Canonical Health States: HEALTHY, DEGRADED, UNAVAILABLE, UNVERIFIED, RECOVERING, NEEDS_ATTENTION
- Subsystem Categorization: RUNTIME_COMPONENT, BACKGROUND_TASK, WATCHER, DEPLOYED_CAPABILITY, PROVIDER
- HealthObservation: Rich structured observation records with empirical evidence, confidence,
  failure classification, consecutive failure count, outstanding worker accounting, and recommended action.
- Functional vs Registry Lookup: Distinguishes mere presence in a registry from bounded, side-effect-aware
  functional verification.
- Worker Safety: Respects Python OS-boundary threading constraints. A caller timeout does NOT assume
  underlying worker exit. Tracks outstanding workers until actual termination and bounds concurrent probe threads.
- OperationalHealthCoordinator: Thread-safe coordinator for tracking, probing, and reporting subsystem health
  with EventBus emission.
"""
from __future__ import annotations

import enum
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from event_bus import EventBus
from memory_model import sanitize_content
from reliability_model import FailureCategory

logger = logging.getLogger(__name__)


class HealthState(str, enum.Enum):
    """Canonical operational health states for Hey Jev subsystems."""
    HEALTHY = "HEALTHY"                    # Fully functional, responsive, and passing verification
    DEGRADED = "DEGRADED"                  # Functional with impaired performance, non-critical faults, or transient retries
    UNAVAILABLE = "UNAVAILABLE"            # Down, unresponsive, or failing critical functional probes
    UNVERIFIED = "UNVERIFIED"              # Registered or started, but functional health check has not yet confirmed operation
    RECOVERING = "RECOVERING"              # Undergoing active automated recovery, state draining, or restart
    NEEDS_ATTENTION = "NEEDS_ATTENTION"    # Requires administrative or human intervention; autonomous recovery exhausted or disallowed


class SubsystemCategory(str, enum.Enum):
    """Classification of monitored components."""
    RUNTIME_COMPONENT = "RUNTIME_COMPONENT"
    BACKGROUND_TASK = "BACKGROUND_TASK"
    WATCHER = "WATCHER"
    DEPLOYED_CAPABILITY = "DEPLOYED_CAPABILITY"
    PROVIDER = "PROVIDER"


@dataclass
class HealthObservation:
    """Rich structured observation record of a subsystem's operational state."""
    component_id: str
    category: SubsystemCategory
    state: HealthState
    failure_classification: Optional[FailureCategory] = None
    evidence: List[str] = field(default_factory=list)
    confidence: float = 1.0  # 0.0 to 1.0
    failure_count: int = 0
    consecutive_failures: int = 0
    first_observed_at: float = field(default_factory=time.time)
    last_observed_at: float = field(default_factory=time.time)
    outstanding_operations: int = 0  # Number of currently executing workers/probes
    recommended_action: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "component_id": self.component_id,
            "category": self.category.value,
            "state": self.state.value,
            "failure_classification": self.failure_classification.value if self.failure_classification else None,
            "evidence": list(self.evidence),
            "confidence": round(self.confidence, 3),
            "failure_count": self.failure_count,
            "consecutive_failures": self.consecutive_failures,
            "first_observed_at": self.first_observed_at,
            "last_observed_at": self.last_observed_at,
            "outstanding_operations": self.outstanding_operations,
            "recommended_action": self.recommended_action,
            "metadata": dict(self.metadata),
        }


class OperationalHealthCoordinator:
    """Coordinates and evaluates operational health across all Hey Jev subsystems."""

    MAX_CONCURRENT_PROBES = 10

    def __init__(self, event_bus: Optional[EventBus] = None) -> None:
        self.event_bus = event_bus or EventBus()
        self._lock = threading.RLock()
        self._observations: Dict[str, HealthObservation] = {}
        self._active_probe_threads: Set[threading.Thread] = set()
        self._probe_lock = threading.Lock()
        self._functional_probers: Dict[str, Callable[..., Tuple[bool, Optional[str]]]] = {}

    def _sanitize(self, val: Any) -> Any:
        if isinstance(val, str):
            return sanitize_content(val)
        if isinstance(val, dict):
            return {k: self._sanitize(v) for k, v in val.items()}
        if isinstance(val, list):
            return [self._sanitize(x) for x in val]
        return val

    def register_functional_prober(
        self,
        component_id: str,
        prober: Callable[..., Tuple[bool, Optional[str]]],
    ) -> None:
        """Register a deterministic, side-effect-aware functional health prober for a component."""
        with self._lock:
            self._functional_probers[component_id] = prober

    def record_observation(
        self,
        component_id: str,
        category: SubsystemCategory,
        state: HealthState,
        evidence: Optional[List[str]] = None,
        failure_classification: Optional[FailureCategory] = None,
        confidence: float = 1.0,
        recommended_action: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> HealthObservation:
        """Record or update a health observation for a subsystem component."""
        with self._lock:
            now = time.time()
            clean_evidence = [sanitize_content(e) for e in (evidence or [])]
            clean_meta = self._sanitize(metadata or {})

            prior = self._observations.get(component_id)
            if prior is not None:
                is_failure = state in (HealthState.DEGRADED, HealthState.UNAVAILABLE, HealthState.NEEDS_ATTENTION)
                consecutive = prior.consecutive_failures + 1 if is_failure else 0
                total_failures = prior.failure_count + (1 if is_failure else 0)
                first_obs = prior.first_observed_at
                out_ops = prior.outstanding_operations
                prior_state = prior.state
            else:
                is_failure = state in (HealthState.DEGRADED, HealthState.UNAVAILABLE, HealthState.NEEDS_ATTENTION)
                consecutive = 1 if is_failure else 0
                total_failures = 1 if is_failure else 0
                first_obs = now
                out_ops = 0
                prior_state = None

            obs = HealthObservation(
                component_id=component_id,
                category=category,
                state=state,
                failure_classification=failure_classification,
                evidence=clean_evidence,
                confidence=max(0.0, min(1.0, confidence)),
                failure_count=total_failures,
                consecutive_failures=consecutive,
                first_observed_at=first_obs,
                last_observed_at=now,
                outstanding_operations=out_ops,
                recommended_action=recommended_action,
                metadata=clean_meta,
            )
            self._observations[component_id] = obs

            # Emit event on bus
            self.event_bus.emit(
                "health.observation",
                component_id,
                data=obs.to_dict(),
            )
            if prior_state != state:
                self.event_bus.emit(
                    "health.state_changed",
                    component_id,
                    data={
                        "component_id": component_id,
                        "category": category.value,
                        "previous_state": prior_state.value if prior_state else None,
                        "new_state": state.value,
                        "consecutive_failures": consecutive,
                        "recommended_action": recommended_action,
                    },
                )

            return obs

    def get_health(self, component_id: str) -> Optional[HealthObservation]:
        """Fetch the current health observation for a specific component."""
        with self._lock:
            return self._observations.get(component_id)

    def get_all_health(
        self,
        category: Optional[SubsystemCategory] = None,
        state: Optional[HealthState] = None,
    ) -> List[HealthObservation]:
        """Return all recorded health observations matching optional category and state filters."""
        with self._lock:
            results = list(self._observations.values())
            if category:
                results = [r for r in results if r.category == category]
            if state:
                results = [r for r in results if r.state == state]
            return results

    def track_worker_start(self, component_id: str) -> None:
        """Increment count of outstanding active workers for a component."""
        with self._lock:
            obs = self._observations.get(component_id)
            if obs:
                obs.outstanding_operations += 1

    def track_worker_finish(self, component_id: str) -> None:
        """Decrement count of outstanding active workers for a component."""
        with self._lock:
            obs = self._observations.get(component_id)
            if obs:
                obs.outstanding_operations = max(0, obs.outstanding_operations - 1)

    def run_functional_probe(
        self,
        component_id: str,
        category: SubsystemCategory,
        prober: Optional[Callable[..., Tuple[bool, Optional[str]]]] = None,
        timeout_seconds: float = 5.0,
    ) -> Tuple[bool, Optional[str]]:
        """Perform a bounded, observable functional verification probe.
        
        Distinguishes mere registry presence from actual functional execution.
        Respects worker safety: cancellation is signaled via cooperative Event.
        Outstanding worker threads are tracked until actual termination.
        """
        active_prober = prober or self._functional_probers.get(component_id)
        if not active_prober:
            # Fallback observation: unverified if no functional prober is available
            self.record_observation(
                component_id=component_id,
                category=category,
                state=HealthState.UNVERIFIED,
                evidence=["No functional prober registered; component exists in registry but unverified functionally."],
                confidence=0.5,
                recommended_action="REGISTER_PROBE",
            )
            return False, "No functional prober registered."

        with self._probe_lock:
            self._active_probe_threads = {t for t in self._active_probe_threads if t.is_alive()}
            if len(self._active_probe_threads) >= self.MAX_CONCURRENT_PROBES:
                self.record_observation(
                    component_id=component_id,
                    category=category,
                    state=HealthState.DEGRADED,
                    evidence=[f"Health probe concurrency limit reached ({self.MAX_CONCURRENT_PROBES}). Probe postponed."],
                    failure_classification=FailureCategory.RESOURCE_EXHAUSTED,
                    confidence=0.8,
                    recommended_action="THROTTLE_PROBES",
                )
                return False, "Probe concurrency limit reached."

        result_holder: List[Tuple[bool, Optional[str]]] = []
        error_holder: List[Exception] = []
        cancel_event = threading.Event()

        self.track_worker_start(component_id)

        def _worker() -> None:
            try:
                try:
                    res = active_prober(cancel_event=cancel_event)
                except TypeError:
                    res = active_prober()
                result_holder.append(res)
            except Exception as e:
                error_holder.append(e)
            finally:
                self.track_worker_finish(component_id)

        thread = threading.Thread(target=_worker, daemon=True)
        with self._probe_lock:
            self._active_probe_threads.add(thread)

        t0 = time.perf_counter()
        thread.start()
        thread.join(timeout=timeout_seconds)
        dur_ms = (time.perf_counter() - t0) * 1000

        if thread.is_alive():
            cancel_event.set()
            err_msg = (
                f"Probe deadline exceeded ({dur_ms:.1f}ms > {timeout_seconds * 1000:.1f}ms). "
                "Worker cancellation signaled; outstanding worker accounting retained until actual thread termination."
            )
            self.record_observation(
                component_id=component_id,
                category=category,
                state=HealthState.UNAVAILABLE,
                evidence=[err_msg],
                failure_classification=FailureCategory.TIMEOUT,
                confidence=0.9,
                recommended_action="RESTART_COMPONENT",
            )
            return False, err_msg

        with self._probe_lock:
            self._active_probe_threads.discard(thread)

        if error_holder:
            err_msg = f"Probe execution raised exception: {str(error_holder[0])}"
            self.record_observation(
                component_id=component_id,
                category=category,
                state=HealthState.UNAVAILABLE,
                evidence=[err_msg],
                failure_classification=FailureCategory.TOOL_FAILURE,
                confidence=1.0,
                recommended_action="RESTART_COMPONENT",
            )
            return False, err_msg

        if result_holder:
            ok, msg = result_holder[0]
            clean_msg = sanitize_content(msg or "")
            if ok:
                self.record_observation(
                    component_id=component_id,
                    category=category,
                    state=HealthState.HEALTHY,
                    evidence=[f"Functional probe passed in {dur_ms:.1f}ms: {clean_msg}" if clean_msg else f"Functional probe passed in {dur_ms:.1f}ms."],
                    confidence=1.0,
                    recommended_action="NO_ACTION",
                )
                return True, clean_msg
            else:
                self.record_observation(
                    component_id=component_id,
                    category=category,
                    state=HealthState.DEGRADED,
                    evidence=[f"Functional probe reported failure ({dur_ms:.1f}ms): {clean_msg}"],
                    failure_classification=FailureCategory.VERIFICATION_FAILURE,
                    confidence=0.95,
                    recommended_action="RETRY_OR_RESTART",
                )
                return False, clean_msg

        return False, "No probe result captured."

    def get_system_health_summary(self) -> Dict[str, Any]:
        """Aggregate holistic system operational health summary."""
        with self._lock:
            total = len(self._observations)
            counts: Dict[str, int] = {s.value: 0 for s in HealthState}
            by_category: Dict[str, Dict[str, int]] = {c.value: {s.value: 0 for s in HealthState} for c in SubsystemCategory}

            for obs in self._observations.values():
                counts[obs.state.value] = counts.get(obs.state.value, 0) + 1
                cat_dict = by_category.get(obs.category.value, {})
                cat_dict[obs.state.value] = cat_dict.get(obs.state.value, 0) + 1

            # Determine overall system health
            if counts[HealthState.UNAVAILABLE.value] > 0 or counts[HealthState.NEEDS_ATTENTION.value] > 0:
                overall_state = HealthState.DEGRADED
            elif counts[HealthState.DEGRADED.value] > 0 or counts[HealthState.RECOVERING.value] > 0:
                overall_state = HealthState.DEGRADED
            elif total > 0 and counts[HealthState.HEALTHY.value] == total:
                overall_state = HealthState.HEALTHY
            elif total > 0 and counts[HealthState.UNVERIFIED.value] > 0:
                overall_state = HealthState.UNVERIFIED
            else:
                overall_state = HealthState.HEALTHY if total > 0 else HealthState.UNVERIFIED

            with self._probe_lock:
                active_probes = len({t for t in self._active_probe_threads if t.is_alive()})

            return {
                "overall_state": overall_state.value,
                "total_monitored_components": total,
                "counts_by_state": counts,
                "counts_by_category": by_category,
                "active_probe_threads": active_probes,
                "timestamp": time.time(),
            }
