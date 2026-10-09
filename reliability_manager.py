"""Reliability Manager, Write-Ahead Logging, Crash Recovery, and Repair Engine for Hey Jev.

Coordinates:
- Persistent SQLite WAL storage for action records, checkpoints, and transactions
- Write-Ahead Intent Recording (pre-mutation logging)
- Checkpoint persistence and SHA-256 integrity validation
- Saga transaction lifecycle (PREPARING -> RUNNING -> COMMITTED / COMPENSATED)
- Crash recovery & startup reconciliation with external state verification
- Orphan detection and cryptographic integrity checking
- Safe RepairManager (strictly repairs internal Jev metadata, never mutates user data)
- Full EventBus lifecycle broadcasting
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import config
from event_bus import EventBus, TaskEvent
from reconciliation_engine import ReconciliationEngine, ReconciliationStatus
from reliability_model import (
    ActionRecord,
    Checkpoint,
    CircuitBreaker,
    ExecutionStatus,
    JevTransaction,
    JevTransactionState,
    ReversibilityType,
    SystemReliabilitySnapshot,
    VerificationLevel,
)
from undo_manager import OperationTimeline, UndoManager


class RepairManager:
    """Repairs internal Jev metadata while strictly protecting user files."""

    def __init__(self, db_conn_factory: Callable[[], sqlite3.Connection]) -> None:
        self.get_conn = db_conn_factory

    def repair_internal_metadata(self) -> Dict[str, Any]:
        """Perform non-destructive cleanup and consistency repair on internal SQLite tables."""
        repaired = {"orphans_repaired": 0, "stale_locks_cleared": 0, "status": "CLEAN"}
        with self.get_conn() as conn:
            # Reconcile incomplete transaction states older than 2 hours
            now = time.time()
            cursor = conn.execute(
                "UPDATE transactions SET state = 'UNKNOWN' WHERE state IN ('RUNNING', 'PREPARING') AND ? - updated_at > 7200",
                (now,)
            )
            repaired["orphans_repaired"] += cursor.rowcount
            conn.commit()

        return repaired


class ReliabilityManager:
    """Central engine managing durable logging, checkpoints, transactions, and recovery."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        event_bus: Optional[EventBus] = None,
        reconciliation_engine: Optional[ReconciliationEngine] = None,
    ) -> None:
        self.db_path = db_path or os.path.join(config.APPDATA_DIR, "heyjev_reliability.db")
        self.event_bus = event_bus or EventBus()
        self.reconciliation_engine = reconciliation_engine or ReconciliationEngine()
        self.timeline = OperationTimeline()
        self.undo_manager = UndoManager(timeline=self.timeline)
        self.repair_manager = RepairManager(self._get_connection)
        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS action_records (
                    action_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    mission_id TEXT,
                    action_type TEXT NOT NULL,
                    target TEXT NOT NULL,
                    status TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    reversibility TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    record_hash TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    mission_id TEXT,
                    sequence INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL,
                    timestamp REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    transaction_id TEXT PRIMARY KEY,
                    mission_id TEXT,
                    state TEXT NOT NULL,
                    transaction_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.commit()

    def _emit_event(self, event_type: str, task_id: str, data: Optional[Dict[str, Any]] = None) -> None:
        if self.event_bus:
            self.event_bus.publish(
                TaskEvent(
                    event_type=event_type,
                    task_id=task_id,
                    agent_name="reliability_manager",
                    data=data or {},
                )
            )

    # =====================================================================
    # 1. Write-Ahead Intent Recording
    # =====================================================================

    def record_intent(self, action: ActionRecord) -> ActionRecord:
        """Record action intention in WAL before physical execution."""
        action.status = ExecutionStatus.ATTEMPTED
        action.compute_hash()
        self.timeline.record_action(action)

        with self._lock, self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO action_records (
                    action_id, task_id, mission_id, action_type, target, status,
                    idempotency_key, reversibility, record_json, created_at, record_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                action.action_id,
                action.task_id,
                action.mission_id,
                action.action_type,
                action.target,
                action.status.value,
                action.idempotency_key,
                action.reversibility.value,
                json.dumps(action.to_dict()),
                action.created_at,
                action.record_hash,
            ))
            conn.commit()

        self._emit_event("action.started", action.task_id, {"action_id": action.action_id, "type": action.action_type})
        return action

    def record_outcome(
        self,
        action: ActionRecord,
        status: ExecutionStatus,
        after_state: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> ActionRecord:
        """Record outcome of action execution.

        Note: Does NOT recompute chain hash. The hash was sealed at record_intent()
        time and is used by subsequent records as their prev_record_hash.
        """
        action.status = status
        action.completed_at = time.time()
        if after_state:
            action.after_state = after_state
        if error:
            action.error = error
        if evidence:
            action.verification_evidence = evidence
            action.verification_state = "VERIFIED" if status in (ExecutionStatus.SUCCEEDED, ExecutionStatus.VERIFIED) else "FAILED"
        # NOTE: intentionally NOT calling action.compute_hash() here.
        # The record_hash was sealed at record_intent() time and chain integrity
        # depends on that hash being stable.

        with self._lock, self._get_connection() as conn:
            conn.execute("""
                UPDATE action_records
                SET status = ?, record_json = ?, record_hash = ?
                WHERE action_id = ?
            """, (
                action.status.value,
                json.dumps(action.to_dict()),
                action.record_hash,
                action.action_id,
            ))
            conn.commit()

        event_name = "action.completed" if status in (ExecutionStatus.SUCCEEDED, ExecutionStatus.VERIFIED, ExecutionStatus.ALREADY_APPLIED) else "action.failed"
        self._emit_event(event_name, action.task_id, {"action_id": action.action_id, "status": status.value, "error": error})
        return action

    # =====================================================================
    # 2. Checkpoints
    # =====================================================================

    def save_checkpoint(self, checkpoint: Checkpoint) -> Checkpoint:
        """Persist a checkpoint with calculated SHA-256 checksum."""
        checkpoint.compute_checksum()
        with self._lock, self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO checkpoints (
                    checkpoint_id, task_id, mission_id, sequence, state, checksum, checkpoint_json, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                checkpoint.checkpoint_id,
                checkpoint.task_id,
                checkpoint.mission_id,
                checkpoint.sequence,
                checkpoint.state,
                checkpoint.checksum,
                json.dumps(checkpoint.to_dict()),
                checkpoint.timestamp,
            ))
            conn.commit()
        return checkpoint

    def load_latest_checkpoint(self, task_id: str) -> Optional[Checkpoint]:
        """Load and verify the latest checkpoint for a task."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT checkpoint_json, checksum FROM checkpoints WHERE task_id = ? ORDER BY sequence DESC LIMIT 1",
                (task_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            data = json.loads(row[0])
            db_checksum = row[1]
            chk = Checkpoint.from_dict(data)
            # Integrity check against stored checksum and column checksum
            if not chk.verify_checksum() or (db_checksum and db_checksum != chk.checksum):
                self._emit_event("checkpoint.corrupted", task_id, {"checkpoint_id": chk.checkpoint_id})
                return None
            return chk

    # =====================================================================
    # 3. Saga Transactions
    # =====================================================================

    def begin_transaction(self, mission_id: Optional[str] = None) -> JevTransaction:
        tx = JevTransaction(mission_id=mission_id, state=JevTransactionState.RUNNING)
        with self._lock, self._get_connection() as conn:
            conn.execute("""
                INSERT INTO transactions (transaction_id, mission_id, state, transaction_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (tx.transaction_id, tx.mission_id, tx.state.value, json.dumps(tx.to_dict()), tx.created_at, tx.updated_at))
            conn.commit()
        self._emit_event("transaction.started", tx.transaction_id)
        return tx

    def commit_transaction(self, tx: JevTransaction) -> bool:
        tx.state = JevTransactionState.COMMITTED
        tx.updated_at = time.time()
        with self._lock, self._get_connection() as conn:
            conn.execute("""
                UPDATE transactions SET state = ?, transaction_json = ?, updated_at = ? WHERE transaction_id = ?
            """, (tx.state.value, json.dumps(tx.to_dict()), tx.updated_at, tx.transaction_id))
            conn.commit()
        self._emit_event("transaction.committed", tx.transaction_id)
        return True

    def compensate_transaction(self, tx: JevTransaction) -> bool:
        """Execute compensating actions in reverse dependency order."""
        tx.state = JevTransactionState.COMPENSATING
        tx.updated_at = time.time()
        self._emit_event("transaction.compensating", tx.transaction_id)

        all_compensated = True
        # Execute in reverse order
        for comp in reversed(tx.compensation_actions):
            ok = self.undo_manager._execute_compensation_step(comp)
            if not ok:
                all_compensated = False

        tx.state = JevTransactionState.COMPENSATED if all_compensated else JevTransactionState.PARTIALLY_COMPENSATED
        tx.updated_at = time.time()

        with self._lock, self._get_connection() as conn:
            conn.execute("""
                UPDATE transactions SET state = ?, transaction_json = ?, updated_at = ? WHERE transaction_id = ?
            """, (tx.state.value, json.dumps(tx.to_dict()), tx.updated_at, tx.transaction_id))
            conn.commit()

        event_name = "transaction.compensated" if all_compensated else "transaction.partial"
        self._emit_event(event_name, tx.transaction_id)
        return all_compensated

    # =====================================================================
    # 4. Crash Recovery & Startup Reconciliation
    # =====================================================================

    def reconcile_on_startup(self) -> Dict[str, Any]:
        """Audit stranded tasks and transactions on startup and reconcile against actual state."""
        report: Dict[str, Any] = {
            "reconciled_actions": [],
            "reconciled_transactions": [],
            "already_applied_count": 0,
            "recovered_count": 0,
        }

        with self._lock, self._get_connection() as conn:
            # 1. Audit incomplete transactions
            cursor = conn.execute("SELECT transaction_id, transaction_json FROM transactions WHERE state = 'RUNNING'")
            for tx_id, tx_json in cursor.fetchall():
                tx = JevTransaction.from_dict(json.loads(tx_json))
                # Run compensation if needed or mark unknown
                self.compensate_transaction(tx)
                report["reconciled_transactions"].append(tx_id)

            # 2. Audit stranded ATTEMPTED actions
            cursor = conn.execute("SELECT action_id, record_json FROM action_records WHERE status = 'ATTEMPTED'")
            for act_id, act_json in cursor.fetchall():
                act = ActionRecord.from_dict(json.loads(act_json))
                # Reconcile against external disk state
                if act.action_type in ("file_move", "file_create") and act.target:
                    if os.path.exists(act.target):
                        # Action completed before crash
                        act.status = ExecutionStatus.ALREADY_APPLIED
                        report["already_applied_count"] += 1
                    else:
                        act.status = ExecutionStatus.UNKNOWN
                else:
                    act.status = ExecutionStatus.UNKNOWN

                report["reconciled_actions"].append(act_id)
                conn.execute(
                    "UPDATE action_records SET status = ?, record_json = ? WHERE action_id = ?",
                    (act.status.value, json.dumps(act.to_dict()), act.action_id)
                )
            conn.commit()

        report["recovered_count"] = len(report["reconciled_actions"]) + len(report["reconciled_transactions"])
        return report

    # =====================================================================
    # 5. Integrity & Diagnostics
    # =====================================================================

    def check_integrity(self) -> Tuple[bool, List[str]]:
        """Verify timeline cryptographic hash chain and checkpoint integrity."""
        timeline_ok, errors = self.timeline.verify_integrity()

        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("SELECT checkpoint_id, checkpoint_json, checksum FROM checkpoints")
            for cid, cjson, expected_chk in cursor.fetchall():
                chk = Checkpoint.from_dict(json.loads(cjson))
                if chk.compute_checksum() != expected_chk:
                    errors.append(f"Corrupted checkpoint detected in SQLite: {cid}")

        return (len(errors) == 0), errors

    def get_reliability_snapshot(self) -> SystemReliabilitySnapshot:
        """Generate structured observability snapshot."""
        with self._lock, self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM transactions WHERE state = 'RUNNING'")
            active_tx = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM action_records WHERE status = 'UNKNOWN'")
            uncertain_acts = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM action_records WHERE status = 'FAILED'")
            failed_acts = cur.fetchone()[0]

        _, integrity_errs = self.check_integrity()

        return SystemReliabilitySnapshot(
            active_transactions=active_tx,
            uncertain_tasks=uncertain_acts,
            failed_tasks=failed_acts,
            integrity_problems=integrity_errs,
        )
