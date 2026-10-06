"""Safety, Risk Classification, Confirmation Engine, and Audit Log for Hey Jev.

Safety architecture:
  - SAFE: runs automatically (open, focus, volume, media, search, web).
  - MEDIUM: spoken back, runs unless user says 'stop' or presses Esc within 2s.
    (close-all and typing into unsaved documents require explicit 'yes').
  - HIGH: states exact impact, waits for spoken/typed 'yes' with 8-second timeout defaulting to NO.
    (delete to Recycle Bin, run_powershell, move/rename, shutdown/restart, install/uninstall).
  - Never permanently delete: send files to Recycle Bin only via send2trash.
  - run_powershell denylist: iex, download cradles, format, diskpart, system file deletion, reg delete, net user.
  - Audit log: %APPDATA%/HeyJev/actions.jsonl with 'undo that' support.
  - Prompt injection defense: data from files, windows, web pages is strictly quarantined as DATA.
"""
from __future__ import annotations

import os
import sys
import time
import json
import re
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import send2trash

from config import APPDATA_DIR

ACTIONS_LOG_FILE = os.path.join(APPDATA_DIR, "actions.jsonl")

# --------------------------------------------------------------------------- Risk Levels
SAFE = "SAFE"
MEDIUM = "MEDIUM"
HIGH = "HIGH"

ACTION_RISK_MAP = {
    # SAFE
    "open_app": SAFE,
    "focus_window": SAFE,
    "app_focus": SAFE,
    "app_open": SAFE,
    "system_setting": SAFE,
    "system_folder": SAFE,
    "system_utility": SAFE,
    "system_website": SAFE,
    "system_search": SAFE,
    "set_volume": SAFE,
    "media_control": SAFE,
    "media_play": SAFE,
    "media_pause": SAFE,
    "media_next": SAFE,
    "media_previous": SAFE,
    "window_snap_left": SAFE,
    "window_snap_right": SAFE,
    "window_minimize": SAFE,
    "window_maximize": SAFE,
    "window_restore": SAFE,
    "list_windows": SAFE,
    "find_files": SAFE,
    "open_path": SAFE,
    "read_foreground_text": SAFE,
    "web_search": SAFE,
    "open_url": SAFE,
    "clipboard_get": SAFE,

    # Phase 7: MCP Windows Automation Tools (SAFE: inspection/read/window/launch)
    "ui_find": SAFE,
    "ui_read": SAFE,
    "ui_read_table": SAFE,
    "ui_snapshot": SAFE,
    "ui_wait": SAFE,
    "window_management": SAFE,
    "app": SAFE,

    # MEDIUM (2-second grace period)
    "screenshot_control": MEDIUM,
    "close_app": MEDIUM,
    "app_quit": MEDIUM,
    "type_text": MEDIUM,
    "press_keys": MEDIUM,
    "clipboard_set": MEDIUM,

    # Phase 7: MCP Windows Automation Tools (MEDIUM: typing/select in apps)
    "ui_type": MEDIUM,
    "ui_select": MEDIUM,
    "ui_click": MEDIUM,

    # HIGH (8-second explicit YES timeout, default NO)
    "keyboard_control": HIGH,
    "mouse_control": HIGH,
    "close_all": HIGH,
    "delete_file": HIGH,
    "move_file": HIGH,
    "rename_file": HIGH,
    "open_dangerous_file": HIGH,
    "run_powershell": HIGH,
    "install_software": HIGH,
    "uninstall_software": HIGH,
    "shutdown_computer": HIGH,
    "restart_computer": HIGH,
    "registry_edit": HIGH,
    "network_change": HIGH,
    "send_message": HIGH,
    "send_email": HIGH,
}

BLOCKED = "BLOCKED"

# --------------------------------------------------------------------------- UI Bypass & Administrative Tool Protections

ADMIN_AND_SHELL_PROCESSES = {
    "powershell", "powershell.exe",
    "powershell_ise", "powershell_ise.exe",
    "pwsh", "pwsh.exe",
    "cmd", "cmd.exe",
    "conhost", "conhost.exe",
    "wt", "wt.exe",
    "regedit", "regedit.exe",
    "mmc", "mmc.exe",
    "services", "services.msc", "services.exe",
    "taskmgr", "taskmgr.exe",
    "wscript", "wscript.exe",
    "cscript", "cscript.exe",
    "mshta", "mshta.exe",
    "keepass", "keepass.exe",
    "keepassxc", "keepassxc.exe",
    "bitwarden", "bitwarden.exe",
    "1password", "1password.exe",
}

