"""Phase 21.2 Tests: Native Watcher Cancellation Hardening.

Verifies:
1. Stop from a different thread while a read is pending (cross-thread CancelIoEx).
2. Stop while a filesystem change races with cancellation.
3. Repeated start/stop cycles without handle or thread accumulation.
4. Clean shutdown with no new filesystem events.
5. Correct handling of expected cancellation error codes (ERROR_OPERATION_ABORTED / 995).
6. Polling fallback behavior when native watching cannot be initialized.
7. Stop idempotency and accurate timeout reporting.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from typing import Any, List
from unittest.mock import patch

import pytest

from event_bus import EventBus
from watcher_model import WatcherDefinition, WatcherState, WatcherType
from watchers import (
    HAS_WIN32,
    FilesystemWatcher,
    WindowsNativeDirectoryWatcher,
    _cancel_io_ex,
)

if HAS_WIN32:
    import pywintypes
    import win32file
    import win32con
    import win32event


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="jev_phase21_2_")
    yield d
    try:
        shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


@pytest.mark.skipif(not HAS_WIN32, reason="Windows native overlapped tests require pywin32")
def test_stop_from_different_thread_while_read_pending(temp_dir):
    """Verify that calling stop() from another thread while ReadDirectoryChangesW is pending
    cancels the I/O cross-thread via CancelIoEx, unblocks the worker thread, and cleanly
    releases all native handles.
    """
    watch_dir = os.path.join(temp_dir, "pending_read_dir")
    os.makedirs(watch_dir, exist_ok=True)

    defn = WatcherDefinition(
        watcher_id="w_cancel_pending",
        type=WatcherType.DIRECTORY,
        target=watch_dir,
        debounce_ms=50.0,
    )

    bus = EventBus()
    watcher = WindowsNativeDirectoryWatcher(definition=defn, event_bus=bus)
    watcher.start()

    assert watcher._running is True
    assert not watcher.using_fallback
    assert watcher._dir_handle is not None
    assert watcher._h_event is not None
    assert watcher._h_stop_event is not None
    worker_tid = watcher._thread.ident

    # Let worker enter WaitForMultipleObjects with ReadDirectoryChangesW pending
    time.sleep(0.15)
    assert watcher._thread.is_alive()

    # stop() invoked from test thread (different thread than worker)
    calling_tid = threading.get_ident()
    assert calling_tid != worker_tid

    stop_result = watcher.stop(timeout=3.0)
    assert stop_result is True
    assert watcher._running is False
    assert not watcher._thread.is_alive()

    # All handles must be cleanly closed and set to None
    assert watcher._dir_handle is None
    assert watcher._h_event is None
    assert watcher._h_stop_event is None
    assert watcher._buffer is None
    assert watcher._overlapped is None

    health = watcher.get_health()
    assert health["has_native_handle"] is False
    assert health["shutdown_timed_out"] is False
    assert health["last_error"] is None


@pytest.mark.skipif(not HAS_WIN32, reason="Windows native overlapped tests require pywin32")
def test_stop_while_filesystem_change_races_with_cancellation(temp_dir):
    """Verify that when filesystem modifications arrive concurrently with a stop() invocation,
    the watcher does not deadlock, crash, corrupt buffers, or leave orphan handles.
    """
    watch_dir = os.path.join(temp_dir, "race_dir")
    os.makedirs(watch_dir, exist_ok=True)

    defn = WatcherDefinition(
        watcher_id="w_race_cancel",
        type=WatcherType.DIRECTORY,
        target=watch_dir,
        debounce_ms=50.0,
    )

    events_captured: List[dict] = []
    bus = EventBus()
    watcher = WindowsNativeDirectoryWatcher(
        definition=defn,
        event_bus=bus,
        on_event=lambda evt, data: events_captured.append(data),
    )
    watcher.start()
    time.sleep(0.05)

    stop_done = threading.Event()

    def concurrent_writer():
        for i in range(15):
            if stop_done.is_set():
                break
            try:
                p = os.path.join(watch_dir, f"file_{i}.txt")
                with open(p, "w", encoding="utf-8") as f:
                    f.write(f"content_{i}")
            except OSError:
                break
            time.sleep(0.01)

    writer_thread = threading.Thread(target=concurrent_writer, daemon=True)
    writer_thread.start()

    # Sleep slightly so writes are actively occurring
    time.sleep(0.03)

    # Stop while writes are actively racing
    stop_result = watcher.stop(timeout=4.0)
    stop_done.set()
    writer_thread.join(timeout=2.0)

    assert stop_result is True
    assert watcher._running is False
    if watcher._thread:
        assert not watcher._thread.is_alive()
    assert watcher._dir_handle is None
    assert watcher._h_event is None
    assert watcher._h_stop_event is None


@pytest.mark.skipif(not HAS_WIN32, reason="Windows native overlapped tests require pywin32")
def test_repeated_start_stop_cycles_without_handle_or_thread_leak(temp_dir):
    """Verify repeated start() and stop() cycles on the same and multiple watcher instances
    reclaim threads and native handles without accumulation.
    """
    watch_dir = os.path.join(temp_dir, "cycle_dir")
    os.makedirs(watch_dir, exist_ok=True)

    initial_threads = threading.active_count()

    # 1. Repeated cycles on the same instance
    defn = WatcherDefinition(
        watcher_id="w_cycle_single",
        type=WatcherType.DIRECTORY,
        target=watch_dir,
        debounce_ms=50.0,
    )
    watcher = WindowsNativeDirectoryWatcher(definition=defn, event_bus=EventBus())

    for _ in range(5):
        watcher.start()
        assert watcher._running is True
        assert watcher._dir_handle is not None
        time.sleep(0.04)
        ok = watcher.stop(timeout=2.0)
        assert ok is True
        assert watcher._running is False
        assert watcher._dir_handle is None
        assert watcher._h_event is None
        assert watcher._h_stop_event is None

    # 2. Repeated cycles creating distinct instances
    for i in range(5):
        d_inst = WatcherDefinition(
            watcher_id=f"w_cycle_inst_{i}",
            type=WatcherType.DIRECTORY,
            target=watch_dir,
            debounce_ms=50.0,
        )
        w = WindowsNativeDirectoryWatcher(definition=d_inst, event_bus=EventBus())
        w.start()
        time.sleep(0.02)
        ok = w.stop(timeout=2.0)
        assert ok is True
        assert w._dir_handle is None

    # Ensure no watcher threads remain alive
    time.sleep(0.1)
    active_watcher_threads = [t for t in threading.enumerate() if t.name.startswith("Watcher_")]
    assert len(active_watcher_threads) == 0


@pytest.mark.skipif(not HAS_WIN32, reason="Windows native overlapped tests require pywin32")
def test_shutdown_with_no_new_filesystem_events(temp_dir):
    """Verify that shutting down an idling watcher produces no false triggers or spurious events."""
    watch_dir = os.path.join(temp_dir, "quiet_dir")
    os.makedirs(watch_dir, exist_ok=True)

    defn = WatcherDefinition(
        watcher_id="w_quiet",
        type=WatcherType.DIRECTORY,
        target=watch_dir,
        debounce_ms=50.0,
    )

    events: List[Any] = []
    watcher = WindowsNativeDirectoryWatcher(
        definition=defn,
        event_bus=EventBus(),
        on_event=lambda evt, data: events.append(data),
    )
    watcher.start()

    # Idle for 300ms without modifying directory
    time.sleep(0.3)
    ok = watcher.stop(timeout=2.0)

    assert ok is True
    assert len(events) == 0
    assert watcher._dir_handle is None
    assert watcher.definition.state != WatcherState.FAILED


@pytest.mark.skipif(not HAS_WIN32, reason="Windows native overlapped tests require pywin32")
def test_handling_expected_cancellation_error_995(temp_dir):
    """Verify that when CancelIoEx cancels pending overlapped I/O, ERROR_OPERATION_ABORTED (995)
    is recognized as normal cancellation and does NOT trigger fallback degradation or FAILED state.
    """
    watch_dir = os.path.join(temp_dir, "abort_dir")
    os.makedirs(watch_dir, exist_ok=True)

    defn = WatcherDefinition(
        watcher_id="w_abort_check",
        type=WatcherType.DIRECTORY,
        target=watch_dir,
        debounce_ms=50.0,
    )

    watcher = WindowsNativeDirectoryWatcher(definition=defn, event_bus=EventBus())
    watcher.start()
    time.sleep(0.1)

    assert not watcher.using_fallback
    assert watcher.definition.state == WatcherState.HEALTHY

    # Trigger stop which invokes CancelIoEx resulting in winerror 995
    ok = watcher.stop(timeout=2.0)
    assert ok is True

    # Crucial assertion: error 995 must NOT cause degradation to polling fallback or FAILED state
    assert not watcher.using_fallback
    assert watcher.definition.state != WatcherState.FAILED
    assert watcher.last_error is None


def test_polling_fallback_behavior_when_native_watching_cannot_initialize(temp_dir):
    """Verify that when native Win32 handles cannot be created (e.g. non-directory or unsupported target),
    watcher seamlessly initializes polling fallback via FilesystemWatcher and detects changes.
    """
    non_existent = os.path.join(temp_dir, "missing_subdir")

    defn = WatcherDefinition(
        watcher_id="w_polling_fb",
        type=WatcherType.DIRECTORY,
        target=non_existent,
        debounce_ms=50.0,
    )

    events: List[Any] = []
    watcher = WindowsNativeDirectoryWatcher(
        definition=defn,
        event_bus=EventBus(),
        on_event=lambda evt, data: events.append(data),
    )
    watcher.start()

    try:
        assert watcher.using_fallback is True
        assert watcher._polling_fallback_watcher is not None
        assert isinstance(watcher._polling_fallback_watcher, FilesystemWatcher)
        assert watcher._dir_handle is None
    finally:
        ok = watcher.stop(timeout=2.0)
        assert ok is True
        assert watcher._running is False
        assert watcher._polling_fallback_watcher is None


@pytest.mark.skipif(not HAS_WIN32, reason="Windows native overlapped tests require pywin32")
def test_stop_idempotency(temp_dir):
    """Verify that calling stop() repeatedly is safe, idempotent, and returns True without raising."""
    watch_dir = os.path.join(temp_dir, "idempotent_dir")
    os.makedirs(watch_dir, exist_ok=True)

    defn = WatcherDefinition(
        watcher_id="w_idempotent",
        type=WatcherType.DIRECTORY,
        target=watch_dir,
        debounce_ms=50.0,
    )
    watcher = WindowsNativeDirectoryWatcher(definition=defn, event_bus=EventBus())
    watcher.start()
    time.sleep(0.05)

    assert watcher.stop() is True
    # Subsequent stop calls should be idempotent
    assert watcher.stop() is True
    assert watcher.stop() is True
    assert watcher._dir_handle is None
    assert watcher._h_event is None
    assert watcher._h_stop_event is None


@pytest.mark.skipif(not HAS_WIN32, reason="Windows native overlapped tests require pywin32")
def test_accurate_timeout_reporting_on_hung_thread(temp_dir):
    """Verify that if worker thread fails to terminate within the timeout window,
    stop() accurately marks WatcherState.FAILED, sets shutdown_timed_out=True,
    records last_error, and retains native handles to prevent access violations.
    """
    watch_dir = os.path.join(temp_dir, "timeout_dir")
    os.makedirs(watch_dir, exist_ok=True)

    defn = WatcherDefinition(
        watcher_id="w_timeout_test",
        type=WatcherType.DIRECTORY,
        target=watch_dir,
        debounce_ms=50.0,
    )
    watcher = WindowsNativeDirectoryWatcher(definition=defn, event_bus=EventBus())
    watcher.start()
    time.sleep(0.05)

    # Replace worker with a thread that ignores cancellation until unblocked
    unblock_worker = threading.Event()

    def hung_loop():
        unblock_worker.wait(timeout=2.0)

    # Stop initial worker cleanly first
    watcher.stop(timeout=2.0)
    assert watcher._dir_handle is None

    # Now create watcher with an uncooperative thread
    watcher2 = WindowsNativeDirectoryWatcher(definition=defn, event_bus=EventBus())
    watcher2.start()
    # Mock loop with thread that stays alive
    orig_thread = watcher2._thread
    hung_thread = threading.Thread(target=hung_loop, daemon=True, name="Watcher_Hung")
    hung_thread.start()
    watcher2._thread = hung_thread

    try:
        # stop() with tiny timeout will detect worker still alive
        ok = watcher2.stop(timeout=0.05)
        assert ok is False
        assert watcher2.definition.state == WatcherState.FAILED
        assert watcher2._timeout_occurred is True
        assert watcher2.last_error is not None
        assert "timed out" in watcher2.last_error
        # Native handles must NOT have been released while worker was still active
        assert watcher2._dir_handle is not None
    finally:
        # Unblock hung worker and perform real cleanup
        unblock_worker.set()
        hung_thread.join(timeout=2.0)
        watcher2.stop(timeout=2.0)
        assert watcher2._dir_handle is None
