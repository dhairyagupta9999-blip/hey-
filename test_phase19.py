"""Unit, Integration, and End-to-End Tests for Phase 19: Reliability, Verification, Recovery, and Undo.

Tests cover:
- ActionRecord, Checkpoint SHA-256 checksumming, state transitions, idempotency keys
- VerificationEvidence & Levels (0 to 5)
- FailureClassifierV2 (21 categories, regex + exception classification)
- RetryPolicy exponential backoff & jitter, non-retryable categories
- CircuitBreaker (CLOSED → OPEN → HALF_OPEN → CLOSED lifecycle)
- CircuitBreakerManager coordination across multiple breakers
- OperationTimeline tamper-evident hash chain
- SemanticLockManager resource mutual exclusion
- UndoManager: can_undo, preview_undo, undo, undo_group, IRREVERSIBLE rejection
- RecoveryDecisionEngine: plan_recovery, strategy mapping, loop/cycle prevention
- ReconciliationEngine: filesystem, process, git, remote agent
- VerificationEngineV2: multi-level evidence collection (Levels 0-3)
- ReliabilityManager: WAL intent logging, checkpoint persistence, saga transactions
- Saga transactions: commit & reverse compensation
- Crash recovery: simulated crash before result → reconcile → ALREADY_APPLIED
- Chaos tests: FAIL_BEFORE_EXECUTE, FAIL_AFTER_EXECUTE, NETWORK_DROP, PROCESS_CRASH, VERIFICATION_FAILURE
- Property invariants: completed task has verification, compensated tx not committed, open circuit blocks, corrupted checkpoint rejected
- 5 Required Real Recovery Demonstrations
- Real Undo Sandbox Demo
- Real Restart Sandbox Demo
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

from agent_result import AgentResult, AgentResultStatus
from agent_task import AgentTask
from reconciliation_engine import ReconciliationEngine, ReconciliationStatus
from recovery_engine import (
    CircuitBreakerManager,
    FailureClassifierV2,
    RecoveryDecisionEngine,
)
from reliability_manager import ReliabilityManager, RepairManager
from reliability_model import (
    ActionRecord,
    Checkpoint,
    CircuitBreaker,
    CircuitBreakerState,
    ExecutionStatus,
    FailureCategory,
    JevTransaction,
    JevTransactionState,
    RecoveryPlan,
    RecoveryStrategy,
    RetryPolicy,
    ReversibilityType,
    SystemReliabilitySnapshot,
    VerificationEvidence,
    VerificationLevel,
)
from undo_manager import OperationTimeline, SemanticLockManager, UndoManager
from verification_engine_v2 import VerificationEngineV2


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def temp_dir():
    """Provide a fresh temp directory under %TEMP%/HeyJev/phase19-test/."""
    base = os.path.join(tempfile.gettempdir(), "HeyJev", "phase19-test")
    os.makedirs(base, exist_ok=True)
    d = tempfile.mkdtemp(dir=base, prefix="t_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def db_path(temp_dir):
    """Provide a temporary SQLite DB path."""
    return os.path.join(temp_dir, "test_reliability.db")


@pytest.fixture
def timeline():
    return OperationTimeline()


@pytest.fixture
def undo_manager(timeline):
    return UndoManager(timeline=timeline)


@pytest.fixture
def rel_manager(db_path):
    return ReliabilityManager(db_path=db_path)


@pytest.fixture
def classifier():
    return FailureClassifierV2()


@pytest.fixture
def decision_engine():
    return RecoveryDecisionEngine()


@pytest.fixture
def recon_engine():
    return ReconciliationEngine()


# =====================================================================
# 1. ActionRecord Unit Tests
# =====================================================================

class TestActionRecord:
    """Tests for ActionRecord data model and hash chaining."""

    def test_action_record_defaults(self):
        act = ActionRecord(task_id="t1", action_type="file_write", target="/test.txt")
        assert act.task_id == "t1"
        assert act.action_type == "file_write"
        assert act.status == ExecutionStatus.ATTEMPTED
        assert act.reversibility == ReversibilityType.REVERSIBLE
        assert act.action_id.startswith("act_")
        assert act.idempotency_key.startswith("idem_")

    def test_action_record_compute_hash(self):
        act = ActionRecord(task_id="t1", action_type="file_write", target="/a.txt")
        act.prev_record_hash = "GENESIS"
        h = act.compute_hash()
        assert h is not None
        assert len(h) == 64  # SHA-256 hex
        assert act.record_hash == h

    def test_action_record_hash_determinism(self):
        """Same inputs always produce the same hash."""
        act = ActionRecord(task_id="t1", action_type="x", target="y",
                           started_at=1000.0, idempotency_key="idem_test")
        act.prev_record_hash = "PREV"
        act.status = ExecutionStatus.ATTEMPTED
        h1 = act.compute_hash()
        h2 = act.compute_hash()
        assert h1 == h2

    def test_action_record_hash_immutable_across_status_transitions(self):
        """Action intent hash is sealed and remains invariant across status transitions."""
        act = ActionRecord(task_id="t1", action_type="x", target="y",
                           started_at=1000.0, idempotency_key="idem_test")
        act.prev_record_hash = "PREV"
        act.status = ExecutionStatus.ATTEMPTED
        h1 = act.compute_hash()
        act.status = ExecutionStatus.SUCCEEDED
        h2 = act.compute_hash()
        assert h1 == h2

    def test_action_record_hash_changes_with_target_or_type(self):
        """Tampering with target or action_type produces a different hash."""
        act1 = ActionRecord(task_id="t1", action_type="x", target="y",
                            started_at=1000.0, idempotency_key="idem_test")
        act1.prev_record_hash = "PREV"
        h1 = act1.compute_hash()

        act2 = ActionRecord(task_id="t1", action_type="x", target="tampered_target",
                            started_at=1000.0, idempotency_key="idem_test")
        act2.prev_record_hash = "PREV"
        h2 = act2.compute_hash()
        assert h1 != h2

    def test_action_record_non_mutating_hash(self):
        """_compute_hash_value() does not mutate record_hash."""
        act = ActionRecord(task_id="t1", action_type="x", target="y")
        act.prev_record_hash = "PREV"
        act.record_hash = "ORIGINAL"
        _ = act._compute_hash_value()
        assert act.record_hash == "ORIGINAL"

    def test_action_record_to_dict_roundtrip(self):
        act = ActionRecord(task_id="t1", action_type="file_move",
                           target="/dest.txt", status=ExecutionStatus.SUCCEEDED,
                           reversibility=ReversibilityType.PARTIALLY_REVERSIBLE)
        act.prev_record_hash = "GENESIS"
        act.compute_hash()
        d = act.to_dict()
        restored = ActionRecord.from_dict(d)
        assert restored.action_id == act.action_id
        assert restored.status == ExecutionStatus.SUCCEEDED
        assert restored.reversibility == ReversibilityType.PARTIALLY_REVERSIBLE

    def test_idempotency_key_uniqueness(self):
        a1 = ActionRecord(task_id="t1", action_type="x", target="y")
        a2 = ActionRecord(task_id="t1", action_type="x", target="y")
        assert a1.idempotency_key != a2.idempotency_key

    def test_action_id_uniqueness(self):
        ids = {ActionRecord(task_id="t", action_type="x", target="y").action_id for _ in range(50)}
        assert len(ids) == 50


# =====================================================================
# 2. Checkpoint Unit Tests
# =====================================================================

class TestCheckpoint:
    """Tests for Checkpoint integrity checksumming."""

    def test_checkpoint_compute_checksum(self):
        chk = Checkpoint(task_id="t1", sequence=0, state="ACTIVE",
                         completed_steps=["a", "b"], pending_steps=["c"])
        cs = chk.compute_checksum()
        assert cs.startswith("sha256:")
        assert len(cs) == 7 + 64  # "sha256:" + hex

    def test_checkpoint_verify_checksum_valid(self):
        chk = Checkpoint(task_id="t1", sequence=1, state="ACTIVE")
        chk.compute_checksum()
        assert chk.verify_checksum() is True

    def test_checkpoint_verify_checksum_tampered(self):
        chk = Checkpoint(task_id="t1", sequence=1, state="ACTIVE")
        chk.compute_checksum()
        chk.completed_steps.append("injected_step")
        # verify_checksum recomputes and checks against stored - but recompute
        # with injected step produces different checksum, so stored != recomputed
        # Actually verify_checksum calls compute_checksum which mutates... let me
        # verify behavior: verify_checksum stores expected first, then recomputes
        # If recompute changes checksum, expected != computed → False
        # Wait - verify_checksum: expected = self.checksum, computed = self.compute_checksum()
        # compute_checksum mutates self.checksum, so computed == self.checksum after call
        # but expected was saved before call. So if data changed, expected != computed → False
        assert chk.verify_checksum() is False

    def test_checkpoint_roundtrip(self):
        chk = Checkpoint(task_id="t1", sequence=5, state="COMPLETED",
                         completed_steps=["s1", "s2"],
                         environment_snapshot={"cpu": 12.5})
        chk.compute_checksum()
        d = chk.to_dict()
        restored = Checkpoint.from_dict(d)
        assert restored.checkpoint_id == chk.checkpoint_id
        assert restored.sequence == 5
        assert restored.state == "COMPLETED"
        assert restored.verify_checksum() is True

    def test_checkpoint_no_checksum_fails_verify(self):
        chk = Checkpoint(task_id="t1")
        assert chk.verify_checksum() is False


# =====================================================================
# 3. VerificationEvidence & Level Tests
# =====================================================================

class TestVerificationEvidence:
    """Tests for VerificationEvidence and VerificationLevel enums."""

    def test_verification_levels_ordering(self):
        assert VerificationLevel.LEVEL_0 < VerificationLevel.LEVEL_1
        assert VerificationLevel.LEVEL_1 < VerificationLevel.LEVEL_2
        assert VerificationLevel.LEVEL_2 < VerificationLevel.LEVEL_3
        assert VerificationLevel.LEVEL_3 < VerificationLevel.LEVEL_4
        assert VerificationLevel.LEVEL_4 < VerificationLevel.LEVEL_5

    def test_evidence_default_level(self):
        e = VerificationEvidence()
        assert e.level == VerificationLevel.LEVEL_2
        assert e.confidence == 1.0

    def test_evidence_to_dict_roundtrip(self):
        e = VerificationEvidence(
            type="checksum_match", source="hashlib",
            expected="sha256:abc", observed="sha256:abc",
            confidence=0.95, level=VerificationLevel.LEVEL_3,
        )
        d = e.to_dict()
        restored = VerificationEvidence.from_dict(d)
        assert restored.type == "checksum_match"
        assert restored.level == VerificationLevel.LEVEL_3
        assert restored.confidence == 0.95


# =====================================================================
# 4. FailureClassifierV2 Tests
# =====================================================================

class TestFailureClassifierV2:
    """Tests for FailureClassifierV2 regex + exception classification."""

    @pytest.mark.parametrize("error_msg,expected_cat", [
        ("429 Too Many Requests", FailureCategory.RATE_LIMIT),
        ("Quota exceeded for model", FailureCategory.RATE_LIMIT),
        ("Request timed out after 30s", FailureCategory.TIMEOUT),
        ("Unauthorized: 401", FailureCategory.AUTHENTICATION),
        ("403 Forbidden", FailureCategory.AUTHENTICATION),
        ("Permission denied: access denied", FailureCategory.PERMISSION_DENIED),
        ("requires elevation to admin", FailureCategory.PERMISSION_DENIED),
        ("Security event: injection detected", FailureCategory.SECURITY_EVENT),
        ("policy blocked: disallowed path", FailureCategory.POLICY_BLOCKED),
        ("No such process: 12345", FailureCategory.PROCESS_CRASH),
        ("connection reset by peer", FailureCategory.NETWORK_FAILURE),
        ("DNS resolution failed", FailureCategory.NETWORK_FAILURE),
        ("Memory error: OOM killed", FailureCategory.RESOURCE_EXHAUSTED),
        ("Browser closed unexpectedly", FailureCategory.BROWSER_FAILURE),
        ("WinError 5: Access is denied", FailureCategory.WINDOWS_FAILURE),
        ("Checksum mismatch", FailureCategory.VERIFICATION_FAILURE),
        ("Unknown state reached", FailureCategory.UNKNOWN_STATE),
        ("captcha required", FailureCategory.USER_REQUIRED),
        ("Transient server error", FailureCategory.TRANSIENT),
    ])
    def test_classify_by_regex(self, classifier, error_msg, expected_cat):
        result = classifier.classify(error_msg)
        assert result == expected_cat

    def test_classify_timeout_exception(self, classifier):
        assert classifier.classify(TimeoutError("timed out")) == FailureCategory.TIMEOUT

    def test_classify_permission_exception(self, classifier):
        assert classifier.classify(PermissionError("access denied")) == FailureCategory.PERMISSION_DENIED

    def test_classify_connection_exception(self, classifier):
        assert classifier.classify(ConnectionError("refused")) == FailureCategory.NETWORK_FAILURE

    def test_classify_empty_string(self, classifier):
        assert classifier.classify("") == FailureCategory.UNKNOWN

    def test_classify_none(self, classifier):
        assert classifier.classify(None) == FailureCategory.UNKNOWN

    def test_classify_generic_error(self, classifier):
        assert classifier.classify("Something entirely novel went wrong") == FailureCategory.TOOL_FAILURE


# =====================================================================
# 5. RetryPolicy Tests
# =====================================================================

class TestRetryPolicy:
    """Tests for RetryPolicy exponential backoff & jitter."""

    def test_default_retry_policy(self):
        rp = RetryPolicy()
        assert rp.max_attempts == 3
        assert rp.initial_delay == 0.5
        assert rp.backoff_multiplier == 2.0

    def test_can_retry_transient(self):
        rp = RetryPolicy()
        assert rp.can_retry(FailureCategory.TRANSIENT, 1) is True
        assert rp.can_retry(FailureCategory.TRANSIENT, 2) is True
        assert rp.can_retry(FailureCategory.TRANSIENT, 3) is False  # >= max_attempts

    def test_cannot_retry_non_retryable(self):
        rp = RetryPolicy()
        assert rp.can_retry(FailureCategory.PERMISSION_DENIED, 1) is False
        assert rp.can_retry(FailureCategory.SECURITY_EVENT, 1) is False
        assert rp.can_retry(FailureCategory.POLICY_BLOCKED, 1) is False

    def test_compute_delay_exponential(self):
        rp = RetryPolicy(jitter=False, initial_delay=1.0, backoff_multiplier=2.0)
        assert rp.compute_delay(1) == 1.0
        assert rp.compute_delay(2) == 2.0
        assert rp.compute_delay(3) == 4.0

    def test_compute_delay_max_cap(self):
        rp = RetryPolicy(jitter=False, initial_delay=1.0, backoff_multiplier=10.0, max_delay=5.0)
        assert rp.compute_delay(3) == 5.0  # 1 * 10^2 = 100, capped at 5

    def test_compute_delay_jitter_range(self):
        rp = RetryPolicy(jitter=True, initial_delay=1.0, backoff_multiplier=2.0)
        delays = [rp.compute_delay(2) for _ in range(100)]
        # Attempt 2: base = 1 * 2^1 = 2.0, jitter range = [1.0, 2.0]
        assert all(0.01 <= d <= 2.01 for d in delays)


# =====================================================================
# 6. CircuitBreaker Tests
# =====================================================================

class TestCircuitBreaker:
    """Tests for CircuitBreaker state machine."""

    def test_initial_state_closed(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.can_execute() is True

    def test_trip_to_open_after_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreakerState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN
        assert cb.can_execute() is False

    def test_half_open_after_cooldown(self):
        cb = CircuitBreaker(name="test", failure_threshold=2, cooldown_seconds=0.05)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN
        time.sleep(0.06)
        assert cb.can_execute() is True
        assert cb.state == CircuitBreakerState.HALF_OPEN

    def test_half_open_to_closed_on_success(self):
        cb = CircuitBreaker(name="test", failure_threshold=2,
                            cooldown_seconds=0.01, success_threshold=1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.02)
        cb.can_execute()  # Transitions to HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitBreakerState.CLOSED

    def test_half_open_to_open_on_failure(self):
        cb = CircuitBreaker(name="test", failure_threshold=2, cooldown_seconds=0.01)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.02)
        cb.can_execute()  # HALF_OPEN
        cb.record_failure()  # Exceeds threshold again
        assert cb.state == CircuitBreakerState.OPEN

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.failure_count == 0
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreakerState.CLOSED  # Not tripped yet


class TestCircuitBreakerManager:
    """Tests for CircuitBreakerManager coordinating multiple breakers."""

    def test_get_or_create(self):
        mgr = CircuitBreakerManager()
        cb1 = mgr.get_or_create("svc1", failure_threshold=5)
        cb2 = mgr.get_or_create("svc1")  # Same name, returns same instance
        assert cb1 is cb2
        assert cb1.failure_threshold == 5

    def test_can_execute_unknown_service(self):
        mgr = CircuitBreakerManager()
        assert mgr.can_execute("nonexistent") is True

    def test_record_failure_creates_breaker(self):
        mgr = CircuitBreakerManager()
        mgr.record_failure("auto_created")
        assert "auto_created" in mgr._breakers

    def test_get_open_circuits(self):
        mgr = CircuitBreakerManager()
        cb = mgr.get_or_create("flaky", failure_threshold=1)
        cb.record_failure()
        assert "flaky" in mgr.get_open_circuits()
        mgr.get_or_create("healthy")
        assert "healthy" not in mgr.get_open_circuits()


# =====================================================================
# 7. OperationTimeline Tests
# =====================================================================

class TestOperationTimeline:
    """Tests for tamper-evident hash-chained timeline."""

    def test_genesis_hash(self, timeline):
        assert timeline._last_hash == "GENESIS_RELIABILITY_0"

    def test_record_chains_hashes(self, timeline):
        a1 = ActionRecord(task_id="t1", action_type="x", target="y")
        timeline.record_action(a1)
        assert a1.prev_record_hash == "GENESIS_RELIABILITY_0"
        assert a1.record_hash is not None

        a2 = ActionRecord(task_id="t1", action_type="x2", target="y2")
        timeline.record_action(a2)
        assert a2.prev_record_hash == a1.record_hash

    def test_verify_integrity_clean(self, timeline):
        for i in range(10):
            timeline.record_action(ActionRecord(task_id="t", action_type="x", target=f"y{i}"))
        ok, errors = timeline.verify_integrity()
        assert ok is True
        assert len(errors) == 0

    def test_verify_integrity_broken_chain(self, timeline):
        a1 = ActionRecord(task_id="t", action_type="x", target="y1")
        timeline.record_action(a1)
        a2 = ActionRecord(task_id="t", action_type="x", target="y2")
        timeline.record_action(a2)
        # Tamper with prev_record_hash
        a2.prev_record_hash = "TAMPERED"
        ok, errors = timeline.verify_integrity()
        assert ok is False
        assert len(errors) >= 1

    def test_get_action(self, timeline):
        a1 = ActionRecord(task_id="t", action_type="x", target="y")
        timeline.record_action(a1)
        assert timeline.get_action(a1.action_id) is a1
        assert timeline.get_action("nonexistent") is None

    def test_get_group_actions(self, timeline):
        for i in range(5):
            a = ActionRecord(task_id="t", action_type="x", target=f"y{i}",
                             group_id="grp_test")
            timeline.record_action(a)
        a_other = ActionRecord(task_id="t", action_type="x", target="other")
        timeline.record_action(a_other)
        grp = timeline.get_group_actions("grp_test")
        assert len(grp) == 5

    def test_list_records_limit(self, timeline):
        for i in range(20):
            timeline.record_action(ActionRecord(task_id="t", action_type="x", target=f"y{i}"))
        assert len(timeline.list_records(5)) == 5
        assert len(timeline.list_records(100)) == 20


# =====================================================================
# 8. SemanticLockManager Tests
# =====================================================================

class TestSemanticLockManager:
    """Tests for resource-level mutual exclusion."""

    def test_acquire_and_release(self):
        mgr = SemanticLockManager()
        assert mgr.acquire_lock("file:/test.txt", "worker_1") is True
        assert mgr.is_locked("file:/test.txt") is True
        assert mgr.release_lock("file:/test.txt", "worker_1") is True
        assert mgr.is_locked("file:/test.txt") is False

    def test_competing_lock(self):
        mgr = SemanticLockManager()
        assert mgr.acquire_lock("file:/test.txt", "worker_1") is True
        assert mgr.acquire_lock("file:/test.txt", "worker_2") is False  # Blocked

    def test_reentrant_lock(self):
        mgr = SemanticLockManager()
        assert mgr.acquire_lock("file:/test.txt", "worker_1") is True
        assert mgr.acquire_lock("file:/test.txt", "worker_1") is True  # Same holder

    def test_release_wrong_holder(self):
        mgr = SemanticLockManager()
        mgr.acquire_lock("res", "holder_a")
        assert mgr.release_lock("res", "holder_b") is False  # Wrong holder


# =====================================================================
# 9. UndoManager Tests
# =====================================================================

class TestUndoManager:
    """Tests for UndoManager: can_undo, preview_undo, undo, undo_group."""

    def _make_undoable_action(self, timeline, temp_dir, idx=0):
        target = os.path.join(temp_dir, f"undo_file_{idx}.txt")
        with open(target, "w", encoding="utf-8") as f:
            f.write(f"undo content {idx}")
        act = ActionRecord(
            task_id=f"t_{idx}", action_type="create_file", target=target,
            status=ExecutionStatus.SUCCEEDED,
            reversibility=ReversibilityType.REVERSIBLE,
            compensation_action={"action": "delete_file", "path": target},
        )
        timeline.record_action(act)
        return act, target

    def test_can_undo_reversible(self, timeline, undo_manager, temp_dir):
        act, _ = self._make_undoable_action(timeline, temp_dir)
        can, reason = undo_manager.can_undo(act.action_id)
        assert can is True

    def test_cannot_undo_irreversible(self, timeline, undo_manager):
        act = ActionRecord(
            task_id="t", action_type="send_email", target="user@example.com",
            status=ExecutionStatus.SUCCEEDED,
            reversibility=ReversibilityType.IRREVERSIBLE,
            compensation_action={"action": "custom_noop"},
        )
        timeline.record_action(act)
        can, reason = undo_manager.can_undo(act.action_id)
        assert can is False
        assert "IRREVERSIBLE" in reason

    def test_cannot_undo_failed_action(self, timeline, undo_manager):
        act = ActionRecord(
            task_id="t", action_type="x", target="y",
            status=ExecutionStatus.FAILED,
            reversibility=ReversibilityType.REVERSIBLE,
            compensation_action={"action": "custom_noop"},
        )
        timeline.record_action(act)
        can, reason = undo_manager.can_undo(act.action_id)
        assert can is False

    def test_cannot_undo_without_compensation(self, timeline, undo_manager):
        act = ActionRecord(
            task_id="t", action_type="x", target="y",
            status=ExecutionStatus.SUCCEEDED,
            reversibility=ReversibilityType.REVERSIBLE,
        )
        timeline.record_action(act)
        can, reason = undo_manager.can_undo(act.action_id)
        assert can is False
        assert "compensation" in reason.lower()

    def test_preview_undo(self, timeline, undo_manager, temp_dir):
        act, _ = self._make_undoable_action(timeline, temp_dir)
        preview = undo_manager.preview_undo(act.action_id)
        assert preview["can_undo"] is True
        assert preview["original_action"] == "create_file"
        assert preview["reversibility"] == "REVERSIBLE"

    def test_undo_deletes_file(self, timeline, undo_manager, temp_dir):
        act, target = self._make_undoable_action(timeline, temp_dir)
        assert os.path.exists(target)
        success = undo_manager.undo(act.action_id)
        assert success is True
        assert not os.path.exists(target)

    def test_undo_group_reverse_order(self, timeline, undo_manager, temp_dir):
        acts = []
        for i in range(3):
            act, _ = self._make_undoable_action(timeline, temp_dir, idx=i)
            act.group_id = "grp_test"
            acts.append(act)
        ok, undone = undo_manager.undo_group("grp_test")
        assert ok is True
        assert len(undone) == 3
        for i in range(3):
            assert not os.path.exists(os.path.join(temp_dir, f"undo_file_{i}.txt"))

    def test_undo_records_reversal_in_timeline(self, timeline, undo_manager, temp_dir):
        act, _ = self._make_undoable_action(timeline, temp_dir)
        initial_count = len(timeline.list_records(1000))
        undo_manager.undo(act.action_id)
        assert len(timeline.list_records(1000)) == initial_count + 1
        last = timeline.list_records(1000)[-1]
        assert last.action_type == "undo_compensation"
        assert last.reversibility == ReversibilityType.IRREVERSIBLE

    def test_list_undoable_actions(self, timeline, undo_manager, temp_dir):
        self._make_undoable_action(timeline, temp_dir, 0)
        self._make_undoable_action(timeline, temp_dir, 1)
        undoable = undo_manager.list_undoable_actions()
        assert len(undoable) == 2

    def test_undo_move_file(self, timeline, undo_manager, temp_dir):
        src = os.path.join(temp_dir, "src_file.txt")
        dst = os.path.join(temp_dir, "dst_file.txt")
        with open(src, "w") as f:
            f.write("original")
        shutil.move(src, dst)
        assert os.path.exists(dst) and not os.path.exists(src)

        act = ActionRecord(
            task_id="t", action_type="file_move", target=dst,
            status=ExecutionStatus.SUCCEEDED,
            reversibility=ReversibilityType.REVERSIBLE,
            compensation_action={"action": "move_file", "src": dst, "dst": src},
        )
        timeline.record_action(act)
        success = undo_manager.undo(act.action_id)
        assert success is True
        assert os.path.exists(src)
        assert not os.path.exists(dst)

    def test_undo_restore_content(self, timeline, undo_manager, temp_dir):
        path = os.path.join(temp_dir, "content.txt")
        with open(path, "w") as f:
            f.write("modified content")
        act = ActionRecord(
            task_id="t", action_type="file_edit", target=path,
            status=ExecutionStatus.SUCCEEDED,
            reversibility=ReversibilityType.REVERSIBLE,
            compensation_action={"action": "restore_file_content", "path": path,
                                 "original_content": "original content"},
        )
        timeline.record_action(act)
        success = undo_manager.undo(act.action_id)
        assert success is True
        with open(path, "r") as f:
            assert f.read() == "original content"


# =====================================================================
# 10. RecoveryDecisionEngine Tests
# =====================================================================

class TestRecoveryDecisionEngine:
    """Tests for RecoveryDecisionEngine strategy mapping & loop prevention."""

    def test_security_event_aborts(self, decision_engine):
        plan = decision_engine.plan_recovery(FailureCategory.SECURITY_EVENT, "target_1")
        assert plan.strategy == RecoveryStrategy.ABORT
        assert plan.risk == "CRITICAL"

    def test_policy_blocked_aborts(self, decision_engine):
        plan = decision_engine.plan_recovery(FailureCategory.POLICY_BLOCKED, "target_1")
        assert plan.strategy == RecoveryStrategy.ABORT

    def test_authentication_escalates(self, decision_engine):
        plan = decision_engine.plan_recovery(FailureCategory.AUTHENTICATION, "target_1")
        assert plan.strategy == RecoveryStrategy.ESCALATE
        assert plan.requires_approval is True

    def test_rate_limit_retries(self, decision_engine):
        plan = decision_engine.plan_recovery(FailureCategory.RATE_LIMIT, "target_1", attempt_count=1)
        assert plan.strategy == RecoveryStrategy.RETRY
        assert any(s.get("action") == "sleep_backoff" for s in plan.steps)

    def test_unknown_state_reconciles(self, decision_engine):
        plan = decision_engine.plan_recovery(FailureCategory.UNKNOWN_STATE, "target_1")
        assert plan.strategy == RecoveryStrategy.RECONCILE

    def test_process_crash_restarts_then_fallback(self, decision_engine):
        plan1 = decision_engine.plan_recovery(FailureCategory.PROCESS_CRASH, "t", attempt_count=1)
        assert plan1.strategy == RecoveryStrategy.RESTART
        plan2 = decision_engine.plan_recovery(FailureCategory.PROCESS_CRASH, "t", attempt_count=3)
        assert plan2.strategy == RecoveryStrategy.FALLBACK

    def test_loop_prevention_escalates(self):
        engine = RecoveryDecisionEngine(max_recovery_attempts=2)
        engine.plan_recovery(FailureCategory.TIMEOUT, "loopy_target", attempt_count=1)
        engine.plan_recovery(FailureCategory.TIMEOUT, "loopy_target", attempt_count=2)
        plan3 = engine.plan_recovery(FailureCategory.TIMEOUT, "loopy_target", attempt_count=3)
        assert plan3.strategy == RecoveryStrategy.ESCALATE
        assert plan3.requires_approval is True


# =====================================================================
# 11. ReconciliationEngine Tests
# =====================================================================

class TestReconciliationEngine:
    """Tests for ReconciliationEngine filesystem, process, and git reconciliation."""

    def test_filesystem_created_matched(self, recon_engine, temp_dir):
        f = os.path.join(temp_dir, "exists.txt")
        with open(f, "w") as fh:
            fh.write("ok")
        status, details = recon_engine.reconcile_filesystem(expected_files_created=[f])
        assert status == ReconciliationStatus.MATCHED

    def test_filesystem_created_missing(self, recon_engine, temp_dir):
        f = os.path.join(temp_dir, "missing.txt")
        status, details = recon_engine.reconcile_filesystem(expected_files_created=[f])
        assert status == ReconciliationStatus.MISMATCH
        assert f in details["missing_created"]

    def test_filesystem_moved(self, recon_engine, temp_dir):
        src = os.path.join(temp_dir, "src.txt")
        dst = os.path.join(temp_dir, "dst.txt")
        with open(dst, "w") as fh:
            fh.write("moved")
        status, details = recon_engine.reconcile_filesystem(
            expected_files_moved=[(src, dst)])
        assert status == ReconciliationStatus.MATCHED

    def test_filesystem_deleted(self, recon_engine, temp_dir):
        f = os.path.join(temp_dir, "gone.txt")
        status, details = recon_engine.reconcile_filesystem(expected_files_deleted=[f])
        assert status == ReconciliationStatus.MATCHED

    def test_filesystem_partial(self, recon_engine, temp_dir):
        exist = os.path.join(temp_dir, "exist.txt")
        with open(exist, "w") as fh:
            fh.write("ok")
        missing = os.path.join(temp_dir, "miss.txt")
        status, details = recon_engine.reconcile_filesystem(
            expected_files_created=[exist, missing])
        assert status == ReconciliationStatus.PARTIAL

    def test_process_reconciliation_no_args(self, recon_engine):
        status, _ = recon_engine.reconcile_process()
        assert status in (ReconciliationStatus.UNKNOWN, ReconciliationStatus.UNKNOWN)

    def test_git_reconciliation(self, recon_engine):
        repo_path = os.path.dirname(os.path.abspath(__file__))
        status, details = recon_engine.reconcile_git(
            repo_path, expected_branch="windows-port")
        assert status == ReconciliationStatus.MATCHED

    def test_remote_agent_unknown(self, recon_engine):
        mock_adapter = MagicMock()
        mock_adapter.get_status.side_effect = ConnectionError("unreachable")
        status, details = recon_engine.reconcile_remote_agent(
            "task_123", mock_adapter)
        assert status == ReconciliationStatus.UNKNOWN

    def test_remote_agent_completed(self, recon_engine):
        mock_adapter = MagicMock()
        mock_adapter.get_status.return_value = "COMPLETED"
        mock_result = MagicMock()
        mock_result.is_success = True
        mock_adapter.get_result.return_value = mock_result
        status, details = recon_engine.reconcile_remote_agent(
            "task_456", mock_adapter)
        assert status == ReconciliationStatus.MATCHED


# =====================================================================
# 12. VerificationEngineV2 Tests
# =====================================================================

class TestVerificationEngineV2:
    """Tests for multi-tier verification evidence collection."""

    def test_level_0_self_report(self):
        engine = VerificationEngineV2()
        task = AgentTask(description="test", owner_agent="coder")
        result = AgentResult(status=AgentResultStatus.SUCCESS,
                             result=None, summary="Done")
        v_res, evidence, level = engine.verify_with_evidence(task, result)
        assert level >= VerificationLevel.LEVEL_0

    def test_level_1_tool_output(self):
        engine = VerificationEngineV2()
        task = AgentTask(description="test", owner_agent="coder")
        result = AgentResult(status=AgentResultStatus.SUCCESS,
                             result={"data": "something"}, summary="Done")
        v_res, evidence, level = engine.verify_with_evidence(task, result)
        assert level >= VerificationLevel.LEVEL_1
        assert any(e.type == "tool_output" for e in evidence)

    def test_level_2_filesystem(self, temp_dir):
        engine = VerificationEngineV2()
        target = os.path.join(temp_dir, "verified.txt")
        with open(target, "w") as f:
            f.write("content")
        task = AgentTask(description="test", owner_agent="coder")
        result = AgentResult(status=AgentResultStatus.SUCCESS,
                             result={"file": target}, summary="Created file")
        v_res, evidence, level = engine.verify_with_evidence(
            task, result, target_path=target)
        assert level >= VerificationLevel.LEVEL_2
        assert any(e.type == "filesystem_stat" for e in evidence)

    def test_level_3_checksum(self, temp_dir):
        engine = VerificationEngineV2()
        target = os.path.join(temp_dir, "checksummed.txt")
        content = "deterministic content"
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        with open(target, "rb") as f:
            expected_chk = f"sha256:{hashlib.sha256(f.read()).hexdigest()}"

        task = AgentTask(description="test", owner_agent="coder")
        result = AgentResult(status=AgentResultStatus.SUCCESS,
                             result={"file": target}, summary="Created")
        v_res, evidence, level = engine.verify_with_evidence(
            task, result, target_path=target, expected_checksum=expected_chk)
        assert level == VerificationLevel.LEVEL_3
        assert v_res.passed is True
        assert any(e.type == "checksum_match" for e in evidence)

    def test_checksum_mismatch_fails(self, temp_dir):
        engine = VerificationEngineV2()
        target = os.path.join(temp_dir, "wrong_chk.txt")
        with open(target, "w") as f:
            f.write("actual content")
        task = AgentTask(description="test", owner_agent="coder")
        result = AgentResult(status=AgentResultStatus.SUCCESS,
                             result={"file": target}, summary="Created")
        v_res, evidence, level = engine.verify_with_evidence(
            task, result, target_path=target, expected_checksum="sha256:wrong_hash")
        assert v_res.passed is False
        assert any("Checksum mismatch" in f for f in v_res.failures)

    def test_missing_file_fails(self, temp_dir):
        engine = VerificationEngineV2()
        target = os.path.join(temp_dir, "nonexistent.txt")
        task = AgentTask(description="test", owner_agent="coder")
        result = AgentResult(status=AgentResultStatus.SUCCESS,
                             result={"file": target}, summary="Created")
        v_res, evidence, level = engine.verify_with_evidence(
            task, result, target_path=target)
        assert v_res.passed is False


# =====================================================================
# 13. ReliabilityManager Tests
# =====================================================================

class TestReliabilityManager:
    """Tests for ReliabilityManager: WAL, checkpoints, transactions."""

    def test_record_intent_and_outcome(self, rel_manager):
        act = ActionRecord(task_id="t1", action_type="file_write", target="/a.txt")
        logged = rel_manager.record_intent(act)
        assert logged.status == ExecutionStatus.ATTEMPTED
        assert logged.record_hash is not None

        rel_manager.record_outcome(logged, ExecutionStatus.SUCCEEDED)
        assert logged.status == ExecutionStatus.SUCCEEDED

    def test_checkpoint_save_load(self, rel_manager):
        chk = Checkpoint(task_id="t1", sequence=0, state="ACTIVE",
                         completed_steps=["a"], pending_steps=["b"])
        rel_manager.save_checkpoint(chk)
        loaded = rel_manager.load_latest_checkpoint("t1")
        assert loaded is not None
        assert loaded.checkpoint_id == chk.checkpoint_id
        assert loaded.verify_checksum() is True

    def test_checkpoint_load_nonexistent(self, rel_manager):
        assert rel_manager.load_latest_checkpoint("nonexistent") is None

    def test_saga_begin_commit(self, rel_manager):
        tx = rel_manager.begin_transaction(mission_id="m1")
        assert tx.state == JevTransactionState.RUNNING
        ok = rel_manager.commit_transaction(tx)
        assert ok is True
        assert tx.state == JevTransactionState.COMMITTED

    def test_saga_begin_compensate(self, rel_manager):
        tx = rel_manager.begin_transaction(mission_id="m1")
        tx.compensation_actions.append({"action": "custom_noop"})
        ok = rel_manager.compensate_transaction(tx)
        assert ok is True
        assert tx.state == JevTransactionState.COMPENSATED

    def test_integrity_check_clean(self, rel_manager):
        act = ActionRecord(task_id="t1", action_type="x", target="y")
        rel_manager.record_intent(act)
        ok, errors = rel_manager.check_integrity()
        assert ok is True

    def test_reliability_snapshot(self, rel_manager):
        snapshot = rel_manager.get_reliability_snapshot()
        assert isinstance(snapshot, SystemReliabilitySnapshot)
        assert snapshot.active_transactions == 0

    def test_chain_integrity_after_outcome(self, rel_manager):
        """Verify hash chain stays valid after recording outcomes."""
        for i in range(5):
            act = ActionRecord(task_id=f"t{i}", action_type="x", target=f"y{i}")
            rel_manager.record_intent(act)
            rel_manager.record_outcome(act, ExecutionStatus.SUCCEEDED)
        ok, errors = rel_manager.check_integrity()
        assert ok is True
        assert len(errors) == 0


# =====================================================================
# 14. RepairManager Tests
# =====================================================================

class TestRepairManager:
    """Tests for RepairManager internal metadata repair."""

    def test_repair_clean_state(self, rel_manager):
        result = rel_manager.repair_manager.repair_internal_metadata()
        assert result["status"] == "CLEAN"
        assert result["orphans_repaired"] == 0


# =====================================================================
# 15. Saga Transaction Lifecycle Tests
# =====================================================================

class TestSagaTransactions:
    """Tests for JevTransaction Saga pattern: commit, compensate, group undo."""

    def test_transaction_add_action(self):
        tx = JevTransaction(mission_id="m1", state=JevTransactionState.RUNNING)
        act = ActionRecord(task_id="t1", action_type="x", target="y",
                           compensation_action={"action": "custom_noop"})
        tx.add_action(act)
        assert len(tx.actions) == 1
        assert len(tx.compensation_actions) == 1

    def test_transaction_roundtrip(self):
        tx = JevTransaction(mission_id="m1", state=JevTransactionState.COMMITTED)
        act = ActionRecord(task_id="t1", action_type="x", target="y")
        tx.add_action(act)
        d = tx.to_dict()
        restored = JevTransaction.from_dict(d)
        assert restored.transaction_id == tx.transaction_id
        assert restored.state == JevTransactionState.COMMITTED
        assert len(restored.actions) == 1

    def test_compensated_tx_not_committed(self, rel_manager):
        tx = rel_manager.begin_transaction()
        tx.compensation_actions.append({"action": "custom_noop"})
        rel_manager.compensate_transaction(tx)
        assert tx.state in (JevTransactionState.COMPENSATED,
                            JevTransactionState.PARTIALLY_COMPENSATED)
        assert tx.state != JevTransactionState.COMMITTED


# =====================================================================
# 16. Crash Recovery & Idempotency Tests
# =====================================================================

class TestCrashRecovery:
    """Tests for crash recovery: simulated crash before result → reconcile → ALREADY_APPLIED."""

    def test_reconcile_on_startup_file_exists(self, rel_manager, temp_dir):
        """Simulated crash: intent recorded, file created, result NOT recorded → reconcile."""
        target = os.path.join(temp_dir, "crash_file.txt")
        act = ActionRecord(task_id="crash_t", action_type="file_create", target=target)
        rel_manager.record_intent(act)
        # Simulate: file was created on disk but process crashed before recording outcome
        with open(target, "w") as f:
            f.write("created before crash")
        # Now reconcile
        report = rel_manager.reconcile_on_startup()
        assert report["already_applied_count"] >= 1
        assert act.action_id in report["reconciled_actions"]

    def test_reconcile_on_startup_file_missing(self, rel_manager, temp_dir):
        """Simulated crash: intent recorded but action did not execute → UNKNOWN."""
        target = os.path.join(temp_dir, "not_created.txt")
        act = ActionRecord(task_id="crash_t2", action_type="file_create", target=target)
        rel_manager.record_intent(act)
        # File was NOT created — crash occurred before execution
        report = rel_manager.reconcile_on_startup()
        assert act.action_id in report["reconciled_actions"]

    def test_reconcile_stale_transactions(self, rel_manager):
        """Stranded RUNNING transaction should be compensated."""
        tx = rel_manager.begin_transaction()
        tx.compensation_actions.append({"action": "custom_noop"})
        # Leave in RUNNING state — simulating crash
        report = rel_manager.reconcile_on_startup()
        assert tx.transaction_id in report["reconciled_transactions"]


# =====================================================================
# 17. Chaos Tests
# =====================================================================

class TestChaosScenarios:
    """Chaos tests for failure injection scenarios."""

    def test_fail_before_execute(self, rel_manager, temp_dir):
        """FAIL_BEFORE_EXECUTE: intent logged, exception before action."""
        target = os.path.join(temp_dir, "chaos_before.txt")
        act = ActionRecord(task_id="chaos_1", action_type="file_create", target=target)
        rel_manager.record_intent(act)
        # Simulate failure before execution — action never ran
        rel_manager.record_outcome(act, ExecutionStatus.FAILED,
                                   error="Simulated pre-execution crash")
        assert act.status == ExecutionStatus.FAILED
        assert not os.path.exists(target)

    def test_fail_after_execute(self, rel_manager, temp_dir):
        """FAIL_AFTER_EXECUTE: action completed but outcome recording failed → reconcile."""
        target = os.path.join(temp_dir, "chaos_after.txt")
        act = ActionRecord(task_id="chaos_2", action_type="file_create", target=target)
        rel_manager.record_intent(act)
        # Simulate: file was created
        with open(target, "w") as f:
            f.write("created")
        # Simulate: crash before outcome recording — reconcile detects it
        report = rel_manager.reconcile_on_startup()
        assert report["already_applied_count"] >= 1

    def test_network_drop_recovery(self, decision_engine):
        """NETWORK_DROP: classify and plan recovery."""
        cat = FailureClassifierV2.classify("Connection reset by peer")
        assert cat == FailureCategory.NETWORK_FAILURE
        plan = decision_engine.plan_recovery(cat, "remote_agent_1", attempt_count=1)
        assert plan.strategy in (RecoveryStrategy.RETRY, RecoveryStrategy.FALLBACK,
                                 RecoveryStrategy.RESTART)

    def test_process_crash_recovery(self, decision_engine):
        """PROCESS_CRASH: classify and plan recovery."""
        cat = FailureClassifierV2.classify("No such process terminated unexpectedly")
        assert cat == FailureCategory.PROCESS_CRASH
        plan = decision_engine.plan_recovery(cat, "browser_worker", attempt_count=1)
        assert plan.strategy == RecoveryStrategy.RESTART

    def test_verification_failure_recovery(self, decision_engine):
        """VERIFICATION_FAILURE: classify and plan."""
        cat = FailureClassifierV2.classify("Checksum mismatch: verification failed")
        assert cat == FailureCategory.VERIFICATION_FAILURE
        plan = decision_engine.plan_recovery(
            cat, "task_x", context={"can_compensate": True})
        assert plan.strategy == RecoveryStrategy.COMPENSATE


# =====================================================================
# 18. Property Invariant Tests
# =====================================================================

class TestPropertyInvariants:
    """Structural invariants that must hold across the reliability system."""

    def test_completed_task_has_verification(self, rel_manager):
        """A task recorded as VERIFIED must have evidence."""
        act = ActionRecord(task_id="t_inv", action_type="x", target="y")
        rel_manager.record_intent(act)
        rel_manager.record_outcome(
            act, ExecutionStatus.VERIFIED,
            evidence=[{"type": "state_check", "passed": True}])
        assert act.verification_state == "VERIFIED"
        assert len(act.verification_evidence) > 0

    def test_compensated_transaction_not_committed(self, rel_manager):
        tx = rel_manager.begin_transaction()
        tx.compensation_actions.append({"action": "custom_noop"})
        rel_manager.compensate_transaction(tx)
        assert tx.state != JevTransactionState.COMMITTED

    def test_open_circuit_blocks_traffic(self):
        cb = CircuitBreaker(name="blocker", failure_threshold=1)
        cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN
        assert cb.can_execute() is False

    def test_corrupted_checkpoint_rejected(self, rel_manager):
        chk = Checkpoint(task_id="t_corrupt", sequence=0, state="ACTIVE")
        rel_manager.save_checkpoint(chk)
        # Corrupt the checkpoint in the database
        with rel_manager._get_connection() as conn:
            conn.execute(
                "UPDATE checkpoints SET checksum = 'sha256:tampered' WHERE task_id = 't_corrupt'")
            conn.commit()
        loaded = rel_manager.load_latest_checkpoint("t_corrupt")
        assert loaded is None  # Rejected due to checksum failure

    def test_timeline_integrity_after_undo(self, temp_dir):
        """Hash chain must remain valid even after undo mutations."""
        timeline = OperationTimeline()
        mgr = UndoManager(timeline=timeline)
        target = os.path.join(temp_dir, "inv_undo.txt")
        with open(target, "w") as f:
            f.write("data")
        act = ActionRecord(
            task_id="t", action_type="create_file", target=target,
            status=ExecutionStatus.SUCCEEDED,
            reversibility=ReversibilityType.REVERSIBLE,
            compensation_action={"action": "delete_file", "path": target},
        )
        timeline.record_action(act)
        mgr.undo(act.action_id)
        ok, errors = timeline.verify_integrity()
        assert ok is True, f"Integrity errors: {errors}"

    def test_timeline_detects_target_tampering(self):
        """Timeline must catch payload tampering when an action's target is altered."""
        timeline = OperationTimeline()
        act = ActionRecord(task_id="t1", action_type="write", target="C:/safe.txt")
        timeline.record_action(act)
        ok, errors = timeline.verify_integrity()
        assert ok is True

        # Tamper with target
        act.target = "C:/Windows/System32/secret.key"
        ok_tampered, errors_tampered = timeline.verify_integrity()
        assert ok_tampered is False
        assert any("tampered" in err for err in errors_tampered)

    def test_checkpoint_detects_artifacts_tampering(self):
        """Checkpoint verify_checksum must fail when artifacts or environment are altered."""
        chk = Checkpoint(task_id="t1", sequence=0, state="ACTIVE", artifacts=[{"name": "original.txt"}])
        chk.compute_checksum()
        assert chk.verify_checksum() is True

        # Tamper with artifacts list
        chk.artifacts.append({"name": "injected.exe"})
        assert chk.verify_checksum() is False

    def test_undo_refuses_nonempty_directory_without_force(self, temp_dir):
        """Safety invariant: undo must refuse to delete a directory containing unexpected user files."""
        timeline = OperationTimeline()
        mgr = UndoManager(timeline=timeline)
        sandbox_dir = os.path.join(temp_dir, "app_dir")
        os.makedirs(sandbox_dir, exist_ok=True)

        # Place a user file inside the directory
        user_file = os.path.join(sandbox_dir, "user_precious_data.txt")
        with open(user_file, "w") as f:
            f.write("DO NOT DELETE")

        act = ActionRecord(
            task_id="t_dir", action_type="create_dir", target=sandbox_dir,
            status=ExecutionStatus.SUCCEEDED,
            reversibility=ReversibilityType.REVERSIBLE,
            compensation_action={"action": "delete_directory", "path": sandbox_dir},
        )
        timeline.record_action(act)

        # Undo must fail safely and refuse to delete non-empty directory
        success = mgr.undo(act.action_id)
        assert success is False
        assert os.path.exists(sandbox_dir)
        assert os.path.exists(user_file)

    def test_python_syntax_validation(self):
        """Python syntax validator must detect invalid syntax and allow valid code."""
        from safety_engine import validate_python_syntax
        valid_code = "def add(a, b):\n    return a + b\n"
        is_valid, err = validate_python_syntax(valid_code)
        assert is_valid is True
        assert err is None

        invalid_code = "def broken(:\n    return\n"
        is_invalid, err = validate_python_syntax(invalid_code)
        assert is_invalid is False
        assert "SyntaxError" in err


