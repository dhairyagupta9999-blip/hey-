"""Environmental Watchers and Event Generation for Hey Jev.

Provides:
- BaseWatcher: Observable watcher lifecycle with debouncing and bounded restart health
- FilesystemWatcher: Native Windows ReadDirectoryChangesW / Polling directory observer
- ProjectWatcher: Active project state, test execution, and Git monitoring
- GitWatcher: Safe read-only repository status watcher
- ProcessWatcher: Application and PID lifecycle watcher via psutil
- BrowserWatcher: Browser state and URL transition watcher
- MissionWatcher: Agent mission lifecycle observer
"""
from __future__ import annotations

import fnmatch
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set

from event_bus import EventBus
from git_inspector import GitInspector
from project_health import ProjectHealthAssessor, ProjectHealthSnapshot
from project_model import Project
from project_registry import ProjectRegistry
from watcher_model import WatcherDefinition, WatcherState, WatcherType

try:
    import win32file
    import win32con
    import win32event
    import pywintypes
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False



class BaseWatcher:
    """Base watcher with self-health tracking, debouncing, and bounded restart."""

    def __init__(
        self,
        definition: WatcherDefinition,
        event_bus: Optional[EventBus] = None,
        on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.definition = definition
        self.event_bus = event_bus or EventBus()
        self.on_event = on_event

        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._debounce_timer: Optional[threading.Timer] = None
        self._pending_events: List[Dict[str, Any]] = []

    def start(self) -> None:
        with self._lock:
            if self._running or not self.definition.enabled:
                return
            self._running = True
            self.definition.state = WatcherState.HEALTHY
            self._thread = threading.Thread(target=self._watch_loop, daemon=True, name=f"Watcher_{self.definition.watcher_id}")
            self._thread.start()

        self.event_bus.emit(
            event_type="watcher.started",
            task_id=self.definition.watcher_id,
            agent_name=self.__class__.__name__,
            data={"type": self.definition.type.value, "target": self.definition.target},
        )

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            if self._pending_events:
                self._flush_debounced_events()
            if self._debounce_timer:
                self._debounce_timer.cancel()
                self._debounce_timer = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

        self.event_bus.emit(
            event_type="watcher.stopped",
            task_id=self.definition.watcher_id,
            agent_name=self.__class__.__name__,
            data={"type": self.definition.type.value},
        )

    def restart(self) -> bool:
        """Attempt bounded restart if watcher enters DEGRADED or FAILED state."""
        with self._lock:
            if self.definition.restart_count >= self.definition.max_restarts:
                self.definition.state = WatcherState.FAILED
                self.event_bus.emit(
                    "watcher.failed",
                    self.definition.watcher_id,
                    data={"reason": "Max restarts exceeded"},
                )
                return False

            self.definition.restart_count += 1
            self.definition.state = WatcherState.DEGRADED

        self.stop()
        time.sleep(0.5)
        self.start()

        self.event_bus.emit(
            "watcher.restarted",
            self.definition.watcher_id,
            data={"restart_count": self.definition.restart_count},
        )
        return True

    def emit_debounced_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Debounce burst events (e.g. rapid filesystem writes) into a single event."""
        with self._lock:
            self.definition.record_event()
            self._pending_events.append({"event_type": event_type, "data": data})

            if self._debounce_timer:
                self._debounce_timer.cancel()

            delay = max(0.05, self.definition.debounce_ms / 1000.0)
            self._debounce_timer = threading.Timer(delay, self._flush_debounced_events)
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _flush_debounced_events(self) -> None:
        with self._lock:
            if not self._pending_events:
                return

            events_to_flush = list(self._pending_events)
            self._pending_events.clear()
            self._debounce_timer = None

            if not self.definition.can_trigger():
                return

            self.definition.record_trigger()

        # Combine payload
        first_event = events_to_flush[0]
        payload = dict(first_event["data"])
        payload["burst_count"] = len(events_to_flush)
        payload["all_events"] = [e["event_type"] for e in events_to_flush]
        payload["watcher_id"] = self.definition.watcher_id
        payload["target"] = self.definition.target

        evt_type = first_event["event_type"]
        self.event_bus.emit(
            event_type=evt_type,
            task_id=self.definition.watcher_id,
            agent_name=self.__class__.__name__,
            data=payload,
        )

        if self.on_event:
            try:
                import inspect
                sig = inspect.signature(self.on_event)
                if len(sig.parameters) == 1:
                    payload["event_name"] = evt_type
                    self.on_event(payload)
                else:
                    self.on_event(evt_type, payload)
            except Exception:
                try:
                    payload["event_name"] = evt_type
                    self.on_event(payload)
                except Exception:
                    pass

    def _watch_loop(self) -> None:
        """Subclass override for specific watcher behavior."""
        pass

    def get_health(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "watcher_id": self.definition.watcher_id,
                "type": self.definition.type.value,
                "target": self.definition.target,
                "state": self.definition.state.value,
                "running": self._running,
                "restart_count": self.definition.restart_count,
                "last_event_at": self.definition.last_event_at,
                "last_trigger_at": self.definition.last_trigger_at,
            }


class FilesystemWatcher(BaseWatcher):
    """Filesystem watcher supporting settling checks, stability verification, and polling fallback."""

    def __init__(
        self,
        definition: WatcherDefinition,
        event_bus: Optional[EventBus] = None,
        on_event: Optional[Callable[..., None]] = None,
        poll_interval_sec: float = 0.5,
        settle_window_sec: Optional[float] = None,
    ) -> None:
        super().__init__(definition, event_bus, on_event=on_event)
        self.poll_interval = poll_interval_sec
        if settle_window_sec is not None:
            self.settle_window_sec = float(settle_window_sec)
        else:
            self.settle_window_sec = float(self.definition.metadata.get("settle_window_sec", 0.0))
        self.max_settle_timeout_sec = float(self.definition.metadata.get("max_settle_timeout_sec", 5.0))
        self._known_files: Dict[str, float] = {}
        self._known_sizes: Dict[str, int] = {}
        self._pending_settle: Dict[str, Dict[str, Any]] = {}

    def start(self) -> None:
        if not self._is_safe_target(self.definition.target):
            self.definition.state = WatcherState.FAILED
            raise ValueError(f"Safety check failed: Target '{self.definition.target}' rejected by safety policy.")
        super().start()

    def _is_safe_target(self, target: str) -> bool:
        """Reject dangerous system roots and entire drive roots."""
        if not target or not target.strip():
            return False
        norm = os.path.abspath(target)
        drive, tail = os.path.splitdrive(norm)
        if tail in ("\\", "/", ""):
            return False
        forbidden = {
            os.path.abspath(os.environ.get("SystemRoot", "C:\\Windows")),
            os.path.abspath(os.environ.get("ProgramFiles", "C:\\Program Files")),
        }
        return norm not in forbidden

    def _matches_filters(self, filename: str) -> bool:
        """Check if filename matches configured pattern filters."""
        if not self.definition.filters:
            return True
        return any(fnmatch.fnmatch(filename, pat) for pat in self.definition.filters)

    def _scan_directory_detailed(self) -> Dict[str, Tuple[float, int]]:
        """Scan directory returning map of filepath -> (mtime, size)."""
        target = self.definition.target
        if not os.path.exists(target):
            return {}

        found: Dict[str, Tuple[float, int]] = {}
        if os.path.isfile(target):
            try:
                st = os.stat(target)
                found[target] = (st.st_mtime, st.st_size)
            except OSError:
                pass
            return found

        try:
            for root, _, files in os.walk(target):
                rel = os.path.relpath(root, target)
                if rel != "." and len(rel.split(os.sep)) > 3:
                    continue

                for f in files:
                    if self._matches_filters(f):
                        full_p = os.path.join(root, f)
                        try:
                            st = os.stat(full_p)
                            found[full_p] = (st.st_mtime, st.st_size)
                        except OSError:
                            pass
        except OSError:
            pass
        return found

    def _scan_directory(self) -> Dict[str, float]:
        """Scan directory returning map of filepath -> mtime."""
        detailed = self._scan_directory_detailed()
        for p, (mt, sz) in detailed.items():
            self._known_sizes[p] = sz
        return {p: mt for p, (mt, _) in detailed.items()}

    def _check_settled_events(self, now: float, force: bool = False) -> None:
        """Flush events that have settled or reached timeout."""
        with self._lock:
            for fpath, info in list(self._pending_settle.items()):
                is_stable = (now - info["stable_since"]) >= self.settle_window_sec
                is_timeout = (now - info["first_seen"]) >= self.max_settle_timeout_sec
                if force or is_stable or is_timeout:
                    self.emit_debounced_event(
                        info["event_type"],
                        {
                            "file": fpath,
                            "path": fpath,
                            "file_path": fpath,
                            "settled": True,
                            "settle_duration": round(now - info["first_seen"], 3),
                            "size": info["last_size"],
                        },
                    )
                    self._pending_settle.pop(fpath, None)

    def _watch_loop(self) -> None:
        """Run directory monitoring with debounced event dispatch."""
        target = self.definition.target
        if not self._is_safe_target(target):
            self.definition.state = WatcherState.FAILED
            self.event_bus.emit(
                "watcher.failed",
                self.definition.watcher_id,
                data={"error": f"Target '{target}' rejected by safety policy."},
            )
            return

        self._known_files = self._scan_directory()

        while self._running:
            time.sleep(self.poll_interval)
            if not self._running:
                break
            self.poll_cycle(force_flush=False, force_settle=False)

    def poll_cycle(self, force_flush: bool = True, force_settle: bool = False) -> None:
        """Scan directory synchronously once and process file changes with settling."""
        now = time.time()
        current_detailed = self._scan_directory_detailed()
        current_files = {p: mt for p, (mt, _) in current_detailed.items()}

        for fpath, (mtime, size) in current_detailed.items():
            is_new = fpath not in self._known_files
            is_mod = not is_new and (mtime > self._known_files[fpath] or size != self._known_sizes.get(fpath, size))

            if is_new or is_mod:
                evt_type = "filesystem.file_created" if is_new else "filesystem.file_modified"
                if self.settle_window_sec > 0.0 and not force_settle:
                    if fpath not in self._pending_settle:
                        self._pending_settle[fpath] = {
                            "event_type": evt_type,
                            "first_seen": now,
                            "last_mtime": mtime,
                            "last_size": size,
                            "stable_since": now,
                        }
                    else:
                        info = self._pending_settle[fpath]
                        if info["last_mtime"] != mtime or info["last_size"] != size:
                            info["last_mtime"] = mtime
                            info["last_size"] = size
                            info["stable_since"] = now
                else:
                    self.emit_debounced_event(evt_type, {"file": fpath, "path": fpath, "file_path": fpath, "size": size})

            self._known_sizes[fpath] = size

        if self.settle_window_sec > 0.0:
            self._check_settled_events(now, force=force_settle)

        for fpath in list(self._known_files.keys()):
            if fpath not in current_files:
                self._pending_settle.pop(fpath, None)
                self._known_sizes.pop(fpath, None)
                self.emit_debounced_event("filesystem.file_deleted", {"file": fpath, "path": fpath, "file_path": fpath})

        self._known_files = current_files
        if force_flush and self._pending_events:
            self._flush_debounced_events()


class WindowsNativeDirectoryWatcher(BaseWatcher):
    """OS-native Windows ReadDirectoryChangesW observer with polling fallback."""

    def __init__(
        self,
        definition: WatcherDefinition,
        event_bus: Optional[EventBus] = None,
        on_event: Optional[Callable[..., None]] = None,
        settle_window_sec: Optional[float] = None,
    ) -> None:
        super().__init__(definition, event_bus, on_event=on_event)
        if settle_window_sec is not None:
            self.settle_window_sec = float(settle_window_sec)
        else:
            self.settle_window_sec = float(self.definition.metadata.get("settle_window_sec", 0.0))
        self.max_settle_timeout_sec = float(self.definition.metadata.get("max_settle_timeout_sec", 5.0))
        self.using_fallback: bool = False
        self._dir_handle: Any = None
        self._h_event: Any = None
        self._pending_settle: Dict[str, Dict[str, Any]] = {}
        self._polling_fallback_watcher: Optional[FilesystemWatcher] = None

    def start(self) -> None:
        target = self.definition.target
        if not self._is_safe_target(target):
            self.definition.state = WatcherState.FAILED
            raise ValueError(f"Safety check failed: Target '{target}' rejected by safety policy.")

        if not HAS_WIN32 or not os.path.isdir(target):
            self.using_fallback = True
            self._fallback_init()
            super().start()
            return

        try:
            self._dir_handle = win32file.CreateFile(
                target,
                win32con.GENERIC_READ,
                win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
                None,
                win32con.OPEN_EXISTING,
                win32con.FILE_FLAG_BACKUP_SEMANTICS | win32con.FILE_FLAG_OVERLAPPED,
                None,
            )
            self._h_event = win32event.CreateEvent(None, True, False, None)
            self.using_fallback = False
        except Exception:
            self.using_fallback = True
            self._fallback_init()

        super().start()

    def _fallback_init(self) -> None:
        self._polling_fallback_watcher = FilesystemWatcher(
            definition=self.definition,
            event_bus=self.event_bus,
            on_event=self.on_event,
            settle_window_sec=self.settle_window_sec,
        )

    def _is_safe_target(self, target: str) -> bool:
        if not target or not target.strip():
            return False
        norm = os.path.abspath(target)
        drive, tail = os.path.splitdrive(norm)
        if tail in ("\\", "/", ""):
            return False
        forbidden = {
            os.path.abspath(os.environ.get("SystemRoot", "C:\\Windows")),
            os.path.abspath(os.environ.get("ProgramFiles", "C:\\Program Files")),
        }
        return norm not in forbidden

    def _matches_filters(self, filename: str) -> bool:
        if not self.definition.filters:
            return True
        return any(fnmatch.fnmatch(filename, pat) for pat in self.definition.filters)

    def _check_settled_events(self, now: float, force: bool = False) -> None:
        with self._lock:
            for fpath, info in list(self._pending_settle.items()):
                try:
                    st = os.stat(fpath)
                    curr_size = st.st_size
                    curr_mtime = st.st_mtime
                    if curr_size != info["last_size"] or curr_mtime != info["last_mtime"]:
                        info["last_size"] = curr_size
                        info["last_mtime"] = curr_mtime
                        info["stable_since"] = now
                except OSError:
                    pass

                is_stable = (now - info["stable_since"]) >= self.settle_window_sec
                is_timeout = (now - info["first_seen"]) >= self.max_settle_timeout_sec
                if force or is_stable or is_timeout:
                    self.emit_debounced_event(
                        info["event_type"],
                        {
                            "file": fpath,
                            "path": fpath,
                            "file_path": fpath,
                            "native": True,
                            "settled": True,
                            "settle_duration": round(now - info["first_seen"], 3),
                            "size": info["last_size"],
                        },
                    )
                    self._pending_settle.pop(fpath, None)

    def _watch_loop(self) -> None:
        if self.using_fallback:
            if self._polling_fallback_watcher:
                self._polling_fallback_watcher._watch_loop()
            return

        target = self.definition.target
        buffer = win32file.AllocateReadBuffer(8192)
        overlapped = pywintypes.OVERLAPPED()
        overlapped.hEvent = self._h_event

        while self._running:
            try:
                win32event.ResetEvent(self._h_event)
                win32file.ReadDirectoryChangesW(
                    self._dir_handle,
                    buffer,
                    True,
                    win32con.FILE_NOTIFY_CHANGE_FILE_NAME |
                    win32con.FILE_NOTIFY_CHANGE_DIR_NAME |
                    win32con.FILE_NOTIFY_CHANGE_LAST_WRITE |
                    win32con.FILE_NOTIFY_CHANGE_SIZE,
                    overlapped,
                )

                wait_res = win32event.WaitForSingleObject(self._h_event, 200)
                if not self._running:
                    break
                now = time.time()

                if wait_res == win32event.WAIT_OBJECT_0:
                    nbytes = win32file.GetOverlappedResult(self._dir_handle, overlapped, False)
                    notifications = win32file.FILE_NOTIFY_INFORMATION(buffer, nbytes)
                    for action, rel_path in notifications:
                        if not self._matches_filters(rel_path):
                            continue
                        full_p = os.path.join(target, rel_path)

                        if action in (1, 3):
                            evt_type = "filesystem.file_created" if action == 1 else "filesystem.file_modified"
                            size = 0
                            mtime = now
                            try:
                                st = os.stat(full_p)
                                size = st.st_size
                                mtime = st.st_mtime
                            except OSError:
                                pass

                            if self.settle_window_sec > 0.0:
                                if full_p not in self._pending_settle:
                                    self._pending_settle[full_p] = {
                                        "event_type": evt_type,
                                        "first_seen": now,
                                        "last_mtime": mtime,
                                        "last_size": size,
                                        "stable_since": now,
                                    }
                                else:
                                    info = self._pending_settle[full_p]
                                    if info["last_mtime"] != mtime or info["last_size"] != size:
                                        info["last_mtime"] = mtime
                                        info["last_size"] = size
                                        info["stable_since"] = now
                            else:
                                self.emit_debounced_event(evt_type, {"file": full_p, "path": full_p, "file_path": full_p, "native": True, "size": size})
                        elif action == 2:
                            self._pending_settle.pop(full_p, None)
                            self.emit_debounced_event("filesystem.file_deleted", {"file": full_p, "path": full_p, "file_path": full_p, "native": True})
                        elif action in (4, 5):
                            self.emit_debounced_event("filesystem.file_renamed", {"file": full_p, "path": full_p, "file_path": full_p, "native": True, "action": action})

                elif wait_res == win32event.WAIT_TIMEOUT:
                    pass

                if self.settle_window_sec > 0.0:
                    self._check_settled_events(now)

            except Exception:
                if not self._running:
                    break
                self.using_fallback = True
                self._fallback_init()
                if self._polling_fallback_watcher:
                    self._polling_fallback_watcher._watch_loop()
                break

    def stop(self) -> None:
        with self._lock:
            self._running = False
            if self._dir_handle and self._dir_handle != getattr(win32file, "INVALID_HANDLE_VALUE", None):
                try:
                    win32file.CancelIo(self._dir_handle)
                except Exception:
                    pass
            if self._h_event:
                try:
                    win32event.SetEvent(self._h_event)
                except Exception:
                    pass

        # Join the loop thread before closing handles
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

        with self._lock:
            if self._dir_handle and self._dir_handle != getattr(win32file, "INVALID_HANDLE_VALUE", None):
                try:
                    win32file.CloseHandle(self._dir_handle)
                except Exception:
                    pass
                self._dir_handle = None
            if self._h_event:
                try:
                    win32file.CloseHandle(self._h_event)
                except Exception:
                    pass
                self._h_event = None

        if self._polling_fallback_watcher:
            self._polling_fallback_watcher.stop()
        super().stop()

    def poll_cycle(self, force_flush: bool = True, force_settle: bool = False) -> None:
        """Poll cycle for testing or manual stepping."""
        if self.using_fallback and self._polling_fallback_watcher:
            self._polling_fallback_watcher.poll_cycle(force_flush=force_flush, force_settle=force_settle)
        else:
            if self.settle_window_sec > 0.0:
                self._check_settled_events(time.time(), force=force_settle)
            if force_flush and self._pending_events:
                self._flush_debounced_events()


class ProjectWatcher(BaseWatcher):
    """Monitors active project state, Git cleanliness, test results, and health."""

    def __init__(
        self,
        definition: WatcherDefinition,
        project_registry: Optional[ProjectRegistry] = None,
        git_inspector: Optional[GitInspector] = None,
        health_assessor: Optional[ProjectHealthAssessor] = None,
        event_bus: Optional[EventBus] = None,
        on_event: Optional[Callable[..., None]] = None,
        poll_interval_sec: float = 2.0,
    ) -> None:
        super().__init__(definition, event_bus, on_event=on_event)
        self.registry = project_registry
        self.git_inspector = git_inspector or GitInspector()
        self.health_assessor = health_assessor or ProjectHealthAssessor(git_inspector=self.git_inspector)
        self.poll_interval = poll_interval_sec
        self._last_commit = ""
        self._last_test_result = "UNKNOWN"
        self._last_clean = True
        self._last_health: Optional[ProjectHealthSnapshot] = None
        self._recent_failures: List[str] = []
        self._active_processes: List[Dict[str, Any]] = []

    def record_test_result(self, result: str, failure_details: Optional[List[str]] = None) -> None:
        """Record test result from test runner and immediately evaluate health."""
        with self._lock:
            self._last_test_result = str(result).upper()
            if failure_details:
                self._recent_failures.extend(failure_details)
            elif self._last_test_result == "PASSED":
                self._recent_failures.clear()
        self.poll_cycle()

    def _resolve_project(self) -> Optional[Project]:
        proj_id = self.definition.target
        proj = self.registry.get(proj_id) if self.registry else None
        if not proj and os.path.exists(proj_id):
            proj = Project(project_id="local_project", name="Local", root_path=proj_id)
        return proj

    def poll_cycle(self) -> None:
        """Synchronously check Git status and assess project health."""
        proj = self._resolve_project()
        if not proj:
            return

        proj_id = proj.project_id

        # 1. Git State Inspection
        if proj.root_path and os.path.exists(proj.root_path):
            git_data = self.git_inspector.inspect(proj.root_path)
            curr_commit = git_data.get("commit", "")
            curr_clean = git_data.get("clean", True)

            if self._last_commit and curr_commit != self._last_commit:
                self.emit_debounced_event("project.commit", {
                    "project_id": proj_id,
                    "commit": curr_commit,
                    "message": git_data.get("commit_message", ""),
                })

            if curr_clean != self._last_clean:
                self.emit_debounced_event("project.working_tree_changed", {
                    "project_id": proj_id,
                    "clean": curr_clean,
                    "modified_count": git_data.get("modified_count", 0),
                })

            self._last_commit = curr_commit
            self._last_clean = curr_clean

        # 2. Project Health Assessment
        snapshot = self.health_assessor.assess(
            project=proj,
            last_test_result=self._last_test_result,
            recent_failures=list(self._recent_failures),
            active_processes=list(self._active_processes),
        )

        if self._last_health is not None:
            was_working = self._last_health.is_working
            is_now_working = snapshot.is_working
            test_regressed = (self._last_health.last_test_result != "FAILED" and snapshot.last_test_result == "FAILED")
            build_regressed = (self._last_health.known_build_status == "HEALTHY" and snapshot.known_build_status == "BROKEN")
            new_failures = len(snapshot.recent_failures) > len(self._last_health.recent_failures)

            if (was_working and not is_now_working) or test_regressed or build_regressed or new_failures:
                # Deterministic non-LLM triage
                if snapshot.last_test_result == "FAILED":
                    reg_reason = "TEST_FAILURE"
                elif snapshot.known_build_status == "BROKEN":
                    reg_reason = "BUILD_BROKEN"
                elif not snapshot.working_tree_clean:
                    reg_reason = "UNCOMMITTED_CHANGES"
                elif not snapshot.repository_healthy:
                    reg_reason = "REPOSITORY_CORRUPT"
                else:
                    reg_reason = "HEALTH_REGRESSION"

                self.emit_debounced_event("project.health_regressed", {
                    "project_id": proj_id,
                    "regression_type": reg_reason,
                    "is_working": snapshot.is_working,
                    "last_test_result": snapshot.last_test_result,
                    "known_build_status": snapshot.known_build_status,
                    "warnings": list(snapshot.warnings),
                    "recent_failures": list(snapshot.recent_failures),
                    "snapshot": snapshot.to_dict(),
                })
            elif not was_working and is_now_working:
                self.emit_debounced_event("project.health_recovered", {
                    "project_id": proj_id,
                    "is_working": True,
                    "last_test_result": snapshot.last_test_result,
                    "known_build_status": snapshot.known_build_status,
                    "snapshot": snapshot.to_dict(),
                })

        self._last_health = snapshot
        if self._pending_events:
            self._flush_debounced_events()

    def _watch_loop(self) -> None:
        while self._running:
            time.sleep(self.poll_interval)
            if not self._running:
                break
            self.poll_cycle()



class GitWatcher(BaseWatcher):
    """Read-only Git repository observer."""

    def __init__(
        self,
        definition: WatcherDefinition,
        git_inspector: Optional[GitInspector] = None,
        event_bus: Optional[EventBus] = None,
        on_event: Optional[Callable[..., None]] = None,
        poll_interval_sec: float = 2.0,
    ) -> None:
        super().__init__(definition, event_bus, on_event=on_event)
        self.git_inspector = git_inspector or GitInspector()
        self.poll_interval = poll_interval_sec
        self._last_commit = ""
        self._last_branch = ""

    def _watch_loop(self) -> None:
        repo_path = self.definition.target
        while self._running:
            time.sleep(self.poll_interval)
            if not self._running:
                break

            info = self.git_inspector.inspect(repo_path)
            if not info.get("is_git"):
                continue

            curr_commit = info.get("commit", "")
            curr_branch = info.get("branch", "")

            if self._last_commit and curr_commit != self._last_commit:
                self.emit_debounced_event("git.commit", {
                    "repo_path": repo_path,
                    "commit": curr_commit,
                    "branch": curr_branch,
                    "message": info.get("commit_message", ""),
                })

            if self._last_branch and curr_branch != self._last_branch:
                self.emit_debounced_event("git.branch_changed", {
                    "repo_path": repo_path,
                    "old_branch": self._last_branch,
                    "new_branch": curr_branch,
                })

            self._last_commit = curr_commit
            self._last_branch = curr_branch


class ProcessWatcher(BaseWatcher):
    """Monitors specific target application or PID lifecycle using psutil."""

    def __init__(
        self,
        definition: WatcherDefinition,
        event_bus: Optional[EventBus] = None,
        on_event: Optional[Callable[..., None]] = None,
        poll_interval_sec: float = 1.0,
        target_pid: Optional[int] = None,
    ) -> None:
        super().__init__(definition, event_bus, on_event=on_event)
        self.poll_interval = poll_interval_sec
        if target_pid is not None:
            self.definition.target = str(target_pid)
        self._target_running = False

    def _is_process_active(self, target: str) -> Tuple[bool, Optional[int]]:
        """Check if target process name or PID is currently running."""
        try:
            import psutil
            if str(target).isdigit():
                pid = int(target)
                return psutil.pid_exists(pid), pid

            target_lower = str(target).lower()
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    name = proc.info.get("name", "")
                    if name and name.lower() == target_lower:
                        return True, proc.info.get("pid")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass
        return False, None

    def poll_cycle(self) -> None:
        """Poll process status synchronously once."""
        target = self.definition.target
        active, pid = self._is_process_active(target)
        if active and not self._target_running:
            self.emit_debounced_event("process.started", {"target": target, "pid": pid})
        elif not active and self._target_running:
            self.emit_debounced_event("process.exited", {"target": target})
        self._target_running = active
        if self._pending_events:
            self._flush_debounced_events()

    def _watch_loop(self) -> None:
        target = self.definition.target
        active, pid = self._is_process_active(target)
        self._target_running = active

        while self._running:
            time.sleep(self.poll_interval)
            if not self._running:
                break
            self.poll_cycle()


class BrowserWatcher(BaseWatcher):
    """Observes browser state changes (URL, title, page loading status)."""

    def __init__(
        self,
        definition: WatcherDefinition,
        event_bus: Optional[EventBus] = None,
        on_event: Optional[Callable[..., None]] = None,
        poll_interval_sec: float = 2.0,
        url_checker: Optional[Callable[[str], str]] = None,
    ) -> None:
        super().__init__(definition, event_bus, on_event=on_event)
        self.poll_interval = poll_interval_sec
        self.url_checker = url_checker
        self._last_url = ""
        self._last_title = ""
        self._last_body_hash = ""

    def update_state(self, url: str, title: str) -> None:
        """Feed live browser observation into watcher."""
        if url != self._last_url:
            self.emit_debounced_event("browser.url_changed", {"old_url": self._last_url, "new_url": url, "title": title})
        self._last_url = url
        self._last_title = title

    def check_url(self, url: str) -> None:
        """Check URL content using url_checker and emit event on change."""
        if self.url_checker:
            content = self.url_checker(url)
            import hashlib
            content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
            if self._last_body_hash and content_hash != self._last_body_hash:
                self.emit_debounced_event("browser.content_changed", {
                    "url": url,
                    "content_hash": content_hash,
                    "content": content,
                })
            self._last_body_hash = content_hash
            if self._pending_events:
                self._flush_debounced_events()


class MissionWatcher(BaseWatcher):
    """Listens for mission lifecycle events on EventBus."""

    def __init__(
        self,
        definition: WatcherDefinition,
        event_bus: Optional[EventBus] = None,
        on_event: Optional[Callable[..., None]] = None,
        mission_tracker: Optional[Any] = None,
    ) -> None:
        super().__init__(definition, event_bus, on_event=on_event)
        self.mission_tracker = mission_tracker
        self._subscribed = False

    def start(self) -> None:
        super().start()
        with self._lock:
            if not self._subscribed:
                self.event_bus.subscribe("mission.*", self._handle_mission_event)
                self._subscribed = True

    def stop(self) -> None:
        with self._lock:
            if self._subscribed:
                self.event_bus.unsubscribe("mission.*", self._handle_mission_event)
                self._subscribed = False
        super().stop()

    def report_mission_status(self, mission_id: str, status: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Explicitly notify watcher of a mission status change."""
        evt_name = f"mission.{status.lower()}"
        data = dict(details or {})
        data["mission_id"] = mission_id
        data["status"] = status
        self.emit_debounced_event(evt_name, data)
        if self._pending_events:
            self._flush_debounced_events()

    def _handle_mission_event(self, event: Any) -> None:
        # Match target mission_id if specified
        target_mid = self.definition.target
        if target_mid and target_mid != "*" and event.task_id != target_mid:
            return

        self.emit_debounced_event(f"watched.{event.event_type}", {
            "mission_id": event.task_id,
            "original_event": event.event_type,
            "data": event.data,
        })
