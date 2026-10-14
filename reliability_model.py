"""Reliability Models, Action Records, Checkpoints, and Circuit Breakers for Hey Jev.

Defines:
- ActionRecord: Atomic, tamper-evident record of tool/agent operations with before/after states
- Checkpoint: Periodic durable execution state snapshots with SHA-256 checksum integrity
- VerificationEvidence & VerificationLevel (0 to 5)
- FailureCategory (21 fine-grained failure classifications)
- RecoveryPlan & RecoveryStrategy
- RetryPolicy with exponential backoff & jitter
- CircuitBreaker (CLOSED, OPEN, HALF_OPEN)
- JevTransaction & ReversibilityType (Saga pattern support)
- SystemReliabilitySnapshot
"""
from __future__ import annotations

import hashlib
import json
import random
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class ReliabilityLevel(str, Enum):
    """Reliability scope hierarchy."""
    TOOL_RELIABILITY = "TOOL_RELIABILITY"
    TASK_RELIABILITY = "TASK_RELIABILITY"
    MISSION_RELIABILITY = "MISSION_RELIABILITY"
    SWARM_RELIABILITY = "SWARM_RELIABILITY"


class ExecutionStatus(str, Enum):
    """Execution status distinguishing confirmed outcomes from uncertainty."""
    ATTEMPTED = "ATTEMPTED"
    SUCCEEDED = "SUCCEEDED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    ALREADY_APPLIED = "ALREADY_APPLIED"


class ReversibilityType(str, Enum):
    """Reversibility classification for operations and compensations."""
    REVERSIBLE = "REVERSIBLE"
    PARTIALLY_REVERSIBLE = "PARTIALLY_REVERSIBLE"
    IRREVERSIBLE = "IRREVERSIBLE"


class VerificationLevel(int, Enum):
    """Explicit verification confidence levels."""
    LEVEL_0 = 0  # Agent self-report
    LEVEL_1 = 1  # Tool return status
    LEVEL_2 = 2  # State inspection (file on disk, window title, process list)
    LEVEL_3 = 3  # Deterministic test (pytest, linter, regex match)
    LEVEL_4 = 4  # Independent reviewing verifier
    LEVEL_5 = 5  # Independent external validation


class RecoveryStrategy(str, Enum):
    """Strategies available to the RecoveryDecisionEngine."""
    RETRY = "RETRY"
    RESTART = "RESTART"
    RECONCILE = "RECONCILE"
    FALLBACK = "FALLBACK"
    COMPENSATE = "COMPENSATE"
    ROLLBACK = "ROLLBACK"
    REPLAN = "REPLAN"
    ESCALATE = "ESCALATE"
    ABORT = "ABORT"


class FailureCategory(str, Enum):
    """Comprehensive failure classification taxonomy."""
    TRANSIENT = "TRANSIENT"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    AUTHENTICATION = "AUTHENTICATION"
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"
    TOOL_FAILURE = "TOOL_FAILURE"
    MODEL_FAILURE = "MODEL_FAILURE"
    BROWSER_FAILURE = "BROWSER_FAILURE"
    WINDOWS_FAILURE = "WINDOWS_FAILURE"
    PROCESS_CRASH = "PROCESS_CRASH"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
    STATE_MISMATCH = "STATE_MISMATCH"
    UNKNOWN_STATE = "UNKNOWN_STATE"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    USER_REQUIRED = "USER_REQUIRED"
    SECURITY_EVENT = "SECURITY_EVENT"
    UNKNOWN = "UNKNOWN"


class CircuitBreakerState(str, Enum):
    """Operational states for protective circuit breakers."""
    CLOSED = "CLOSED"      # Normal operation
    OPEN = "OPEN"          # Tripped, blocking calls
    HALF_OPEN = "HALF_OPEN"  # Testing probe


class JevTransactionState(str, Enum):
    """Saga transaction lifecycle states."""
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    COMMITTING = "COMMITTING"
    COMMITTED = "COMMITTED"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"
    PARTIALLY_COMPENSATED = "PARTIALLY_COMPENSATED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass
class VerificationEvidence:
    """Individual piece of objective evidence supporting task verification."""
    evidence_id: str = field(default_factory=lambda: f"evi_{uuid.uuid4().hex[:8]}")
    type: str = "state_inspection"  # state_inspection, file_exists, test_run, process_check
    source: str = "TaskVerifier"
    timestamp: float = field(default_factory=time.time)
    value: Any = None
    expected: Any = None
    observed: Any = None
    confidence: float = 1.0  # 0.0 - 1.0
    level: VerificationLevel = VerificationLevel.LEVEL_2
    artifact_ref: Optional[str] = None
    checksum: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "type": self.type,
            "source": self.source,
            "timestamp": self.timestamp,
            "value": self.value,
            "expected": self.expected,
            "observed": self.observed,
            "confidence": self.confidence,
            "level": self.level.value if isinstance(self.level, VerificationLevel) else int(self.level),
            "artifact_ref": self.artifact_ref,
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VerificationEvidence:
        d = dict(data)
        if "level" in d:
            d["level"] = VerificationLevel(d["level"])
        return cls(**d)


@dataclass
class ActionRecord:
    """Atomic, tamper-evident log of an executed action or mutation."""
    action_id: str = field(default_factory=lambda: f"act_{uuid.uuid4().hex[:10]}")
    task_id: str = ""
    mission_id: Optional[str] = None
    agent_id: str = "jev_core"
    skill_id: Optional[str] = None
    tool_id: Optional[str] = None
    action_type: str = "mutation"  # file_move, file_create, process_launch, etc.
    target: str = ""               # Path, URL, or process target
    input_summary: Dict[str, Any] = field(default_factory=dict)
    risk_level: str = "NORMAL"
    permission_context: List[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    status: ExecutionStatus = ExecutionStatus.ATTEMPTED
    before_state: Dict[str, Any] = field(default_factory=dict)
    after_state: Dict[str, Any] = field(default_factory=dict)
    verification_state: str = "UNVERIFIED"
    verification_evidence: List[Dict[str, Any]] = field(default_factory=list)
    retry_count: int = 0
    attempt_id: int = 1
    idempotency_key: str = field(default_factory=lambda: f"idem_{uuid.uuid4().hex[:12]}")
    reversibility: ReversibilityType = ReversibilityType.REVERSIBLE
    rollback_metadata: Dict[str, Any] = field(default_factory=dict)
    compensation_action: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    group_id: Optional[str] = None
    prev_record_hash: Optional[str] = None
    record_hash: Optional[str] = None

    def _compute_hash_value(self) -> str:
        """Compute hash without storing — used for non-destructive integrity checks.
        
        Seals immutable action attributes (prev_record_hash, action_id, action_type, target,
        started_at, idempotency_key). Status is mutable during action lifecycle and tracked
        via state transitions and outcome records.
        """
        payload = f"{self.prev_record_hash}|{self.action_id}|{self.action_type}|{self.target}|{self.started_at}|{self.idempotency_key}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def compute_hash(self) -> str:
        """Compute cryptographic hash chaining this record to the previous one."""
        self.record_hash = self._compute_hash_value()
        return self.record_hash

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "task_id": self.task_id,
            "mission_id": self.mission_id,
            "agent_id": self.agent_id,
            "skill_id": self.skill_id,
            "tool_id": self.tool_id,
            "action_type": self.action_type,
            "target": self.target,
            "input_summary": self.input_summary,
            "risk_level": self.risk_level,
            "permission_context": list(self.permission_context),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status.value,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "verification_state": self.verification_state,
            "verification_evidence": self.verification_evidence,
            "retry_count": self.retry_count,
            "attempt_id": self.attempt_id,
            "idempotency_key": self.idempotency_key,
            "reversibility": self.reversibility.value,
            "rollback_metadata": self.rollback_metadata,
            "compensation_action": self.compensation_action,
            "error": self.error,
            "created_at": self.created_at,
            "group_id": self.group_id,
            "prev_record_hash": self.prev_record_hash,
            "record_hash": self.record_hash or self.compute_hash(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ActionRecord:
        d = dict(data)
        if "status" in d:
            d["status"] = ExecutionStatus(d["status"])
        if "reversibility" in d:
            d["reversibility"] = ReversibilityType(d["reversibility"])
        return cls(**d)


@dataclass
class Checkpoint:
    """Periodic durable execution state snapshot."""
    checkpoint_id: str = field(default_factory=lambda: f"chk_{uuid.uuid4().hex[:10]}")
    task_id: str = ""
    mission_id: Optional[str] = None
    sequence: int = 0
    timestamp: float = field(default_factory=time.time)
    state: str = "ACTIVE"
    completed_steps: List[str] = field(default_factory=list)
    pending_steps: List[str] = field(default_factory=list)
    active_workers: List[str] = field(default_factory=list)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    memory_refs: List[str] = field(default_factory=list)
    environment_snapshot: Dict[str, Any] = field(default_factory=dict)
    resource_state: Dict[str, Any] = field(default_factory=dict)
    verification_state: str = "UNVERIFIED"
    checksum: Optional[str] = None

    def compute_checksum(self) -> str:
        """Calculate SHA-256 integrity checksum over full checkpoint body."""
        raw = (
            f"{self.checkpoint_id}|{self.task_id}|{self.mission_id}|{self.sequence}|{self.state}|"
            f"{json.dumps(sorted(self.completed_steps))}|{json.dumps(sorted(self.pending_steps))}|"
            f"{json.dumps(self.artifacts, sort_keys=True)}|{json.dumps(sorted(self.memory_refs))}|"
            f"{json.dumps(self.environment_snapshot, sort_keys=True)}|{json.dumps(self.resource_state, sort_keys=True)}"
        )
        self.checksum = f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"
        return self.checksum

    def verify_checksum(self) -> bool:
        """Validate stored checksum against computed checksum."""
        if not self.checksum:
            return False
        expected = self.checksum
        computed = self.compute_checksum()
        return expected == computed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "mission_id": self.mission_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "state": self.state,
            "completed_steps": list(self.completed_steps),
            "pending_steps": list(self.pending_steps),
            "active_workers": list(self.active_workers),
            "artifacts": list(self.artifacts),
            "memory_refs": list(self.memory_refs),
            "environment_snapshot": dict(self.environment_snapshot),
            "resource_state": dict(self.resource_state),
            "verification_state": self.verification_state,
            "checksum": self.checksum or self.compute_checksum(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Checkpoint:
        return cls(**dict(data))


@dataclass
class RecoveryPlan:
    """Actionable recovery specification emitted by the RecoveryDecisionEngine."""
    recovery_id: str = field(default_factory=lambda: f"rec_{uuid.uuid4().hex[:8]}")
    failure_id: str = ""
    strategy: RecoveryStrategy = RecoveryStrategy.RETRY
    steps: List[Dict[str, Any]] = field(default_factory=list)
    risk: str = "LOW"
    requires_approval: bool = False
    created_at: float = field(default_factory=time.time)
    executed_at: Optional[float] = None
    result: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recovery_id": self.recovery_id,
            "failure_id": self.failure_id,
            "strategy": self.strategy.value,
            "steps": list(self.steps),
            "risk": self.risk,
            "requires_approval": self.requires_approval,
            "created_at": self.created_at,
            "executed_at": self.executed_at,
            "result": self.result,
        }


@dataclass
class RetryPolicy:
    """Configurable exponential backoff retry policy with full jitter."""
    max_attempts: int = 3
    initial_delay: float = 0.5
    backoff_multiplier: float = 2.0
    max_delay: float = 10.0
    jitter: bool = True
    retryable_categories: List[FailureCategory] = field(default_factory=lambda: [
        FailureCategory.TRANSIENT,
        FailureCategory.TIMEOUT,
        FailureCategory.RATE_LIMIT,
        FailureCategory.NETWORK_FAILURE,
    ])
    non_retryable_categories: List[FailureCategory] = field(default_factory=lambda: [
        FailureCategory.PERMISSION_DENIED,
        FailureCategory.POLICY_BLOCKED,
        FailureCategory.SECURITY_EVENT,
        FailureCategory.AUTHENTICATION,
    ])

    def can_retry(self, category: FailureCategory, current_attempt: int) -> bool:
        if current_attempt >= self.max_attempts:
            return False
        if category in self.non_retryable_categories:
            return False
        return category in self.retryable_categories

    def compute_delay(self, attempt: int) -> float:
        delay = min(self.max_delay, self.initial_delay * (self.backoff_multiplier ** (attempt - 1)))
        if self.jitter:
            delay = random.uniform(0.5 * delay, delay)
        return max(0.01, delay)


class CircuitBreaker:
    """Protects external providers and subsystems from cascading failure loops."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        cooldown_seconds: float = 5.0,
        success_threshold: int = 1,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.success_threshold = success_threshold

        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0.0

    def can_execute(self) -> bool:
        now = time.time()
        if self.state == CircuitBreakerState.CLOSED:
            return True
        elif self.state == CircuitBreakerState.OPEN:
            if (now - self.last_failure_time) > self.cooldown_seconds:
                # Transition to HALF_OPEN probe state
                self.state = CircuitBreakerState.HALF_OPEN
                self.success_count = 0
                return True
            return False
        elif self.state == CircuitBreakerState.HALF_OPEN:
            return True
        return False

    def record_success(self) -> None:
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = CircuitBreakerState.CLOSED
                self.failure_count = 0
        elif self.state == CircuitBreakerState.CLOSED:
            self.failure_count = 0

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN


@dataclass
class JevTransaction:
    """Saga-style container managing forward mutations and compensating actions."""
    transaction_id: str = field(default_factory=lambda: f"tx_{uuid.uuid4().hex[:10]}")
    mission_id: Optional[str] = None
    state: JevTransactionState = JevTransactionState.PREPARING
    actions: List[ActionRecord] = field(default_factory=list)
    compensation_actions: List[Dict[str, Any]] = field(default_factory=list)
    checkpoints: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: Optional[str] = None

    def add_action(self, action: ActionRecord) -> None:
        self.actions.append(action)
        self.updated_at = time.time()
        # Collect compensation if defined
        if action.compensation_action:
            self.compensation_actions.append(action.compensation_action)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "mission_id": self.mission_id,
            "state": self.state.value,
            "actions": [a.to_dict() for a in self.actions],
            "compensation_actions": list(self.compensation_actions),
            "checkpoints": list(self.checkpoints),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> JevTransaction:
        d = dict(data)
        if "state" in d:
            d["state"] = JevTransactionState(d["state"])
        if "actions" in d and isinstance(d["actions"], list):
            d["actions"] = [
                ActionRecord.from_dict(a) if isinstance(a, dict) else a
                for a in d["actions"]
            ]
        return cls(**d)


@dataclass
class SystemReliabilitySnapshot:
    """Comprehensive observability snapshot across all reliability systems."""
    timestamp: float = field(default_factory=time.time)
    active_transactions: int = 0
    uncertain_tasks: int = 0
    failed_tasks: int = 0
    recovery_attempts: int = 0
    open_circuits: List[str] = field(default_factory=list)
    degraded_providers: List[str] = field(default_factory=list)
    orphan_records: int = 0
    integrity_problems: List[str] = field(default_factory=list)
    pending_approvals: int = 0

    def summary_text(self) -> str:
        status_str = "HEALTHY" if not self.open_circuits and not self.integrity_problems and not self.failed_tasks else "DEGRADED"
        return (
            f"Reliability Status: {status_str} | "
            f"Tx Active: {self.active_transactions} | "
            f"Failed Tasks: {self.failed_tasks} | "
            f"Open Circuits: {len(self.open_circuits)} | "
            f"Integrity Issues: {len(self.integrity_problems)}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