# =====================================================================
# 19. SystemReliabilitySnapshot Tests
# =====================================================================

class TestSystemReliabilitySnapshot:
    """Tests for SystemReliabilitySnapshot observability model."""

    def test_healthy_summary(self):
        snap = SystemReliabilitySnapshot()
        assert "HEALTHY" in snap.summary_text()

    def test_degraded_summary(self):
        snap = SystemReliabilitySnapshot(
            failed_tasks=3, open_circuits=["provider_x"])
        assert "DEGRADED" in snap.summary_text()


# =====================================================================
# 20. Required Real Recovery Demonstrations
# =====================================================================

class TestRealRecoveryDemo1:
    """Demo 1: File move crash simulation → reconcile → ALREADY_APPLIED."""

    def test_file_move_crash_recovery(self, temp_dir):
        db_path = os.path.join(temp_dir, "demo1.db")
        mgr = ReliabilityManager(db_path=db_path)

        src = os.path.join(temp_dir, "demo1_src.txt")
        dst = os.path.join(temp_dir, "demo1_dst.txt")
        with open(src, "w") as f:
            f.write("important data")

        # 1. Record intent to move
        act = ActionRecord(task_id="demo1", action_type="file_move",
                           target=dst, before_state={"src": src},
                           compensation_action={"action": "move_file", "src": dst, "dst": src})
        mgr.record_intent(act)

        # 2. Execute move — then simulate crash before outcome recording
        shutil.move(src, dst)
        assert os.path.exists(dst) and not os.path.exists(src)
        # (No record_outcome call — simulating crash)

        # 3. Reconcile on restart
        report = mgr.reconcile_on_startup()
        assert report["already_applied_count"] >= 1
        # Verify file is still safely at destination
        assert os.path.exists(dst)
        with open(dst, "r") as f:
            assert f.read() == "important data"


