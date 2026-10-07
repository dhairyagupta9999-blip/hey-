"""Unit and Integration Tests for Phase 17: Background Tasks, Watchers & Event-Driven Automation."""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from background_status import BackgroundStatus
from background_task_manager import BackgroundTaskManager
from background_task_model import (
    BackgroundTaskDefinition,
    BackgroundTaskPriority,
    BackgroundTaskState,
    NotificationLevel,
    ResourceClass,
)
from change_detector import ChangeDetector, ChangeType
from event_rule_engine import EventActionType, EventRule, EventRuleEngine
from git_inspector import GitInspector
from memory_model import EpistemicStatus, MemoryRecord, MemorySensitivity, MemorySource, MemoryType
from memory_store import SqliteMemoryStore
from notification_manager import NotificationManager
from project_model import Project
from project_registry import ProjectRegistry
from recovery_manager import RecoveryManager
from resource_governor import ResourceGovernor
from scheduler_backend import InProcessSchedulerBackend
from trigger_model import TriggerDefinition, TriggerType
from watcher_model import WatcherDefinition, WatcherState, WatcherType
from watchers import (
    BrowserWatcher,
    FilesystemWatcher,
    GitWatcher,
    MissionWatcher,
    ProcessWatcher,
    ProjectWatcher,
)


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="heyjev_p17_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def task_manager(temp_dir):
    db_path = os.path.join(temp_dir, "test_background.db")
    mgr = BackgroundTaskManager(db_path=db_path)
    yield mgr
    mgr.stop()


# ---------------------------------------------------------
# 1. Models & Schemas
# ---------------------------------------------------------

def test_background_task_model_creation():
    trigger = TriggerDefinition(
        trigger_type=TriggerType.INTERVAL,
        interval_seconds=60.0,
    )
    task = BackgroundTaskDefinition(
        task_id="task_123",
        name="Test Interval Task",
        description="A test task",
        action_type="shell",
        action_payload={"command": "dir"},
        trigger=trigger,
        priority=BackgroundTaskPriority.HIGH,
        resource_class=ResourceClass.LIGHT,
        notification_level=NotificationLevel.NORMAL,
        state=BackgroundTaskState.DRAFT,
    )
    assert task.task_id == "task_123"
    assert task.state == BackgroundTaskState.DRAFT
    assert task.priority == BackgroundTaskPriority.HIGH
    assert task.resource_class == ResourceClass.LIGHT
    assert task.trigger.interval_seconds == 60.0


def test_watcher_model_creation():
    wdef = WatcherDefinition(
        watcher_id="w_fs_1",
        watcher_type=WatcherType.FILESYSTEM,
        name="FS Watcher",
        target_path="C:\\test",
        poll_interval=2.0,
    )
    assert wdef.watcher_id == "w_fs_1"
    assert wdef.state == WatcherState.INITIALIZING
    assert wdef.poll_interval == 2.0


# ---------------------------------------------------------
# 2. Task Manager CRUD & Persistence
# ---------------------------------------------------------

def test_task_manager_crud_and_persistence(temp_dir):
    db_path = os.path.join(temp_dir, "persist_test.db")
    mgr1 = BackgroundTaskManager(db_path=db_path)

    task1 = BackgroundTaskDefinition(
        task_id="t1",
        name="Task 1",
        action_type="custom",
        action_payload={"arg": 42},
        priority=BackgroundTaskPriority.HIGH,
    )
    mgr1.register_task(task1)
    retrieved = mgr1.get_task("t1")
    assert retrieved is not None
    assert retrieved.name == "Task 1"
    assert retrieved.priority == BackgroundTaskPriority.HIGH
    assert retrieved.state == BackgroundTaskState.READY

    mgr1.stop()

    # Re-open with new instance on same database
    mgr2 = BackgroundTaskManager(db_path=db_path)
    retrieved2 = mgr2.get_task("t1")
    assert retrieved2 is not None
    assert retrieved2.name == "Task 1"
    assert retrieved2.action_payload == {"arg": 42}

    # Delete
    deleted = mgr2.delete_task("t1")
    assert deleted is True
    assert mgr2.get_task("t1") is None
    mgr2.stop()


