"""Proactive Heartbeat Engine and Autonomous Decision Coordination for Hey Jev.

Coordinates proactive, bounded background intelligence:
- Periodically inspects eligible system, project, and task signals
- Bounded decision outputs:
  - DO_NOTHING (quiet default, never invents urgency)
  - NOTIFY_USER (important status updates)
  - PREPARE_RECOMMENDATION (gentle deferred suggestions)
  - PROPOSE_TASK (consequential tasks requiring explicit user confirmation)
  - EXECUTE_LOW_RISK (pre-authorized SAFE maintenance tasks executed within governor limits)
- Resource Headroom Guard: Suppresses proactive activity when CPU/RAM is constrained
- Untrusted Input Quarantine: External/untrusted events cannot execute autonomously
"""
from __future__ import annotations

import os
import time
import uuid
import threading
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

import psutil

from event_bus import EventBus
from project_health import ProjectHealthSnapshot
from project_model import Project
from project_registry import ProjectRegistry
from safety_engine import evaluate_risk, SAFE, MEDIUM, HIGH, BLOCKED, ACTIONS_LOG_FILE


class HeartbeatDecisionType(str, Enum):
    """Bounded decisions emitted by the proactive heartbeat engine."""
    DO_NOTHING = "DO_NOTHING"
    NOTIFY_USER = "NOTIFY_USER"
    PREPARE_RECOMMENDATION = "PREPARE_RECOMMENDATION"
    PROPOSE_TASK = "PROPOSE_TASK"
    EXECUTE_LOW_RISK = "EXECUTE_LOW_RISK"


class SignalSeverity(str, Enum):
    """Urgency level of a detected environmental signal."""
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class HeartbeatSignal:
    """A single normalized observation evaluated during a heartbeat cycle."""
    signal_id: str = field(default_factory=lambda: f"sig_{uuid.uuid4().hex[:8]}")
    source: str = ""                         # e.g., "system_resources", "project_health", "failed_task", "stale_cache"
    severity: SignalSeverity = SignalSeverity.INFO
    confidence: float = 1.0                  # 0.0 to 1.0 (threshold 0.70)
    is_untrusted: bool = False               # Marked True if originating from external / untrusted input
    summary: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class HeartbeatDecision:
    """The bounded outcome of a proactive heartbeat evaluation."""
    decision_type: HeartbeatDecisionType
    reason: str
    signal: Optional[HeartbeatSignal] = None
    proposed_task: Optional[Dict[str, Any]] = None
    risk_level: str = SAFE
    requires_approval: bool = False
    confidence: float = 1.0
    action_result: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_type": self.decision_type.value,
            "reason": self.reason,
            "signal": self.signal.to_dict() if self.signal else None,
            "proposed_task": dict(self.proposed_task) if self.proposed_task else None,
            "risk_level": self.risk_level,
            "requires_approval": self.requires_approval,
            "confidence": self.confidence,
            "action_result": self.action_result,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Signal Providers
# ---------------------------------------------------------------------------

class BaseSignalProvider:
    """Base interface for environmental signal providers."""

    def get_signals(self) -> List[HeartbeatSignal]:
        return []


class SystemResourceSignalProvider(BaseSignalProvider):
    """Inspects CPU and memory to detect resource pressure."""

    def __init__(self, max_cpu_percent: float = 80.0, min_memory_mb: float = 400.0) -> None:
        self.max_cpu_percent = max_cpu_percent
        self.min_memory_mb = min_memory_mb

    def get_signals(self) -> List[HeartbeatSignal]:
        signals = []
        try:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            free_mb = mem.available / (1024 * 1024)

            if cpu > self.max_cpu_percent or free_mb < self.min_memory_mb:
                signals.append(
                    HeartbeatSignal(
                        source="system_resources",
                        severity=SignalSeverity.CRITICAL,
                        confidence=1.0,
                        summary=f"Resource headroom low (CPU: {cpu:.1f}%, Free RAM: {free_mb:.0f}MB)",
                        payload={"cpu_percent": cpu, "free_memory_mb": free_mb},
                    )
                )
        except Exception:
            pass
        return signals