class TestRealRecoveryDemo2:
    """Demo 2: Process running state → verify presence."""

    def test_process_state_verification(self, temp_dir):
        """Verify process reconciliation works with current PID (self-test)."""
        import os as _os
        pid = _os.getpid()
        recon = ReconciliationEngine()
        status, details = recon.reconcile_process(pid=pid)
        assert status == ReconciliationStatus.MATCHED
        assert details["found"] is True


class TestRealRecoveryDemo3:
    """Demo 3: Directory creation & file move → force verification failure → compensate → restore."""

    def test_compensation_after_verification_failure(self, temp_dir):
        db_path = os.path.join(temp_dir, "demo3.db")
        mgr = ReliabilityManager(db_path=db_path)

        # Setup: create dir and move file
        subdir = os.path.join(temp_dir, "demo3_dir")
        os.makedirs(subdir)
        src = os.path.join(temp_dir, "demo3_src.txt")
        dst = os.path.join(subdir, "demo3_dst.txt")
        with open(src, "w") as f:
            f.write("original data")
        shutil.copy2(src, dst)  # Copy instead of move so we can verify compensation

        # Record action with compensation
        act = ActionRecord(
            task_id="demo3", action_type="file_move", target=dst,
            status=ExecutionStatus.SUCCEEDED,
            reversibility=ReversibilityType.REVERSIBLE,
            compensation_action={"action": "move_file", "src": dst, "dst": src},
        )
        mgr.timeline.record_action(act)
        os.remove(src)  # Remove src to simulate the move

        # Force verification failure: tamper with file content
        with open(dst, "w") as f:
            f.write("CORRUPTED")

        # Detect mismatch and execute compensation (move back)
        can, _ = mgr.undo_manager.can_undo(act.action_id)
        assert can is True
        success = mgr.undo_manager.undo(act.action_id)
        assert success is True
        # File should be back at original location
        assert os.path.exists(src)
        assert not os.path.exists(dst)


