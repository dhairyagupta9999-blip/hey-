"""Persistent Task Storage with SQLite for Hey Jev Agent Harness.

Ensures tasks, dependencies, audit logs, and agent results survive
application restarts with atomic transactions and WAL journaling.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

import config
from agent_result import AgentResult, AgentResultStatus
from agent_task import AgentTask, TaskPriority, TaskStatus


class TaskStorage:
    """SQLite-backed persistence layer for AgentTasks and audit records."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            self.db_path = os.path.join(config.APPDATA_DIR, "tasks.db")
        else:
            self.db_path = db_path

        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create tables and indexes if they do not exist."""
        with self._lock, self._get_connection() as conn:
            # Enable WAL mode for concurrency and durability
            if self.db_path != ":memory:":
                conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    parent_task_id TEXT,
                    goal TEXT,
                    description TEXT,
                    status TEXT,
                    priority TEXT,
                    created_at REAL,
                    updated_at REAL,
                    current_agent TEXT,
                    assigned_agents_json TEXT,
                    context_json TEXT,
                    inputs_json TEXT,
                    outputs_json TEXT,
                    artifacts_json TEXT,
                    errors_json TEXT,
                    recommendations_json TEXT,
                    next_action TEXT,
                    confidence REAL,
                    requires_approval INTEGER,
                    approval_state TEXT,
                    retry_count INTEGER,
                    max_retries INTEGER,
                    timestamps_json TEXT,
                    cancellation_state_json TEXT,
                    permissions_json TEXT,
                    dependencies_json TEXT
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    old_status TEXT,
                    new_status TEXT,
                    agent_name TEXT,
                    details_json TEXT,
                    created_at REAL NOT NULL
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_results (
                    task_id TEXT PRIMARY KEY,
                    agent_name TEXT,
                    status TEXT,
                    result_json TEXT,
                    summary TEXT,
                    error TEXT,
                    files_changed_json TEXT,
                    commands_run_json TEXT,
                    questions_json TEXT,
                    recommendations_json TEXT,
                    confidence REAL,
                    next_action TEXT,
                    artifacts_json TEXT,
                    evidence_json TEXT,
                    created_at REAL NOT NULL
                );
            """)

            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_task ON task_audit_log(task_id);")
            conn.commit()

    def save_task(self, task: AgentTask) -> None:
        """Insert or replace an AgentTask in the database."""
        with self._lock, self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO tasks (
                    task_id, parent_task_id, goal, description, status, priority,
                    created_at, updated_at, current_agent, assigned_agents_json,
                    context_json, inputs_json, outputs_json, artifacts_json,
                    errors_json, recommendations_json, next_action, confidence,
                    requires_approval, approval_state, retry_count, max_retries,
                    timestamps_json, cancellation_state_json, permissions_json,
                    dependencies_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?
                );
            """, (
                task.task_id,
                task.parent_task_id,
                task.goal,
                task.description,
                task.status.value,
                task.priority.value,
                task.created_at,
                task.updated_at,
                task.current_agent,
                json.dumps(task.assigned_agents),
                json.dumps(task.context),
                json.dumps(task.inputs),
                json.dumps(task.outputs),
                json.dumps(task.artifacts),
                json.dumps(task.errors),
                json.dumps(task.recommendations),
                task.next_action,
                task.confidence,
                1 if task.requires_approval else 0,
                task.approval_state,
                task.retry_count,
                task.max_retries,
                json.dumps(task.timestamps),
                json.dumps(task.cancellation_state),
                json.dumps([p.value for p in task.permissions]),
                json.dumps(task.dependencies),
            ))
            conn.commit()

    def _row_to_task(self, row: sqlite3.Row) -> AgentTask:
        data = {
            "task_id": row["task_id"],
            "parent_task_id": row["parent_task_id"],
            "goal": row["goal"] or "",
            "description": row["description"] or "",
            "status": row["status"],
            "priority": row["priority"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "current_agent": row["current_agent"],
            "assigned_agents": json.loads(row["assigned_agents_json"] or "[]"),
            "context": json.loads(row["context_json"] or "{}"),
            "inputs": json.loads(row["inputs_json"] or "{}"),
            "outputs": json.loads(row["outputs_json"] or "{}"),
            "artifacts": json.loads(row["artifacts_json"] or "[]"),
            "errors": json.loads(row["errors_json"] or "[]"),
            "recommendations": json.loads(row["recommendations_json"] or "[]"),
            "next_action": row["next_action"],
            "confidence": row["confidence"],
            "requires_approval": bool(row["requires_approval"]),
            "approval_state": row["approval_state"] or "NONE",
            "retry_count": row["retry_count"] or 0,
            "max_retries": row["max_retries"] or 3,
            "timestamps": json.loads(row["timestamps_json"] or "{}"),
            "cancellation_state": json.loads(row["cancellation_state_json"] or "{}"),
            "permissions": json.loads(row["permissions_json"] or "[]"),
            "dependencies": json.loads(row["dependencies_json"] or "[]"),
        }
        return AgentTask.from_dict(data)

    def get_task(self, task_id: str) -> Optional[AgentTask]:
        """Fetch task by ID."""
        with self._lock, self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM tasks WHERE task_id = ?;", (task_id,))
            row = cur.fetchone()
            if row:
                return self._row_to_task(row)
        return None

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        parent_id: Optional[str] = None,
    ) -> List[AgentTask]:
        """List tasks matching optional filters."""
        query = "SELECT * FROM tasks WHERE 1=1"
        params: List[Any] = []
        if status is not None:
            query += " AND status = ?"
            params.append(status.value if isinstance(status, TaskStatus) else status)
        if parent_id is not None:
            query += " AND parent_task_id = ?"
            params.append(parent_id)
        query += " ORDER BY created_at ASC;"

        with self._lock, self._get_connection() as conn:
            cur = conn.execute(query, params)
            return [self._row_to_task(row) for row in cur.fetchall()]

    def get_unfinished_tasks(self) -> List[AgentTask]:
        """Return tasks that were in-flight (not COMPLETED, FAILED, or CANCELLED)."""
        terminal_statuses = (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value)
        query = f"SELECT * FROM tasks WHERE status NOT IN (?, ?, ?) ORDER BY created_at ASC;"
        with self._lock, self._get_connection() as conn:
            cur = conn.execute(query, terminal_statuses)
            return [self._row_to_task(row) for row in cur.fetchall()]

    def record_audit(
        self,
        task_id: str,
        event_type: str,
        old_status: str = "",
        new_status: str = "",
        agent_name: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append an audit record."""
        with self._lock, self._get_connection() as conn:
            conn.execute("""
                INSERT INTO task_audit_log (
                    task_id, event_type, old_status, new_status, agent_name, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
            """, (
                task_id,
                event_type,
                old_status,
                new_status,
                agent_name,
                json.dumps(details or {}),
                time.time(),
            ))
            conn.commit()

    def get_audit_history(self, task_id: str) -> List[Dict[str, Any]]:
        """Retrieve audit history records for a task."""
        with self._lock, self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM task_audit_log WHERE task_id = ? ORDER BY created_at ASC;",
                (task_id,),
            )
            records: List[Dict[str, Any]] = []
            for r in cur.fetchall():
                records.append({
                    "id": r["id"],
                    "task_id": r["task_id"],
                    "event_type": r["event_type"],
                    "old_status": r["old_status"],
                    "new_status": r["new_status"],
                    "agent_name": r["agent_name"],
                    "details": json.loads(r["details_json"] or "{}"),
                    "created_at": r["created_at"],
                })
            return records

    def save_result(self, task_id: str, agent_name: str, result: AgentResult) -> None:
        """Persist structured AgentResult."""
        with self._lock, self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO task_results (
                    task_id, agent_name, status, result_json, summary, error,
                    files_changed_json, commands_run_json, questions_json,
                    recommendations_json, confidence, next_action,
                    artifacts_json, evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                task_id,
                agent_name,
                result.status.value,
                json.dumps(result.result, default=str),
                result.summary,
                result.error,
                json.dumps(result.files_changed),
                json.dumps(result.commands_run),
                json.dumps(result.questions),
                json.dumps(result.recommendations),
                result.confidence,
                result.next_action,
                json.dumps(result.artifacts),
                json.dumps(result.evidence, default=str),
                time.time(),
            ))
            conn.commit()

    def get_result(self, task_id: str) -> Optional[AgentResult]:
        """Fetch persisted AgentResult."""
        with self._lock, self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM task_results WHERE task_id = ?;", (task_id,))
            row = cur.fetchone()
            if not row:
                return None
            return AgentResult(
                result=json.loads(row["result_json"] or "null"),
                summary=row["summary"] or "",
                error=row["error"],
                files_changed=json.loads(row["files_changed_json"] or "[]"),
                commands_run=json.loads(row["commands_run_json"] or "[]"),
                questions=json.loads(row["questions_json"] or "[]"),
                recommendations=json.loads(row["recommendations_json"] or "[]"),
                confidence=row["confidence"],
                next_action=row["next_action"],
                artifacts=json.loads(row["artifacts_json"] or "[]"),
                evidence=json.loads(row["evidence_json"] or "[]"),
                status=AgentResultStatus(row["status"]),
            )

    def delete_task(self, task_id: str) -> bool:
        """Delete task and its associated audit records."""
        with self._lock, self._get_connection() as conn:
            conn.execute("DELETE FROM task_audit_log WHERE task_id = ?;", (task_id,))
            conn.execute("DELETE FROM task_results WHERE task_id = ?;", (task_id,))
            res = conn.execute("DELETE FROM tasks WHERE task_id = ?;", (task_id,))
            conn.commit()
            return res.rowcount > 0