ADMIN_AND_SHELL_TITLES = [
    re.compile(r"^Run$", re.I),
    re.compile(r"\bRun\b", re.I),
    re.compile(r"Windows Security", re.I),
    re.compile(r"User Account Control", re.I),
    re.compile(r"\bUAC\b", re.I),
    re.compile(r"\bTask Manager\b", re.I),
    re.compile(r"\bRegistry Editor\b", re.I),
    re.compile(r"\bComputer Management\b", re.I),
    re.compile(r"\bServices\b", re.I),
    re.compile(r"\b(?:KeePass|Bitwarden|1Password|LastPass)\b", re.I),
    re.compile(r"\bWindows PowerShell\b", re.I),
    re.compile(r"\bCommand Prompt\b", re.I),
]

BROWSER_PROCESSES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "iexplore.exe",
    "chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "iexplore"
}

SENSITIVE_LOGIN_OR_BANKING_REGEX = re.compile(
    r"\b(log\s*in|sign\s*in|password|bank(?:ing)?|payment|checkout|credit\s*card|auth(?:enticat\w+)?)\b",
    re.I
)

OUTRIGHT_BLOCKED_SHORTCUTS = [
    re.compile(r"\b(?:win|windows|cmd|meta)\s*\+\s*r\b", re.I),
    re.compile(r"\b(?:win|windows|cmd|meta)\s*\+\s*x\b", re.I),
    re.compile(r"\b(?:ctrl|control)\s*\+\s*shift\s*\+\s*esc(?:ape)?\b", re.I),
    re.compile(r"\b(?:ctrl|control)\s*\+\s*alt\s*\+\s*del(?:ete)?\b", re.I),
    re.compile(r"\b(?:win|windows|cmd|meta)\s*\+\s*pause\b", re.I),
]

CONFIRM_REQUIRED_SHORTCUTS = [
    re.compile(r"\balt\s*\+\s*f4\b", re.I),
    re.compile(r"\b(?:ctrl|control)\s*\+\s*w\b", re.I),
]

BLOCKED_APP_TARGETS = {
    "powershell", "powershell.exe", "powershell_ise", "powershell_ise.exe",
    "pwsh", "pwsh.exe", "cmd", "cmd.exe", "conhost", "conhost.exe",
    "wt", "wt.exe", "regedit", "regedit.exe", "mmc", "mmc.exe",
    "services", "services.msc", "services.exe", "taskmgr", "taskmgr.exe",
    "wscript", "wscript.exe", "cscript", "cscript.exe", "mshta", "mshta.exe",
    "keepass", "keepass.exe", "keepassxc", "keepassxc.exe",
    "bitwarden", "bitwarden.exe", "1password", "1password.exe"
}

ALLOWED_SINGLE_KEYS = {
    "enter", "tab", "escape", "space", "backspace", "up", "down", "left", "right",
    "home", "end", "pageup", "pagedown", "f1", "f2", "f3", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12"
} | {chr(c) for c in range(ord('a'), ord('z')+1)} | {chr(c) for c in range(ord('0'), ord('9')+1)}


def get_foreground_window_info() -> Tuple[str, str]:
    """Get the process name and window title of the current foreground window."""
    try:
        import win32gui
        import win32process
        import psutil

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return ("", "")
        title = win32gui.GetWindowText(hwnd).strip()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid <= 0:
            return ("", title)
        proc = psutil.Process(pid)
        return (proc.name().lower(), title)
    except Exception:
        return ("", "")