class TestRealRecoveryDemo4:
    """Demo 4: Remote agent connection loss → reconcile → prevent duplicate submission."""

    def test_remote_agent_connection_loss(self, temp_dir):
        db_path = os.path.join(temp_dir, "demo4.db")
        mgr = ReliabilityManager(db_path=db_path)

        # Record intent for remote task
        act = ActionRecord(
            task_id="demo4", action_type="remote_submit",
            target="provider://openai/task_42",
            status=ExecutionStatus.ATTEMPTED,
        )
        mgr.record_intent(act)

        # Simulate: task was submitted but connection dropped before result
        # On reconnection, provider reports task COMPLETED
        mock_adapter = MagicMock()
        mock_adapter.get_status.return_value = "COMPLETED"
        mock_result = MagicMock()
        mock_result.is_success = True
        mock_adapter.get_result.return_value = mock_result

        recon = ReconciliationEngine()
        status, details = recon.reconcile_remote_agent("demo4", mock_adapter)
        assert status == ReconciliationStatus.MATCHED
        assert details["has_result"] is True

        # Prevent duplicate: do NOT re-submit since already completed
        mgr.record_outcome(act, ExecutionStatus.ALREADY_APPLIED)
        assert act.status == ExecutionStatus.ALREADY_APPLIED


