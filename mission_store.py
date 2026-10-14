"""Persistent SQLite Store for Hey Jev Missions (Phase 27).

Provides durable, thread-safe persistence for Missions, Plans, Tasks,
and Acceptance Criteria using SQLite with WAL journaling.

Supports:
- Immediate atomic transactions
- Restart recovery and interrupted task reconciliation
- Secret redaction via sanitize_content
- Querying active, pending, interrupted, and completed missions
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from memory_model import sanitize_content
from mission_model import (
    AcceptanceCriterion,
    AcceptanceStatus,
    Mission,
    MissionBudget,
    MissionMode,
    MissionPlan,
    MissionStatus,
    MissionTask,
    MissionTaskStatus,
)
from tool_model import RiskLevel

logger = logging.getLogger(__name__)


class SqliteMissionStore:
    """Thread-safe SQLite persistent store for missions, DAG plans, and verification records."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None or db_path == "":
            appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
            jev_dir = os.path.join(appdata, "hey-jev")
            os.makedirs(jev_dir, exist_ok=True)
            self.db_path = os.path.join(jev_dir, "hey_jev_missions.db")
        else:
            self.db_path = db_path
            if db_path != ":memory:":
                parent = os.path.dirname(db_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)

        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=10.0,
            )
            self._conn.row_factory = sqlite3.Row
            with self._conn:
                if self.db_path != ":memory:":
                    self._conn.execute("PRAGMA journal_mode=WAL;")
                self._conn.execute("PRAGMA busy_timeout=5000;")
                self._conn.execute("PRAGMA foreign_keys=ON;")
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS missions (
                        mission_id TEXT PRIMARY KEY,
                        objective TEXT NOT NULL,
                        normalized_goal TEXT,
                        explicit_constraints TEXT,
                        inferred_constraints TEXT,
                        mode TEXT NOT NULL,
                        status TEXT NOT NULL,
                        status_reason TEXT,
                        budget TEXT,
                        required_permissions TEXT,
                        approved_plan_version INTEGER,
                        approved_tasks TEXT,
                        blockers TEXT,
                        assumptions TEXT,
                        unresolved_questions TEXT,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        final_report TEXT
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mission_plans (
                        plan_id TEXT PRIMARY KEY,
                        mission_id TEXT NOT NULL,
                        plan_version INTEGER NOT NULL,
                        created_at REAL NOT NULL,
                        replan_reason TEXT,
                        materially_changed INTEGER NOT NULL DEFAULT 0,
                        FOREIGN KEY (mission_id) REFERENCES missions(mission_id) ON DELETE CASCADE
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mission_tasks (
                        task_id TEXT PRIMARY KEY,
                        plan_id TEXT NOT NULL,
                        mission_id TEXT NOT NULL,
                        description TEXT,
                        intended_result TEXT,
                        dependencies TEXT,
                        capability_id TEXT,
                        inputs TEXT,
                        preconditions TEXT,
                        required_permissions TEXT,
                        risk_level TEXT NOT NULL,
                        approval_checkpoint INTEGER NOT NULL DEFAULT 0,
                        approval_reason TEXT,
                        status TEXT NOT NULL,
                        retry_count INTEGER NOT NULL DEFAULT 0,
                        max_retries INTEGER NOT NULL DEFAULT 2,
                        timeout_s REAL NOT NULL DEFAULT 30.0,
                        verification_method TEXT,
                        evidence TEXT,
                        output TEXT,
                        error TEXT,
                        is_idempotent INTEGER NOT NULL DEFAULT 1,
                        side_effects_occurred INTEGER NOT NULL DEFAULT 0,
                        started_at REAL,
                        completed_at REAL,
                        FOREIGN KEY (plan_id) REFERENCES mission_plans(plan_id) ON DELETE CASCADE,
                        FOREIGN KEY (mission_id) REFERENCES missions(mission_id) ON DELETE CASCADE
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mission_acceptance_criteria (
                        criterion_id TEXT PRIMARY KEY,
                        mission_id TEXT NOT NULL,
                        description TEXT,
                        status TEXT NOT NULL,
                        required INTEGER NOT NULL DEFAULT 1,
                        evidence TEXT,
                        verified_at REAL,
                        external_dependency TEXT,
                        FOREIGN KEY (mission_id) REFERENCES missions(mission_id) ON DELETE CASCADE
                    );
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_missions_status ON missions(status);"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tasks_mission ON mission_tasks(mission_id);"
                )

    def save_mission(self, mission: Mission) -> str:
        """Persist or update a mission, its criteria, and its active plan."""
        with self._lock:
            conn = self._get_conn()
            with conn:
                conn.execute(
                    """
                    INSERT INTO missions (
                        mission_id, objective, normalized_goal, explicit_constraints,
                        inferred_constraints, mode, status, status_reason, budget,
                        required_permissions, approved_plan_version, approved_tasks,
                        blockers, assumptions, unresolved_questions, created_at,
                        updated_at, final_report
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(mission_id) DO UPDATE SET
                        objective=excluded.objective,
                        normalized_goal=excluded.normalized_goal,
                        explicit_constraints=excluded.explicit_constraints,
                        inferred_constraints=excluded.inferred_constraints,
                        mode=excluded.mode,
                        status=excluded.status,
                        status_reason=excluded.status_reason,
                        budget=excluded.budget,
                        required_permissions=excluded.required_permissions,
                        approved_plan_version=excluded.approved_plan_version,
                        approved_tasks=excluded.approved_tasks,
                        blockers=excluded.blockers,
                        assumptions=excluded.assumptions,
                        unresolved_questions=excluded.unresolved_questions,
                        updated_at=excluded.updated_at,
                        final_report=excluded.final_report;
                    """,
                    (
                        mission.mission_id,
                        sanitize_content(mission.objective),
                        sanitize_content(mission.normalized_goal),
                        json.dumps([sanitize_content(c) for c in mission.explicit_constraints]),
                        json.dumps([sanitize_content(c) for c in mission.inferred_constraints]),
                        mission.mode.value,
                        mission.status.value,
                        sanitize_content(mission.status_reason),
                        json.dumps(mission.budget.to_dict()),
                        json.dumps(mission.required_permissions),
                        mission.approved_plan_version,
                        json.dumps(list(mission.approved_tasks)),
                        json.dumps([sanitize_content(b) for b in mission.blockers]),
                        json.dumps([sanitize_content(a) for a in mission.assumptions]),
                        json.dumps([sanitize_content(q) for q in mission.unresolved_questions]),
                        mission.created_at,
                        mission.updated_at,
                        json.dumps(mission.final_report) if mission.final_report else None,
                    ),
                )

                # Save acceptance criteria
                for ac in mission.acceptance_criteria:
                    self._save_criterion_locked(conn, mission.mission_id, ac)

                # Save plan if attached
                if mission.plan:
                    self._save_plan_locked(conn, mission.mission_id, mission.plan)

        return mission.mission_id

    def _save_criterion_locked(
        self, conn: sqlite3.Connection, mission_id: str, ac: AcceptanceCriterion
    ) -> None:
        conn.execute(
            """
            INSERT INTO mission_acceptance_criteria (
                criterion_id, mission_id, description, status, required, evidence, verified_at, external_dependency
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(criterion_id) DO UPDATE SET
                description=excluded.description,
                status=excluded.status,
                required=excluded.required,
                evidence=excluded.evidence,
                verified_at=excluded.verified_at,
                external_dependency=excluded.external_dependency;
            """,
            (
                ac.criterion_id,
                mission_id,
                sanitize_content(ac.description),
                ac.status.value,
                1 if ac.required else 0,
                json.dumps(ac.evidence),
                ac.verified_at,
                ac.external_dependency,
            ),
        )

    def _save_plan_locked(
        self, conn: sqlite3.Connection, mission_id: str, plan: MissionPlan
    ) -> None:
        conn.execute(
            """
            INSERT INTO mission_plans (
                plan_id, mission_id, plan_version, created_at, replan_reason, materially_changed
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(plan_id) DO UPDATE SET
                plan_version=excluded.plan_version,
                replan_reason=excluded.replan_reason,
                materially_changed=excluded.materially_changed;
            """,
            (
                plan.plan_id,
                mission_id,
                plan.plan_version,
                plan.created_at,
                sanitize_content(plan.replan_reason) if plan.replan_reason else None,
                1 if plan.materially_changed else 0,
            ),
        )

        for task in plan.tasks:
            conn.execute(
                """
                INSERT INTO mission_tasks (
                    task_id, plan_id, mission_id, description, intended_result, dependencies,
                    capability_id, inputs, preconditions, required_permissions, risk_level,
                    approval_checkpoint, approval_reason, status, retry_count, max_retries,
                    timeout_s, verification_method, evidence, output, error, is_idempotent,
                    side_effects_occurred, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    plan_id=excluded.plan_id,
                    description=excluded.description,
                    intended_result=excluded.intended_result,
                    dependencies=excluded.dependencies,
                    capability_id=excluded.capability_id,
                    inputs=excluded.inputs,
                    preconditions=excluded.preconditions,
                    required_permissions=excluded.required_permissions,
                    risk_level=excluded.risk_level,
                    approval_checkpoint=excluded.approval_checkpoint,
                    approval_reason=excluded.approval_reason,
                    status=excluded.status,
                    retry_count=excluded.retry_count,
                    max_retries=excluded.max_retries,
                    timeout_s=excluded.timeout_s,
                    verification_method=excluded.verification_method,
                    evidence=excluded.evidence,
                    output=excluded.output,
                    error=excluded.error,
                    is_idempotent=excluded.is_idempotent,
                    side_effects_occurred=excluded.side_effects_occurred,
                    started_at=excluded.started_at,
                    completed_at=excluded.completed_at;
                """,
                (
                    task.task_id,
                    plan.plan_id,
                    mission_id,
                    sanitize_content(task.description),
                    sanitize_content(task.intended_result),
                    json.dumps(task.dependencies),
                    task.capability_id,
                    json.dumps(task.inputs),
                    json.dumps(task.preconditions),
                    json.dumps(task.required_permissions),
                    task.risk_level.value,
                    1 if task.approval_checkpoint else 0,
                    sanitize_content(task.approval_reason),
                    task.status.value,
                    task.retry_count,
                    task.max_retries,
                    task.timeout_s,
                    task.verification_method,
                    json.dumps(task.evidence),
                    json.dumps(task.output) if task.output else None,
                    sanitize_content(task.error) if task.error else None,
                    1 if task.is_idempotent else 0,
                    1 if task.side_effects_occurred else 0,
                    task.started_at,
                    task.completed_at,
                ),
            )

    def update_mission_status(
        self, mission_id: str, status: MissionStatus, reason: str = ""
    ) -> None:
        with self._lock:
            conn = self._get_conn()
            with conn:
                conn.execute(
                    """
                    UPDATE missions
                    SET status = ?, status_reason = ?, updated_at = ?
                    WHERE mission_id = ?;
                    """,
                    (status.value, sanitize_content(reason), time.time(), mission_id),
                )

    def update_task_status(
        self,
        task_id: str,
        status: MissionTaskStatus,
        evidence: Optional[Dict[str, Any]] = None,
        output: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        side_effects_occurred: Optional[bool] = None,
        retry_count: Optional[int] = None,
    ) -> None:
        with self._lock:
            conn = self._get_conn()
            with conn:
                updates = ["status = ?"]
                params: List[Any] = [status.value]
                if retry_count is not None:
                    updates.append("retry_count = ?")
                    params.append(int(retry_count))
                if evidence is not None:
                    updates.append("evidence = ?")
                    params.append(json.dumps(evidence))
                if output is not None:
                    updates.append("output = ?")
                    params.append(json.dumps(output))
                if error is not None:
                    updates.append("error = ?")
                    params.append(sanitize_content(error))
                if side_effects_occurred is not None:
                    updates.append("side_effects_occurred = ?")
                    params.append(1 if side_effects_occurred else 0)
                if status == MissionTaskStatus.COMPLETED:
                    updates.append("completed_at = ?")
                    params.append(time.time())
                elif status == MissionTaskStatus.RUNNING:
                    updates.append("started_at = ?")
                    params.append(time.time())

                params.append(task_id)
                conn.execute(
                    f"UPDATE mission_tasks SET {', '.join(updates)} WHERE task_id = ?;",
                    tuple(params),
                )

    def update_acceptance_criterion(
        self,
        criterion_id: str,
        status: AcceptanceStatus,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            conn = self._get_conn()
            with conn:
                conn.execute(
                    """
                    UPDATE mission_acceptance_criteria
                    SET status = ?, evidence = ?, verified_at = ?
                    WHERE criterion_id = ?;
                    """,
                    (
                        status.value,
                        json.dumps(evidence or {}),
                        time.time() if status == AcceptanceStatus.PASS else None,
                        criterion_id,
                    ),
                )

    def get_mission(self, mission_id: str) -> Optional[Mission]:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM missions WHERE mission_id = ?;", (mission_id,)
            ).fetchone()
            if not row:
                return None

            # Load criteria
            crit_rows = conn.execute(
                "SELECT * FROM mission_acceptance_criteria WHERE mission_id = ?;",
                (mission_id,),
            ).fetchall()
            criteria = [
                AcceptanceCriterion(
                    criterion_id=cr["criterion_id"],
                    description=cr["description"] or "",
                    status=AcceptanceStatus(cr["status"]),
                    required=bool(cr["required"]),
                    evidence=json.loads(cr["evidence"] or "{}"),
                    verified_at=cr["verified_at"],
                    external_dependency=cr["external_dependency"],
                )
                for cr in crit_rows
            ]

            # Load latest plan
            plan_row = conn.execute(
                """
                SELECT * FROM mission_plans
                WHERE mission_id = ?
                ORDER BY plan_version DESC LIMIT 1;
                """,
                (mission_id,),
            ).fetchone()

            plan = None
            if plan_row:
                plan_id = plan_row["plan_id"]
                task_rows = conn.execute(
                    "SELECT * FROM mission_tasks WHERE plan_id = ?;", (plan_id,)
                ).fetchall()
                tasks = [
                    MissionTask(
                        task_id=tr["task_id"],
                        description=tr["description"] or "",
                        intended_result=tr["intended_result"] or "",
                        dependencies=json.loads(tr["dependencies"] or "[]"),
                        capability_id=tr["capability_id"],
                        inputs=json.loads(tr["inputs"] or "{}"),
                        preconditions=json.loads(tr["preconditions"] or "[]"),
                        required_permissions=json.loads(tr["required_permissions"] or "[]"),
                        risk_level=RiskLevel(tr["risk_level"]),
                        approval_checkpoint=bool(tr["approval_checkpoint"]),
                        approval_reason=tr["approval_reason"] or "",
                        status=MissionTaskStatus(tr["status"]),
                        retry_count=tr["retry_count"],
                        max_retries=tr["max_retries"],
                        timeout_s=tr["timeout_s"],
                        verification_method=tr["verification_method"] or "",
                        evidence=json.loads(tr["evidence"] or "{}"),
                        output=json.loads(tr["output"]) if tr["output"] else None,
                        error=tr["error"],
                        is_idempotent=bool(tr["is_idempotent"]),
                        side_effects_occurred=bool(tr["side_effects_occurred"]),
                        started_at=tr["started_at"],
                        completed_at=tr["completed_at"],
                    )
                    for tr in task_rows
                ]
                plan = MissionPlan(
                    plan_id=plan_id,
                    plan_version=plan_row["plan_version"],
                    tasks=tasks,
                    created_at=plan_row["created_at"],
                    replan_reason=plan_row["replan_reason"],
                    materially_changed=bool(plan_row["materially_changed"]),
                )

            budget_data = json.loads(row["budget"] or "{}")
            return Mission(
                mission_id=row["mission_id"],
                objective=row["objective"],
                normalized_goal=row["normalized_goal"] or "",
                explicit_constraints=json.loads(row["explicit_constraints"] or "[]"),
                inferred_constraints=json.loads(row["inferred_constraints"] or "[]"),
                acceptance_criteria=criteria,
                plan=plan,
                mode=MissionMode(row["mode"]),
                status=MissionStatus(row["status"]),
                status_reason=row["status_reason"] or "",
                budget=MissionBudget.from_dict(budget_data),
                required_permissions=json.loads(row["required_permissions"] or "[]"),
                approved_plan_version=row["approved_plan_version"],
                approved_tasks=set(json.loads(row["approved_tasks"] or "[]")),
                blockers=json.loads(row["blockers"] or "[]"),
                assumptions=json.loads(row["assumptions"] or "[]"),
                unresolved_questions=json.loads(row["unresolved_questions"] or "[]"),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                final_report=json.loads(row["final_report"]) if row["final_report"] else None,
            )

    def list_missions(self, status: Optional[MissionStatus] = None) -> List[Mission]:
        with self._lock:
            conn = self._get_conn()
            if status is not None:
                rows = conn.execute(
                    "SELECT mission_id FROM missions WHERE status = ? ORDER BY created_at DESC;",
                    (status.value,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT mission_id FROM missions ORDER BY created_at DESC;"
                ).fetchall()

            missions: List[Mission] = []
            for r in rows:
                m = self.get_mission(r["mission_id"])
                if m:
                    missions.append(m)
            return missions

    def get_interrupted_missions(self) -> List[Mission]:
        """Find missions interrupted mid-execution across process restarts."""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                """
                SELECT mission_id FROM missions
                WHERE status IN ('RUNNING', 'PLANNING', 'VERIFYING', 'CANCELLING')
                ORDER BY updated_at DESC;
                """
            ).fetchall()
            missions: List[Mission] = []
            for r in rows:
                m = self.get_mission(r["mission_id"])
                if m:
                    missions.append(m)
            return missions

    def reconcile_interrupted_mission(self, mission_id: str) -> Optional[Mission]:
        """Reconcile an interrupted mission on startup.

        Guarantees:
        - Interrupted tasks with side effects or non-idempotent nature are flagged BLOCKED/NEEDS_ATTENTION.
        - Idempotent tasks with no side effects are reset to PENDING.
        - Completed verified tasks are strictly preserved.
        - Missions interrupted during CANCELLING are transitioned to CANCELLED.
        - Missions interrupted during PLANNING without a plan are transitioned to NEEDS_ATTENTION.
        - Mission status is reconciled safely to NEEDS_ATTENTION, CANCELLED, or READY.
        """
        mission = self.get_mission(mission_id)
        if not mission:
            return None

        with self._lock:
            # Handle cancellation interrupted across process boundary
            if mission.status == MissionStatus.CANCELLING:
                if mission.plan:
                    for task in mission.plan.tasks:
                        if task.status in {MissionTaskStatus.RUNNING, MissionTaskStatus.PENDING, MissionTaskStatus.NEEDS_APPROVAL}:
                            task.status = MissionTaskStatus.CANCELLED
                            self.update_task_status(task.task_id, status=MissionTaskStatus.CANCELLED)
                self.update_mission_status(
                    mission.mission_id,
                    MissionStatus.CANCELLED,
                    "Cancellation completed upon process restart.",
                )
                return self.get_mission(mission.mission_id)

            # Handle planning interrupted before plan was produced
            if mission.status == MissionStatus.PLANNING and not mission.plan:
                self.update_mission_status(
                    mission.mission_id,
                    MissionStatus.NEEDS_ATTENTION,
                    "Planning interrupted by process restart; manual re-planning required.",
                )
                return self.get_mission(mission.mission_id)

            if not mission.plan:
                return mission

            has_ambiguity = False
            for task in mission.plan.tasks:
                if task.status == MissionTaskStatus.RUNNING:
                    if task.side_effects_occurred or not task.is_idempotent:
                        # Ambiguous outcome - cannot safely retry without user inspection
                        task.status = MissionTaskStatus.BLOCKED
                        task.error = "Interrupted by process termination with potential side effects."
                        self.update_task_status(
                            task.task_id,
                            status=MissionTaskStatus.BLOCKED,
                            error=task.error,
                            side_effects_occurred=True,
                        )
                        has_ambiguity = True
                    else:
                        # Safe to retry
                        task.status = MissionTaskStatus.PENDING
                        self.update_task_status(
                            task.task_id,
                            status=MissionTaskStatus.PENDING,
                        )

            new_status = (
                MissionStatus.NEEDS_ATTENTION
                if has_ambiguity
                else MissionStatus.READY
            )
            reason = (
                "Recovered after restart with ambiguous task side effects."
                if has_ambiguity
                else "Recovered after restart; pending tasks reset."
            )
            self.update_mission_status(mission.mission_id, new_status, reason)
            return self.get_mission(mission.mission_id)

    def close(self) -> None:
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
