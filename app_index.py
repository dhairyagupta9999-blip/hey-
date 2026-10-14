"""Open-vocabulary App Index, Resolver, and Process Controller (Tier 2).

Discovers and caches every launchable application across:
  - User and Common Start Menu shortcuts (.lnk)
  - Store / UWP / MSIX applications (shell:AppsFolder via Get-StartApps)
  - Windows App Paths registry keys (HKLM & HKCU)
  - Common built-in Windows applications and PATH executables
  - User-defined apps.json aliases (which take absolute priority)

Provides fuzzy matching with phonetic tolerance (rapidfuzz), ambiguity detection,
graceful app launching, and process-tree termination after WM_CLOSE for tray apps.
"""
from __future__ import annotations

import os
import sys
import time
import json
import glob
import re
import threading
import subprocess
import csv
import io
import winreg
from typing import Any, Dict, List, Optional, Tuple

import psutil
from rapidfuzz import fuzz, process

from config import (
    APPDATA_DIR, USER_APPS_FILE, DEFAULT_APPS_FILE
)

CACHE_FILE = os.path.join(APPDATA_DIR, "app_index.json")

# Standard built-in Windows apps
BUILTIN_APPS = {
    "calculator": {"name": "Calculator", "target": "calc.exe", "aliases": ["calc", "calculator", "calculater"], "process": "CalculatorApp.exe"},
    "notepad": {"name": "Notepad", "target": "notepad.exe", "aliases": ["notepad", "note pad", "notes"], "process": "notepad.exe"},
    "paint": {"name": "Paint", "target": "mspaint.exe", "aliases": ["paint", "mspaint", "ms paint"], "process": "mspaint.exe"},
    "task manager": {"name": "Task Manager", "target": "taskmgr.exe", "aliases": ["task manager", "taskmgr", "task list"], "process": "Taskmgr.exe"},
    "control panel": {"name": "Control Panel", "target": "control.exe", "aliases": ["control panel", "settings panel"], "process": "control.exe"},
    "command prompt": {"name": "Command Prompt", "target": "cmd.exe", "aliases": ["cmd", "command prompt", "terminal cmd"], "process": "cmd.exe"},
    "powershell": {"name": "PowerShell", "target": "powershell.exe", "aliases": ["powershell", "posh"], "process": "powershell.exe"},
    "windows terminal": {"name": "Terminal", "target": "wt.exe", "aliases": ["terminal", "windows terminal", "wt"], "process": "WindowsTerminal.exe"},
    "file explorer": {"name": "File Explorer", "target": "explorer.exe", "aliases": ["explorer", "file explorer", "files", "my computer"], "process": "explorer.exe"},
    "settings": {"name": "Settings", "target": "ms-settings:", "aliases": ["settings", "windows settings", "pc settings"], "process": "SystemSettings.exe"},
    "snipping tool": {"name": "Snipping Tool", "target": "snippingtool.exe", "aliases": ["snipping tool", "snip", "screen capture"], "process": "SnippingTool.exe"},
    "registry editor": {"name": "Registry Editor", "target": "regedit.exe", "aliases": ["regedit", "registry editor", "registry"], "process": "regedit.exe"},
}

# Phonetic & speech recognition replacements
PHONETIC_REPLACEMENTS = [
    (re.compile(r"\bsportify\b", re.IGNORECASE), "spotify"),
    (re.compile(r"\bspotifye\b", re.IGNORECASE), "spotify"),
    (re.compile(r"\bnote\s*pad\b", re.IGNORECASE), "notepad"),
    (re.compile(r"\bnot\s*pad\b", re.IGNORECASE), "notepad"),
    (re.compile(r"\bms\s*paint\b", re.IGNORECASE), "paint"),
    (re.compile(r"\bcalculater\b", re.IGNORECASE), "calculator"),
    (re.compile(r"\bv\s*s\s*code\b", re.IGNORECASE), "vscode"),
    (re.compile(r"\bvisual\s+studio\s+code\b", re.IGNORECASE), "vscode"),
    (re.compile(r"\bthis\s+code\b", re.IGNORECASE), "vscode"),
    (re.compile(r"\bgoogle\s+chrome\b", re.IGNORECASE), "chrome"),
    (re.compile(r"\bbrave\s+browser\b", re.IGNORECASE), "brave"),
    (re.compile(r"\bcloud\s+code\b", re.IGNORECASE), "claude code"),
    (re.compile(r"\bclawed\s+code\b", re.IGNORECASE), "claude code"),
    (re.compile(r"\bclod\s+code\b", re.IGNORECASE), "claude code"),
    (re.compile(r"\bmime\s*stream\b", re.IGNORECASE), "mimestream"),
]