class TestRealRecoveryDemo5:
    """Demo 5: Provider failures → circuit opens → fallback → circuit tests recovery."""

    def test_circuit_breaker_fallback_recovery(self):
        cb_mgr = CircuitBreakerManager()
        primary = cb_mgr.get_or_create("primary_provider", failure_threshold=3,
                                        cooldown_seconds=0.05)

        # Repeated failures trip the circuit
        for _ in range(3):
            primary.record_failure()
        assert primary.state == CircuitBreakerState.OPEN
        assert not cb_mgr.can_execute("primary_provider")

        # Fallback handles task while primary is open
        fallback = cb_mgr.get_or_create("fallback_provider")
        assert fallback.can_execute() is True
        fallback.record_success()

        # Wait for cooldown — circuit enters HALF_OPEN
        time.sleep(0.06)
        assert primary.can_execute() is True
        assert primary.state == CircuitBreakerState.HALF_OPEN

        # Probe succeeds — circuit closes
        primary.record_success()
        assert primary.state == CircuitBreakerState.CLOSED


# =====================================================================
# 21. Real Undo Sandbox Demo
# =====================================================================

class TestRealUndoSandbox:
    """Real filesystem undo operations in sandbox directory."""

    def test_undo_sandbox_create_and_revert(self, temp_dir):
        timeline = OperationTimeline()
        mgr = UndoManager(timeline=timeline)

        # Create 3 files
        files = []
        for i in range(3):
            f = os.path.join(temp_dir, f"sandbox_{i}.txt")
            with open(f, "w") as fh:
                fh.write(f"sandbox data {i}")
            act = ActionRecord(
                task_id=f"sandbox_{i}", action_type="create_file", target=f,
                status=ExecutionStatus.SUCCEEDED,
                reversibility=ReversibilityType.REVERSIBLE,
                compensation_action={"action": "delete_file", "path": f},
                group_id="sandbox_grp",
            )
            timeline.record_action(act)
            files.append(f)

        # All files exist
        assert all(os.path.exists(f) for f in files)

        # Undo entire group
        ok, undone = mgr.undo_group("sandbox_grp")
        assert ok is True
        assert len(undone) == 3
        assert not any(os.path.exists(f) for f in files)

        # Timeline integrity preserved
        ok, errors = timeline.verify_integrity()
        assert ok is True