def test_task_manager_lifecycle_states(task_manager):
    task = BackgroundTaskDefinition(
        task_id="t_life",
        name="Lifecycle Task",
        action_type="custom",
    )
    task_manager.register_task(task)
    assert task_manager.get_task("t_life").state == BackgroundTaskState.READY

    task_manager.pause_task("t_life")
    assert task_manager.get_task("t_life").state == BackgroundTaskState.PAUSED

    task_manager.resume_task("t_life")
    assert task_manager.get_task("t_life").state == BackgroundTaskState.READY

    task_manager.cancel_task("t_life")
    assert task_manager.get_task("t_life").state == BackgroundTaskState.CANCELLED


def test_task_manager_run_now(task_manager):
    executed = []

    def mock_handler(task: BackgroundTaskDefinition):
        executed.append(task.task_id)
        return {"result": "success", "echo": task.action_payload.get("msg")}

    task_manager.register_action_handler("test_echo", mock_handler)

    task = BackgroundTaskDefinition(
        task_id="t_run",
        name="Run Now Task",
        action_type="test_echo",
        action_payload={"msg": "hello world"},
    )
    task_manager.register_task(task)

    result = task_manager.run_task_now("t_run")
    assert result.get("result") == "success"
    assert result.get("echo") == "hello world"
    assert "t_run" in executed
    assert task_manager.get_task("t_run").state == BackgroundTaskState.COMPLETED


# ---------------------------------------------------------
# 3. Scheduler Backend
# ---------------------------------------------------------

def test_inprocess_scheduler_interval():
    scheduler = InProcessSchedulerBackend(misfire_threshold_sec=0.5)
    triggered = []

    def callback(task_id: str):
        triggered.append(task_id)

    # Schedule a task due immediately (in past)
    task = BackgroundTaskDefinition(
        task_id="t_sched_1",
        name="Scheduled 1",
        action_type="noop",
        trigger=TriggerDefinition(
            trigger_type=TriggerType.INTERVAL,
            interval_seconds=1.0,
        ),
        next_run_at=time.time() - 1.0,
    )
    scheduler.schedule_task(task, callback)
    scheduler.step()

    assert "t_sched_1" in triggered
    health = scheduler.get_health_status()
    assert health["scheduled_jobs"] == 1
    assert health["misfired_jobs_count"] >= 1  # was in past so counted as misfired


# ---------------------------------------------------------
# 4. Watchers
# ---------------------------------------------------------

def test_filesystem_watcher_safety_rejection():
    # Attempting to watch root drive like C:\ or \ should fail
    wdef = WatcherDefinition(
        watcher_id="w_unsafe",
        watcher_type=WatcherType.FILESYSTEM,
        name="Unsafe Watcher",
        target_path="C:\\",
    )
    watcher = FilesystemWatcher(definition=wdef, on_event=lambda e: None)
    with pytest.raises(ValueError, match="Safety check failed"):
        watcher.start()


def test_filesystem_watcher_event_detection(temp_dir):
    events = []

    def handler(evt):
        events.append(evt)

    watch_path = os.path.join(temp_dir, "watch_folder")
    os.makedirs(watch_path, exist_ok=True)

    wdef = WatcherDefinition(
        watcher_id="w_safe",
        watcher_type=WatcherType.FILESYSTEM,
        name="Safe Watcher",
        target_path=watch_path,
        poll_interval=0.1,
        debounce_ms=10.0,
        cooldown_sec=0.0,
    )
    watcher = FilesystemWatcher(definition=wdef, on_event=handler)
    watcher.start()
    time.sleep(0.15)

    # Create a file
    new_file = os.path.join(watch_path, "sample.txt")
    with open(new_file, "w", encoding="utf-8") as f:
        f.write("content")

    # Step watcher poll manually
    watcher.poll_cycle()
    time.sleep(0.05)
    watcher.stop()

    assert len(events) >= 1
    assert events[0].get("event_name") == "filesystem.file_created"
    assert "sample.txt" in events[0].get("file_path", "")


def test_process_watcher():
    events = []
    wdef = WatcherDefinition(
        watcher_id="w_proc",
        watcher_type=WatcherType.PROCESS,
        name="Current Process Watcher",
        poll_interval=0.1,
    )
    # Watch current process PID (which is alive)
    watcher = ProcessWatcher(
        definition=wdef,
        on_event=lambda e: events.append(e),
        target_pid=os.getpid(),
    )
    watcher.start()
    watcher.poll_cycle()
    assert watcher.definition.state == WatcherState.HEALTHY
    watcher.stop()