def phonetic_normalize(query: str) -> str:
    """Normalize common speech-to-text transcript slips to canonical terms."""
    q = query.strip()
    for rx, rep in PHONETIC_REPLACEMENTS:
        q = rx.sub(rep, q)
    return q.strip()

def clean_name(s: str) -> str:
    """Clean name for comparison (lowercased, alphanumeric and spaces)."""
    return re.sub(r"[^\w\s]", "", s).lower().strip()


class AppIndex:
    """In-memory searchable index of all Windows applications."""

    def __init__(self, cache_file: str = CACHE_FILE):
        self.cache_file = cache_file
        self.apps: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._loaded = False
        self.load_cache_or_build()

    def load_cache_or_build(self):
        """Quickly load cached index if available, or build synchronously on first run."""
        cache_loaded = False
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list) and data:
                        with self._lock:
                            self.apps = data
                            self._loaded = True
                            cache_loaded = True
            except Exception as e:
                print(f"  [app_index] Failed to load cache: {e}")

        if cache_loaded:
            # Background refresh to pick up newly installed/uninstalled apps
            t = threading.Thread(target=self.refresh, kwargs={"force_sync": True}, daemon=True)
            t.start()
        else:
            # First run without cache: build synchronously
            self.refresh(force_sync=True)

    def refresh(self, force_sync: bool = False):
        """Re-scan all application sources and update cache."""
        new_apps: List[Dict[str, Any]] = []
        seen_targets = set()

        # 1. User apps.json aliases (Priority 100 - Wins over all other sources)
        apps_file = USER_APPS_FILE if os.path.exists(USER_APPS_FILE) else DEFAULT_APPS_FILE
        if os.path.exists(apps_file):
            try:
                with open(apps_file, "r", encoding="utf-8") as f:
                    apps_json = json.load(f)
                for alias_key, val in apps_json.items():
                    if isinstance(val, str):
                        display_name = val
                        heard_as = [alias_key, val.lower()]
                    elif isinstance(val, dict):
                        display_name = val.get("say", val.get("app", alias_key))
                        heard_as = [alias_key, val.get("app", "").lower()] + [h.lower() for h in val.get("heard_as", [])]
                    else:
                        continue
                    entry = {
                        "name": display_name,
                        "clean_name": clean_name(display_name),
                        "target": alias_key,
                        "source": "apps_json",
                        "app_id": None,
                        "aliases": list(set([a for a in heard_as if a])),
                        "priority": 100
                    }
                    new_apps.append(entry)
                    seen_targets.add(f"alias:{alias_key.lower()}")
            except Exception as exc:
                print(f"  [app_index] Error loading apps.json: {exc}")

        # 2. Builtin standard Windows tools (Priority 80)
        for key, info in BUILTIN_APPS.items():
            entry = {
                "name": info["name"],
                "clean_name": clean_name(info["name"]),
                "target": info["target"],
                "source": "builtin",
                "app_id": None,
                "process": info.get("process"),
                "aliases": info["aliases"],
                "priority": 80
            }
            new_apps.append(entry)
            seen_targets.add(info["target"].lower())

        # 3. Store / UWP Apps (Priority 50)
        store_scanned = False
        try:
            p = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "Get-StartApps | ConvertTo-Csv -NoTypeInformation"],
                capture_output=True, text=True, timeout=15
            )
            if p.returncode == 0 and p.stdout:
                reader = csv.DictReader(io.StringIO(p.stdout))
                for row in reader:
                    name = row.get("Name", "").strip()
                    app_id = row.get("AppID", "").strip()
                    if not name or not app_id:
                        continue
                    if app_id.lower() in seen_targets:
                        continue
                    seen_targets.add(app_id.lower())
                    new_apps.append({
                        "name": name,
                        "clean_name": clean_name(name),
                        "target": f"shell:AppsFolder\\{app_id}",
                        "source": "store_app",
                        "app_id": app_id,
                        "aliases": [clean_name(name)],
                        "priority": 50
                    })
                store_scanned = True
        except Exception as exc:
            print(f"  [app_index] Warning: Get-StartApps scan failed: {exc}")

        # Fallback to cache if Get-StartApps timed out and cache has store_app entries
        if not store_scanned and os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                for item in cached:
                    if item.get("source") == "store_app" and item.get("app_id"):
                        app_id = item["app_id"]
                        if app_id.lower() not in seen_targets:
                            seen_targets.add(app_id.lower())
                            new_apps.append(item)
            except Exception:
                pass

        # 4. Start Menu Shortcuts (.lnk) (Priority 50)
        start_menu_dirs = [
            os.path.expandvars(r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs"),
            os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        ]
        for sm_dir in start_menu_dirs:
            if os.path.exists(sm_dir):
                for lnk in glob.glob(os.path.join(sm_dir, "**", "*.lnk"), recursive=True):
                    base = os.path.splitext(os.path.basename(lnk))[0]
                    clean_b = clean_name(base)
                    # Filter out uninstaller / help links
                    if any(un in clean_b for un in ["uninstall", "help", "documentation", "readme"]):
                        continue
                    target_key = lnk.lower()
                    if target_key in seen_targets:
                        continue
                    seen_targets.add(target_key)
                    new_apps.append({
                        "name": base,
                        "clean_name": clean_b,
                        "target": lnk,
                        "source": "start_menu",
                        "app_id": None,
                        "aliases": [clean_b],
                        "priority": 50
                    })

        # 5. App Paths Registry (Priority 40)
        for root_key in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root_key, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths") as key:
                    count, _, _ = winreg.QueryInfoKey(key)
                    for i in range(count):
                        try:
                            subkey_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, subkey_name) as subkey:
                                exe_path, _ = winreg.QueryValueEx(subkey, "")
                                if exe_path and os.path.exists(exe_path):
                                    name = os.path.splitext(subkey_name)[0]
                                    target_key = exe_path.lower()
                                    if target_key not in seen_targets:
                                        seen_targets.add(target_key)
                                        new_apps.append({
                                            "name": name,
                                            "clean_name": clean_name(name),
                                            "target": exe_path,
                                            "source": "registry",
                                            "app_id": None,
                                            "process": os.path.basename(exe_path),
                                            "aliases": [clean_name(name)],
                                            "priority": 40
                                        })
                        except Exception:
                            continue
            except Exception:
                pass

        with self._lock:
            self.apps = new_apps
            self._loaded = True

        # Save to cache
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(new_apps, f, indent=2)
        except Exception as exc:
            print(f"  [app_index] Failed to write cache: {exc}")

        return len(new_apps)

    def find_app(self, query: str) -> Tuple[Optional[Dict[str, Any]], float, Optional[List[Dict[str, Any]]]]:
        """Find an application matching the spoken query.

        Returns:
          (best_entry, best_score, ambiguous_choices_or_none)
        If two candidates have scores within 6 points of each other (and score >= 65),
        ambiguous_choices_or_none is returned as [top1, top2] for clarification.
        """
        if not query or not query.strip():
            return None, 0.0, None

        q_raw = query.strip()
        q_norm = phonetic_normalize(q_raw)
        q_clean = clean_name(q_norm)

        with self._lock:
            candidates = list(self.apps)

        if not candidates:
            return None, 0.0, None

        # Pass 1a: Exact alias or clean_name match from apps.json / builtins (priority >= 80)
        for app in candidates:
            if app["priority"] >= 80:
                for alias in app.get("aliases", []):
                    if q_clean == clean_name(alias) or q_norm == alias.lower():
                        return app, 100.0, None
                if q_clean == app["clean_name"] or q_norm == app["name"].lower():
                    return app, 100.0, None

        # Pass 1b: Exact match across ALL candidates (Chrome means Chrome)
        for app in candidates:
            for alias in app.get("aliases", []):
                if q_clean == clean_name(alias) or q_norm == alias.lower():
                    return app, 100.0, None
            if q_clean == app["clean_name"] or q_norm == app["name"].lower():
                return app, 100.0, None

        # Pass 1c: Exact token match on multi-word apps (e.g. "chrome" in "google chrome") if unique
        token_matches = [
            app for app in candidates
            if q_clean in app["clean_name"].split() and len(q_clean) >= 4
        ]
        if len(token_matches) == 1:
            return token_matches[0], 100.0, None

        # Pass 2: Fuzzy matching with rapidfuzz across all candidates
        scored_candidates: List[Tuple[float, Dict[str, Any]]] = []

        for app in candidates:
            # Score against display name
            s1 = fuzz.WRatio(q_norm, app["name"])
            s2 = fuzz.token_sort_ratio(q_clean, app["clean_name"])
            best_s = max(s1, s2)

            # Score against aliases
            for alias in app.get("aliases", []):
                sa = max(fuzz.WRatio(q_norm, alias), fuzz.token_sort_ratio(q_clean, clean_name(alias)))
                if sa > best_s:
                    best_s = sa

            # Add priority weight bias (e.g. apps.json gets +5 bonus)
            bonus = 5.0 if app["priority"] >= 80 else 0.0
            final_score = min(100.0, best_s + bonus)
            scored_candidates.append((final_score, app))

        scored_candidates.sort(key=lambda x: x[0], reverse=True)

        if not scored_candidates or scored_candidates[0][0] < 60.0:
            return None, 0.0, None

        top1_score, top1_app = scored_candidates[0]

        # Check for ambiguity: if top2 score is close to top1 (within 6 points)
        if len(scored_candidates) > 1:
            top2_score, top2_app = scored_candidates[1]
            if top1_score >= 65.0 and top2_score >= 65.0 and abs(top1_score - top2_score) <= 6.0:
                # If they are essentially identical apps or same target, don't flag ambiguity
                if top1_app["clean_name"] != top2_app["clean_name"]:
                    return top1_app, top1_score, [top1_app, top2_app]

        return top1_app, top1_score, None

    def launch_app(self, target_or_entry: Any) -> bool:
        """Launch an application from an AppEntry dict or target name."""
        if isinstance(target_or_entry, dict):
            entry = target_or_entry
        else:
            entry, score, _ = self.find_app(str(target_or_entry))
            if not entry:
                # Fallback to direct shell start
                try:
                    subprocess.Popen(["cmd", "/c", "start", "", str(target_or_entry)], shell=False)
                    return True
                except Exception:
                    return False

        target = entry.get("target")
        app_id = entry.get("app_id")
        source = entry.get("source")

        try:
            # Store / UWP apps
            if app_id:
                os.startfile(f"shell:AppsFolder\\{app_id}")
                return True

            # URI schemes (e.g., spotify:, ms-settings:)
            if target and (":" in target and not "\\" in target and not "/" in target):
                os.startfile(target)
                return True

            # .lnk or .exe files
            if target and os.path.exists(target):
                os.startfile(target)
                return True

            # apps.json alias fallback (e.g. 'spotify' -> actions_win.launch_app)
            if source == "apps_json":
                from actions_win import launch_app as win_launch
                return win_launch(target)

            # Generic start
            subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
            return True
        except Exception as exc:
            print(f"  [app_index] Failed to launch {entry.get('name')}: {exc}")
            return False

    def close_app(self, target_name: str) -> Dict[str, Any]:
        """Close an application: WM_CLOSE first, then terminate process tree after 1.5s."""
        from win32gui import EnumWindows, GetWindowText, IsWindowVisible, PostMessage
        import win32process
        import win32con

        entry, _, _ = self.find_app(target_name)
        target_lower = target_name.lower().replace(".exe", "")
        known_proc = entry.get("process", "").lower() if entry else ""
        canonical_clean = entry.get("clean_name", "") if entry else ""

        # Find matching processes
        matching_procs = []
        matching_pids = set()

        for p in psutil.process_iter(["pid", "name", "exe"]):
            try:
                if hasattr(p, "info") and isinstance(p.info, dict):
                    raw_name = p.info.get("name") or ""
                    raw_exe = p.info.get("exe") or ""
                    p_pid = p.info.get("pid") or p.pid
                else:
                    raw_name = p.name()
                    raw_exe = p.exe() if hasattr(p, "exe") else ""
                    p_pid = p.pid
                p_name = raw_name.lower()
                p_exe = raw_exe.lower()
                clean_pn = p_name.replace(".exe", "")

                matched = False
                if target_lower and (target_lower in clean_pn or clean_pn in target_lower):
                    matched = True
                elif known_proc and (known_proc in p_name or p_name in known_proc):
                    matched = True
                elif canonical_clean and (canonical_clean in clean_pn or clean_pn in canonical_clean):
                    matched = True

                if matched:
                    matching_procs.append(p)
                    matching_pids.add(p.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # 1. Send WM_CLOSE to matching windows
        closed_hwnds = []
        def enum_cb(hwnd, _):
            if IsWindowVisible(hwnd):
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                w_title = GetWindowText(hwnd).lower()
                if pid in matching_pids or (target_lower and target_lower in w_title):
                    PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                    closed_hwnds.append(hwnd)
                    matching_pids.add(pid)
        try:
            EnumWindows(enum_cb, None)
        except Exception:
            pass

        # Collect all parent processes and their children
        all_targets: List[psutil.Process] = []
        for p in matching_procs:
            try:
                all_targets.extend(p.children(recursive=True))
            except Exception:
                pass
            all_targets.append(p)

        # 1. Send WM_CLOSE / terminate
        for p in all_targets:
            try:
                p.terminate()
            except Exception:
                pass

        # 2. Wait 1.5 s for graceful shutdown
        time.sleep(1.5)

        # 3. Force-kill any processes/children still alive (tray apps like Spotify, Slack)
        terminated_count = 0
        for p in all_targets:
            try:
                if p.is_running():
                    p.kill()
                    terminated_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        return {
            "success": len(matching_procs) > 0 or len(closed_hwnds) > 0,
            "target": target_name,
            "matched_processes": len(matching_procs),
            "force_killed": terminated_count
        }

    def close_all_apps(self, confirmed: bool = False) -> Dict[str, Any]:
        """Close all user windows. Requires confirmation first."""
        if not confirmed:
            return {
                "needs_confirmation": True,
                "action": "close_all",
                "risk_level": "MEDIUM",
                "message": "Are you sure you want to close all open windows?"
            }

        from win32gui import EnumWindows, GetWindowText, IsWindowVisible, PostMessage
        import win32con

        closed = 0
        def enum_cb(hwnd, _):
            nonlocal closed
            if IsWindowVisible(hwnd):
                title = GetWindowText(hwnd)
                # Ignore system desktop / tray windows
                if title and title not in ("Program Manager", "Hey Jev"):
                    PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                    closed += 1
        try:
            EnumWindows(enum_cb, None)
        except Exception:
            pass

        return {"success": True, "closed_windows": closed}


_GLOBAL_APP_INDEX: Optional[AppIndex] = None

def get_app_index() -> AppIndex:
    """Singleton getter for AppIndex."""
    global _GLOBAL_APP_INDEX
    if _GLOBAL_APP_INDEX is None:
        _GLOBAL_APP_INDEX = AppIndex()
    return _GLOBAL_APP_INDEX

def refresh_apps() -> int:
    """On-demand app refresh entry point."""
    return get_app_index().refresh(force_sync=True)