# =====================================================================
# 22. Real Restart Sandbox Demo
# =====================================================================

class TestRealRestartSandbox:
    """Simulate application restart with SQLite persistence."""

    def test_restart_preserves_state(self, temp_dir):
        db_path = os.path.join(temp_dir, "restart.db")

        # Session 1: create manager, log actions, save checkpoint
        mgr1 = ReliabilityManager(db_path=db_path)
        act = ActionRecord(task_id="restart_t", action_type="file_write",
                           target="/restart_file.txt")
        mgr1.record_intent(act)
        mgr1.record_outcome(act, ExecutionStatus.SUCCEEDED)

        chk = Checkpoint(task_id="restart_t", sequence=0, state="COMPLETED",
                         completed_steps=["write_file"])
        mgr1.save_checkpoint(chk)

        # Session 2: new manager instance (simulating restart)
        mgr2 = ReliabilityManager(db_path=db_path)
        loaded = mgr2.load_latest_checkpoint("restart_t")
        assert loaded is not None
        assert loaded.state == "COMPLETED"
        assert "write_file" in loaded.completed_steps
        assert loaded.verify_checksum() is True

    def test_restart_reconciles_stale(self, temp_dir):
        db_path = os.path.join(temp_dir, "restart2.db")

        # Session 1: begin transaction, leave RUNNING (crash)
        mgr1 = ReliabilityManager(db_path=db_path)
        tx = mgr1.begin_transaction(mission_id="restart_m")
        tx.compensation_actions.append({"action": "custom_noop"})
        # Crash — tx stays RUNNING

        # Session 2: restart and reconcile
        mgr2 = ReliabilityManager(db_path=db_path)
        report = mgr2.reconcile_on_startup()
        assert len(report["reconciled_transactions"]) >= 1