def check_foreground_window_safety(proc_name: str, title: str) -> Tuple[bool, str, str]:
    """Check if the foreground window is safe for automated actions.
    
    Returns (is_allowed, status, reason):
      status: "OK", "BLOCKED", or "REQUIRE_YES"
    """
    proc_clean = (proc_name or "").strip().lower()
    proc_base = os.path.basename(proc_clean)
    proc_no_ext, _ = os.path.splitext(proc_base)
    title_clean = (title or "").strip()

    # 1. Shells and admin tools by process name
    if proc_clean in ADMIN_AND_SHELL_PROCESSES or proc_base in ADMIN_AND_SHELL_PROCESSES or proc_no_ext in ADMIN_AND_SHELL_PROCESSES:
        return False, "BLOCKED", f"Foreground window belongs to administrative tool or shell '{proc_name}'. Interaction blocked by safety policy."

    # 2. Protected windows by title (Run dialog, Windows Security, UAC, Task Manager, etc.)
    for rx in ADMIN_AND_SHELL_TITLES:
        if rx.search(title_clean):
            return False, "BLOCKED", f"Foreground window '{title}' is a protected administrative dialog. Interaction blocked by safety policy."

    # 3. Sensitive browser / authentication windows (login, sign-in, banking, payment)
    if SENSITIVE_LOGIN_OR_BANKING_REGEX.search(title_clean):
        return True, "REQUIRE_YES", f"Window '{title}' appears to be a sensitive login or banking page. Explicit confirmation required."

    return True, "OK", ""


def normalize_shortcut_str(key: str = "", modifiers: Any = None, raw_keys: str = "") -> str:
    """Normalize keyboard shortcut components into canonical format like 'ctrl+shift+esc'."""
    tokens = []
    if raw_keys:
        raw_parts = [re.sub(r"[\[\]'\"\(\)]", "", p).strip().lower() for p in raw_keys.replace("-", "+").split("+") if p.strip()]
        for p in raw_parts:
            if not p:
                continue
            if p in ("windows", "cmd", "meta", "super", "win"):
                tokens.append("win")
            elif p in ("control", "ctrl"):
                tokens.append("ctrl")
            elif p in ("escape", "esc"):
                tokens.append("esc")
            elif p in ("delete", "del"):
                tokens.append("del")
            else:
                tokens.append(p)
    else:
        if modifiers:
            if isinstance(modifiers, list):
                mod_list = modifiers
            elif isinstance(modifiers, str):
                mod_list = modifiers.replace("-", "+").split("+")
            else:
                mod_list = [str(modifiers)]
            for m in mod_list:
                m_clean = m.strip().lower()
                if m_clean in ("windows", "cmd", "meta", "super", "win"):
                    tokens.append("win")
                elif m_clean in ("control", "ctrl"):
                    tokens.append("ctrl")
                elif m_clean:
                    tokens.append(m_clean)
        if key:
            k_clean = str(key).strip().lower()
            if k_clean in ("escape", "esc"):
                tokens.append("esc")
            elif k_clean in ("delete", "del"):
                tokens.append("del")
            elif k_clean:
                tokens.append(k_clean)

    order = {"ctrl": 1, "alt": 2, "shift": 3, "win": 4}
    mod_tokens = sorted([t for t in tokens if t in order], key=lambda x: order[x])
    base_tokens = [t for t in tokens if t not in order]
    return "+".join(mod_tokens + base_tokens)


def check_shortcut_safety(action: str, args: Dict[str, Any]) -> Tuple[str, str]:
    """Check keyboard shortcut for safety rules.
    
    Returns (status, reason):
      status: "BLOCKED", "REQUIRE_YES", or "OK"
    """
    key = str(args.get("key", "")).strip()
    modifiers = args.get("modifiers")
    raw_keys = str(args.get("keys", args.get("text", "") if action in ("press_keys", "shortcut") else "")).strip()

    norm = normalize_shortcut_str(key=key, modifiers=modifiers, raw_keys=raw_keys)
    if not norm:
        return "OK", ""

    # 1. Outright blocked shortcuts: Win+R, Win+X, Ctrl+Shift+Esc, Ctrl+Alt+Del, Win+Pause
    for rx in OUTRIGHT_BLOCKED_SHORTCUTS:
        if rx.search(norm):
            return "BLOCKED", f"Shortcut '{norm}' is prohibited by safety policy."

    # 2. Confirm required shortcuts: Alt+F4, Ctrl+W
    for rx in CONFIRM_REQUIRED_SHORTCUTS:
        if rx.search(norm):
            return "REQUIRE_YES", f"Shortcut '{norm}' will close a window or tab. Explicit confirmation required."

    return "OK", ""