def test_mission_and_browser_watchers():
    m_events = []
    w_m_def = WatcherDefinition(
        watcher_id="w_mission",
        watcher_type=WatcherType.MISSION,
        name="Mission Watcher",
    )
    m_watcher = MissionWatcher(definition=w_m_def, on_event=lambda e: m_events.append(e))
    m_watcher.report_mission_status("mission_001", "COMPLETED", {"summary": "all done"})
    assert len(m_events) == 1
    assert m_events[0]["event_name"] == "mission.completed"

    b_events = []
    w_b_def = WatcherDefinition(
        watcher_id="w_browser",
        watcher_type=WatcherType.BROWSER,
        name="Browser Watcher",
    )
    b_watcher = BrowserWatcher(
        definition=w_b_def,
        on_event=lambda e: b_events.append(e),
        url_checker=lambda url: "<html><body>Version 2.0</body></html>",
    )
    b_watcher.check_url("https://example.com")  # baseline
    # change content
    b_watcher.url_checker = lambda url: "<html><body>Version 3.0</body></html>"
    b_watcher.check_url("https://example.com")
    assert len(b_events) == 1
    assert b_events[0]["event_name"] == "browser.content_changed"


# ---------------------------------------------------------
# 5. Event Rule Engine
# ---------------------------------------------------------

def test_event_rule_engine_matching_and_actions():
    engine = EventRuleEngine()
    rule = EventRule(
        rule_id="r1",
        name="Alert on error",
        event_name="app.error",
        conditions={"severity": {"equals": "critical"}},
        action_type=EventActionType.SEND_NOTIFICATION,
        action_payload={"title": "Critical Error Occurred"},
    )
    engine.register_rule(rule)

    # Does not match
    res_no = engine.evaluate_event("app.error", {"severity": "low"})
    assert len(res_no) == 0

    # Matches
    res_yes = engine.evaluate_event("app.error", {"severity": "critical"})
    assert len(res_yes) == 1
    assert res_yes[0]["action_type"] == EventActionType.SEND_NOTIFICATION.value


def test_event_rule_engine_loop_prevention():
    engine = EventRuleEngine(max_chain_depth=3)
    rule = EventRule(
        rule_id="loop_rule",
        name="Loop Rule",
        event_name="ping",
        conditions={},
        action_type=EventActionType.DISPATCH_EVENT,
        action_payload={"event_name": "ping"},
    )
    engine.register_rule(rule)

    # Exceeding chain depth should yield empty actions
    actions = engine.evaluate_event("ping", {}, chain_depth=4)
    assert len(actions) == 0


# ---------------------------------------------------------
# 6. Change Detector
# ---------------------------------------------------------

def test_change_detector_dicts_and_text():
    cd = ChangeDetector()
    d1 = {"a": 1, "b": "hello", "c": 3}
    d2 = {"a": 1, "b": "world", "d": 4}

    diff_d = cd.diff_dicts(d1, d2)
    assert diff_d["has_changes"] is True
    assert "d" in diff_d["added"]
    assert "c" in diff_d["removed"]
    assert "b" in diff_d["modified"]

    t1 = "line 1\nline 2\nline 3"
    t2 = "line 1\nline 2 mod\nline 3\nline 4"
    diff_t = cd.diff_text(t1, t2)
    assert diff_t["has_changes"] is True
    assert diff_t["additions_count"] >= 1


# ---------------------------------------------------------
# 7. Resource Governor & Preemption
# ---------------------------------------------------------

def test_resource_governor_concurrency_and_preemption():
    gov = ResourceGovernor(max_foreground=1, max_background=1)

    t_bg1 = BackgroundTaskDefinition(task_id="bg1", name="BG 1", priority=BackgroundTaskPriority.NORMAL)
    t_bg2 = BackgroundTaskDefinition(task_id="bg2", name="BG 2", priority=BackgroundTaskPriority.LOW)

    # First background task acquires slot
    res1 = gov.acquire_slot(t_bg1, is_foreground=False)
    assert res1.allowed is True

    # Second background task cannot acquire because max_background=1
    res2 = gov.acquire_slot(t_bg2, is_foreground=False)
    assert res2.allowed is False
    assert "Capacity reached" in res2.reason

    # Preemption: When voice is active, background tasks are blocked
    gov.release_slot("bg1")
    gov.set_voice_active(True)
    res3 = gov.acquire_slot(t_bg1, is_foreground=False)
    assert res3.allowed is False
    assert "Voice interaction active" in res3.reason

    # Foreground task can still run even during voice
    t_fg = BackgroundTaskDefinition(task_id="fg1", name="FG 1", priority=BackgroundTaskPriority.HIGH)
    res_fg = gov.acquire_slot(t_fg, is_foreground=True)
    assert res_fg.allowed is True

    gov.release_slot("fg1")
    gov.set_voice_active(False)


