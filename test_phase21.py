"""Tests for Phase 21: Event-Driven Watchers and Proactive Automation.

Validates:
1. File write stability / settling checks (preventing premature firing on in-flight writes)
2. Windows native directory watcher with fallback to polling
3. Project health regression detection with non-LLM deterministic triage
4. Background task failure recovery bridge with RecoveryDecisionEngine
5. EventRuleEngine semantic deduplication and causal loop prevention
6. Proactive Heartbeat Engine with 5 bounded decision types, quiet default, and safety quarantine
7. Three End-to-End Demonstrations:
   - Demo 1: Project changes -> Health regression detected & routed
   - Demo 2: Task failure -> Recovery attempted within policy -> Accurate status notification
   - Demo 3: Proactive heartbeat identifies opportunity -> Requests approval before consequential action
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

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
from git_inspector import GitInspector
from notification_manager import NotificationManager
from proactive_heartbeat import (
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
)
from project_health import ProjectHealthAssessor, ProjectHealthSnapshot
from project_model import Project
from project_registry import ProjectRegistry
from recovery_engine import FailureClassifierV2, RecoveryDecisionEngine
from reliability_model import FailureCategory, RecoveryPlan, RecoveryStrategy
from safety_engine import evaluate_risk, SAFE, MEDIUM, HIGH, BLOCKED
from watcher_model import WatcherDefinition, WatcherState, WatcherType
from watchers import (
    BaseWatcher,
    FilesystemWatcher,
    ProjectWatcher,
    WindowsNativeDirectoryWatcher,
)


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="heyjev_p21_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def task_manager(temp_dir):
    db_path = os.path.join(temp_dir, "test_p21_background.db")
    bus = EventBus()
    mgr = BackgroundTaskManager(db_path=db_path, event_bus=bus)
    yield mgr
    mgr.stop()


# ---------------------------------------------------------------------------
# 1. File Stability / Settling Check Tests
# ---------------------------------------------------------------------------

def test_filesystem_watcher_settling_in_flight_write_deferred(temp_dir):
    """Verify in-flight file write does not trigger until file has settled."""
    watch_path = os.path.join(temp_dir, "settle_watch")
    os.makedirs(watch_path, exist_ok=True)
    events: List[Dict[str, Any]] = []

    wdef = WatcherDefinition(
        watcher_id="w_settle_test",
        watcher_type=WatcherType.FILESYSTEM,
        target_path=watch_path,
        debounce_ms=10.0,
        cooldown_sec=0.0,
    )
    # Settling window: 0.25 seconds
    watcher = FilesystemWatcher(
        definition=wdef,
        on_event=lambda e: events.append(e),
        settle_window_sec=0.25,
    )
    watcher.start()

    test_file = os.path.join(watch_path, "growing_file.log")

    # Pass 1: Initial write (in-flight)
    with open(test_file, "w", encoding="utf-8") as f:
        f.write("chunk 1\n")

    watcher.poll_cycle(force_flush=True, force_settle=False)
    # File is in pending_settle; must not fire yet
    assert len(events) == 0

    # Pass 2: Write additional bytes within settling window
    time.sleep(0.05)
    with open(test_file, "a", encoding="utf-8") as f:
        f.write("chunk 2\n")

    watcher.poll_cycle(force_flush=True, force_settle=False)
    # Still growing; must not fire
    assert len(events) == 0

    # Pass 3: Wait for settling window to pass without writes
    time.sleep(0.30)
    watcher.poll_cycle(force_flush=True, force_settle=False)
    time.sleep(0.05)
    watcher.stop()

    # Now the settled file event should have fired
    assert len(events) >= 1
    assert events[0].get("event_name") in ("filesystem.file_created", "filesystem.file_modified")
    assert events[0].get("settled") is True
    assert "growing_file.log" in events[0].get("file_path", "")


def test_filesystem_watcher_zero_settling_window_fires_immediately(temp_dir):
    """Verify zero settling window preserves immediate firing behavior."""
    watch_path = os.path.join(temp_dir, "zero_settle_watch")
    os.makedirs(watch_path, exist_ok=True)
    events: List[Dict[str, Any]] = []

    wdef = WatcherDefinition(
        watcher_id="w_zero_settle",
        watcher_type=WatcherType.FILESYSTEM,
        target_path=watch_path,
        debounce_ms=10.0,
        cooldown_sec=0.0,
    )
    watcher = FilesystemWatcher(
        definition=wdef,
        on_event=lambda e: events.append(e),
        settle_window_sec=0.0,
    )
    watcher.start()
    time.sleep(0.15)

    test_file = os.path.join(watch_path, "instant.txt")
    with open(test_file, "w", encoding="utf-8") as f:
        f.write("instant data")

    watcher.poll_cycle(force_flush=True, force_settle=False)
    time.sleep(0.05)
    watcher.stop()

    assert len(events) >= 1
    assert events[0].get("event_name") == "filesystem.file_created"


# ---------------------------------------------------------------------------
# 2. Windows Native Directory Watcher Tests
# ---------------------------------------------------------------------------

def test_windows_native_directory_watcher_fallback_on_invalid_handle(temp_dir):
    """Verify WindowsNativeDirectoryWatcher gracefully falls back to polling on unsupported target."""
    fake_file = os.path.join(temp_dir, "not_a_directory.txt")
    with open(fake_file, "w", encoding="utf-8") as f:
        f.write("hello")

    wdef = WatcherDefinition(
        watcher_id="w_native_fallback",
        watcher_type=WatcherType.DIRECTORY,
        target_path=fake_file,
    )
    watcher = WindowsNativeDirectoryWatcher(definition=wdef)
    watcher.start()
    # Target is file, so CreateFile with directory semantics fails or is routed to fallback
    assert watcher.using_fallback is True
    watcher.stop()


def test_windows_native_directory_watcher_lifecycle(temp_dir):
    """Verify WindowsNativeDirectoryWatcher starts, monitors, and stops cleanly without hanging."""
    watch_path = os.path.join(temp_dir, "native_watch")
    os.makedirs(watch_path, exist_ok=True)
    events: List[Dict[str, Any]] = []

    wdef = WatcherDefinition(
        watcher_id="w_native_live",
        watcher_type=WatcherType.DIRECTORY,
        target_path=watch_path,
        debounce_ms=10.0,
    )
    watcher = WindowsNativeDirectoryWatcher(
        definition=wdef,
        on_event=lambda e: events.append(e),
        settle_window_sec=0.0,
    )
    watcher.start()
    time.sleep(0.1)

    # Create file
    sample = os.path.join(watch_path, "native_sample.txt")
    with open(sample, "w", encoding="utf-8") as f:
        f.write("native content")

    time.sleep(0.3)
    watcher.stop()

    assert watcher.definition.state in (WatcherState.HEALTHY, WatcherState.INITIALIZING)
    assert len(events) >= 1
    assert events[0].get("event_name") == "filesystem.file_created"


# ---------------------------------------------------------------------------
# 3. Project Health Regression Tests
# ---------------------------------------------------------------------------

def test_project_watcher_detects_health_regression(temp_dir):
    """Verify ProjectWatcher detects transition to failing tests and emits project.health_regressed."""
    proj_dir = os.path.join(temp_dir, "regress_proj")
    os.makedirs(proj_dir, exist_ok=True)
    events: List[Dict[str, Any]] = []

    registry = ProjectRegistry(db_path=os.path.join(temp_dir, "p21_proj.db"))
    proj = Project(project_id="test_regress_p", name="RegressProj", root_path=proj_dir)
    registry.register(proj)

    wdef = WatcherDefinition(
        watcher_id="w_proj_health",
        watcher_type=WatcherType.PROJECT,
        target=proj.project_id,
        debounce_ms=5.0,
        cooldown_sec=0.0,
    )
    p_watcher = ProjectWatcher(
        definition=wdef,
        project_registry=registry,
        on_event=lambda e: events.append(e),
    )

    # 1. Baseline: healthy state
    p_watcher.record_test_result("PASSED")
    assert len(events) == 0  # Baseline establishing, no regression yet

    # 2. Regress: test execution fails
    p_watcher.record_test_result("FAILED", failure_details=["test_core.py: AssertionError"])
    time.sleep(0.05)

    assert len(events) >= 1
    regression_evt = events[-1]
    assert regression_evt.get("event_name") == "project.health_regressed"
    assert regression_evt.get("regression_type") == "TEST_FAILURE"
    assert regression_evt.get("is_working") is False
    assert regression_evt.get("last_test_result") == "FAILED"
    assert len(regression_evt.get("recent_failures", [])) >= 1

    # 3. Recover: tests pass again
    p_watcher.record_test_result("PASSED")
    time.sleep(0.05)
    recover_evt = events[-1]
    assert recover_evt.get("event_name") == "project.health_recovered"
    assert recover_evt.get("is_working") is True


# ---------------------------------------------------------------------------
# 4. Semantic Deduplication & Loop Prevention Tests
# ---------------------------------------------------------------------------

def test_event_rule_engine_semantic_deduplication():
    """Verify semantic deduplication prevents event storms for same entity within window."""
    engine = EventRuleEngine(semantic_dedup_window_sec=0.5)
    fired_count = 0

    def counter(rule, event):
        nonlocal fired_count
        fired_count += 1

    engine.action_dispatcher = counter
    rule = EventRule(
        rule_id="r_file_mod",
        name="File Modified Alert",
        event_pattern="filesystem.file_modified",
        action_type=EventActionType.NOTIFY_USER,
    )
    engine.register_rule(rule)

    # Rapid burst 1: File A modified
    engine.evaluate_event("filesystem.file_modified", {"file_path": "C:\\repo\\main.py", "size": 100})
    # Rapid burst 2: File A modified again 10ms later (different payload/mtime)
    engine.evaluate_event("filesystem.file_modified", {"file_path": "C:\\repo\\main.py", "size": 105})
    # Different entity: File B modified
    engine.evaluate_event("filesystem.file_modified", {"file_path": "C:\\repo\\other.py", "size": 200})

    # File A should fire once, File B should fire once (total 2)
    assert fired_count == 2


def test_event_rule_engine_causal_cycle_prevention():
    """Verify recursive causal loop (Rule A -> Rule A) is blocked."""
    engine = EventRuleEngine()
    rule = EventRule(
        rule_id="recursive_rule",
        event_pattern="trigger.loop",
        action_type=EventActionType.CREATE_TASK,
    )
    engine.register_rule(rule)

    # First trigger: rule not in causal chain, fires normally
    res1 = engine.evaluate_event("trigger.loop", {"_causal_rules": []})
    assert len(res1) == 1

    # Cascading attempt: rule is already in causal chain leading to event -> blocked!
    res2 = engine.evaluate_event("trigger.loop", {"_causal_rules": ["recursive_rule"]})
    assert len(res2) == 0


# ---------------------------------------------------------------------------
# 5. Background Task Failure Recovery Bridge Tests
# ---------------------------------------------------------------------------

def test_background_task_failure_recovery_bridge(task_manager):
    """Verify task failure triggers FailureClassifierV2 and plans recovery without runaway loop."""
    events: List[Dict[str, Any]] = []
    task_manager.event_bus.subscribe("*", lambda evt: events.append({"type": evt.event_type, "data": evt.data}))

    # Executor that throws timeout error
    def failing_executor(task_def):
        raise TimeoutError("Connection to database timed out after 30s")

    task_manager.task_executor = failing_executor

    t = BackgroundTaskDefinition(
        task_id="t_fail_recover",
        name="Database Query Task",
        action_type="query",
        max_retries=2,
        notification_level=NotificationLevel.IMPORTANT,
    )
    task_manager.register_task(t)

    # Run task and let it fail
    task_manager.run_now(t.background_task_id)
    time.sleep(0.3)

    planned_events = [e for e in events if e["type"] == "recovery.planned"]
    failed_events = [e for e in events if e["type"] == "background_task.failed"]

    assert len(planned_events) >= 1
    assert len(failed_events) >= 1

    plan_data = planned_events[0]["data"]
    assert plan_data["failure_category"] == FailureCategory.TIMEOUT.value
    assert plan_data["strategy"] == RecoveryStrategy.RETRY.value
    assert "retry_delay" in plan_data

    # Verify task state is READY for retry with delay
    updated_t = task_manager.get_task(t.background_task_id)
    assert updated_t is not None
    assert updated_t.state == BackgroundTaskState.READY
    assert updated_t.failure_count == 1
    assert "next_retry_delay" in updated_t.metadata


# ---------------------------------------------------------------------------
# 6. Proactive Heartbeat Engine Tests
# ---------------------------------------------------------------------------

def test_heartbeat_quiet_default_nominal_state():
    """Verify heartbeat returns DO_NOTHING when all monitored signals are nominal."""
    with patch("psutil.cpu_percent", return_value=15.0), patch("psutil.virtual_memory") as mock_mem:
        mock_mem.return_value.available = 4096 * 1024 * 1024
        engine = ProactiveHeartbeatEngine(signal_providers=[], max_cpu_percent=100.0, min_memory_mb=10.0)
        decision = engine.evaluate_once()
        assert decision.decision_type == HeartbeatDecisionType.DO_NOTHING
        assert "nominal" in decision.reason.lower()


def test_heartbeat_suppressed_under_resource_pressure():
    """Verify heartbeat suppresses proactive work when CPU or RAM headroom is exhausted."""
    class MockHighCpuProvider(SystemResourceSignalProvider):
        def get_signals(self):
            return [
                HeartbeatSignal(
                    source="system_resources",
                    severity=SignalSeverity.CRITICAL,
                    confidence=1.0,
                    summary="CPU pegged at 98%",
                    payload={"cpu_percent": 98.0, "free_memory_mb": 100},
                )
            ]

    engine = ProactiveHeartbeatEngine(signal_providers=[MockHighCpuProvider()])
    # Force max_cpu_percent threshold to trigger
    engine.max_cpu_percent = 50.0

    with patch("psutil.cpu_percent", return_value=95.0):
        decision = engine.evaluate_once()
        assert decision.decision_type == HeartbeatDecisionType.DO_NOTHING
        assert "headroom" in decision.reason.lower() or "pressure" in decision.reason.lower()


def test_heartbeat_executes_low_risk_safe_maintenance(temp_dir):
    """Verify pre-authorized safe maintenance is executed under EXECUTE_LOW_RISK."""
    cache_dir = os.path.join(temp_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)

    # Create stale file (2 days old)
    stale_f = os.path.join(cache_dir, "old_tmp.tmp")
    with open(stale_f, "w", encoding="utf-8") as f:
        f.write("junk")
    old_time = time.time() - 172800
    os.utime(stale_f, (old_time, old_time))

    cache_prov = StaleCacheSignalProvider(cache_dir=cache_dir, ttl_seconds=86400)
    executed_tasks = []

    def mock_executor(task):
        executed_tasks.append(task)
        for f in task.get("files", []):
            if os.path.exists(f):
                os.remove(f)
        return {"status": "SUCCESS", "pruned_count": len(task.get("files", []))}

    engine = ProactiveHeartbeatEngine(
        signal_providers=[cache_prov],
        task_executor=mock_executor,
        max_cpu_percent=100.0,
        min_memory_mb=10.0,
    )
    decision = engine.evaluate_once()

    assert decision.decision_type == HeartbeatDecisionType.EXECUTE_LOW_RISK
    assert decision.risk_level == SAFE
    assert decision.requires_approval is False
    assert len(executed_tasks) == 1
    assert decision.action_result is not None
    assert decision.action_result["status"] == "SUCCESS"
    assert not os.path.exists(stale_f)


def test_heartbeat_quarantines_untrusted_input(temp_dir):
    """Verify untrusted external input cannot execute autonomously and is forced to PROPOSE_TASK."""
    class UntrustedCacheProvider(BaseSignalProvider):
        def get_signals(self):
            return [
                HeartbeatSignal(
                    source="stale_cache",
                    severity=SignalSeverity.LOW,
                    confidence=0.9,
                    is_untrusted=True,  # Untrusted external event
                    summary="External script requested file prune",
                    payload={"files": ["C:\\path\\file.tmp"], "action": "prune_temp_cache"},
                )
            ]

    engine = ProactiveHeartbeatEngine(signal_providers=[UntrustedCacheProvider()], max_cpu_percent=100.0, min_memory_mb=10.0)
    decision = engine.evaluate_once()

    assert decision.decision_type == HeartbeatDecisionType.PROPOSE_TASK
    assert decision.requires_approval is True
    assert "untrusted" in decision.reason.lower() or "quarantined" in decision.reason.lower()


def test_heartbeat_proposes_task_for_consequential_action():
    """Verify consequential tasks (risk > SAFE) are never executed silently and require confirmation."""
    class FailingProjectProvider(BaseSignalProvider):
        def get_signals(self):
            return [
                HeartbeatSignal(
                    source="project_health",
                    severity=SignalSeverity.HIGH,
                    confidence=0.95,
                    summary="Test failure detected in core project",
                    payload={"project_id": "proj_1", "action": "run_diagnostics", "name": "Core"},
                )
            ]

    engine = ProactiveHeartbeatEngine(signal_providers=[FailingProjectProvider()], max_cpu_percent=100.0, min_memory_mb=10.0)
    decision = engine.evaluate_once()

    assert decision.decision_type == HeartbeatDecisionType.PROPOSE_TASK
    assert decision.risk_level == HIGH
    assert decision.requires_approval is True
    assert decision.proposed_task is not None
    assert decision.proposed_task["action"] == "run_diagnostics"


# ---------------------------------------------------------------------------
# 7. Isolated End-to-End Demonstrations
# ---------------------------------------------------------------------------

def test_demo_1_watched_project_changes_detect_health_regression(temp_dir):
    """E2E Demo 1: Watched project changes and Jev detects a meaningful health regression.
    
    1. A monitored project experiences test failure / broken health.
    2. ProjectWatcher detects regression without calling LLM.
    3. Emits project.health_regressed with deterministic triage.
    4. EventRuleEngine catches event and triggers triage notification.
    """
    proj_dir = os.path.join(temp_dir, "demo1_project")
    os.makedirs(proj_dir, exist_ok=True)

    bus = EventBus()
    rule_engine = EventRuleEngine(event_bus=bus)
    triage_fired = []

    def on_rule_action(rule, event):
        triage_fired.append({"rule": rule.rule_id, "data": event.data})

    rule_engine.action_dispatcher = on_rule_action
    rule_engine.register_rule(
        EventRule(
            rule_id="r_triage_regression",
            event_pattern="project.health_regressed",
            action_type=EventActionType.NOTIFY_USER,
            action_payload={"title": "Project Health Alert"},
        )
    )
    rule_engine.start_listening()

    registry = ProjectRegistry(db_path=os.path.join(temp_dir, "demo1_reg.db"))
    proj = Project(project_id="demo1_p", name="Demo1 Project", root_path=proj_dir)
    registry.register(proj)

    p_watcher = ProjectWatcher(
        definition=WatcherDefinition(watcher_id="w_demo1", target=proj.project_id, debounce_ms=5.0),
        project_registry=registry,
        event_bus=bus,
    )

    # Baseline establish
    p_watcher.record_test_result("PASSED")

    # Simulate breaking change that fails tests
    p_watcher.record_test_result("FAILED", failure_details=["tests/test_api.py::test_auth FAILED"])
    time.sleep(0.05)

    assert len(triage_fired) == 1
    triage_payload = triage_fired[0]["data"]
    assert triage_payload["regression_type"] == "TEST_FAILURE"
    assert triage_payload["is_working"] is False
    assert "tests/test_api.py::test_auth FAILED" in triage_payload["recent_failures"]
    rule_engine.stop_listening()


def test_demo_2_background_task_failure_recovery_and_status(task_manager):
    """E2E Demo 2: Background task fails, recovery is attempted within policy, and user receives accurate status.
    
    1. A background task encounters a transient timeout.
    2. Task failure is classified as TIMEOUT and RecoveryDecisionEngine selects RETRY with backoff.
    3. User notification accurately reflects retry delay and attempt count.
    4. Second failure exceeding max retries results in ESCALATE.
    """
    call_count = 0

    def unreliable_worker(task_def):
        nonlocal call_count
        call_count += 1
        raise TimeoutError("Read timeout from remote API")

    task_manager.task_executor = unreliable_worker

    t = BackgroundTaskDefinition(
        task_id="demo2_task",
        name="Remote Data Sync",
        max_retries=1,  # Fails once -> retry; fails second time -> escalate
    )
    task_manager.register_task(t)

    # First attempt: Failure -> Retry planned
    task_manager.run_now(t.background_task_id)
    time.sleep(0.25)

    t1 = task_manager.get_task(t.background_task_id)
    assert t1.state == BackgroundTaskState.READY
    assert t1.failure_count == 1
    recent_notifs = task_manager.notification_manager.get_recent_notifications(5)
    assert len(recent_notifs) >= 1
    assert "Retrying in" in recent_notifs[0].message

    # Second attempt: Exceeds max_retries -> Escalated
    task_manager.run_now(t.background_task_id)
    time.sleep(0.25)

    t2 = task_manager.get_task(t.background_task_id)
    assert t2.state == BackgroundTaskState.FAILED
    assert t2.failure_count == 2
    recent_notifs_after = task_manager.notification_manager.get_recent_notifications(5)
    assert "Task Failed" in recent_notifs_after[0].title


def test_demo_3_proactive_heartbeat_requires_approval_for_consequential_action():
    """E2E Demo 3: Heartbeat identifies opportunity but requests approval before a consequential action.
    
    1. Proactive Heartbeat detects a project with broken checks.
    2. Heartbeat identifies the opportunity to run diagnostics.
    3. Because running diagnostics/scripts is consequential (HIGH risk),
       Heartbeat emits PROPOSE_TASK with requires_approval=True.
    4. Refuses silent autonomous execution, ensuring strict safety compliance.
    """
    class ProjectRegressionSignalProvider(BaseSignalProvider):
        def get_signals(self):
            return [
                HeartbeatSignal(
                    source="project_health",
                    severity=SignalSeverity.HIGH,
                    confidence=0.92,
                    summary="Build broken in service checkout_v2",
                    payload={"project_id": "checkout_v2", "action": "run_diagnostics", "name": "Checkout Service"},
                )
            ]

    engine = ProactiveHeartbeatEngine(signal_providers=[ProjectRegressionSignalProvider()], max_cpu_percent=100.0, min_memory_mb=10.0)
    decision = engine.evaluate_once()

    # Must be PROPOSE_TASK, not EXECUTE_LOW_RISK
    assert decision.decision_type == HeartbeatDecisionType.PROPOSE_TASK
    assert decision.requires_approval is True
    assert decision.risk_level == HIGH
    assert decision.proposed_task is not None
    assert decision.proposed_task["action"] == "run_diagnostics"
    assert "checkout_v2" in decision.reason or "checkout_v2" in decision.proposed_task["project_id"]
