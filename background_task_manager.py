"""Background Task Manager and Persistent Automation Coordinator for Hey Jev.

Coordinates:
- BackgroundTaskDefinition lifecycle (create, enable, disable, pause, resume, cancel, run_now, delete)
- SQLite persistence with WAL mode
- SchedulerBackend dispatching
- ResourceGovernor concurrency and preemption
- EventRuleEngine reactive triggers
- Environmental Watchers management
- NotificationManager alert dispatching
- RecoveryManager startup reconciliation
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import config
from background_status import BackgroundStatus, BackgroundStatusSnapshot
from background_task_model import (
    BackgroundTaskDefinition,
    BackgroundTaskPriority,
    BackgroundTaskState,
    NotificationLevel,
    ResourceClass,
)
from event_bus import EventBus
from event_rule_engine import EventActionType, EventRule, EventRuleEngine
from notification_manager import NotificationManager
from recovery_manager import RecoveryManager
from resource_governor import ResourceGovernor
from scheduler_backend import InProcessSchedulerBackend, SchedulerBackend
from watcher_model import WatcherDefinition, WatcherState, WatcherType
from watchers import (
    BaseWatcher,
    BrowserWatcher,
    FilesystemWatcher,
    GitWatcher,
    MissionWatcher,
    ProcessWatcher,
    ProjectWatcher,
)


class BackgroundTaskManager:
    """Central manager for persistent background execution, watchers, and scheduling."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        event_bus: Optional[EventBus] = None,
        governor: Optional[ResourceGovernor] = None,
        scheduler: Optional[SchedulerBackend] = None,
        notification_manager: Optional[NotificationManager] = None,
        task_executor: Optional[Callable[[BackgroundTaskDefinition], Dict[str, Any]]] = None,
    ) -> None:
        if db_path is None:
            self.db_path = os.path.join(config.APPDATA_DIR, "background_tasks.db")
        else:
            self.db_path = db_path

        self.event_bus = event_bus or EventBus()
        self.governor = governor or ResourceGovernor()
        self.notification_manager = notification_manager or NotificationManager(
            event_bus=self.event_bus, governor=self.governor
        )
        self.task_executor = task_executor
        self.recovery_manager = RecoveryManager(event_bus=self.event_bus)

        self._lock = threading.RLock()
        self._mem_conn: Optional[sqlite3.Connection] = None
        self._init_db()

        # Initialize scheduler
        self.scheduler = scheduler or InProcessSchedulerBackend(
            event_bus=self.event_bus,
            task_runner=self._execute_scheduled_task,
        )

        # Initialize EventRuleEngine
        self.rule_engine = EventRuleEngine(
            event_bus=self.event_bus,
            action_dispatcher=self._dispatch_rule_action,
        )

        # In-memory active watcher instances: watcher_id -> BaseWatcher
        self._active_watchers: Dict[str, BaseWatcher] = {}
        self._action_handlers: Dict[str, Callable[[BackgroundTaskDefinition], Dict[str, Any]]] = {}
        self._running = False

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            if self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._mem_conn.row_factory = sqlite3.Row
            return self._mem_conn
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create tables for background tasks and watchers."""
        with self._lock, self._get_connection() as conn:
            if self.db_path != ":memory:":
                conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS background_tasks (
                    background_task_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    mission_id TEXT,
                    skill_id TEXT,
                    task_template_json TEXT,
                    trigger_type TEXT,
                    schedule TEXT,
                    conditions_json TEXT,
                    state TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    next_run_at REAL,
                    last_run_at REAL,
                    last_result_json TEXT,
                    failure_count INTEGER NOT NULL,
                    max_retries INTEGER NOT NULL,
                    retry_policy_json TEXT,
                    resource_class TEXT NOT NULL,
                    permission_profile_json TEXT,
                    notification_policy TEXT NOT NULL,
                    is_interactive INTEGER NOT NULL,
                    policy_expires_at REAL,
                    metadata_json TEXT
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS watchers (
                    watcher_id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    target TEXT NOT NULL,
                    filters_json TEXT,
                    conditions_json TEXT,
                    debounce_ms REAL NOT NULL,
                    cooldown_sec REAL NOT NULL,
                    enabled INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    last_event_at REAL,
                    last_trigger_at REAL,
                    task_template_json TEXT,
                    permission_profile_json TEXT,
                    state TEXT NOT NULL,
                    restart_count INTEGER NOT NULL,
                    max_restarts INTEGER NOT NULL,
                    policy_expires_at REAL,
                    metadata_json TEXT
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bg_state ON background_tasks(state);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bg_next ON background_tasks(next_run_at);")

    def _row_to_task(self, row: sqlite3.Row) -> BackgroundTaskDefinition:
        d = dict(row)
        return BackgroundTaskDefinition(
            background_task_id=d["background_task_id"],
            name=d["name"],
            description=d.get("description", ""),
            mission_id=d.get("mission_id"),
            skill_id=d.get("skill_id"),
            task_template=json.loads(d["task_template_json"]) if d.get("task_template_json") else {},
            trigger_type=d.get("trigger_type", "TIME"),
            schedule=d.get("schedule"),
            conditions=json.loads(d["conditions_json"]) if d.get("conditions_json") else {},
            state=BackgroundTaskState(d["state"]),
            priority=BackgroundTaskPriority(d["priority"]),
            enabled=bool(d["enabled"]),
            created_at=float(d["created_at"]),
            updated_at=float(d["updated_at"]),
            next_run_at=float(d["next_run_at"]) if d.get("next_run_at") is not None else None,
            last_run_at=float(d["last_run_at"]) if d.get("last_run_at") is not None else None,
            last_result=json.loads(d["last_result_json"]) if d.get("last_result_json") else None,
            failure_count=int(d["failure_count"]),
            max_retries=int(d["max_retries"]),
            retry_policy=json.loads(d["retry_policy_json"]) if d.get("retry_policy_json") else {},
            resource_class=ResourceClass(d["resource_class"]),
            permission_profile=json.loads(d["permission_profile_json"]) if d.get("permission_profile_json") else [],
            notification_policy=NotificationLevel(d["notification_policy"]),
            is_interactive=bool(d["is_interactive"]),
            policy_expires_at=float(d["policy_expires_at"]) if d.get("policy_expires_at") is not None else None,
            metadata=json.loads(d["metadata_json"]) if d.get("metadata_json") else {},
        )

    def _row_to_watcher(self, row: sqlite3.Row) -> WatcherDefinition:
        d = dict(row)
        return WatcherDefinition(
            watcher_id=d["watcher_id"],
            type=WatcherType(d["type"]),
            target=d["target"],
            filters=json.loads(d["filters_json"]) if d.get("filters_json") else [],
            conditions=json.loads(d["conditions_json"]) if d.get("conditions_json") else {},
            debounce_ms=float(d["debounce_ms"]),
            cooldown_sec=float(d["cooldown_sec"]),
            enabled=bool(d["enabled"]),
            created_at=float(d["created_at"]),
            last_event_at=float(d["last_event_at"]) if d.get("last_event_at") is not None else None,
            last_trigger_at=float(d["last_trigger_at"]) if d.get("last_trigger_at") is not None else None,
            task_template=json.loads(d["task_template_json"]) if d.get("task_template_json") else {},
            permission_profile=json.loads(d["permission_profile_json"]) if d.get("permission_profile_json") else [],
            state=WatcherState(d["state"]),
            restart_count=int(d["restart_count"]),
            max_restarts=int(d["max_restarts"]),
            policy_expires_at=float(d["policy_expires_at"]) if d.get("policy_expires_at") is not None else None,
            metadata=json.loads(d["metadata_json"]) if d.get("metadata_json") else {},
        )

    # -------------------------------------------------------------------------
    # Task Operations
    # -------------------------------------------------------------------------

    def create_task(
        self,
        name: str,
        schedule: Optional[str] = None,
        trigger_type: str = "TIME",
        task_template: Optional[Dict[str, Any]] = None,
        description: str = "",
        mission_id: Optional[str] = None,
        skill_id: Optional[str] = None,
        priority: BackgroundTaskPriority = BackgroundTaskPriority.NORMAL,
        resource_class: ResourceClass = ResourceClass.NORMAL,
        permission_profile: Optional[List[str]] = None,
        is_interactive: bool = False,
        policy_expires_at: Optional[float] = None,
        notification_policy: NotificationLevel = NotificationLevel.NORMAL,
    ) -> BackgroundTaskDefinition:
        """Create and persist a new background task."""
        now = time.time()
        initial_state = BackgroundTaskState.SCHEDULED if schedule else BackgroundTaskState.READY

        task_def = BackgroundTaskDefinition(
            name=name,
            description=description,
            mission_id=mission_id,
            skill_id=skill_id,
            task_template=dict(task_template or {}),
            trigger_type=trigger_type,
            schedule=schedule,
            state=initial_state,
            priority=priority,
            resource_class=resource_class,
            permission_profile=list(permission_profile or []),
            is_interactive=is_interactive,
            policy_expires_at=policy_expires_at,
            notification_policy=notification_policy,
            created_at=now,
            updated_at=now,
        )

        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO background_tasks (
                    background_task_id, name, description, mission_id, skill_id,
                    task_template_json, trigger_type, schedule, conditions_json, state,
                    priority, enabled, created_at, updated_at, next_run_at, last_run_at,
                    last_result_json, failure_count, max_retries, retry_policy_json,
                    resource_class, permission_profile_json, notification_policy,
                    is_interactive, policy_expires_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    task_def.background_task_id,
                    task_def.name,
                    task_def.description,
                    task_def.mission_id,
                    task_def.skill_id,
                    json.dumps(task_def.task_template),
                    task_def.trigger_type,
                    task_def.schedule,
                    json.dumps(task_def.conditions),
                    task_def.state.value,
                    task_def.priority.value,
                    1 if task_def.enabled else 0,
                    task_def.created_at,
                    task_def.updated_at,
                    task_def.next_run_at,
                    task_def.last_run_at,
                    json.dumps(task_def.last_result) if task_def.last_result else None,
                    task_def.failure_count,
                    task_def.max_retries,
                    json.dumps(task_def.retry_policy),
                    task_def.resource_class.value,
                    json.dumps(task_def.permission_profile),
                    task_def.notification_policy.value,
                    1 if task_def.is_interactive else 0,
                    task_def.policy_expires_at,
                    json.dumps(task_def.metadata),
                ),
            )

        if self._running and task_def.enabled and task_def.schedule:
            self.scheduler.schedule(task_def)

        self.event_bus.emit(
            event_type="background_task.created",
            task_id=task_def.background_task_id,
            agent_name="BackgroundTaskManager",
            data={"name": task_def.name, "state": task_def.state.value},
        )
        return task_def

    def register_task(self, task_def: BackgroundTaskDefinition) -> str:
        """Register or update an existing BackgroundTaskDefinition instance in SQLite."""
        now = time.time()
        if not task_def.created_at:
            task_def.created_at = now
        task_def.updated_at = now

        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO background_tasks (
                    background_task_id, name, description, mission_id, skill_id,
                    task_template_json, trigger_type, schedule, conditions_json, state,
                    priority, enabled, created_at, updated_at, next_run_at, last_run_at,
                    last_result_json, failure_count, max_retries, retry_policy_json,
                    resource_class, permission_profile_json, notification_policy,
                    is_interactive, policy_expires_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    task_def.background_task_id,
                    task_def.name,
                    task_def.description,
                    task_def.mission_id,
                    task_def.skill_id,
                    json.dumps(task_def.task_template),
                    task_def.trigger_type,
                    task_def.schedule,
                    json.dumps(task_def.conditions),
                    task_def.state.value,
                    task_def.priority.value,
                    1 if task_def.enabled else 0,
                    task_def.created_at,
                    task_def.updated_at,
                    task_def.next_run_at,
                    task_def.last_run_at,
                    json.dumps(task_def.last_result) if task_def.last_result else None,
                    task_def.failure_count,
                    task_def.max_retries,
                    json.dumps(task_def.retry_policy),
                    task_def.resource_class.value,
                    json.dumps(task_def.permission_profile),
                    task_def.notification_policy.value,
                    1 if task_def.is_interactive else 0,
                    task_def.policy_expires_at,
                    json.dumps(task_def.metadata),
                ),
            )

        if self._running and task_def.enabled and task_def.schedule:
            self.scheduler.schedule(task_def)

        return task_def.background_task_id

    def update_task_state(self, task_id: str, new_state: BackgroundTaskState, error_msg: Optional[str] = None) -> bool:
        """Update lifecycle state of a specific task."""
        t = self.get_task(task_id)
        if not t:
            return False
        t.state = new_state
        if error_msg:
            t.metadata["last_error"] = error_msg
        return self.update_task(t)

    def register_action_handler(self, action_type: str, handler: Callable[[BackgroundTaskDefinition], Dict[str, Any]]) -> None:
        """Register a handler for a named action_type."""
        with self._lock:
            self._action_handlers[action_type] = handler

    def register_watcher(self, watcher: BaseWatcher) -> None:
        """Register an active watcher instance."""
        with self._lock:
            self._active_watchers[watcher.definition.watcher_id] = watcher

    def handle_watcher_event(self, event: Dict[str, Any]) -> None:
        """Route incoming watcher event through rule engine."""
        event_name = event.get("event_name", event.get("event_type", "watcher.event"))
        self.rule_engine.evaluate_event(event_name, event)

    def run_task_now(self, task_id: str) -> Dict[str, Any]:
        """Synchronously execute a task handler and return its result dict."""
        task_def = self.get_task(task_id)
        if not task_def:
            return {"status": "FAILED", "error": f"Task {task_id} not found"}

        task_def.state = BackgroundTaskState.RUNNING
        self.update_task(task_def)

        act_type = task_def.action_type or task_def.task_template.get("action_type")
        handler = self._action_handlers.get(act_type)

        res = {}
        try:
            if handler:
                res = handler(task_def)
            elif self.task_executor:
                res = self.task_executor(task_def)
            else:
                res = {"status": "SUCCESS", "message": f"Task {task_def.name} executed"}
            task_def.state = BackgroundTaskState.COMPLETED
            task_def.last_result = res
        except Exception as e:
            res = {"status": "FAILED", "error": str(e)}
            task_def.state = BackgroundTaskState.FAILED
            task_def.last_result = res

        self.update_task(task_def)
        return res

    def update_task(self, task_def: BackgroundTaskDefinition) -> bool:
        """Update existing persisted task."""
        task_def.updated_at = time.time()
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE background_tasks SET
                    name = ?, description = ?, mission_id = ?, skill_id = ?,
                    task_template_json = ?, trigger_type = ?, schedule = ?, conditions_json = ?,
                    state = ?, priority = ?, enabled = ?, updated_at = ?, next_run_at = ?,
                    last_run_at = ?, last_result_json = ?, failure_count = ?, max_retries = ?,
                    retry_policy_json = ?, resource_class = ?, permission_profile_json = ?,
                    notification_policy = ?, is_interactive = ?, policy_expires_at = ?,
                    metadata_json = ?
                WHERE background_task_id = ?;
                """,
                (
                    task_def.name,
                    task_def.description,
                    task_def.mission_id,
                    task_def.skill_id,
                    json.dumps(task_def.task_template),
                    task_def.trigger_type,
                    task_def.schedule,
                    json.dumps(task_def.conditions),
                    task_def.state.value,
                    task_def.priority.value,
                    1 if task_def.enabled else 0,
                    task_def.updated_at,
                    task_def.next_run_at,
                    task_def.last_run_at,
                    json.dumps(task_def.last_result) if task_def.last_result else None,
                    task_def.failure_count,
                    task_def.max_retries,
                    json.dumps(task_def.retry_policy),
                    task_def.resource_class.value,
                    json.dumps(task_def.permission_profile),
                    task_def.notification_policy.value,
                    1 if task_def.is_interactive else 0,
                    task_def.policy_expires_at,
                    json.dumps(task_def.metadata),
                    task_def.background_task_id,
                ),
            )
            return cursor.rowcount > 0

    def get_task(self, task_id: str) -> Optional[BackgroundTaskDefinition]:
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM background_tasks WHERE background_task_id = ?;", (task_id,))
            row = cursor.fetchone()
            return self._row_to_task(row) if row else None

    def list_tasks(self, state: Optional[BackgroundTaskState] = None) -> List[BackgroundTaskDefinition]:
        with self._lock, self._get_connection() as conn:
            if state:
                cursor = conn.execute("SELECT * FROM background_tasks WHERE state = ? ORDER BY created_at DESC;", (state.value,))
            else:
                cursor = conn.execute("SELECT * FROM background_tasks ORDER BY created_at DESC;")
            return [self._row_to_task(r) for r in cursor.fetchall()]

    def enable_task(self, task_id: str) -> bool:
        t = self.get_task(task_id)
        if not t:
            return False
        t.enabled = True
        t.state = BackgroundTaskState.SCHEDULED if t.schedule else BackgroundTaskState.READY
        res = self.update_task(t)
        if res and t.schedule and self._running:
            self.scheduler.schedule(t)
        self.event_bus.emit("background_task.enabled", task_id, data={})
        return res

    def disable_task(self, task_id: str) -> bool:
        t = self.get_task(task_id)
        if not t:
            return False
        t.enabled = False
        t.state = BackgroundTaskState.DISABLED
        self.scheduler.unschedule(task_id)
        res = self.update_task(t)
        self.event_bus.emit("background_task.disabled", task_id, data={})
        return res

    def pause_task(self, task_id: str) -> bool:
        t = self.get_task(task_id)
        if not t:
            return False
        t.state = BackgroundTaskState.PAUSED
        self.scheduler.unschedule(task_id)
        self.governor.release(task_id)
        res = self.update_task(t)
        self.event_bus.emit("background_task.paused", task_id, data={})
        return res

    def resume_task(self, task_id: str) -> bool:
        t = self.get_task(task_id)
        if not t or t.state != BackgroundTaskState.PAUSED:
            return False
        t.state = BackgroundTaskState.SCHEDULED if t.schedule else BackgroundTaskState.READY
        res = self.update_task(t)
        if res and t.schedule and self._running:
            self.scheduler.schedule(t)
        self.event_bus.emit("background_task.resumed", task_id, data={})
        return res

    def cancel_task(self, task_id: str) -> bool:
        t = self.get_task(task_id)
        if not t:
            return False
        t.state = BackgroundTaskState.CANCELLED
        t.enabled = False
        self.scheduler.unschedule(task_id)
        self.governor.release(task_id)
        res = self.update_task(t)
        self.event_bus.emit("background_task.cancelled", task_id, data={})
        return res

    def delete_task(self, task_id: str) -> bool:
        self.scheduler.unschedule(task_id)
        self.governor.release(task_id)
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM background_tasks WHERE background_task_id = ?;", (task_id,))
            deleted = cursor.rowcount > 0
        if deleted:
            self.event_bus.emit("background_task.deleted", task_id, data={})
        return deleted

    def run_now(self, task_id: str) -> bool:
        """Immediately trigger execution of task, respecting governor and policy expiration."""
        task_def = self.get_task(task_id)
        if not task_def:
            return False

        if task_def.is_policy_expired():
            task_def.state = BackgroundTaskState.BLOCKED
            task_def.metadata["blocked_reason"] = "Temporary authorization policy expired"
            self.update_task(task_def)
            self.notification_manager.notify(
                title=f"Authorization Expired: {task_def.name}",
                message="Background task blocked because its permission window expired.",
                level=NotificationLevel.IMPORTANT,
            )
            return False

        # Acquire concurrency slot from governor
        acquired = self.governor.acquire(
            task_id=task_def.background_task_id,
            resource_class=task_def.resource_class,
            is_foreground=False,
        )
        if not acquired:
            task_def.state = BackgroundTaskState.WAITING
            self.update_task(task_def)
            return False

        # Execute in thread to keep asynchronous
        threading.Thread(
            target=self._run_task_worker,
            args=(task_def,),
            daemon=True,
            name=f"Worker_{task_def.background_task_id}",
        ).start()
        return True

    def _execute_scheduled_task(self, task_def: BackgroundTaskDefinition) -> None:
        """Callback invoked by scheduler."""
        self.run_now(task_def.background_task_id)

    def _run_task_worker(self, task_def: BackgroundTaskDefinition) -> None:
        """Internal execution worker."""
        task_def.state = BackgroundTaskState.RUNNING
        task_def.last_run_at = time.time()
        self.update_task(task_def)

        self.event_bus.emit(
            event_type="background_task.started",
            task_id=task_def.background_task_id,
            agent_name="BackgroundTaskManager",
            data={"name": task_def.name},
        )

        success = True
        error_msg = None
        result_payload = {}

        try:
            if self.task_executor:
                result_payload = self.task_executor(task_def)
            else:
                result_payload = {"status": "SUCCESS", "message": f"Task {task_def.name} executed successfully."}
        except Exception as e:
            success = False
            error_msg = str(e)
            result_payload = {"status": "FAILED", "error": error_msg}

        # Release resource slot
        self.governor.release(task_def.background_task_id)

        now = time.time()
        task_def.updated_at = now
        task_def.last_result = result_payload

        if success:
            task_def.failure_count = 0
            task_def.state = BackgroundTaskState.SCHEDULED if task_def.schedule and task_def.enabled else BackgroundTaskState.COMPLETED
            self.event_bus.emit(
                event_type="background_task.completed",
                task_id=task_def.background_task_id,
                agent_name="BackgroundTaskManager",
                data={"result": result_payload},
            )
            if task_def.notification_policy != NotificationLevel.SILENT:
                self.notification_manager.notify(
                    title=f"Task Completed: {task_def.name}",
                    message=result_payload.get("message", "Task finished successfully."),
                    level=task_def.notification_policy,
                )
        else:
            task_def.failure_count += 1
            if task_def.failure_count >= task_def.max_retries:
                task_def.state = BackgroundTaskState.FAILED
            else:
                task_def.state = BackgroundTaskState.READY  # Ready for retry

            self.event_bus.emit(
                event_type="background_task.failed",
                task_id=task_def.background_task_id,
                agent_name="BackgroundTaskManager",
                data={"error": error_msg, "failure_count": task_def.failure_count},
            )
            self.notification_manager.notify(
                title=f"Task Failed: {task_def.name}",
                message=f"Error: {error_msg} (Attempt {task_def.failure_count}/{task_def.max_retries})",
                level=NotificationLevel.IMPORTANT,
            )

        self.update_task(task_def)

    # -------------------------------------------------------------------------
    # Watcher Operations
    # -------------------------------------------------------------------------

    def add_watcher(self, definition: WatcherDefinition) -> BaseWatcher:
        """Persist and start an environmental watcher."""
        # Persist definition
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO watchers (
                    watcher_id, type, target, filters_json, conditions_json, debounce_ms,
                    cooldown_sec, enabled, created_at, last_event_at, last_trigger_at,
                    task_template_json, permission_profile_json, state, restart_count,
                    max_restarts, policy_expires_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    definition.watcher_id,
                    definition.type.value,
                    definition.target,
                    json.dumps(definition.filters),
                    json.dumps(definition.conditions),
                    definition.debounce_ms,
                    definition.cooldown_sec,
                    1 if definition.enabled else 0,
                    definition.created_at,
                    definition.last_event_at,
                    definition.last_trigger_at,
                    json.dumps(definition.task_template),
                    json.dumps(definition.permission_profile),
                    definition.state.value,
                    definition.restart_count,
                    definition.max_restarts,
                    definition.policy_expires_at,
                    json.dumps(definition.metadata),
                ),
            )

        # Create concrete watcher instance
        watcher_inst: BaseWatcher
        if definition.type == WatcherType.DIRECTORY or definition.type == WatcherType.FILE:
            watcher_inst = FilesystemWatcher(definition, event_bus=self.event_bus)
        elif definition.type == WatcherType.GIT:
            watcher_inst = GitWatcher(definition, event_bus=self.event_bus)
        elif definition.type == WatcherType.PROCESS:
            watcher_inst = ProcessWatcher(definition, event_bus=self.event_bus)
        elif definition.type == WatcherType.BROWSER:
            watcher_inst = BrowserWatcher(definition, event_bus=self.event_bus)
        elif definition.type == WatcherType.MISSION:
            watcher_inst = MissionWatcher(definition, event_bus=self.event_bus)
        else:
            watcher_inst = BaseWatcher(definition, event_bus=self.event_bus)

        with self._lock:
            self._active_watchers[definition.watcher_id] = watcher_inst

        if self._running and definition.enabled:
            watcher_inst.start()

        return watcher_inst

    def get_watcher(self, watcher_id: str) -> Optional[BaseWatcher]:
        with self._lock:
            return self._active_watchers.get(watcher_id)

    def remove_watcher(self, watcher_id: str) -> bool:
        with self._lock:
            inst = self._active_watchers.pop(watcher_id, None)
            if inst:
                inst.stop()
            with self._get_connection() as conn:
                cursor = conn.execute("DELETE FROM watchers WHERE watcher_id = ?;", (watcher_id,))
                return cursor.rowcount > 0

    def list_watchers(self) -> List[WatcherDefinition]:
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM watchers;")
            return [self._row_to_watcher(r) for r in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Rule Dispatcher & Lifecycle
    # -------------------------------------------------------------------------

    def _dispatch_rule_action(self, rule: EventRule, event: Any) -> None:
        """Execute action triggered by an EventRule."""
        if rule.action_type in (EventActionType.CREATE_TASK, EventActionType.CREATE_BACKGROUND_TASK):
            event_type = getattr(event, "event_type", "event")
            name = rule.action_payload.get("task_name", f"Triggered from {event_type}")
            action_type = rule.action_payload.get("action_type", "custom")
            event_data = dict(event.data) if hasattr(event, "data") else (dict(event) if isinstance(event, dict) else {})
            task_template = dict(rule.action_payload)
            task_template["action_type"] = action_type
            task_template["payload"] = event_data
            self.create_task(name=name, task_template=task_template)
        elif rule.action_type in (EventActionType.NOTIFY_USER, EventActionType.SEND_NOTIFICATION):
            event_type = getattr(event, "event_type", "event")
            title = rule.action_payload.get("title", f"Alert: {rule.name}")
            msg = rule.action_payload.get("message", f"Event {event_type} fired.")
            lvl = rule.action_payload.get("level", NotificationLevel.NORMAL)
            self.notification_manager.notify(title=title, message=msg, level=lvl)

    def start(self) -> None:
        """Start the background task system, scheduler, watchers, and rule engine."""
        with self._lock:
            if self._running:
                return
            self._running = True

            # 1. Startup task reconciliation
            persisted = self.list_tasks()
            reconciled = self.recovery_manager.reconcile_startup_tasks(persisted)
            for t in reconciled:
                self.update_task(t)

            # 2. Start scheduler
            self.scheduler.start()
            for t in self.list_tasks(BackgroundTaskState.SCHEDULED):
                if t.enabled and t.schedule:
                    self.scheduler.schedule(t)

            # 3. Start event rule engine
            self.rule_engine.start_listening()

            # 4. Start active watchers
            for w in self._active_watchers.values():
                if w.definition.enabled:
                    w.start()

    def stop(self) -> None:
        """Stop all background processing cleanly."""
        with self._lock:
            if not self._running:
                return
            self._running = False

            # Stop watchers
            for w in self._active_watchers.values():
                w.stop()

            # Stop rule engine
            self.rule_engine.stop_listening()

            # Stop scheduler
            self.scheduler.stop()

    def get_status_snapshot(self) -> BackgroundStatusSnapshot:
        """Generate structured status snapshot."""
        return BackgroundStatus.create_snapshot(
            tasks=self.list_tasks(),
            watchers=self.list_watchers(),
            governor=self.governor,
        )

    def get_summary(self) -> str:
        """Produce human-readable work summary."""
        snap = self.get_status_snapshot()
        return snap.summary