# ---------------------------------------------------------
# 8. Notification Manager
# ---------------------------------------------------------

def test_notification_manager():
    nm = NotificationManager()
    notif = nm.send_notification(
        title="Backup Complete",
        message="Daily backup succeeded",
        level=NotificationLevel.NORMAL,
    )
    assert notif.notification_id is not None
    assert notif.title == "Backup Complete"

    recent = nm.get_recent_notifications(10)
    assert len(recent) >= 1
    assert recent[0].title == "Backup Complete"


# ---------------------------------------------------------
# 9. Recovery Manager
# ---------------------------------------------------------

def test_recovery_manager_reconciliation(task_manager):
    # Simulate a task left in RUNNING due to sudden crash
    task = BackgroundTaskDefinition(
        task_id="t_crash",
        name="Crashed Task",
        action_type="custom",
        state=BackgroundTaskState.RUNNING,
    )
    task_manager.register_task(task)
    # Force state to RUNNING directly in DB to simulate crash
    task_manager.update_task_state("t_crash", BackgroundTaskState.RUNNING)
    assert task_manager.get_task("t_crash").state == BackgroundTaskState.RUNNING

    recovery = RecoveryManager()
    recon_report = recovery.reconcile_startup_tasks(task_manager)
    assert recon_report["reconciled_count"] == 1

    # Should be reset to READY
    assert task_manager.get_task("t_crash").state == BackgroundTaskState.READY


# ---------------------------------------------------------
# 10. Background Status
# ---------------------------------------------------------

def test_background_status(task_manager):
    status = BackgroundStatus(task_manager)
    snapshot = status.get_status_snapshot()
    assert snapshot.total_tasks >= 0
    assert isinstance(snapshot.summary_text, str)

    summary = status.get_active_work_summary()
    assert "Active Background Work" in summary


# =========================================================
# 11. END-TO-END DEMO SCENARIOS (1 to 5)
# =========================================================

def test_e2e_demo1_folder_watch_to_verification_and_notify(task_manager, temp_dir):
    """Demo 1: Folder watcher -> file created -> rule -> task -> verify -> notify."""
    watch_folder = os.path.join(temp_dir, "inbox")
    os.makedirs(watch_folder, exist_ok=True)

    verified_files = []

    def verify_action(task: BackgroundTaskDefinition):
        file_path = task.action_payload.get("file_path")
        if os.path.exists(file_path):
            verified_files.append(file_path)
            task_manager.notification_manager.send_notification(
                title="Verification Complete",
                message=f"Verified file {os.path.basename(file_path)}",
                level=NotificationLevel.NORMAL,
            )
            return {"status": "verified", "file": file_path}
        return {"status": "missing"}

    task_manager.register_action_handler("verify_file", verify_action)

    # 1. Register rule: on file created -> create background verification task
    rule = EventRule(
        rule_id="r_file_verify",
        name="Auto Verify File",
        event_name="filesystem.file_created",
        conditions={"file_path": {"contains": "inbox"}},
        action_type=EventActionType.CREATE_BACKGROUND_TASK,
        action_payload={
            "task_name": "Verify Incoming File",
            "action_type": "verify_file",
        },
    )
    task_manager.rule_engine.register_rule(rule)

    # 2. Filesystem watcher
    wdef = WatcherDefinition(
        watcher_id="w_inbox",
        watcher_type=WatcherType.FILESYSTEM,
        name="Inbox Watcher",
        target_path=watch_folder,
        poll_interval=0.1,
    )
    fs_watcher = FilesystemWatcher(
        definition=wdef,
        on_event=lambda evt: task_manager.handle_watcher_event(evt),
    )
    task_manager.register_watcher(fs_watcher)

    # 3. Create file in folder
    test_file = os.path.join(watch_folder, "data_report.csv")
    with open(test_file, "w", encoding="utf-8") as f:
        f.write("id,value\n1,100\n")

    # 4. Trigger poll cycle and execute created task
    fs_watcher.poll_cycle()

    # Find the created task and run it
    ready_tasks = task_manager.list_tasks(state=BackgroundTaskState.READY)
    verify_tasks = [t for t in ready_tasks if t.action_type == "verify_file"]
    assert len(verify_tasks) == 1

    res = task_manager.run_task_now(verify_tasks[0].task_id)
    assert res.get("status") == "verified"
    assert test_file in verified_files

    # 5. Verify notification was dispatched
    notifs = task_manager.notification_manager.get_recent_notifications(5)
    assert any("Verified file data_report.csv" in n.message for n in notifs)


