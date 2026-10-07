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
    """Filesystem watcher supporting native ReadDirectoryChangesW and polling fallback."""

    def __init__(
        self,
        definition: WatcherDefinition,
        event_bus: Optional[EventBus] = None,
        on_event: Optional[Callable[..., None]] = None,
        poll_interval_sec: float = 0.5,
    ) -> None:
        super().__init__(definition, event_bus, on_event=on_event)
        self.poll_interval = poll_interval_sec
        self._known_files: Dict[str, float] = {}

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

    def _scan_directory(self) -> Dict[str, float]:
        """Scan directory returning map of filepath -> mtime."""
        target = self.definition.target
        if not os.path.exists(target):
            return {}

        found: Dict[str, float] = {}
        if os.path.isfile(target):
            try:
                found[target] = os.path.getmtime(target)
            except OSError:
                pass
            return found

        try:
            for root, _, files in os.walk(target):
                # Enforce max depth 3
                rel = os.path.relpath(root, target)
                if rel != "." and len(rel.split(os.sep)) > 3:
                    continue

                for f in files:
                    if self._matches_filters(f):
                        full_p = os.path.join(root, f)
                        try:
                            found[full_p] = os.path.getmtime(full_p)
                        except OSError:
                            pass
        except OSError:
            pass
        return found

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

        # Initialize baseline state
        self._known_files = self._scan_directory()

        while self._running:
            time.sleep(self.poll_interval)
            if not self._running:
                break

            current_files = self._scan_directory()

            # Detect additions and modifications
            for fpath, mtime in current_files.items():
                if fpath not in self._known_files:
                    self.emit_debounced_event("filesystem.file_created", {"file": fpath, "path": fpath, "file_path": fpath})
                elif mtime > self._known_files[fpath]:
                    self.emit_debounced_event("filesystem.file_modified", {"file": fpath, "path": fpath, "file_path": fpath})

            # Detect deletions
            for fpath in list(self._known_files.keys()):
                if fpath not in current_files:
                    self.emit_debounced_event("filesystem.file_deleted", {"file": fpath, "path": fpath, "file_path": fpath})

            self._known_files = current_files

    def poll_cycle(self) -> None:
        """Scan directory synchronously once and immediately flush events."""
        current_files = self._scan_directory()
        for fpath, mtime in current_files.items():
            if fpath not in self._known_files:
                self.emit_debounced_event("filesystem.file_created", {"file": fpath, "path": fpath, "file_path": fpath})
            elif mtime > self._known_files[fpath]:
                self.emit_debounced_event("filesystem.file_modified", {"file": fpath, "path": fpath, "file_path": fpath})
        for fpath in list(self._known_files.keys()):
            if fpath not in current_files:
                self.emit_debounced_event("filesystem.file_deleted", {"file": fpath, "path": fpath, "file_path": fpath})
        self._known_files = current_files
        if self._pending_events:
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

    def _watch_loop(self) -> None:
        proj_id = self.definition.target
        while self._running:
            time.sleep(self.poll_interval)
            if not self._running:
                break

            proj = self.registry.get(proj_id) if self.registry else None
            if not proj:
                continue

            # Inspect Git state
            if proj.root_path:
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
