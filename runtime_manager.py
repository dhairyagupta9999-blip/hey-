"""Production Runtime Lifecycle and Subsystem Coordinator for Hey Jev.

Coordinates and manages the production runtime lifecycle:
- EventBus (systemwide event routing)
- BackgroundTaskManager (persistent background tasks, scheduler, and rule engine)
- EventRuleEngine (semantic burst deduplication, causal cycle loop prevention)
- ProjectRegistry & ProjectWatchers (automated project monitoring and regression triage)
- WindowsNativeDirectoryWatcher / FilesystemWatcher (OS-native directory changes & write settling)
- ProactiveHeartbeatEngine (resource-aware periodic evaluation, quiet default, bounded decisions)
- Idempotent duplicate-startup protection
- Graceful shutdown, handle cancellation, and resource release
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import config
from background_task_manager import BackgroundTaskManager
from background_task_model import BackgroundTaskDefinition, BackgroundTaskState
from event_bus import EventBus
from event_rule_engine import EventActionType, EventRule, EventRuleEngine
from proactive_heartbeat import (
    BaseSignalProvider,
    HeartbeatDecision,
    HeartbeatDecisionType,
    ProactiveHeartbeatEngine,
    ProjectHealthSignalProvider,
    StaleCacheSignalProvider,
    SystemResourceSignalProvider,
    TaskFailureSignalProvider,
)
from project_model import Project
from project_registry import ProjectRegistry
from resource_governor import ResourceGovernor
from watcher_model import WatcherDefinition, WatcherState, WatcherType
from watchers import BaseWatcher, ProjectWatcher, WindowsNativeDirectoryWatcher

logger = logging.getLogger("hey_jev.runtime")


class JevRuntime:
    """Central production coordinator managing all Hey Jev background automation subsystems."""

    def __init__(
        self,
        appdata_dir: Optional[str] = None,
        background_db_path: Optional[str] = None,
        project_db_path: Optional[str] = None,
        event_bus: Optional[EventBus] = None,
        governor: Optional[ResourceGovernor] = None,
        task_executor: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        heartbeat_interval_sec: float = 30.0,
        enable_heartbeat: bool = True,
        enable_project_watchers: bool = True,
        enable_native_watchers: bool = True,
        max_cpu_percent: float = 80.0,
        min_memory_mb: float = 100.0,
        memory_store: Optional[Any] = None,
    ) -> None:
        self.appdata_dir = appdata_dir or config.APPDATA_DIR
        self.background_db_path = background_db_path or os.path.join(self.appdata_dir, "background_tasks.db")
        self.project_db_path = project_db_path or os.path.join(self.appdata_dir, "projects.db")

        self.event_bus = event_bus or EventBus()
        self.governor = governor or ResourceGovernor()
        self.memory_store = memory_store
        self.task_executor = task_executor
        self.heartbeat_interval_sec = heartbeat_interval_sec
        self.enable_heartbeat = enable_heartbeat
        self.enable_project_watchers = enable_project_watchers
        self.enable_native_watchers = enable_native_watchers
        self.max_cpu_percent = max_cpu_percent
        self.min_memory_mb = min_memory_mb

        self._lock = threading.RLock()
        self._running = False

        # 1. Project Registry
        self.project_registry = ProjectRegistry(db_path=self.project_db_path)

        # 2. Background Task Manager
        self.task_manager = BackgroundTaskManager(
            db_path=self.background_db_path,
            event_bus=self.event_bus,
            governor=self.governor,
            project_registry=self.project_registry,
        )

        # 3. Proactive Heartbeat Engine
        cache_dir = os.path.join(self.appdata_dir, "cache")
        signal_providers: List[BaseSignalProvider] = [
            SystemResourceSignalProvider(max_cpu_percent=self.max_cpu_percent, min_memory_mb=self.min_memory_mb),
            ProjectHealthSignalProvider(project_registry=self.project_registry),
            TaskFailureSignalProvider(task_manager=self.task_manager),
            StaleCacheSignalProvider(cache_dir=cache_dir, ttl_seconds=86400.0),
        ]

        self.heartbeat_engine = ProactiveHeartbeatEngine(
            event_bus=self.event_bus,
            task_manager=self.task_manager,
            project_registry=self.project_registry,
            signal_providers=signal_providers,
            task_executor=self.task_executor,
            interval_sec=self.heartbeat_interval_sec,
            max_cpu_percent=self.max_cpu_percent,
            min_memory_mb=self.min_memory_mb,
        )

        # 4. Capability System & Workflow Engine (Phase 22)
        from capability_registry import CapabilityRegistry
        from capability_discovery import CapabilityDiscoveryEngine
        from capability_workflow import WorkflowComposer
        from workflow_engine import WorkflowEngine
        from tool_registry import ToolRegistry
        from skill_registry import SkillRegistry
        from tool_scanner import ToolScanner
        from builtin_skills import get_builtin_skills

        self.tool_registry = ToolRegistry()
        ToolScanner(self.tool_registry).scan_and_register_all()
        self.skill_registry = SkillRegistry()
        for s in get_builtin_skills():
            self.skill_registry.register(s)

        from skill_executor import SkillExecutor

        self.skill_executor = SkillExecutor(
            tool_registry=self.tool_registry,
            event_bus=self.event_bus,
        )
        self.capability_registry = CapabilityRegistry(
            tool_registry=self.tool_registry,
            skill_registry=self.skill_registry,
            skill_executor=self.skill_executor,
        )
        self.capability_discovery = CapabilityDiscoveryEngine(registry=self.capability_registry)
        self.workflow_composer = WorkflowComposer(
            registry=self.capability_registry,
            discovery_engine=self.capability_discovery,
        )
        self.workflow_engine = WorkflowEngine(
            registry=self.capability_registry,
            event_bus=self.event_bus,
        )

        from extension_manager import ExtensionManager
        self.extension_manager = ExtensionManager(
            capability_registry=self.capability_registry,
            event_bus=self.event_bus,
            local_integrations_dir=os.path.join(self.appdata_dir, "integrations"),
        )
        self.deployment_manager = self.extension_manager.deployment_manager

        # 5. Operational Maintenance Coordinator (Phase 26)
        from maintenance_coordinator import OperationalMaintenanceCoordinator
        self.maintenance_coordinator = OperationalMaintenanceCoordinator(
            event_bus=self.event_bus,
            memory_store=self.memory_store,
            deployment_manager=self.deployment_manager,
            recovery_manager=self.task_manager.recovery_manager,
        )

        from proactive_heartbeat import OperationalHealthSignalProvider
        self.heartbeat_engine.signal_providers.append(
            OperationalHealthSignalProvider(health_coordinator=self.maintenance_coordinator.health_coordinator)
        )

    def plan_and_execute_workflow(
        self,
        goal: str,
        mode: Any = None,
        context: Optional[Dict[str, Any]] = None,
        granted_permissions: Optional[List[TaskPermission]] = None,
    ) -> Tuple[Optional[Any], Optional[Any], Optional[str]]:
        """Compose and execute a workflow for an outcome-oriented goal."""
        from capability_workflow import WorkflowMode
        effective_mode = mode or WorkflowMode.COPILOT
        plan, err = self.workflow_composer.compose_workflow(
            goal=goal,
            context=context,
            mode=effective_mode,
            caller_permissions=granted_permissions,
        )
        if not plan:
            return None, None, err

        report = self.workflow_engine.execute_workflow(
            plan=plan,
            context=context,
            granted_permissions=granted_permissions,
        )
        return plan, report, None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> "JevRuntime":
        """Start all Hey Jev background subsystems idempotently.
        
        Guarantees:
        - Re-entrant and safe: duplicate calls return immediately without creating duplicate threads.
        - Automatically starts BackgroundTaskManager, scheduler, and EventRuleEngine.
        - Restores and instantiates persisted watchers from SQLite.
        - Automatically registers and starts project watchers for active projects.
        - Starts ProactiveHeartbeatEngine.
        """
        with self._lock:
            if self._running:
                logger.info("JevRuntime is already active; duplicate startup suppressed.")
                return self

            logger.info("Starting JevRuntime background automation fleet...")
            try:
                # Step 1: Start BackgroundTaskManager (reconciles tasks, restores watchers, starts scheduler & rule engine)
                self.task_manager.start()

                # Step 2: Auto-activate watchers for registered projects
                if self.enable_project_watchers:
                    self._activate_project_watchers()

                # Step 3: Start Proactive Heartbeat Engine
                if self.enable_heartbeat:
                    self.heartbeat_engine.start()

                # Step 3.5: Crash recovery and reconciliation of capability deployments (Phase 25)
                self.deployment_manager.recover_deployments()

                # Step 3.6: Audit operational health of deployed capabilities (Phase 26)
                self.maintenance_coordinator.audit_all_deployed_capabilities()

                self._running = True

                # Step 4: Emit runtime startup event
                self.event_bus.emit(
                    event_type="runtime.started",
                    task_id="runtime",
                    agent_name="JevRuntime",
                    data={
                        "active_watchers": len(self.task_manager._active_watchers),
                        "heartbeat_enabled": self.enable_heartbeat,
                        "background_db": self.background_db_path,
                    },
                )
            except Exception as e:
                self._running = False
                logger.error("Startup failed in JevRuntime: %s", e)
                try:
                    self.task_manager.stop()
                except Exception:
                    pass
                if self.enable_heartbeat:
                    try:
                        self.heartbeat_engine.stop()
                    except Exception:
                        pass
                raise

        return self

    def _activate_project_watchers(self) -> None:
        """Scan project registry and activate project & native directory watchers."""
        try:
            projects = self.project_registry.list()
        except Exception:
            projects = []

        for proj in projects:
            watcher_id = f"w_proj_{proj.project_id}"
            if self.task_manager.get_watcher(watcher_id) is None:
                defn = WatcherDefinition(
                    watcher_id=watcher_id,
                    type=WatcherType.PROJECT,
                    target=proj.project_id,
                    debounce_ms=500.0,
                    cooldown_sec=5.0,
                    enabled=True,
                    metadata={"project_name": proj.name, "root_path": proj.root_path},
                )
                self.task_manager.add_watcher(defn)

            # If project has root path and native watching is enabled, add directory watcher
            if self.enable_native_watchers and proj.root_path and os.path.exists(proj.root_path):
                native_w_id = f"w_dir_{proj.project_id}"
                if self.task_manager.get_watcher(native_w_id) is None:
                    native_defn = WatcherDefinition(
                        watcher_id=native_w_id,
                        type=WatcherType.DIRECTORY,
                        target=proj.root_path,
                        debounce_ms=250.0,
                        cooldown_sec=2.0,
                        enabled=True,
                        metadata={
                            "native": True,
                            "use_native": True,
                            "settle_window_sec": 0.5,
                            "project_id": proj.project_id,
                        },
                    )
                    self.task_manager.add_watcher(native_defn)

    def stop(self) -> None:
        """Gracefully stop all background subsystems and release OS handles.
        
        Guarantees:
        - Stops heartbeat worker thread.
        - Stops all active watchers (cancelling overlapped I/O and closing native Win32 handles).
        - Stops BackgroundTaskManager (scheduler, event rule engine, workers).
        - Safe to call multiple times (idempotent).
        """
        with self._lock:
            if not self._running:
                return

            logger.info("Stopping JevRuntime background automation fleet...")
            self._running = False

            # Step 1: Stop Heartbeat Engine
            if self.enable_heartbeat:
                self.heartbeat_engine.stop()

            # Step 2: Stop BackgroundTaskManager and all active watchers
            self.task_manager.stop()

            # Step 3: Emit runtime stopped event
            self.event_bus.emit(
                event_type="runtime.stopped",
                task_id="runtime",
                agent_name="JevRuntime",
                data={},
            )

    def get_status(self) -> Dict[str, Any]:
        """Produce structured diagnostic status of the runtime fleet."""
        with self._lock:
            running = self._running
            active_w = len(self.task_manager._active_watchers)
            tasks_count = len(self.task_manager.list_tasks())
            heartbeat_hist = len(self.heartbeat_engine.get_history())

        return {
            "runtime_running": running,
            "background_task_manager_running": getattr(self.task_manager, "_running", False),
            "active_watchers_count": active_w,
            "tasks_count": tasks_count,
            "heartbeat_history_count": heartbeat_hist,
        }

    def get_operational_health_report(self) -> Dict[str, Any]:
        """Produce holistic operational health, incident, and maintenance report (Phase 26)."""
        return self.maintenance_coordinator.get_operational_report()


# ---------------------------------------------------------------------------
# Global Singleton Management
# ---------------------------------------------------------------------------

_GLOBAL_RUNTIME: Optional[JevRuntime] = None
_RUNTIME_LOCK = threading.RLock()


def get_runtime(**kwargs) -> JevRuntime:
    """Retrieve or initialize the process-wide JevRuntime instance."""
    global _GLOBAL_RUNTIME
    with _RUNTIME_LOCK:
        if _GLOBAL_RUNTIME is None:
            _GLOBAL_RUNTIME = JevRuntime(**kwargs)
        return _GLOBAL_RUNTIME


def start_runtime(**kwargs) -> JevRuntime:
    """Start the process-wide JevRuntime background automation fleet."""
    runtime = get_runtime(**kwargs)
    return runtime.start()


def stop_runtime() -> None:
    """Stop the process-wide JevRuntime instance and release all OS resources."""
    global _GLOBAL_RUNTIME
    with _RUNTIME_LOCK:
        if _GLOBAL_RUNTIME is not None:
            _GLOBAL_RUNTIME.stop()


def is_runtime_running() -> bool:
    """Check whether the process-wide JevRuntime is currently running."""
    with _RUNTIME_LOCK:
        return _GLOBAL_RUNTIME is not None and _GLOBAL_RUNTIME.is_running


def reset_runtime_for_testing() -> None:
    """Tear down and clear singleton instance for isolated test suites."""
    global _GLOBAL_RUNTIME
    with _RUNTIME_LOCK:
        if _GLOBAL_RUNTIME is not None:
            _GLOBAL_RUNTIME.stop()
            _GLOBAL_RUNTIME = None
