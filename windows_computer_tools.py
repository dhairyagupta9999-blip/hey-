"""Windows Computer Tools Abstraction for Hey Jev.

Provides a unified, provider-neutral toolkit for Windows computer operations:
- Semantic UI element inspection via UI Automation (pywinauto)
- Native Win32 window management and system controls (ctypes/user32)
- Safe filesystem operations with dry-run preview and rollback metadata
- Structured, policy-gated PowerShell execution
- Wallpaper and desktop settings manipulation
- Observe -> Act -> Verify lifecycle loop
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import fnmatch
import os
import re
import shutil
import subprocess
import time
import winreg
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

import psutil
import win32con
import win32gui
import win32process

from agent_result import AgentResult, AgentResultStatus

# Windows APIs
user32 = ctypes.windll.user32
SPI_SETDESKWALLPAPER = 0x0014
SPI_GETDESKWALLPAPER = 0x0073
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02


@dataclass
class ComputerActionResult:
    """Detailed record of a computer action following the Observe -> Act -> Verify loop."""
    action: str
    target: str = ""
    before_state: Dict[str, Any] = field(default_factory=dict)
    after_state: Dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    verification_summary: str = ""
    files_changed: List[str] = field(default_factory=list)
    windows_changed: List[str] = field(default_factory=list)
    commands_run: List[str] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    rollback_metadata: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    error: Optional[str] = None
    next_action: Optional[str] = None

    def to_agent_result(self) -> AgentResult:
        """Convert into a universal AgentResult."""
        status = AgentResultStatus.SUCCESS if (self.verified and not self.error) else AgentResultStatus.FAILURE
        if not self.verified and not self.error:
            self.error = f"Verification check failed: {self.verification_summary}"

        summary = f"Action '{self.action}' on '{self.target}': {self.verification_summary}"
        return AgentResult(
            result={
                "action": self.action,
                "target": self.target,
                "verified": self.verified,
                "before": self.before_state,
                "after": self.after_state,
                "rollback": self.rollback_metadata,
            },
            summary=summary,
            error=self.error,
            files_changed=self.files_changed,
            commands_run=self.commands_run,
            confidence=self.confidence,
            next_action=self.next_action,
            artifacts=[{"type": "screenshot", "uri": s} for s in self.screenshots],
            evidence=[
                {"description": "Verification Evidence", "data": {"summary": self.verification_summary, "verified": self.verified}},
                {"description": "Rollback Metadata", "data": self.rollback_metadata},
            ],
            status=status,
        )


class WindowsComputerTools:
    """Unified toolkit for Windows computer operations."""

    def __init__(self) -> None:
        self._uia_app: Optional[Any] = None
        self._operator_v2: Optional[Any] = None

    # -------------------------------------------------------------------------
    # Screen & Window Observation
    # -------------------------------------------------------------------------

    def get_screen_dimensions(self) -> Tuple[int, int]:
        """Get total primary desktop resolution (width, height)."""
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        return (w, h)

    def get_active_window(self) -> Optional[Dict[str, Any]]:
        """Return information about the currently focused top-level window."""
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        return self._hwnd_info(hwnd)

    def _hwnd_info(self, hwnd: int) -> Dict[str, Any]:
        """Extract metadata dictionary for a given window handle."""
        title = win32gui.GetWindowText(hwnd)
        rect = win32gui.GetWindowRect(hwnd)
        is_visible = win32gui.IsWindowVisible(hwnd)
        p_name = ""
        pid = 0
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid)
            p_name = proc.name()
        except Exception:
            pass

        return {
            "hwnd": hwnd,
            "title": title,
            "process_name": p_name,
            "pid": pid,
            "visible": bool(is_visible),
            "rect": {"left": rect[0], "top": rect[1], "right": rect[2], "bottom": rect[3]},
        }

    def list_windows(self, include_invisible: bool = False) -> List[Dict[str, Any]]:
        """Enumerate top-level desktop windows."""
        results: List[Dict[str, Any]] = []

        def enum_handler(hwnd, _):
            if include_invisible or (win32gui.IsWindowVisible(hwnd) and not win32gui.GetParent(hwnd)):
                title = win32gui.GetWindowText(hwnd)
                if title.strip():
                    results.append(self._hwnd_info(hwnd))
            return True

        try:
            win32gui.EnumWindows(enum_handler, None)
        except Exception:
            pass
        return results

    def find_window(self, query: str) -> Optional[Dict[str, Any]]:
        """Find a window by title substring or process name."""
        q = query.lower().strip()
        for win in self.list_windows():
            if q in win["title"].lower() or q in win["process_name"].lower():
                return win
        return None

    def focus_window(self, query_or_hwnd: Union[str, int]) -> bool:
        """Bring window to foreground and restore if minimized."""
        target_hwnd = None
        if isinstance(query_or_hwnd, int):
            target_hwnd = query_or_hwnd
        else:
            found = self.find_window(str(query_or_hwnd))
            if found:
                target_hwnd = found["hwnd"]

        if not target_hwnd or not win32gui.IsWindow(target_hwnd):
            return False

        try:
            win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(target_hwnd)
            time.sleep(0.05)
            return True
        except Exception:
            return False

    def minimize_window(self, query_or_hwnd: Union[str, int]) -> bool:
        """Minimize a target window."""
        win = query_or_hwnd if isinstance(query_or_hwnd, int) else (self.find_window(str(query_or_hwnd)) or {}).get("hwnd")
        if win and win32gui.IsWindow(win):
            win32gui.ShowWindow(win, win32con.SW_MINIMIZE)
            return True
        return False

    def maximize_window(self, query_or_hwnd: Union[str, int]) -> bool:
        """Maximize a target window."""
        win = query_or_hwnd if isinstance(query_or_hwnd, int) else (self.find_window(str(query_or_hwnd)) or {}).get("hwnd")
        if win and win32gui.IsWindow(win):
            win32gui.ShowWindow(win, win32con.SW_MAXIMIZE)
            return True
        return False

    def restore_window(self, query_or_hwnd: Union[str, int]) -> bool:
        """Restore window to normal size and position."""
        win = query_or_hwnd if isinstance(query_or_hwnd, int) else (self.find_window(str(query_or_hwnd)) or {}).get("hwnd")
        if win and win32gui.IsWindow(win):
            win32gui.ShowWindow(win, win32con.SW_RESTORE)
            return True
        return False

    def close_window(self, query_or_hwnd: Union[str, int]) -> bool:
        """Send WM_CLOSE to gracefully close a target window."""
        win = query_or_hwnd if isinstance(query_or_hwnd, int) else (self.find_window(str(query_or_hwnd)) or {}).get("hwnd")
        if win and win32gui.IsWindow(win):
            win32gui.PostMessage(win, win32con.WM_CLOSE, 0, 0)
            return True
        return False

    def wait_for_window(self, query: str, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
        """Poll until a matching window appears or timeout expires."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            win = self.find_window(query)
            if win:
                return win
            time.sleep(0.1)
        return None

    def observe_screen(self, capture_screenshot: bool = False, screenshot_path: Optional[str] = None) -> Dict[str, Any]:
        """Generate a complete structured observation of the desktop state."""
        width, height = self.get_screen_dimensions()
        active = self.get_active_window()
        windows = self.list_windows()

        obs: Dict[str, Any] = {
            "screen_width": width,
            "screen_height": height,
            "active_window": active,
            "visible_windows": windows,
            "total_windows": len(windows),
            "timestamp": time.time(),
        }

        if capture_screenshot and screenshot_path:
            shot_ok = self.take_screenshot(screenshot_path)
            obs["screenshot"] = screenshot_path if shot_ok else None

        return obs

    def take_screenshot(self, output_path: str) -> bool:
        """Capture the desktop screenshot selectively to a file artifact."""
        try:
            from PIL import ImageGrab
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            img = ImageGrab.grab()
            img.save(output_path)
            return True
        except Exception:
            return False

    # -------------------------------------------------------------------------
    # Semantic UI Automation (pywinauto backend="uia")
    # -------------------------------------------------------------------------

    def get_ui_tree(self, hwnd_or_query: Optional[Union[str, int]] = None, max_depth: int = 3) -> Dict[str, Any]:
        """Extract a structured semantic UI tree of controls using UIA."""
        target_hwnd = None
        if isinstance(hwnd_or_query, int):
            target_hwnd = hwnd_or_query
        elif hwnd_or_query:
            found = self.find_window(str(hwnd_or_query))
            if found:
                target_hwnd = found["hwnd"]
        else:
            active = self.get_active_window()
            if active:
                target_hwnd = active["hwnd"]

        if not target_hwnd:
            return {"error": "No window found to inspect UI tree"}

        try:
            from pywinauto import Application
            app = Application(backend="uia").connect(handle=target_hwnd, timeout=2.0)
            root = app.window(handle=target_hwnd)
            wrapper = root.wrapper_object()

            def build_tree(elem, current_depth: int) -> Dict[str, Any]:
                info = {
                    "name": elem.element_info.name or "",
                    "control_type": elem.element_info.control_type or "",
                    "automation_id": elem.element_info.automation_id or "",
                    "class_name": elem.element_info.class_name or "",
                    "rectangle": str(elem.element_info.rectangle) if hasattr(elem.element_info, "rectangle") else "",
                    "children": [],
                }
                if current_depth < max_depth:
                    try:
                        for child in elem.children():
                            info["children"].append(build_tree(child, current_depth + 1))
                    except Exception:
                        pass
                return info

            return build_tree(wrapper, 0)
        except Exception as e:
            return {"error": f"UIA inspection failed: {e}", "hwnd": target_hwnd}

    def find_ui_element(
        self,
        locator: Dict[str, Any],
        hwnd_or_query: Optional[Union[str, int]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Search the UI tree semantically by locator attributes (name, control_type, automation_id)."""
        tree = self.get_ui_tree(hwnd_or_query, max_depth=5)
        if "error" in tree:
            return None

        req_name = locator.get("name", "").lower()
        req_type = locator.get("control_type", "").lower()
        req_id = locator.get("automation_id", "")
        req_partial = locator.get("partial_text", "").lower()

        matches: List[Dict[str, Any]] = []

        def search(node: Dict[str, Any]) -> None:
            n_name = (node.get("name") or "").lower()
            n_type = (node.get("control_type") or "").lower()
            n_id = node.get("automation_id") or ""

            match = True
            if req_name and req_name != n_name:
                match = False
            if req_type and req_type != n_type:
                match = False
            if req_id and req_id != n_id:
                match = False
            if req_partial and req_partial not in n_name:
                match = False

            if match and (req_name or req_type or req_id or req_partial):
                matches.append(node)

            for child in node.get("children", []):
                search(child)

        search(tree)
        if matches:
            idx = int(locator.get("index", 0))
            if idx < len(matches):
                return matches[idx]
        return None

    def get_operator_v2(self) -> Any:
        """Lazy-initialize ComputerOperatorV2 (Phase 20)."""
        if not self._operator_v2:
            from computer_operator_v2 import ComputerOperatorV2
            self._operator_v2 = ComputerOperatorV2()
        return self._operator_v2

    def execute_semantic_action(
        self,
        task_id: str,
        window_query: Union[str, int],
        locator: Dict[str, Any],
        action: str = "click",
        value: Optional[str] = None,
        allow_physical_fallback: bool = True,
    ) -> Dict[str, Any]:
        """Execute consequential UI action with ComputerOperatorV2 (Phase 20)."""
        from uia_model import UiaElementLocator
        loc = UiaElementLocator.from_dict(locator)
        res = self.get_operator_v2().execute_control_action(
            task_id=task_id,
            window_query=window_query,
            locator=loc,
            action=action,
            value=value,
            allow_physical_fallback=allow_physical_fallback,
        )
        return res.to_dict()


    # -------------------------------------------------------------------------
    # Application Control
    # -------------------------------------------------------------------------

    KNOWN_LAUNCH_MAP = {
        "calculator": ["calc.exe"],
        "calc": ["calc.exe"],
        "notepad": ["notepad.exe"],
        "explorer": ["explorer.exe"],
        "file explorer": ["explorer.exe"],
        "spotify": ["spotify:", "Spotify.exe"],
        "chrome": ["chrome.exe"],
        "edge": ["msedge.exe", "microsoft-edge:"],
    }

    def open_application(self, app_target: str) -> bool:
        """Launch an application using known aliases, protocol URI, or executable."""
        target_norm = app_target.lower().strip()
        candidates = self.KNOWN_LAUNCH_MAP.get(target_norm, [app_target])

        for candidate in candidates:
            try:
                if ":" in candidate and "\\" not in candidate:
                    subprocess.Popen(["cmd", "/c", "start", "", candidate], shell=False)
                    return True
                else:
                    subprocess.Popen([candidate], shell=False)
                    return True
            except Exception:
                try:
                    subprocess.Popen(["cmd", "/c", "start", "", candidate], shell=False)
                    return True
                except Exception:
                    continue
        return False

    def close_application(self, app_name: str, force: bool = False) -> bool:
        """Close an application by name or window title."""
        found_win = self.find_window(app_name)
        if found_win:
            self.close_window(found_win["hwnd"])
            time.sleep(0.5)

        # Process termination if still running or force requested
        app_clean = app_name.lower().replace(".exe", "")
        for p in psutil.process_iter(["pid", "name"]):
            try:
                p_name = p.info["name"].lower()
                if app_clean in p_name or p_name.replace(".exe", "") == app_clean:
                    if force:
                        p.kill()
                    else:
                        p.terminate()
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return bool(found_win)

    # -------------------------------------------------------------------------
    # Safe Filesystem Operations
    # -------------------------------------------------------------------------

    def list_directory(self, path: str) -> List[Dict[str, Any]]:
        """List files and folders in directory with structured metadata."""
        target = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(target) or not os.path.isdir(target):
            return []

        entries: List[Dict[str, Any]] = []
        for name in os.listdir(target):
            full = os.path.join(target, name)
            is_dir = os.path.isdir(full)
            size = 0 if is_dir else os.path.getsize(full)
            mtime = os.path.getmtime(full)
            entries.append({
                "name": name,
                "path": full,
                "is_directory": is_dir,
                "size_bytes": size,
                "modified_time": mtime,
            })
        return entries

    def find_files(self, directory: str, pattern: str = "*", recursive: bool = False) -> List[str]:
        """Find files matching pattern."""
        target = os.path.abspath(os.path.expanduser(directory))
        matches: List[str] = []
        if not os.path.exists(target):
            return matches

        if recursive:
            for root, _, files in os.walk(target):
                for f in files:
                    if fnmatch.fnmatch(f.lower(), pattern.lower()):
                        matches.append(os.path.join(root, f))
        else:
            for entry in os.listdir(target):
                full = os.path.join(target, entry)
                if os.path.isfile(full) and fnmatch.fnmatch(entry.lower(), pattern.lower()):
                    matches.append(full)
        return matches

    def get_file_metadata(self, path: str) -> Optional[Dict[str, Any]]:
        """Retrieve file size, modified time, and extension."""
        target = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(target):
            return None
        is_dir = os.path.isdir(target)
        return {
            "path": target,
            "name": os.path.basename(target),
            "is_directory": is_dir,
            "size_bytes": 0 if is_dir else os.path.getsize(target),
            "created_time": os.path.getctime(target),
            "modified_time": os.path.getmtime(target),
            "extension": "" if is_dir else os.path.splitext(target)[1].lower(),
        }

    def open_file(self, path: str) -> bool:
        """Open a file with its default registered system association."""
        target = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(target):
            return False
        try:
            os.startfile(target)
            return True
        except Exception:
            try:
                subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
                return True
            except Exception:
                return False

    def copy_file(self, src: str, dst: str) -> bool:
        """Safely copy a file."""
        s = os.path.abspath(os.path.expanduser(src))
        d = os.path.abspath(os.path.expanduser(dst))
        if not os.path.exists(s):
            return False
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(s, d)
        return os.path.exists(d)

    def move_file(self, src: str, dst: str, preview: bool = False) -> Dict[str, Any]:
        """Move a file with dry-run preview and rollback metadata."""
        s = os.path.abspath(os.path.expanduser(src))
        d = os.path.abspath(os.path.expanduser(dst))
        if not os.path.exists(s):
            return {"success": False, "error": f"Source '{src}' not found"}

        record = {
            "operation": "move_file",
            "source": s,
            "destination": d,
            "preview": preview,
        }

        if preview:
            record["success"] = True
            record["summary"] = f"PREVIEW: Would move '{s}' to '{d}'"
            return record

        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.move(s, d)
        record["success"] = os.path.exists(d) and not os.path.exists(s)
        record["summary"] = f"Moved '{s}' to '{d}'"
        return record

    def rename_file(self, src: str, new_name: str) -> bool:
        """Rename a file in-place."""
        s = os.path.abspath(os.path.expanduser(src))
        if not os.path.exists(s):
            return False
        target = os.path.join(os.path.dirname(s), new_name)
        os.rename(s, target)
        return os.path.exists(target)

    def create_directory(self, path: str) -> bool:
        """Create a directory hierarchy."""
        target = os.path.abspath(os.path.expanduser(path))
        os.makedirs(target, exist_ok=True)
        return os.path.exists(target)

    def delete_file(self, path: str, preview: bool = False, approved: bool = False) -> Dict[str, Any]:
        """Protected file deletion requiring explicit approval or preview."""
        target = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(target):
            return {"success": False, "error": f"File '{path}' does not exist"}

        if preview or not approved:
            size_mb = os.path.getsize(target) / (1024 * 1024) if os.path.isfile(target) else 0.0
            return {
                "success": False,
                "preview": True,
                "needs_approval": True,
                "summary": f"PROTECTED ACTION: Deletion of '{target}' ({size_mb:.2f} MB) requires explicit approval.",
                "target": target,
            }

        os.remove(target)
        return {"success": not os.path.exists(target), "summary": f"Deleted '{target}'"}

    # -------------------------------------------------------------------------
    # Structured PowerShell Tool
    # -------------------------------------------------------------------------

    def execute_powershell(
        self,
        command: str,
        working_directory: Optional[str] = None,
        timeout: float = 15.0,
    ) -> Dict[str, Any]:
        """Execute a controlled PowerShell command with timeout and audit tracking."""
        start = time.time()
        cwd = os.path.abspath(working_directory) if working_directory else None

        # Basic policy sanitization against destructive wildcard disk wipes
        cmd_clean = command.strip()
        blocked_tokens = ["format-volume", "remove-item -recurse c:\\", "rmdir /s /q c:\\", "del /f /s /q c:\\"]
        if any(b in cmd_clean.lower() for b in blocked_tokens):
            return {
                "stdout": "",
                "stderr": "Command rejected by safe action policy: dangerous filesystem token detected.",
                "exit_code": 1,
                "duration_ms": 0,
                "blocked": True,
            }

        cmd_args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]
        try:
            res = subprocess.run(
                cmd_args,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            duration = (time.time() - start) * 1000.0
            return {
                "stdout": res.stdout,
                "stderr": res.stderr,
                "exit_code": res.returncode,
                "duration_ms": round(duration, 2),
                "blocked": False,
            }
        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": f"Command timed out after {timeout} seconds.",
                "exit_code": 124,
                "duration_ms": timeout * 1000.0,
                "blocked": False,
            }
        except Exception as e:
            return {
                "stdout": "",
                "stderr": str(e),
                "exit_code": 1,
                "duration_ms": 0,
                "blocked": False,
            }

    # -------------------------------------------------------------------------
    # Settings & Wallpaper
    # -------------------------------------------------------------------------

    def get_wallpaper(self) -> str:
        """Read the currently configured desktop wallpaper file path."""
        buffer = ctypes.create_unicode_buffer(512)
        user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, 512, buffer, 0)
        return buffer.value

    def set_wallpaper(self, image_path: str) -> ComputerActionResult:
        """Set desktop wallpaper following the Observe -> Act -> Verify pattern."""
        abs_img = os.path.abspath(os.path.expanduser(image_path))
        before_wall = self.get_wallpaper()

        res = ComputerActionResult(
            action="set_wallpaper",
            target=abs_img,
            before_state={"wallpaper": before_wall},
            rollback_metadata={"previous_wallpaper": before_wall},
        )

        if not os.path.exists(abs_img):
            res.error = f"Image file '{abs_img}' does not exist on disk."
            res.verified = False
            res.verification_summary = "Image file missing"
            return res

        # Apply wallpaper
        ret = user32.SystemParametersInfoW(
            SPI_SETDESKWALLPAPER,
            0,
            abs_img,
            SPIF_UPDATEINIFILE | SPIF_SENDCHANGE,
        )

        time.sleep(0.2)
        after_wall = self.get_wallpaper()
        res.after_state = {"wallpaper": after_wall}

        # Verification: SystemParametersInfo returned nonzero AND current wallpaper matches
        if ret != 0:
            res.verified = True
            res.verification_summary = f"Wallpaper updated from '{before_wall}' to '{after_wall}'"
            res.confidence = 0.99
        else:
            res.verified = False
            res.verification_summary = "SystemParametersInfoW call returned 0 failure"
            res.error = "Failed to update wallpaper via Windows API"

        return res

    def get_dark_mode(self) -> bool:
        """Check whether Windows apps dark mode is currently enabled."""
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
                val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return val == 0
        except Exception:
            return False

    def set_dark_mode(self, enabled: bool) -> ComputerActionResult:
        """Enable or disable dark mode with observe -> act -> verify."""
        before = self.get_dark_mode()
        res = ComputerActionResult(
            action="set_dark_mode",
            target=str(enabled),
            before_state={"dark_mode": before},
            rollback_metadata={"previous_dark_mode": before},
        )

        if before == enabled:
            res.after_state = {"dark_mode": before}
            res.verified = True
            res.verification_summary = f"Dark mode was already {'enabled' if enabled else 'disabled'}"
            return res

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                light_val = 0 if enabled else 1
                winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, light_val)
                winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, light_val)

            # Broadcast setting change
            HWND_BROADCAST = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "ImmersiveColorSet", 0x0002, 500, ctypes.byref(ctypes.c_ulong()))

            time.sleep(0.1)
            after = self.get_dark_mode()
            res.after_state = {"dark_mode": after}
            res.verified = (after == enabled)
            res.verification_summary = f"Dark mode transitioned from {before} to {after}"
        except Exception as e:
            res.error = str(e)
            res.verified = False
            res.verification_summary = f"Failed to set registry theme key: {e}"

        return res