def test_e2e_demo2_project_watcher_to_health_audit_and_notify(task_manager):
    """Demo 2: Project watcher -> test failure -> health -> rule -> task -> notify."""
    diagnostics_log = []

    def run_diagnostics(task: BackgroundTaskDefinition):
        project_name = task.action_payload.get("project_name", "unknown")
        issue = task.action_payload.get("issue", "tests failing")
        diagnostics_log.append((project_name, issue))
        task_manager.notification_manager.send_notification(
            title="Project Health Alert",
            message=f"Project {project_name} has issues: {issue}",
            level=NotificationLevel.HIGH,
        )
        return {"status": "diagnosed", "action": "sent alert"}

    task_manager.register_action_handler("run_diagnostics", run_diagnostics)

    # Rule: project.health_warning -> run_diagnostics task
    rule = EventRule(
        rule_id="r_proj_health",
        name="Project Health Rule",
        event_name="project.health_warning",
        conditions={"status": {"equals": "degraded"}},
        action_type=EventActionType.CREATE_BACKGROUND_TASK,
        action_payload={
            "task_name": "Project Diagnostics",
            "action_type": "run_diagnostics",
        },
    )
    task_manager.rule_engine.register_rule(rule)

    # Emit event simulating ProjectWatcher health degradation
    task_manager.handle_watcher_event({
        "event_name": "project.health_warning",
        "project_name": "hey-jev",
        "status": "degraded",
        "issue": "unit test failure in subsystem",
    })

    # Execute created task
    ready_tasks = [t for t in task_manager.list_tasks(state=BackgroundTaskState.READY) if t.action_type == "run_diagnostics"]
    assert len(ready_tasks) == 1
    task_manager.run_task_now(ready_tasks[0].task_id)

    assert len(diagnostics_log) == 1
    assert diagnostics_log[0] == ("hey-jev", "unit test failure in subsystem")

    notifs = task_manager.notification_manager.get_recent_notifications(5)
    assert any("Project hey-jev has issues" in n.message for n in notifs)


def test_e2e_demo3_scheduled_browser_check_diff_and_notify(task_manager):
    """Demo 3: Scheduled check -> browser check -> change detector -> notify on change."""
    detector = ChangeDetector()
    site_states = {
        "https://example.com/status": "System Version 1.0 - All Systems Green",
    }

    def check_site_action(task: BackgroundTaskDefinition):
        url = task.action_payload.get("url")
        last_state = task.action_payload.get("last_content", "")
        current_state = site_states.get(url, "")

        diff = detector.diff_text(last_state, current_state)
        if diff["has_changes"]:
            task_manager.notification_manager.send_notification(
                title="Site Content Changed",
                message=f"Changes detected on {url}: +{diff['additions_count']} additions",
                level=NotificationLevel.NORMAL,
            )
            # Update state in task
            task.action_payload["last_content"] = current_state
            return {"changed": True, "diff": diff}
        return {"changed": False}

    task_manager.register_action_handler("check_site", check_site_action)

    task = BackgroundTaskDefinition(
        task_id="t_site_monitor",
        name="Monitor Example Status",
        action_type="check_site",
        action_payload={
            "url": "https://example.com/status",
            "last_content": "System Version 1.0 - All Systems Green",
        },
    )
    task_manager.register_task(task)

    # First check: unchanged
    res1 = task_manager.run_task_now("t_site_monitor")
    assert res1.get("changed") is False

    # Site updates
    site_states["https://example.com/status"] = "System Version 1.1 - Maintenance Scheduled"

    # Second check: detected change
    # Reset state to READY to run again
    task_manager.update_task_state("t_site_monitor", BackgroundTaskState.READY)
    res2 = task_manager.run_task_now("t_site_monitor")
    assert res2.get("changed") is True

    notifs = task_manager.notification_manager.get_recent_notifications(5)
    assert any("Site Content Changed" in n.title for n in notifs)


