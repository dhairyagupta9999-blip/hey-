"""MCP Client for Windows UI Automation in Hey Jev Tier 3.

Connects to sbroenne/mcp-windows (Sbroenne.WindowsMcp.exe) over stdio.
Enforces:
  - Tool allowlist (UI find/read/snapshot, click, type, key/shortcut, scroll, window focus/resize, app launch)
  - Deny by default (PowerShell/shell, registry, file write/dialogs, process kill, services, network config)
  - Single clean error message if server fails to start, remaining off
  - Full Safety Engine gating (typing/keys into apps = MEDIUM, inspection/read = SAFE, unknown = BLOCKED)
  - Prompt injection quarantine: all outputs wrapped in <MCP_DATA> tags
  - Esc and 'stop' spoken cancel in-flight operations
  - Audit logging to %APPDATA%/HeyJev/actions.jsonl
"""
from __future__ import annotations

import os
import sys
import time
import json
import queue
import subprocess
import threading
from typing import Any, Dict, List, Optional, Tuple

import safety_engine
from safety_engine import SAFE, MEDIUM, HIGH, BLOCKED, log_action, sanitize_data_content, is_cancel_requested


class McpProcessReader:
    """Non-blocking background reader thread for MCP subprocess stdout."""

    def __init__(self, stdout):
        self.stdout = stdout
        self.queue = queue.Queue(maxsize=1000)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def _worker(self):
        try:
            for line in iter(self.stdout.readline, ""):
                if self.stop_event.is_set():
                    break
                if line:
                    self.queue.put(line)
        except Exception:
            pass

    def stop(self):
        self.stop_event.set()

# --------------------------------------------------------------------------- Configuration & Tool Lists

# Where to find the Windows MCP server binary.
#
# Originally a single hardcoded path inside `.venv-agents/`, which only existed
# on the machine that developed this. A cloner had no way to satisfy it and no
# way to discover what was wanted. Resolution order is now:
#   1. $HEYJEV_WINDOWS_MCP_EXE
#   2. <repo>/mcp/Sbroenne.WindowsMcp.exe      <- the documented drop location
#   3. <repo>/.venv-agents/bin/mcp-windows/...  <- original location, still honoured
#   4. %LOCALAPPDATA%/HeyJev/mcp/...            <- per-user install
# The first hit that exists wins. See README "Optional: Windows MCP server".
_MCP_BINARY_NAME = "Sbroenne.WindowsMcp.exe"


def _candidate_exe_paths() -> List[str]:
    repo = os.path.dirname(os.path.abspath(__file__))
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return [
        os.path.join(repo, "mcp", _MCP_BINARY_NAME),
        os.path.join(repo, ".venv-agents", "bin", "mcp-windows", _MCP_BINARY_NAME),
        os.path.join(local, "HeyJev", "mcp", _MCP_BINARY_NAME),
    ]


def _resolve_exe_path() -> str:
    """Return the first MCP binary that actually exists, or the first candidate."""
    candidates = _candidate_exe_paths()
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


# Default path to standalone binary
DEFAULT_EXE_PATH = _resolve_exe_path()