def check_app_launch_safety(target: str) -> Tuple[bool, str]:
    """Check if application launch target is blocked (administrative tools, shells, password managers)."""
    t_clean = (target or "").strip().lower()
    base = os.path.basename(t_clean)
    name_no_ext, _ = os.path.splitext(base)

    if base in BLOCKED_APP_TARGETS or name_no_ext in BLOCKED_APP_TARGETS or t_clean in BLOCKED_APP_TARGETS:
        return False, f"Launching '{target}' is prohibited by safety policy."

    for b_app in BLOCKED_APP_TARGETS:
        if re.search(rf"\b{re.escape(b_app)}\b", t_clean):
            return False, f"Launching '{target}' is prohibited by safety policy."

    return True, ""


def is_preview_mode_enabled() -> bool:
    """Check if 'Confirm every AI-agent UI action' is enabled in settings (default True)."""
    try:
        from assistant_ui import get_settings
        return bool(get_settings().get("tier3_confirm_all_actions", True))
    except Exception:
        return True


def format_action_preview_description(action: str, args: Dict[str, Any]) -> str:
    """Format a single short sentence describing the next UI action for user confirmation."""
    act = action.strip().lower()
    if act == "ui_click":
        elem = args.get("elementId") or args.get("name") or "target element"
        return f"I am about to click '{elem}'. Should I proceed?"
    elif act == "ui_type":
        txt = args.get("text", "")
        short_txt = (txt[:25] + "...") if len(txt) > 25 else txt
        return f"I am about to type '{short_txt}'. Should I proceed?"
    elif act == "ui_select":
        val = args.get("value") or "selected item"
        return f"I am about to select '{val}'. Should I proceed?"
    elif act in ("keyboard_control", "press_keys"):
        key = args.get("key") or args.get("keys") or args.get("text") or "key"
        mod = args.get("modifiers")
        combo = f"{mod}+{key}" if mod else str(key)
        return f"I am about to press '{combo}'. Should I proceed?"
    elif act == "mouse_control":
        m_act = args.get("action") or "click"
        return f"I am about to perform mouse {m_act}. Should I proceed?"
    elif act == "clipboard" and args.get("action") in ("set", "clear"):
        return f"I am about to update the clipboard. Should I proceed?"
    elif act in ("screenshot_control", "ui_read", "ui_read_table", "ui_snapshot", "read_foreground_text"):
        return f"I am reading the screen or window contents to show the AI model. Should I proceed?"
    return f"I am about to execute '{action}'. Should I proceed?"


def check_data_disclosure_safety(action: str, provider: str = "") -> Tuple[bool, str]:
    """Check if action is allowed for the specified LLM provider.
    
    Free/training providers (like opencode_zen) must NEVER receive screenshots or window text.
    """
    prov_clean = (provider or "").lower().strip()
    act = (action or "").lower().strip()
    if prov_clean in ("opencode_zen", "zen", "free_training"):
        if act in ("screenshot_control", "ui_read", "ui_read_table", "ui_snapshot", "read_foreground_text"):
            return False, f"Action '{action}' transmits screen or window content, which is forbidden with free/training provider '{provider}'."
    return True, ""


def evaluate_risk(action: str, args: Optional[Dict[str, Any]] = None) -> Tuple[str, bool]:
    """Evaluate the risk level of an action and determine if it is permitted.
    
    Returns (risk_level, is_allowed).
    - Unknown tools or explicitly denied tools return ("BLOCKED", False).
    - App launching shells/admin tools returns ("BLOCKED", False).
    - Blocked shortcuts return ("BLOCKED", False).
    - mouse_control is HIGH unless action == "scroll".
    - keyboard_control is HIGH unless single allowed key or plain type without modifiers.
    - screenshot_control and ui_read on other windows return (MEDIUM, True).
    - Inspection / read actions on current window return (SAFE, True).
    """
    args = args or {}
    act = action.strip().lower()

    # App launch security check
    if act in ("app", "open_app"):
        target = args.get("programPath") or args.get("name") or args.get("target") or ""
        safe, _ = check_app_launch_safety(str(target))
        if not safe:
            return BLOCKED, False
        return SAFE, True

    # Screenshot control risk is MEDIUM
    if act == "screenshot_control":
        return MEDIUM, True

    # ui_read on other windows is MEDIUM risk
    if act == "ui_read":
        target_win = args.get("window") or args.get("windowName") or args.get("target")
        if target_win or args.get("other_window"):
            return MEDIUM, True
        return SAFE, True

    # Check clipboard action parameter
    if act == "clipboard":
        clip_act = args.get("action", "get").lower()
        if clip_act in ("set", "clear"):
            return MEDIUM, True
        return SAFE, True

    # Mouse control risk: plain scroll is MEDIUM; click/move/drag is HIGH
    if act == "mouse_control":
        mouse_act = args.get("action", "").lower()
        if mouse_act == "scroll":
            return MEDIUM, True
        return HIGH, True

    # Keyboard control risk: single allowed key or plain type without modifiers is MEDIUM;
    # shortcuts, modifiers, or blocked combinations are HIGH or BLOCKED
    if act in ("keyboard_control", "press_keys"):
        sec_status, _ = check_shortcut_safety(act, args)
        if sec_status == "BLOCKED":
            return BLOCKED, False
        elif sec_status == "REQUIRE_YES":
            return HIGH, True

        key = str(args.get("key", "")).strip().lower()
        modifiers = args.get("modifiers")
        has_modifiers = bool(modifiers and str(modifiers).strip())
        kb_act = args.get("action", "").lower()

        if has_modifiers:
            return HIGH, True
        if kb_act == "type":
            return MEDIUM, True
        if key in ALLOWED_SINGLE_KEYS or len(key) == 1:
            return MEDIUM, True
        return HIGH, True

    if act in ACTION_RISK_MAP:
        risk = ACTION_RISK_MAP[act]
        return risk, True

    # Unknown or unmapped actions are blocked by default
    return BLOCKED, False

