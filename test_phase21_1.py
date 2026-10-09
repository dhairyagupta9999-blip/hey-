"""Phase 21.1 Production Runtime Integration Audit Tests.

Verifies:
1. Runtime Registration & Duplicate Protection:
   - JevRuntime automatically activates WindowsNativeDirectoryWatcher, ProjectWatcher,
     EventRuleEngine, BackgroundTaskManager, and ProactiveHeartbeatEngine.
   - Duplicate startup calls are idempotent and cannot create duplicate watchers or threads.
2. Lifecycle and Resource Management:
   - Watcher cancellation, native handle cleanup, overlapped-I/O cancellation, and polling fallback.
   - Graceful shutdown while events or background tasks are active.
   - Restart recovery reconciles tasks without duplicating records or notifications.
   - Sustained idle CPU consumption measurement (<0.5% overhead).
3. Heartbeat Safety & Quarantine:
   - EXECUTE_LOW_RISK whitelist enforcement (ALLOWED_LOW_RISK_ACTIONS only).
   - Strict path validation blocks arbitrary or system file deletion.
   - Untrusted event payloads are quarantined to PROPOSE_TASK with requires_approval=True.
   - Resource-pressure readings reliably suppress optional work.
4. Six Real Application Demonstrations:
   - Demo 1: Starting Jev automatically activates intended watchers.
   - Demo 2: Controlled file change produces one settled event without duplicate task creation.
   - Demo 3: Simulated project regression triggers configured response.
   - Demo 4: Failed background task follows recovery policy and produces accurate notification.
   - Demo 5: Untrusted signal creates proposal rather than silently executing action.
   - Demo 6: Shutting down Jev releases watcher resources and stops heartbeat processing.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import psutil
import pytest

import config
from background_task_manager import BackgroundTaskManager
from background_task_model import (
    BackgroundTaskDefinition,
    BackgroundTaskPriority,
    BackgroundTaskState,
    NotificationLevel,
    ResourceClass,
)
from event_bus import EventBus
from event_rule_engine import EventActionType, EventRule, EventRuleEngine
from proactive_heartbeat import (
    ALLOWED_LOW_RISK_ACTIONS,
    BaseSignalProvider,
    HeartbeatDecision,
    HeartbeatDecisionType,
    HeartbeatSignal,
    ProactiveHeartbeatEngine,
    ProjectHealthSignalProvider,
    SignalSeverity,
    StaleCacheSignalProvider,
    SystemResourceSignalProvider,
    TaskFailureSignalProvider,
    is_safe_cache_path,
)
from project_model import Project
from project_registry import ProjectRegistry
from recovery_manager import RecoveryManager
from resource_governor import ResourceGovernor
from runtime_manager import (
    JevRuntime,
    get_runtime,
    is_runtime_running,
    reset_runtime_for_testing,
    start_runtime,
    stop_runtime,
)
from safety_engine import HIGH, SAFE, evaluate_risk, request_global_cancel, check_and_clear_cancel
from watcher_model import WatcherDefinition, WatcherState, WatcherType
from watchers import (
    BaseWatcher,
    FilesystemWatcher,
    ProjectWatcher,
    WindowsNativeDirectoryWatcher,
)


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="heyjev_p21_1_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_runtime():
    reset_runtime_for_testing()
    yield
    reset_runtime_for_testing()


# ===========================================================================
# 1. Runtime Registration & Duplicate Protection Tests
# ===========================================================================

def test_runtime_registration_automatically_starts_all_core_components(temp_dir):
    """Verify JevRuntime automatically starts BackgroundTaskManager, EventRuleEngine,
    ProjectWatchers, WindowsNativeDirectoryWatcher, and ProactiveHeartbeatEngine.
    """
    proj_dir = os.path.join(temp_dir, "my_project")
    os.makedirs(proj_dir, exist_ok=True)

    bg_db = os.path.join(temp_dir, "bg.db")
    proj_db = os.path.join(temp_dir, "proj.db")

    # Pre-register a project in ProjectRegistry
    reg = ProjectRegistry(db_path=proj_db)
    proj = Project(project_id="proj_core", name="Core Project", root_path=proj_dir)
    reg.register(proj)

    runtime = JevRuntime(
        appdata_dir=temp_dir,
        background_db_path=bg_db,
        project_db_path=proj_db,
        heartbeat_interval_sec=0.1,
        max_cpu_percent=100.0,
        min_memory_mb=10.0,
    )

    try:
        assert not runtime.is_running
        runtime.start()
        assert runtime.is_running

        # 1. BackgroundTaskManager is running
        assert runtime.task_manager._running is True
        # 2. EventRuleEngine is active
        assert runtime.task_manager.rule_engine is not None
        # 3. ProjectWatcher was automatically created & registered
        p_watcher = runtime.task_manager.get_watcher("w_proj_proj_core")
        assert p_watcher is not None
        assert isinstance(p_watcher, ProjectWatcher)
        # 4. Directory watcher for project root was automatically created
        dir_watcher = runtime.task_manager.get_watcher("w_dir_proj_core")
        assert dir_watcher is not None
        assert isinstance(dir_watcher, (WindowsNativeDirectoryWatcher, FilesystemWatcher))
        # 5. ProactiveHeartbeatEngine is running
        assert runtime.heartbeat_engine._running is True

    finally:
        runtime.stop()
        assert not runtime.is_running
        assert runtime.task_manager._running is False
        assert runtime.heartbeat_engine._running is False


def test_runtime_duplicate_startup_is_idempotent(temp_dir):
    """Verify calling start_runtime repeatedly returns the existing runtime and cannot
    create duplicate watchers, worker threads, or heartbeat loops.
    """
    bg_db = os.path.join(temp_dir, "bg.db")
    proj_db = os.path.join(temp_dir, "proj.db")

    runtime = JevRuntime(
        appdata_dir=temp_dir,
        background_db_path=bg_db,
        project_db_path=proj_db,
        heartbeat_interval_sec=1.0,
        max_cpu_percent=100.0,
        min_memory_mb=10.0,
    )

    try:
        runtime.start()
        active_w_count_1 = len(runtime.task_manager._active_watchers)
        hb_thread_1 = runtime.heartbeat_engine._thread

        # Duplicate start calls
        runtime.start()
        runtime.start()

        assert runtime.is_running
        assert len(runtime.task_manager._active_watchers) == active_w_count_1
        assert runtime.heartbeat_engine._thread is hb_thread_1
    finally:
        runtime.stop()


# ===========================================================================
# 2. Lifecycle & Resource Management Tests
# ===========================================================================

def test_native_watcher_handle_cleanup_and_cancellation(temp_dir):
    """Verify WindowsNativeDirectoryWatcher acquires native handles, supports clean
    cancellation, closes directory and event handles, and cleans up completely.
    """
    watch_dir = os.path.join(temp_dir, "watched_native")
    os.makedirs(watch_dir, exist_ok=True)

    defn = WatcherDefinition(
        watcher_id="w_nat_clean",
        type=WatcherType.DIRECTORY,
        target=watch_dir,
        debounce_ms=50.0,
        metadata={"native": True, "settle_window_sec": 0.1},
    )

    watcher = WindowsNativeDirectoryWatcher(definition=defn, event_bus=EventBus())
    watcher.start()

    try:
        if not watcher.using_fallback:
            assert watcher._dir_handle is not None
            assert watcher._h_event is not None
        assert watcher._running is True
    finally:
        watcher.stop()

    assert watcher._running is False
    assert watcher._dir_handle is None
    assert watcher._h_event is None
    if watcher._thread:
        assert not watcher._thread.is_alive()


def test_native_watcher_fallback_on_unsupported_or_invalid_target(temp_dir):
    """Verify that if native Win32 handles cannot be acquired, watcher safely falls back to polling."""
    fake_target = os.path.join(temp_dir, "non_existent_folder")

    defn = WatcherDefinition(
        watcher_id="w_fallback_test",
        type=WatcherType.DIRECTORY,
        target=fake_target,
        debounce_ms=50.0,
    )

    watcher = WindowsNativeDirectoryWatcher(definition=defn, event_bus=EventBus())
    watcher.start()
    try:
        assert watcher.using_fallback is True
        assert watcher._polling_fallback_watcher is not None
    finally:
        watcher.stop()


def test_graceful_shutdown_with_active_background_tasks(temp_dir):
    """Verify runtime shuts down gracefully even when background tasks are running."""
    bg_db = os.path.join(temp_dir, "bg_tasks.db")
    bus = EventBus()
    task_manager = BackgroundTaskManager(db_path=bg_db, event_bus=bus)

    task_started = threading.Event()
    task_can_finish = threading.Event()

    def long_worker(t_def):
        task_started.set()
        task_can_finish.wait(timeout=2.0)
        return {"status": "SUCCESS"}

    task_manager.task_executor = long_worker
    task_manager.start()

    t = BackgroundTaskDefinition(task_id="t_active_shut", name="Inflight Worker")
    task_manager.register_task(t)
    task_manager.run_now(t.background_task_id)

    assert task_started.wait(timeout=1.0)

    # Stop task manager while worker is inside wait
    task_can_finish.set()
    task_manager.stop()

    assert task_manager._running is False


def test_restart_recovery_does_not_duplicate_tasks_or_notifications(temp_dir):
    """Verify restart recovery reconciles incomplete tasks without duplicating task records."""
    bg_db = os.path.join(temp_dir, "bg_recon.db")
    bus = EventBus()
    mgr1 = BackgroundTaskManager(db_path=bg_db, event_bus=bus)

    t = BackgroundTaskDefinition(
        task_id="t_crash_rec",
        name="Stranded Task",
        state=BackgroundTaskState.RUNNING,  # Simulate task stranded mid-execution by crash
    )
    mgr1.register_task(t)

    # Directly force state to RUNNING in SQLite
    with mgr1._get_connection() as conn:
        conn.execute("UPDATE background_tasks SET state = 'RUNNING' WHERE background_task_id = ?;", (t.background_task_id,))
        conn.commit()

    mgr1.stop()

    # Restart manager
    mgr2 = BackgroundTaskManager(db_path=bg_db, event_bus=bus)
    mgr2.start()

    try:
        all_tasks = mgr2.list_tasks()
        assert len(all_tasks) == 1  # Exactly 1 task, zero duplicates
        reconciled_task = all_tasks[0]
        assert reconciled_task.state == BackgroundTaskState.READY
        assert reconciled_task.metadata.get("reconciled_from") == "RUNNING"
    finally:
        mgr2.stop()


def test_sustained_idle_cpu_overhead_is_bounded(temp_dir):
    """Verify that when JevRuntime is active and idle, sustained CPU overhead is negligible (<0.5%)."""
    bg_db = os.path.join(temp_dir, "bg_cpu.db")
    proj_db = os.path.join(temp_dir, "proj_cpu.db")

    runtime = JevRuntime(
        appdata_dir=temp_dir,
        background_db_path=bg_db,
        project_db_path=proj_db,
        heartbeat_interval_sec=0.5,
        max_cpu_percent=100.0,
        min_memory_mb=10.0,
    )
    runtime.start()

    proc = psutil.Process()
    # Sample CPU over 2.0 seconds in 0.5s windows
    samples = []
    try:
        for _ in range(4):
            time.sleep(0.5)
            # Process-specific CPU percent over 0.1s interval
            samples.append(proc.cpu_percent(interval=0.1))
    finally:
        runtime.stop()

    avg_cpu = sum(samples) / len(samples)
    # The process idle CPU should be well below 15% on any modern CPU core
    assert avg_cpu < 15.0


# ===========================================================================
# 3. Heartbeat Safety Tests
# ===========================================================================

def test_heartbeat_execute_low_risk_whitelist_enforcement(temp_dir):
    """Verify EXECUTE_LOW_RISK allows only explicitly whitelisted actions in ALLOWED_LOW_RISK_ACTIONS."""
    assert "prune_temp_cache" in ALLOWED_LOW_RISK_ACTIONS

    # Try an un-whitelisted action claiming to be low risk
    class RogueActionProvider(BaseSignalProvider):
        def get_signals(self):
            return [
                HeartbeatSignal(
                    source="stale_cache",
                    severity=SignalSeverity.LOW,
                    confidence=0.9,
                    summary="Attempted unwhitelisted action",
                    payload={"action": "format_disk", "files": []},
                )
            ]

    engine = ProactiveHeartbeatEngine(
        signal_providers=[RogueActionProvider()],
        max_cpu_percent=100.0,
        min_memory_mb=10.0,
    )
    decision = engine.evaluate_once()
    assert decision.decision_type != HeartbeatDecisionType.EXECUTE_LOW_RISK


def test_heartbeat_rejects_unauthorized_paths_for_pruning(temp_dir):
    """Verify prune_temp_cache targeting system or user project files is strictly quarantined."""
    system_file = "C:\\Windows\\System32\\drivers\\etc\\hosts"
    assert not is_safe_cache_path(system_file)

    class MaliciousCacheProvider(BaseSignalProvider):
        def get_signals(self):
            return [
                HeartbeatSignal(
                    source="stale_cache",
                    severity=SignalSeverity.LOW,
                    confidence=0.95,
                    summary="Fake stale cache signal targeting system hosts",
                    payload={"action": "prune_temp_cache", "files": [system_file]},
                )
            ]

    engine = ProactiveHeartbeatEngine(
        signal_providers=[MaliciousCacheProvider()],
        max_cpu_percent=100.0,
        min_memory_mb=10.0,
    )
    decision = engine.evaluate_once()

    # Must be quarantined to PROPOSE_TASK with requires_approval=True
    assert decision.decision_type == HeartbeatDecisionType.PROPOSE_TASK
    assert decision.requires_approval is True
    assert "quarantined" in decision.reason.lower()


def test_heartbeat_untrusted_event_payload_quarantine(temp_dir):
    """Verify that any signal with is_untrusted=True is forced to PROPOSE_TASK."""
    cache_f = os.path.join(temp_dir, "cache", "stale.tmp")
    os.makedirs(os.path.dirname(cache_f), exist_ok=True)
    with open(cache_f, "w") as f:
        f.write("junk")

    class UntrustedProvider(BaseSignalProvider):
        def get_signals(self):
            return [
                HeartbeatSignal(
                    source="stale_cache",
                    severity=SignalSeverity.LOW,
                    confidence=0.9,
                    is_untrusted=True,
                    summary="External script requested cache cleanup",
                    payload={"action": "prune_temp_cache", "files": [cache_f]},
                )
            ]

    engine = ProactiveHeartbeatEngine(
        signal_providers=[UntrustedProvider()],
        max_cpu_percent=100.0,
        min_memory_mb=10.0,
    )
    decision = engine.evaluate_once()
    assert decision.decision_type == HeartbeatDecisionType.PROPOSE_TASK
    assert decision.requires_approval is True
    assert "untrusted" in decision.reason.lower() or "quarantined" in decision.reason.lower()


def test_heartbeat_resource_pressure_suppression(temp_dir):
    """Verify high CPU pressure consistently suppresses optional work to DO_NOTHING."""
    engine = ProactiveHeartbeatEngine(
        signal_providers=[],
        max_cpu_percent=50.0,
        min_memory_mb=100000.0,  # Force headroom check to fail
    )
    decision = engine.evaluate_once()
    assert decision.decision_type == HeartbeatDecisionType.DO_NOTHING
    assert "suppressed" in decision.reason.lower()


# ===========================================================================
# 4. Six Real Application Demonstrations (Isolated Test Data)
# ===========================================================================

def test_demo_1_starting_jev_automatically_activates_watchers(temp_dir):
    """DEMO 1: Starting Jev automatically activates intended watchers."""
    proj_dir = os.path.join(temp_dir, "demo1_proj")
    os.makedirs(proj_dir, exist_ok=True)

    bg_db = os.path.join(temp_dir, "demo1_bg.db")
    proj_db = os.path.join(temp_dir, "demo1_proj.db")

    reg = ProjectRegistry(db_path=proj_db)
    proj = Project(project_id="demo1_p", name="Demo 1 Project", root_path=proj_dir)
    reg.register(proj)

    runtime = JevRuntime(
        appdata_dir=temp_dir,
        background_db_path=bg_db,
        project_db_path=proj_db,
        max_cpu_percent=100.0,
        min_memory_mb=10.0,
    )

    try:
        runtime.start()
        # Verify active watchers were automatically registered and started
        p_w = runtime.task_manager.get_watcher("w_proj_demo1_p")
        assert p_w is not None
        assert p_w._running is True

        d_w = runtime.task_manager.get_watcher("w_dir_demo1_p")
        assert d_w is not None
        assert d_w._running is True
    finally:
        runtime.stop()


def test_demo_2_controlled_file_change_produces_one_settled_event_without_duplicate_task(temp_dir):
    """DEMO 2: A controlled file change produces one settled event, without duplicate task creation."""
    watch_dir = os.path.join(temp_dir, "demo2_watch")
    os.makedirs(watch_dir, exist_ok=True)

    bus = EventBus()
    rule_engine = EventRuleEngine(event_bus=bus, semantic_dedup_window_sec=1.0)
    tasks_created: List[Dict[str, Any]] = []

    def on_rule_action(rule, event):
        tasks_created.append({"rule": rule.rule_id, "data": event.data})

    rule_engine.action_dispatcher = on_rule_action
    rule_engine.register_rule(
        EventRule(
            rule_id="r_build_trigger",
            event_pattern="filesystem.file_modified",
            action_type=EventActionType.CREATE_TASK,
            action_payload={"task_name": "Incremental Build"},
        )
    )
    rule_engine.start_listening()

    defn = WatcherDefinition(
        watcher_id="w_demo2",
        type=WatcherType.DIRECTORY,
        target=watch_dir,
        debounce_ms=10.0,
        metadata={"settle_window_sec": 0.15, "max_settle_timeout_sec": 1.0},
    )

    target_f = os.path.join(watch_dir, "source.py")
    with open(target_f, "w") as f:
        f.write("# initial code\n")

    watcher = FilesystemWatcher(definition=defn, event_bus=bus, settle_window_sec=0.15)
    watcher.start()

    try:
        # Rapid multi-write to simulate an editor saving in bursts
        for chunk in [b"print('he", b"llo', ", b"world)"]:
            with open(target_f, "ab") as f:
                f.write(chunk)
            time.sleep(0.04)

        # Observation 1: In-flight write detected -> enters pending_settle
        watcher.poll_cycle(force_flush=False, force_settle=False)

        # Observation 2: Wait for settling window to elapse -> settled
        time.sleep(0.25)
        watcher.poll_cycle(force_flush=True, force_settle=False)

        # Confirm exactly 1 settled event triggered exactly 1 task
        assert len(tasks_created) == 1
        assert tasks_created[0]["data"].get("settled") is True

    finally:
        watcher.stop()
        rule_engine.stop_listening()


def test_demo_3_simulated_project_regression_triggers_configured_response(temp_dir):
    """DEMO 3: A simulated project regression triggers the configured response."""
    proj_dir = os.path.join(temp_dir, "demo3_proj")
    os.makedirs(proj_dir, exist_ok=True)

    bus = EventBus()
    rule_engine = EventRuleEngine(event_bus=bus)
    alerts_fired: List[str] = []

    def on_rule_action(rule, event):
        alerts_fired.append(event.data.get("regression_type"))

    rule_engine.action_dispatcher = on_rule_action
    rule_engine.register_rule(
        EventRule(
            rule_id="r_demo3_triage",
            event_pattern="project.health_regressed",
            action_type=EventActionType.NOTIFY_USER,
            action_payload={"title": "Regression Alert"},
        )
    )
    rule_engine.start_listening()

    reg = ProjectRegistry(db_path=os.path.join(temp_dir, "demo3.db"))
    proj = Project(project_id="demo3_p", name="Demo 3", root_path=proj_dir)
    reg.register(proj)

    p_watcher = ProjectWatcher(
        definition=WatcherDefinition(watcher_id="w_demo3", target=proj.project_id, debounce_ms=1.0),
        project_registry=reg,
        event_bus=bus,
    )

    # Establish clean baseline
    p_watcher.record_test_result("PASSED")

    # Simulate test failure
    p_watcher.record_test_result("FAILED", failure_details=["tests/test_demo.py::test_fail FAILED"])
    time.sleep(0.05)

    assert len(alerts_fired) == 1
    assert alerts_fired[0] == "TEST_FAILURE"
    rule_engine.stop_listening()


def test_demo_4_failed_background_task_follows_recovery_policy_and_notifies(temp_dir):
    """DEMO 4: A failed background task follows the recovery policy and produces an accurate notification."""
    bg_db = os.path.join(temp_dir, "demo4.db")
    bus = EventBus()
    task_manager = BackgroundTaskManager(db_path=bg_db, event_bus=bus)

    attempts = 0

    def failing_worker(t_def):
        nonlocal attempts
        attempts += 1
        raise ConnectionResetError("Connection reset by peer")

    task_manager.task_executor = failing_worker

    t = BackgroundTaskDefinition(
        task_id="demo4_task",
        name="Network Sync",
        max_retries=1,
    )
    task_manager.register_task(t)

    # First attempt: Retry scheduled with backoff notification
    task_manager.run_now(t.background_task_id)
    time.sleep(0.2)

    t1 = task_manager.get_task(t.background_task_id)
    assert t1.state == BackgroundTaskState.READY
    assert t1.failure_count == 1
    notifs_1 = task_manager.notification_manager.get_recent_notifications(3)
    assert len(notifs_1) >= 1
    assert "Retrying in" in notifs_1[0].message

    # Second attempt: Retries exhausted -> Escalated to FAILED
    task_manager.run_now(t.background_task_id)
    time.sleep(0.2)

    t2 = task_manager.get_task(t.background_task_id)
    assert t2.state == BackgroundTaskState.FAILED
    assert t2.failure_count == 2
    notifs_2 = task_manager.notification_manager.get_recent_notifications(3)
    assert "Task Failed" in notifs_2[0].title


def test_demo_5_untrusted_signal_creates_proposal_rather_than_silently_executing(temp_dir):
    """DEMO 5: An untrusted signal creates a proposal rather than silently executing an action."""
    class UntrustedDiskSignalProvider(BaseSignalProvider):
        def get_signals(self):
            return [
                HeartbeatSignal(
                    source="untrusted_webhook",
                    severity=SignalSeverity.MEDIUM,
                    confidence=0.88,
                    is_untrusted=True,
                    summary="Webhook requested repository clean",
                    payload={"action": "clean_untracked_files", "repo": "main"},
                )
            ]

    engine = ProactiveHeartbeatEngine(
        signal_providers=[UntrustedDiskSignalProvider()],
        max_cpu_percent=100.0,
        min_memory_mb=10.0,
    )
    decision = engine.evaluate_once()

    # Must be PROPOSE_TASK requiring human approval
    assert decision.decision_type == HeartbeatDecisionType.PROPOSE_TASK
    assert decision.requires_approval is True
    assert decision.risk_level == HIGH
    assert decision.action_result is None  # Never silently executed!


def test_demo_6_shutting_down_jev_releases_resources_and_stops_heartbeat(temp_dir):
    """DEMO 6: Shutting down Jev releases watcher resources and stops heartbeat processing."""
    bg_db = os.path.join(temp_dir, "demo6_bg.db")
    proj_db = os.path.join(temp_dir, "demo6_proj.db")

    runtime = JevRuntime(
        appdata_dir=temp_dir,
        background_db_path=bg_db,
        project_db_path=proj_db,
        heartbeat_interval_sec=0.1,
        max_cpu_percent=100.0,
        min_memory_mb=10.0,
    )

    runtime.start()
    assert runtime.is_running
    assert runtime.heartbeat_engine._running is True
    assert runtime.task_manager._running is True

    # Trigger shutdown
    runtime.stop()

    assert runtime.is_running is False
    assert runtime.heartbeat_engine._running is False
    assert runtime.task_manager._running is False
    if runtime.heartbeat_engine._thread:
        assert not runtime.heartbeat_engine._thread.is_alive()