# Standard Allowed Tools and their Risk Levels
ALLOWED_TOOLS: Dict[str, Dict[str, Any]] = {
    # UI inspection and reading (SAFE)
    "ui_find": {
        "risk": SAFE,
        "category": "ui_read",
        "description": "Find UI elements by name, controlType, or automationId."
    },
    "ui_read": {
        "risk": SAFE,
        "category": "ui_read",
        "description": "Read text and accessibility properties of an observed element."
    },
    "ui_read_table": {
        "risk": SAFE,
        "category": "ui_read",
        "description": "Read structured rows and columns from a grid or table."
    },
    "ui_snapshot": {
        "risk": SAFE,
        "category": "ui_read",
        "description": "Capture compact element hierarchy for window orientation."
    },
    "screenshot_control": {
        "risk": MEDIUM,
        "category": "ui_read",
        "description": "Capture screenshot of desktop or window for visual state."
    },
    "ui_wait": {
        "risk": SAFE,
        "category": "ui_read",
        "description": "Wait until a UI condition (appear, disappear, state) is met."
    },

    # Window management (SAFE)
    "window_management": {
        "risk": SAFE,
        "category": "window_management",
        "description": "Find, activate, move, resize, snap, minimize, or maximize windows."
    },

    # App launch (SAFE)
    "app": {
        "risk": SAFE,
        "category": "app_launch",
        "description": "Launch Windows applications by name or executable path."
    },

    # Clipboard (get is SAFE, set/clear is MEDIUM)
    "clipboard": {
        "risk": SAFE,
        "category": "clipboard",
        "description": "Read text from the Windows clipboard, or write text to it."
    },

    # UI actions and input in applications (MEDIUM)
    "ui_click": {
        "risk": MEDIUM,
        "category": "ui_action",
        "description": "Click or double-click an element discovered by ui_find or ui_snapshot."
    },
    "ui_type": {
        "risk": MEDIUM,
        "category": "ui_action",
        "description": "Type text into an input field discovered by ui_find or ui_snapshot."
    },
    "ui_select": {
        "risk": MEDIUM,
        "category": "ui_action",
        "description": "Select an item in a combo box, dropdown list, or tab control."
    },
    "keyboard_control": {
        "risk": MEDIUM,
        "category": "ui_action",
        "description": "Send keyboard shortcuts, keystrokes, or key combinations."
    },
    "mouse_control": {
        "risk": MEDIUM,
        "category": "ui_action",
        "description": "Simulate mouse move, click, drag, or scroll."
    },
}

# Explicitly Denied Tools (denied by default)
DENIED_TOOLS: Dict[str, str] = {
    "process": "Process management and process termination are denied by default.",
    "file_save": "Direct file save dialog manipulation is denied by default.",
    "file_open": "Direct file open dialog manipulation is denied by default.",
    "ui_batch": "Batch command execution without per-step validation is denied by default.",
    "powershell": "Arbitrary shell execution is denied by default.",
    "powershell_tool": "PowerShell execution is denied by default.",
    "registry_tool": "Registry modification is denied by default.",
    "process_tool": "Process control is denied by default.",
}

CLEAN_ERROR_MESSAGE = "Windows automation service is unavailable. Please check MCP server installation."


# --------------------------------------------------------------------------- McpClient Class