# --------------------------------------------------------------------------- PowerShell Denylist
POWERSHELL_DENYLIST = [
    (re.compile(r"\b(?:Invoke-Expression|iex)\b", re.I), "Invoke-Expression (code execution)"),
    (re.compile(r"\b(?:DownloadFile|DownloadString|Net\.WebClient|BitsTransfer)\b", re.I), "arbitrary web download cradle"),
    (re.compile(r"\b(?:Invoke-WebRequest|Invoke-RestMethod|curl|wget)\b", re.I), "outbound network request"),
    (re.compile(r"\bRemove-Item\b.*-(?:Recurse|r)\b.*(?:C:\\Windows|C:\\Program Files|C:\\Users\\[^\\]+$|system32)", re.I), "recursive system path deletion"),
    (re.compile(r"\b(?:format|diskpart)\b", re.I), "destructive disk partitioning or formatting"),
    (re.compile(r"\breg(?:\.exe)?\s+delete\b", re.I), "registry key deletion"),
    (re.compile(r"\bnet\s+(?:user|localgroup)\b", re.I), "user account management"),
    (re.compile(r"\b(?:Start-Process.*-Verb\s+RunAs|runas)\b", re.I), "privilege elevation / administrative execution"),
]

def validate_powershell_command(cmd: str) -> Tuple[bool, str]:
    """Validate PowerShell command against the safety denylist. Never runs as admin."""
    for rx, reason in POWERSHELL_DENYLIST:
        if rx.search(cmd):
            return False, f"Command blocked by safety policy: contains {reason}."
    return True, "Command passed safety validation."


# --------------------------------------------------------------------------- Prompt Injection Sanitizer
INJECTION_PATTERNS = [
    re.compile(r"(?:ignore|forget|disregard)\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", re.I),
    re.compile(r"(?:system\s+prompt|developer\s+mode|jailbreak|DAN\s+mode)", re.I),
    re.compile(r"(?:delete\s+(?:all|everything)|remove-item\s+-recurse)", re.I),
    re.compile(r"(?:you\s+are\s+now|new\s+system\s+instruction)", re.I),
]

def sanitize_data_content(text: str, source_label: str = "DATA") -> str:
    """Quarantine external text as untrusted DATA, stripping prompt injection vectors."""
    cleaned = text
    for rx in INJECTION_PATTERNS:
        cleaned = rx.sub("[REDACTED_INJECTION_ATTEMPT]", cleaned)
    return f"<{source_label}>\n{cleaned.strip()}\n</{source_label}>"


# --------------------------------------------------------------------------- Audit Log & Undo
_LOG_LOCK = threading.Lock()