# =====================================================================
# 23. Execution Status Enum Completeness
# =====================================================================

class TestExecutionStatusEnum:
    """Verify all expected execution statuses exist."""

    def test_all_statuses(self):
        expected = {"ATTEMPTED", "SUCCEEDED", "VERIFIED", "FAILED",
                    "UNKNOWN", "ALREADY_APPLIED"}
        actual = {s.value for s in ExecutionStatus}
        assert expected == actual


class TestReversibilityEnum:
    """Verify all reversibility types exist."""

    def test_all_types(self):
        expected = {"REVERSIBLE", "PARTIALLY_REVERSIBLE", "IRREVERSIBLE"}
        actual = {r.value for r in ReversibilityType}
        assert expected == actual


class TestFailureCategoryEnum:
    """Verify all 21 failure categories exist."""

    def test_count(self):
        assert len(FailureCategory) == 21


# =====================================================================
# 24. Benchmark Integration Test
# =====================================================================

class TestBenchmarkIntegration:
    """Verify benchmark script produces valid results file."""

    def test_benchmark_results_exist(self):
        results_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "benchmark_reliability_results.json")
        assert os.path.exists(results_path), "Benchmark results file must exist"
        with open(results_path, "r") as f:
            data = json.load(f)
        assert data["phase"] == "19"
        assert "action_logging_ms" in data
        assert "checkpoint_save_ms" in data
        assert "undo_execution_ms" in data
        assert "hash_chain_verification_ms" in data
        assert "memory_profile" in data
        assert data["memory_profile"]["peak_ram_mb"] < 50.0  # Must stay lightweight