def test_e2e_demo4_mission_completed_to_review_agent_task(task_manager):
    """Demo 4: Mission completed -> rule -> review agent / skill -> notify."""
    reviews_done = []

    def mission_review_action(task: BackgroundTaskDefinition):
        mission_id = task.action_payload.get("mission_id")
        reviews_done.append(mission_id)
        task_manager.notification_manager.send_notification(
            title="Mission Review Complete",
            message=f"Follow-up review completed for mission {mission_id}",
            level=NotificationLevel.NORMAL,
        )
        return {"reviewed": mission_id, "score": "A"}

    task_manager.register_action_handler("review_mission", mission_review_action)

    rule = EventRule(
        rule_id="r_mission_done",
        name="Review on Mission Complete",
        event_name="mission.completed",
        conditions={"status": {"equals": "success"}},
        action_type=EventActionType.CREATE_BACKGROUND_TASK,
        action_payload={
            "task_name": "Mission Review Task",
            "action_type": "review_mission",
        },
    )
    task_manager.rule_engine.register_rule(rule)

    # Trigger event simulating mission completion
    task_manager.handle_watcher_event({
        "event_name": "mission.completed",
        "mission_id": "mission_antigravity_001",
        "status": "success",
    })

    review_tasks = [t for t in task_manager.list_tasks(state=BackgroundTaskState.READY) if t.action_type == "review_mission"]
    assert len(review_tasks) == 1
    task_manager.run_task_now(review_tasks[0].task_id)

    assert "mission_antigravity_001" in reviews_done
    notifs = task_manager.notification_manager.get_recent_notifications(5)
    assert any("Follow-up review completed" in n.message for n in notifs)


def test_e2e_demo5_background_research_to_memory_and_notify(task_manager, temp_dir):
    """Demo 5: Background research -> memory comparison -> change detection -> notify."""
    mem_db = os.path.join(temp_dir, "p16_memory.db")
    mem_store = SqliteMemoryStore(db_path=mem_db)

    # Seed baseline knowledge
    mem_store.add(MemoryRecord(
        memory_id="mem_fact_1",
        content="Antigravity release version is 1.0",
        type=MemoryType.RESEARCH_FACT,
        source=MemorySource.RESEARCH,
        sensitivity=MemorySensitivity.PUBLIC,
        epistemic_status=EpistemicStatus.FACT,
    ))

    def research_poll_action(task: BackgroundTaskDefinition):
        topic = task.action_payload.get("topic")
        # Simulating external fact discovery
        discovered_fact = "Antigravity release version is 2.0 with background watchers"

        # Compare with existing memory
        existing = mem_store.search("Antigravity release version")
        has_novelty = True
        for rec in existing:
            if rec.content == discovered_fact:
                has_novelty = False
                break

        if has_novelty:
            # Store update in memory
            new_record = MemoryRecord(
                memory_id="mem_fact_2",
                content=discovered_fact,
                type=MemoryType.RESEARCH_FACT,
                source=MemorySource.RESEARCH,
                sensitivity=MemorySensitivity.PUBLIC,
                epistemic_status=EpistemicStatus.FACT,
            )
            mem_store.add(new_record)
            task_manager.notification_manager.send_notification(
                title="New Research Finding",
                message=f"Discovered update for {topic}: {discovered_fact}",
                level=NotificationLevel.NORMAL,
            )
            return {"updated": True, "record_id": new_record.memory_id}
        return {"updated": False}

    task_manager.register_action_handler("research_poll", research_poll_action)

    task = BackgroundTaskDefinition(
        task_id="t_research",
        name="Poll Antigravity Updates",
        action_type="research_poll",
        action_payload={"topic": "Antigravity"},
    )
    task_manager.register_task(task)

    res = task_manager.run_task_now("t_research")
    assert res.get("updated") is True

    # Verify fact is in persistent memory
    facts = mem_store.search("version is 2.0")
    v2_facts = [f for f in facts if "2.0" in f.content]
    assert len(v2_facts) == 1
    assert "background watchers" in v2_facts[0].content

    # Verify notification
    notifs = task_manager.notification_manager.get_recent_notifications(5)
    assert any("New Research Finding" in n.title for n in notifs)