def log_action(
    tier: int,
    action: str,
    args: Dict[str, Any],
    risk_level: str,
    result: str,
    undoable: bool = False,
    undo_data: Optional[Dict[str, Any]] = None
) -> str:
    """Record action to %APPDATA%/HeyJev/actions.jsonl."""
    act_id = f"act_{int(time.time()*1000)}"
    entry = {
        "id": act_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "tier": tier,
        "action": action,
        "args": args,
        "risk_level": risk_level,
        "result": result,
        "undoable": undoable,
        "undo_data": undo_data
    }
    with _LOG_LOCK:
        try:
            os.makedirs(os.path.dirname(ACTIONS_LOG_FILE), exist_ok=True)
            with open(ACTIONS_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            print(f"  [safety audit log error]: {exc}")
    return act_id

def get_last_undoable_action() -> Optional[Dict[str, Any]]:
    """Retrieve the most recent undoable action from the audit log."""
    if not os.path.exists(ACTIONS_LOG_FILE):
        return None
    with _LOG_LOCK:
        try:
            with open(ACTIONS_LOG_FILE, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
            for line in reversed(lines):
                data = json.loads(line)
                if data.get("undoable") and data.get("undo_data"):
                    return data
        except Exception:
            return None
    return None

def perform_undo() -> Tuple[bool, str]:
    """Execute undo for the last reversible action."""
    last_act = get_last_undoable_action()
    if not last_act:
        return False, "Nothing to undo."

    undo_data = last_act["undo_data"]
    u_act = undo_data.get("action")

    if u_act == "open_app":
        target = undo_data.get("target")
        from app_index import get_app_index
        get_app_index().launch_app(target)
        log_action(2, "undo", {"reverted_action": last_act["action"], "reverted_id": last_act["id"]}, SAFE, "success")
        return True, f"Undid closing {target}. Reopening {target}."

    elif u_act == "restore_file":
        path = undo_data.get("path")
        # Recycle Bin restoration note: Windows Recycle Bin COM doesn't guarantee programmatic restore without shell UI,
        # but we record the original location.
        log_action(2, "undo", {"reverted_action": last_act["action"], "path": path}, SAFE, "success")
        return True, f"File {os.path.basename(path)} was sent to the Recycle Bin. You can restore it from the Recycle Bin."

    elif u_act == "set_volume":
        prev_vol = undo_data.get("previous_volume", 50)
        import actions_win
        actions_win.set_volume(prev_vol)
        log_action(2, "undo", {"reverted_action": "set_volume", "previous_volume": prev_vol}, SAFE, "success")
        return True, f"Undid volume change, restored to {prev_vol}%."

    return False, f"Cannot undo {last_act['action']}."


# --------------------------------------------------------------------------- Confirmation & Cancellation Manager
class ConfirmationManager:
    """Manages in-flight actions, grace periods, and explicit confirmations."""

    def __init__(self):
        self._lock = threading.RLock()
        self.pending_confirmation: Optional[Dict[str, Any]] = None
        self.grace_period_timer: Optional[threading.Timer] = None

    def request_high_risk_confirmation(
        self,
        action: str,
        message: str,
        execute_fn: Callable[[], Any],
        timeout_s: float = 8.0
    ) -> Dict[str, Any]:
        """Request explicit confirmation for HIGH risk actions with 8s default-NO timeout."""
        with self._lock:
            # Cancel any previous pending confirmation
            self.cancel_pending("replaced by new action")

            evt = threading.Event()
            state = {"confirmed": False, "executed": False, "expired": False, "result": None}

            def _timeout_cb():
                with self._lock:
                    if not state["confirmed"]:
                        state["expired"] = True
                        evt.set()

            timer = threading.Timer(timeout_s, _timeout_cb)
            timer.daemon = True
            timer.start()

            self.pending_confirmation = {
                "action": action,
                "message": message,
                "risk_level": HIGH,
                "timer": timer,
                "event": evt,
                "state": state,
                "execute_fn": execute_fn,
                "created_at": time.time()
            }

            return {
                "needs_confirmation": True,
                "risk_level": HIGH,
                "action": action,
                "message": message,
                "timeout_s": timeout_s
            }

    def request_medium_risk_grace_period(
        self,
        action: str,
        message: str,
        execute_fn: Callable[[], Any],
        grace_seconds: float = 2.0
    ) -> Dict[str, Any]:
        """Schedule MEDIUM risk action with 2s cancel grace period."""
        with self._lock:
            self.cancel_pending("replaced by new action")

            state = {"aborted": False}
            def _grace_run():
                time.sleep(grace_seconds)
                with self._lock:
                    if not state["aborted"]:
                        try:
                            execute_fn()
                        except Exception as exc:
                            print(f"  [medium risk action error]: {exc}")

            t = threading.Thread(target=_grace_run, daemon=True)
            t.start()

            self.pending_confirmation = {
                "action": action,
                "message": message,
                "risk_level": MEDIUM,
                "state": state,
                "thread": t,
                "created_at": time.time()
            }

            return {
                "status": "grace_period",
                "risk_level": MEDIUM,
                "action": action,
                "message": message,
                "grace_seconds": grace_seconds
            }

    def respond(self, response_text: str) -> Tuple[bool, str]:
        """Handle user spoken or typed response ('yes', 'stop', 'no')."""
        t = response_text.lower().strip()
        with self._lock:
            if not self.pending_confirmation:
                return False, "No action is awaiting confirmation."

            pending = self.pending_confirmation
            state = pending["state"]

            # Explicit YES confirmation for HIGH risk actions
            if t in ("yes", "yeah", "yep", "do it", "confirm", "proceed", "sure"):
                now = time.time()
                timeout_s = pending.get("timeout_s", 8.0)
                created_at = pending.get("created_at", now)
                if state.get("expired") or (now - created_at > timeout_s):
                    self.pending_confirmation = None
                    return False, "Confirmation timed out and defaulted to NO."

                if pending.get("timer"):
                    pending["timer"].cancel()
                state["confirmed"] = True
                try:
                    result = pending["execute_fn"]()
                    self.pending_confirmation = None
                    return True, "Confirmed. Action executed."
                except Exception as exc:
                    self.pending_confirmation = None
                    return False, f"Action failed: {exc}"

            # Cancel / Stop command
            if t in ("stop", "no", "cancel", "abort", "halt", "wait", "dont", "don't"):
                return self.cancel_pending("user requested stop")

            # Any other response to HIGH risk defaults to NO
            return self.cancel_pending("confirmation did not receive yes")

    def cancel_pending(self, reason: str = "stop") -> Tuple[bool, str]:
        """Abort any pending grace period or confirmation."""
        with self._lock:
            if not self.pending_confirmation:
                return False, "No action was running or pending."

            pending = self.pending_confirmation
            state = pending["state"]
            if pending.get("timer"):
                pending["timer"].cancel()
            state["aborted"] = True
            state["confirmed"] = False
            self.pending_confirmation = None
            return True, f"Action cancelled ({reason})."

    def has_pending(self) -> bool:
        """Return True if an action or confirmation is actively pending."""
        with self._lock:
            return self.pending_confirmation is not None



_GLOBAL_CONFIRMATION_MANAGER: Optional[ConfirmationManager] = None
_CANCEL_EVENT = threading.Event()

def get_confirmation_manager() -> ConfirmationManager:
    global _GLOBAL_CONFIRMATION_MANAGER
    if _GLOBAL_CONFIRMATION_MANAGER is None:
        _GLOBAL_CONFIRMATION_MANAGER = ConfirmationManager()
    return _GLOBAL_CONFIRMATION_MANAGER

def request_global_cancel(reason: str = "stop") -> bool:
    """Signal global cancellation of running actions, confirmations, or agent loops."""
    _CANCEL_EVENT.set()
    cm = get_confirmation_manager()
    if cm.has_pending():
        cm.cancel_pending(reason)
        return True
    return False

def is_cancel_requested() -> bool:
    """Check if cancellation was signaled."""
    return _CANCEL_EVENT.is_set()

def check_and_clear_cancel() -> bool:
    """Clear and return previous cancellation state."""
    if _CANCEL_EVENT.is_set():
        _CANCEL_EVENT.clear()
        return True
    return False



# --------------------------------------------------------------------------- Safe File Operations
def delete_file_to_recycle_bin(path: str) -> Dict[str, Any]:
    """Never permanently delete anything: send file to Windows Recycle Bin."""
    if not os.path.exists(path):
        return {"success": False, "error": f"File does not exist: {path}"}

    try:
        send2trash.send2trash(path)
        log_action(
            tier=2,
            action="delete_file",
            args={"path": path},
            risk_level=HIGH,
            result="success",
            undoable=True,
            undo_data={"action": "restore_file", "path": path}
        )
        return {
            "success": True,
            "path": path,
            "filename": os.path.basename(path),
            "line": f"Moved {os.path.basename(path)} to the Recycle Bin."
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