class WindowsMcpClient:
    """Client for communicating with the Windows MCP server over stdio."""

    def __init__(self, exe_path: Optional[str] = None):
        self.exe_path = exe_path or os.environ.get("HEYJEV_WINDOWS_MCP_EXE") or DEFAULT_EXE_PATH
        self.proc: Optional[subprocess.Popen] = None
        self._initialized = False
        self._available = False
        self._error_message = ""
        self._cached_tools: List[Dict[str, Any]] = []
        self._cached_tool_names: set[str] = set()
        self._id_counter = 1
        self._reader: Optional[McpProcessReader] = None
        self._lock = threading.RLock()

    def is_available(self) -> bool:
        """Returns True if the MCP server is installed and running."""
        with self._lock:
            return self._available and (self.proc is not None) and (self.proc.poll() is None)

    def get_error_message(self) -> str:
        """Returns clean user-facing error message if server is unavailable."""
        return self._error_message or CLEAN_ERROR_MESSAGE

    def start(self, timeout_s: float = 8.0) -> bool:
        """Start the MCP server subprocess over stdio and complete handshake.
        
        Returns True if handshake succeeds, False otherwise.
        On failure, sets a single clean error message and stays off.
        """
        with self._lock:
            if self.is_available():
                return True

            if not self.exe_path or not os.path.exists(self.exe_path):
                self._available = False
                self._error_message = CLEAN_ERROR_MESSAGE
                return False

            try:
                self.proc = subprocess.Popen(
                    [self.exe_path],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    bufsize=0
                )
                self._reader = McpProcessReader(self.proc.stdout)
            except Exception:
                self._available = False
                self._error_message = CLEAN_ERROR_MESSAGE
                return False

            # Complete JSON-RPC 2.0 handshake
            try:
                init_id = self._next_id()
                self._send_raw({
                    "jsonrpc": "2.0",
                    "id": init_id,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "hey-jev-client", "version": "1.0.0"}
                    }
                })

                init_resp = self._read_raw(timeout_s=timeout_s)
                if not init_resp or init_resp.get("id") != init_id or "result" not in init_resp:
                    self.stop()
                    self._available = False
                    # Name the real cause. A generic "service is unavailable"
                    # previously masked a stale global cancel flag, which made
                    # three integration tests fail for a reason three files
                    # away and cost a long debugging detour. Saying "cancelled"
                    # would have made it obvious immediately.
                    if is_cancel_requested():
                        self._error_message = (
                            "MCP server start aborted: a cancellation is active. "
                            "This is an application-state problem, not a broken "
                            "MCP installation."
                        )
                    elif self.proc is not None and self.proc.poll() is not None:
                        self._error_message = (
                            "MCP server process exited during handshake "
                            f"(exit code {self.proc.returncode}). "
                            "Please check the MCP server installation."
                        )
                    else:
                        self._error_message = CLEAN_ERROR_MESSAGE
                    return False

                # Initialized notification
                self._send_raw({
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {}
                })

                # Query tools list to discover exposed schemas
                tools_id = self._next_id()
                self._send_raw({
                    "jsonrpc": "2.0",
                    "id": tools_id,
                    "method": "tools/list",
                    "params": {}
                })

                tools_resp = self._read_raw(timeout_s=timeout_s)
                raw_tools = []
                if tools_resp and "result" in tools_resp and "tools" in tools_resp["result"]:
                    raw_tools = tools_resp["result"]["tools"]

                # Filter tools strictly by ALLOWED_TOOLS
                self._cached_tools = []
                self._cached_tool_names = set()
                for tool in raw_tools:
                    name = tool.get("name")
                    if name in ALLOWED_TOOLS:
                        self._cached_tools.append(tool)
                        self._cached_tool_names.add(name)

                self._initialized = True
                self._available = True
                self._error_message = ""
                return True

            except Exception:
                self.stop()
                self._available = False
                self._error_message = CLEAN_ERROR_MESSAGE
                return False

    def stop(self) -> None:
        """Terminate the server process and reset state."""
        with self._lock:
            self._available = False
            self._initialized = False
            if self._reader is not None:
                try:
                    self._reader.stop()
                except Exception:
                    pass
                self._reader = None
            if self.proc is not None:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=2.0)
                except Exception:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass
                self.proc = None

    def _next_id(self) -> int:
        cur = self._id_counter
        self._id_counter += 1
        return cur

    def _send_raw(self, msg: Dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("Process stdin is not open")
        payload = json.dumps(msg) + "\n"
        self.proc.stdin.write(payload)
        self.proc.stdin.flush()

    def _read_raw(self, timeout_s: float = 10.0) -> Optional[Dict[str, Any]]:
        if not self.proc or not self.proc.stdout:
            return None

        t_start = time.perf_counter()
        # If reader worker thread is active, read from queue without blocking
        if self._reader is not None:
            while time.perf_counter() - t_start < timeout_s:
                if is_cancel_requested():
                    return None
                try:
                    line = self._reader.queue.get(timeout=0.05)
                    if line:
                        try:
                            return json.loads(line)
                        except Exception:
                            return None
                except queue.Empty:
                    pass
            return None

        # Fallback for mocked proc objects without thread
        while time.perf_counter() - t_start < timeout_s:
            if is_cancel_requested():
                return None
            line = self.proc.stdout.readline()
            if line:
                try:
                    return json.loads(line)
                except Exception:
                    return None
            time.sleep(0.02)
        return None

    def get_openai_tool_specs(self) -> List[Dict[str, Any]]:
        """Return allowed tools formatted for OpenAI/OpenRouter tool_calls."""
        with self._lock:
            specs = []
            for t in self._cached_tools:
                name = t.get("name")
                desc = ALLOWED_TOOLS.get(name, {}).get("description", t.get("description", ""))
                schema = t.get("inputSchema", {"type": "object", "properties": {}})
                specs.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": desc,
                        "parameters": schema
                    }
                })
            return specs

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None, bypass_confirmation: bool = False) -> str:
        """Execute a tool through the MCP server with strict safety gating.
        
        Enforces:
          1. Global cancel check before execution
          2. Tool allowlist validation (denied by default)
          3. App launch target verification (blocks shells, admin tools, pw managers)
          4. Shortcut safety verification (blocks Win+R/Win+X, requires yes for Alt+F4/Ctrl+W)
          5. Safety Engine risk classification (MEDIUM for typing/clicks, SAFE for read)
          6. Foreground window check before every MEDIUM/HIGH action (blocks shells/admin, confirms sensitive login/bank)
          7. Preview mode (prompts before sending clicks, typing, or keys when enabled)
          8. Output sanitization & prompt injection quarantining (<MCP_DATA>)
          9. Action logging to actions.jsonl
        """
        arguments = arguments or {}

        # 1. Global Cancel Check
        if is_cancel_requested():
            return "Action cancelled."

        # 2. Allowlist Check (Deny by default)
        if name not in ALLOWED_TOOLS:
            reason = DENIED_TOOLS.get(name, f"Tool '{name}' is not in the allowed tools list (denied by default).")
            safety_engine.log_action(3, name, arguments, BLOCKED, f"blocked: {reason}")
            return f"Blocked: {reason}"

        # 3. App Launch Security Gating
        if name == "app":
            target = arguments.get("programPath") or arguments.get("name") or arguments.get("target") or ""
            safe, app_reason = safety_engine.check_app_launch_safety(str(target))
            if not safe:
                safety_engine.log_action(3, name, arguments, BLOCKED, f"blocked: {app_reason}")
                return f"Action 'app' blocked by safety policy: {app_reason}"

        # 4. Shortcut Safety Gating
        if name in ("keyboard_control",):
            sec_status, sec_reason = safety_engine.check_shortcut_safety(name, arguments)
            if sec_status == "BLOCKED":
                safety_engine.log_action(3, name, arguments, BLOCKED, f"blocked: {sec_reason}")
                return f"Action '{name}' blocked by safety policy: {sec_reason}"
            if sec_status == "REQUIRE_YES" and not bypass_confirmation:
                desc = f"{sec_reason} " + safety_engine.format_action_preview_description(name, arguments)
                cm = safety_engine.get_confirmation_manager()
                cm.request_high_risk_confirmation(
                    action=name,
                    message=desc,
                    execute_fn=lambda: self.call_tool(name, arguments, bypass_confirmation=True),
                    timeout_s=safety_engine._CONFIRM_TIMEOUT_S
                )
                return desc

        # 5. Safety Engine Risk Gating
        risk_level, is_allowed = safety_engine.evaluate_risk(name, arguments)
        if not is_allowed or risk_level == BLOCKED:
            safety_engine.log_action(3, name, arguments, BLOCKED, "blocked by safety policy")
            return f"Action '{name}' blocked by safety policy."

        # Provider privacy check (free/training providers must never receive screenshots or window contents)
        try:
            from assistant_ui import get_settings
            configured_prov = get_settings().get("tier3_provider", "openrouter")
        except Exception:
            configured_prov = "openrouter"
        prov_safe, prov_reason = safety_engine.check_data_disclosure_safety(name, configured_prov)
        if not prov_safe:
            safety_engine.log_action(3, name, arguments, BLOCKED, f"blocked: {prov_reason}")
            return f"Blocked: {prov_reason}"

        # Spoken disclosure and confirmation for screenshot and ui_read on other windows
        is_other_window_read = (name == "ui_read" and bool(arguments.get("window") or arguments.get("windowName") or arguments.get("target") or arguments.get("other_window")))
        if (name == "screenshot_control" or is_other_window_read) and not bypass_confirmation:
            desc = safety_engine.format_action_preview_description(name, arguments)
            cm = safety_engine.get_confirmation_manager()
            cm.request_high_risk_confirmation(
                action=name,
                message=desc,
                execute_fn=lambda: self.call_tool(name, arguments, bypass_confirmation=True),
                timeout_s=safety_engine._CONFIRM_TIMEOUT_S
            )
            return desc

        # 6. Foreground Window Check before every MEDIUM or HIGH action
        is_input_or_mutation = name in ("ui_click", "ui_type", "ui_select", "keyboard_control", "mouse_control") or (name == "clipboard" and arguments.get("action") in ("set", "clear"))
        if is_input_or_mutation:
            proc_name, title = safety_engine.get_foreground_window_info()
            fw_allowed, fw_status, fw_reason = safety_engine.check_foreground_window_safety(proc_name, title)
            if not fw_allowed or fw_status == "BLOCKED":
                safety_engine.log_action(3, name, arguments, BLOCKED, f"blocked: {fw_reason}")
                return f"Action '{name}' blocked by safety policy: {fw_reason}"
            if fw_status == "REQUIRE_YES" and not bypass_confirmation:
                desc = f"Window '{title}' is a sensitive page. " + safety_engine.format_action_preview_description(name, arguments)
                cm = safety_engine.get_confirmation_manager()
                cm.request_high_risk_confirmation(
                    action=name,
                    message=desc,
                    execute_fn=lambda: self.call_tool(name, arguments, bypass_confirmation=True),
                    timeout_s=safety_engine._CONFIRM_TIMEOUT_S
                )
                return desc

        # 7. Preview Mode (prompt-before-action for UI inputs)
        if safety_engine.is_preview_mode_enabled() and not bypass_confirmation:
            if is_input_or_mutation:
                desc = safety_engine.format_action_preview_description(name, arguments)
                cm = safety_engine.get_confirmation_manager()
                cm.request_high_risk_confirmation(
                    action=name,
                    message=desc,
                    execute_fn=lambda: self.call_tool(name, arguments, bypass_confirmation=True),
                    timeout_s=safety_engine._CONFIRM_TIMEOUT_S
                )
                return desc

        # 8. Check Server Availability
        if not self.is_available():
            # Attempt lazy start once
            started = self.start()
            if not started:
                safety_engine.log_action(3, name, arguments, risk_level, "failed: server unavailable")
                return self.get_error_message()

        # 5. Cancellation check right before dispatch
        if is_cancel_requested():
            return "Action cancelled."

        # 6. Dispatch over stdio
        call_id = self._next_id()
        msg = {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments
            }
        }

        with self._lock:
            try:
                self._send_raw(msg)
                resp = self._read_raw(timeout_s=10.0)
            except Exception as exc:
                safety_engine.log_action(3, name, arguments, risk_level, f"failed: {exc}")
                return f"Tool execution failed: {exc}"

        if is_cancel_requested():
            return "Action cancelled."

        if not resp:
            safety_engine.log_action(3, name, arguments, risk_level, "timeout")
            return f"Tool '{name}' timed out without response."

        if "error" in resp:
            err = resp["error"].get("message", "unknown error")
            safety_engine.log_action(3, name, arguments, risk_level, f"error: {err}")
            return f"Tool error: {err}"

        res_obj = resp.get("result", {})
        is_error = res_obj.get("isError", False)
        content_items = res_obj.get("content", [])
        
        output_parts = []
        for item in content_items:
            if isinstance(item, dict):
                output_parts.append(str(item.get("text", "")))
            else:
                output_parts.append(str(item))

        raw_output = "\n".join(output_parts).strip() if output_parts else "Success."

        # 7. Audit log action to actions.jsonl
        result_status = "failed" if is_error else "success"
        safety_engine.log_action(3, name, arguments, risk_level, result_status)

        # 8. Prompt Injection Quarantine: wrap all external data in <MCP_DATA> tags
        quarantined = sanitize_data_content(raw_output, "MCP_DATA")
        return quarantined


# --------------------------------------------------------------------------- Singleton & Global Access

_GLOBAL_MCP_CLIENT: Optional[WindowsMcpClient] = None
_CLIENT_LOCK = threading.Lock()

def get_mcp_client(exe_path: Optional[str] = None) -> WindowsMcpClient:
    """Retrieve or create the global WindowsMcpClient instance."""
    global _GLOBAL_MCP_CLIENT
    with _CLIENT_LOCK:
        if _GLOBAL_MCP_CLIENT is None:
            _GLOBAL_MCP_CLIENT = WindowsMcpClient(exe_path=exe_path)
        return _GLOBAL_MCP_CLIENT

def init_mcp_client(exe_path: Optional[str] = None) -> WindowsMcpClient:
    """Explicitly initialize and start the global MCP client."""
    client = get_mcp_client(exe_path)
    client.start()
    return client

def shutdown_mcp_client() -> None:
    """Stop the global MCP client subprocess if active."""
    global _GLOBAL_MCP_CLIENT
    with _CLIENT_LOCK:
        if _GLOBAL_MCP_CLIENT is not None:
            _GLOBAL_MCP_CLIENT.stop()
            _GLOBAL_MCP_CLIENT = None