class ProjectHealthSignalProvider(BaseSignalProvider):
    """Monitors registered projects for health regressions or stale uncommitted changes."""

    def __init__(self, project_registry: Optional[ProjectRegistry] = None) -> None:
        self.registry = project_registry

    def get_signals(self) -> List[HeartbeatSignal]:
        if not self.registry:
            return []

        signals = []
        for proj in self.registry.list_projects():
            # Check metadata or stored health indicators
            last_test = proj.metadata.get("last_test_result", "UNKNOWN")
            is_broken = proj.metadata.get("known_build_status") == "BROKEN" or last_test == "FAILED"

            if is_broken:
                signals.append(
                    HeartbeatSignal(
                        source="project_health",
                        severity=SignalSeverity.HIGH,
                        confidence=0.95,
                        summary=f"Project '{proj.name}' reported failing checks ({last_test})",
                        payload={
                            "project_id": proj.project_id,
                            "action": "run_diagnostics",
                            "name": proj.name,
                            "last_test_result": last_test,
                        },
                    )
                )
            elif proj.metadata.get("has_uncommitted_changes") and proj.metadata.get("uncommitted_settled", False):
                signals.append(
                    HeartbeatSignal(
                        source="project_health",
                        severity=SignalSeverity.LOW,
                        confidence=0.80,
                        summary=f"Project '{proj.name}' has uncommitted changes that settled",
                        payload={
                            "project_id": proj.project_id,
                            "action": "recommend_commit",
                            "name": proj.name,
                        },
                    )
                )

        return signals


class TaskFailureSignalProvider(BaseSignalProvider):
    """Detects background task failures requiring user awareness."""

    def __init__(self, task_manager: Optional[Any] = None) -> None:
        self.task_manager = task_manager
        self._notified_task_ids: set = set()

    def get_signals(self) -> List[HeartbeatSignal]:
        if not self.task_manager:
            return []

        signals = []
        try:
            tasks = self.task_manager.list_tasks()
            for t in tasks:
                if t.state.value == "FAILED" and t.background_task_id not in self._notified_task_ids:
                    self._notified_task_ids.add(t.background_task_id)
                    signals.append(
                        HeartbeatSignal(
                            source="failed_task",
                            severity=SignalSeverity.HIGH,
                            confidence=1.0,
                            summary=f"Background task '{t.name}' failed after {t.failure_count} attempts",
                            payload={
                                "task_id": t.background_task_id,
                                "name": t.name,
                                "failure_count": t.failure_count,
                                "error": t.last_result.get("error", "Unknown error"),
                            },
                        )
                    )
        except Exception:
            pass
        return signals


class StaleCacheSignalProvider(BaseSignalProvider):
    """Identifies temporary scratch or stale cache files older than retention policy."""

    def __init__(self, cache_dir: Optional[str] = None, ttl_seconds: float = 86400.0) -> None:
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_seconds

    def get_signals(self) -> List[HeartbeatSignal]:
        if not self.cache_dir or not os.path.exists(self.cache_dir):
            return []

        stale_files = []
        now = time.time()
        try:
            for fname in os.listdir(self.cache_dir):
                fpath = os.path.join(self.cache_dir, fname)
                if os.path.isfile(fpath):
                    st = os.stat(fpath)
                    if (now - st.st_mtime) >= self.ttl_seconds:
                        stale_files.append(fpath)
        except OSError:
            pass

        if stale_files:
            return [
                HeartbeatSignal(
                    source="stale_cache",
                    severity=SignalSeverity.LOW,
                    confidence=0.85,
                    summary=f"Found {len(stale_files)} stale cache file(s) eligible for cleanup",
                    payload={"files": stale_files, "action": "prune_temp_cache", "cache_dir": self.cache_dir},
                )
            ]
        return []


# ---------------------------------------------------------------------------
# Proactive Heartbeat Engine
# ---------------------------------------------------------------------------

class ProactiveHeartbeatEngine:
    """Lightweight periodic evaluation engine for proactive intelligence."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        task_manager: Optional[Any] = None,
        project_registry: Optional[ProjectRegistry] = None,
        signal_providers: Optional[List[BaseSignalProvider]] = None,
        task_executor: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        interval_sec: float = 30.0,
        max_cpu_percent: float = 80.0,
        min_memory_mb: float = 100.0,
        min_confidence: float = 0.70,
    ) -> None:
        self.event_bus = event_bus or EventBus()
        self.task_manager = task_manager
        self.project_registry = project_registry
        self.task_executor = task_executor
        self.interval_sec = interval_sec
        self.max_cpu_percent = max_cpu_percent
        self.min_memory_mb = min_memory_mb
        self.min_confidence = min_confidence

        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._history: List[HeartbeatDecision] = []

        # Configure signal providers
        if signal_providers is not None:
            self.signal_providers = list(signal_providers)
        else:
            self.signal_providers = [
                SystemResourceSignalProvider(max_cpu_percent=max_cpu_percent, min_memory_mb=min_memory_mb),
                ProjectHealthSignalProvider(project_registry=self.project_registry),
                TaskFailureSignalProvider(task_manager=self.task_manager),
            ]

    def register_signal_provider(self, provider: BaseSignalProvider) -> None:
        with self._lock:
            self.signal_providers.append(provider)

    def evaluate_once(self) -> HeartbeatDecision:
        """Execute a single deterministic evaluation pass."""
        now = time.time()

        # Step 1: Resource Headroom Guard
        try:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            free_mb = mem.available / (1024 * 1024)
            if cpu > self.max_cpu_percent or free_mb < self.min_memory_mb:
                decision = HeartbeatDecision(
                    decision_type=HeartbeatDecisionType.DO_NOTHING,
                    reason=f"Suppressed: System resource pressure exceeds headroom limits (CPU: {cpu:.1f}%, RAM free: {free_mb:.0f}MB)",
                    created_at=now,
                )
                self._record_decision(decision)
                return decision
        except Exception:
            pass

        # Step 2: Gather all signals
        all_signals: List[HeartbeatSignal] = []
        with self._lock:
            providers = list(self.signal_providers)

        for prov in providers:
            try:
                sigs = prov.get_signals()
                all_signals.extend(sigs)
            except Exception:
                pass

        # Filter signals by confidence threshold (strictly prevent false urgency)
        eligible_signals = [s for s in all_signals if s.confidence >= self.min_confidence]

        if not eligible_signals:
            # Quiet default
            decision = HeartbeatDecision(
                decision_type=HeartbeatDecisionType.DO_NOTHING,
                reason="All monitored systems nominal; no proactive action needed",
                created_at=now,
            )
            self._record_decision(decision)
            return decision

        # Step 3: Prioritize signals
        severity_rank = {
            SignalSeverity.CRITICAL: 5,
            SignalSeverity.HIGH: 4,
            SignalSeverity.MEDIUM: 3,
            SignalSeverity.LOW: 2,
            SignalSeverity.INFO: 1,
        }
        eligible_signals.sort(key=lambda s: (severity_rank.get(s.severity, 0), s.confidence), reverse=True)
        top_signal = eligible_signals[0]

        # Step 4: Synthesize bounded decision
        decision = self._decide_for_signal(top_signal, now)
        self._record_decision(decision)
        return decision

    def _decide_for_signal(self, signal: HeartbeatSignal, now: float) -> HeartbeatDecision:
        """Map signal to one of the 5 bounded decision types respecting safety and untrusted input rules."""
        source = signal.source
        action_name = signal.payload.get("action", "")

        # 1. System resource alert
        if source == "system_resources":
            return HeartbeatDecision(
                decision_type=HeartbeatDecisionType.DO_NOTHING,
                reason=signal.summary,
                signal=signal,
                confidence=signal.confidence,
                created_at=now,
            )

        # 2. Failed background task -> NOTIFY_USER
        if source == "failed_task":
            return HeartbeatDecision(
                decision_type=HeartbeatDecisionType.NOTIFY_USER,
                reason=signal.summary,
                signal=signal,
                confidence=signal.confidence,
                created_at=now,
            )

        # 3. Project dirty tree settling -> PREPARE_RECOMMENDATION
        if source == "project_health" and action_name == "recommend_commit":
            return HeartbeatDecision(
                decision_type=HeartbeatDecisionType.PREPARE_RECOMMENDATION,
                reason=signal.summary,
                signal=signal,
                proposed_task={"action": "recommend_commit", "project_id": signal.payload.get("project_id")},
                risk_level=SAFE,
                requires_approval=False,
                confidence=signal.confidence,
                created_at=now,
            )

        # 4. Stale cache cleanup -> Low risk safe maintenance OR quarantined
        if source == "stale_cache" and action_name == "prune_temp_cache":
            # Safety check on action
            risk, _ = evaluate_risk("find_files")  # Read/inspection is SAFE

            if signal.is_untrusted:
                # Untrusted input quarantine: force PROPOSE_TASK requiring explicit approval
                return HeartbeatDecision(
                    decision_type=HeartbeatDecisionType.PROPOSE_TASK,
                    reason=f"Quarantined external signal: {signal.summary}. Requires confirmation.",
                    signal=signal,
                    proposed_task={"action": "prune_temp_cache", "files": signal.payload.get("files")},
                    risk_level=SAFE,
                    requires_approval=True,
                    confidence=signal.confidence,
                    created_at=now,
                )

            # Pre-authorized SAFE task executed within policy
            action_res = None
            if self.task_executor:
                try:
                    action_res = self.task_executor({"action": "prune_temp_cache", "files": signal.payload.get("files")})
                except Exception as e:
                    action_res = {"status": "FAILED", "error": str(e)}

            return HeartbeatDecision(
                decision_type=HeartbeatDecisionType.EXECUTE_LOW_RISK,
                reason=signal.summary,
                signal=signal,
                proposed_task={"action": "prune_temp_cache", "files": signal.payload.get("files")},
                risk_level=SAFE,
                requires_approval=False,
                confidence=signal.confidence,
                action_result=action_res,
                created_at=now,
            )

        # 5. Project health regression (failing tests / broken build) -> PROPOSE_TASK
        if source == "project_health" and action_name == "run_diagnostics":
            # Consequential action (runs commands or diagnostics)
            # Evaluated risk is HIGH (runs powershell or test scripts)
            risk, _ = evaluate_risk("run_powershell")
            return HeartbeatDecision(
                decision_type=HeartbeatDecisionType.PROPOSE_TASK,
                reason=signal.summary,
                signal=signal,
                proposed_task={
                    "action": "run_diagnostics",
                    "project_id": signal.payload.get("project_id"),
                    "name": signal.payload.get("name"),
                },
                risk_level=risk,
                requires_approval=True,
                confidence=signal.confidence,
                created_at=now,
            )

        # 6. Any other consequential or external signal
        if signal.is_untrusted or signal.severity in (SignalSeverity.HIGH, SignalSeverity.CRITICAL):
            return HeartbeatDecision(
                decision_type=HeartbeatDecisionType.PROPOSE_TASK,
                reason=signal.summary,
                signal=signal,
                proposed_task=dict(signal.payload),
                risk_level=HIGH,
                requires_approval=True,
                confidence=signal.confidence,
                created_at=now,
            )

        # Default fallback: PREPARE_RECOMMENDATION
        return HeartbeatDecision(
            decision_type=HeartbeatDecisionType.PREPARE_RECOMMENDATION,
            reason=signal.summary,
            signal=signal,
            proposed_task=dict(signal.payload),
            risk_level=SAFE,
            requires_approval=False,
            confidence=signal.confidence,
            created_at=now,
        )

    def _record_decision(self, decision: HeartbeatDecision) -> None:
        with self._lock:
            self._history.append(decision)
            if len(self._history) > 200:
                self._history.pop(0)

        self.event_bus.emit(
            event_type="heartbeat.evaluated",
            task_id="heartbeat",
            agent_name="ProactiveHeartbeatEngine",
            data=decision.to_dict(),
        )

    def get_history(self) -> List[HeartbeatDecision]:
        with self._lock:
            return list(self._history)

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True, name="ProactiveHeartbeatEngine")
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while self._running:
            time.sleep(self.interval_sec)
            if not self._running:
                break
            try:
                self.evaluate_once()
            except Exception:
                pass
