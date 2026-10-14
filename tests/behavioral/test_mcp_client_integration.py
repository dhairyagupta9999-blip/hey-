"""MCP client — real integration against the Windows MCP server.

The MCP client is the path to "open Notepad, see what's on screen, type into
it" — semantic Windows UI Automation via sbroenne/mcp-windows, not blind
coordinate clicking.

These tests exercise the real client object: real allowlist, real risk
classification, real safety-engine gating, real subprocess launch. Nothing
here is mocked.

The server binary is OPTIONAL and is not redistributable with this repo. When
it is absent these tests xfail with an actionable message rather than skipping
or silently passing -- an absent capability must never look like a pass.

Drop the binary at mcp/Sbroenne.WindowsMcp.exe to run them for real.
"""

from __future__ import annotations

import json
import os
import re

import pytest

# A unique marker so the read-back assertion cannot pass on whatever text
# happened to already be in the Notepad buffer.
_UNIQUE_PROBE = "HEYJEV_UIA_PROOF_7391"


def _coerce_handle(value):
    """The server returns window handles as STRINGS ("525350"), not ints."""
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _extract_window_handle(result: str):
    """Pull the window handle out of a window_management result.

    Measured against server v1.3.28: `find` returns
    {"success":true,"windows":[{"handle":"<digits>","title":...}],"count":N}
    and `list` returns a similar shape. Handles are digit STRINGS.
    """
    if not isinstance(result, str):
        return None

    # Every tool result is wrapped in <MCP_DATA>...</MCP_DATA> by the client's
    # prompt-injection quarantine, which is why raw json.loads() fails on it.
    stripped = result
    if "<MCP_DATA>" in stripped:
        stripped = stripped.split("<MCP_DATA>", 1)[1].split("</MCP_DATA>", 1)[0]

    try:
        data = json.loads(stripped)
    except (ValueError, TypeError):
        m = re.search(r'"?(?:windowHandle|handle|hwnd)"?\s*[:=]\s*"?(\d+)', stripped)
        return int(m.group(1)) if m else None

    if not isinstance(data, dict):
        return _coerce_handle(data)

    for key in ("windowHandle", "handle", "hwnd"):
        if key in data:
            got = _coerce_handle(data[key])
            if got:
                return got

    windows = data.get("windows")
    if isinstance(windows, list) and windows:
        # Prefer the foreground window when the server reports several.
        ordered = sorted(windows, key=lambda w: not (isinstance(w, dict) and w.get("isForeground")))
        for window in ordered:
            if isinstance(window, dict):
                got = _coerce_handle(window.get("handle") or window.get("windowHandle"))
                if got:
                    return got

    for nested in data.values():
        if isinstance(nested, dict):
            got = _extract_window_handle(json.dumps(nested))
            if got:
                return got
    return None


@pytest.fixture
def client():
    import mcp_client
    c = mcp_client.WindowsMcpClient()
    yield c
    c.stop()


# --------------------------------------------------------------------------
# Configuration and allowlist (always run -- no binary required)
# --------------------------------------------------------------------------

def test_env_override_beats_every_discovered_path():
    """$HEYJEV_WINDOWS_MCP_EXE must win -- it is documented as step 1.

    An earlier version asserted `DEFAULT_EXE_PATH in _candidate_exe_paths()`,
    which cannot fail: _resolve_exe_path() returns either a member of that list
    or candidates[0]. It also never pinned the override, because the env var is
    read in __init__ and is deliberately NOT in the candidate list.
    """
    import os

    import mcp_client

    sentinel = r"C:\some\other\place\Sbroenne.WindowsMcp.exe"
    saved = os.environ.get("HEYJEV_WINDOWS_MCP_EXE")
    try:
        os.environ["HEYJEV_WINDOWS_MCP_EXE"] = sentinel
        assert mcp_client.WindowsMcpClient().exe_path == sentinel, (
            "the environment override was ignored"
        )
    finally:
        if saved is None:
            os.environ.pop("HEYJEV_WINDOWS_MCP_EXE", None)
        else:
            os.environ["HEYJEV_WINDOWS_MCP_EXE"] = saved


def test_documented_drop_location_is_searched_first():
    """mcp/ at the repo root is the documented location, and it is searched."""
    import mcp_client

    candidates = mcp_client._candidate_exe_paths()
    assert candidates, "no candidate paths defined"
    assert os.path.basename(candidates[0]) == mcp_client._MCP_BINARY_NAME
    assert os.path.basename(candidates[0]) == "Sbroenne.WindowsMcp.exe"
    assert os.path.basename(os.path.dirname(candidates[0])) == "mcp"


def test_allowlist_covers_the_ui_automation_surface():
    """The tools that make UI automation useful must be allowlisted."""
    import mcp_client

    required = {"ui_snapshot", "ui_find", "ui_read", "ui_click", "ui_type",
                "ui_wait", "window_management", "app", "keyboard_control",
                "clipboard"}
    missing = required - set(mcp_client.ALLOWED_TOOLS)
    assert not missing, f"UI automation tools missing from allowlist: {missing}"


def test_dangerous_tools_are_denied_by_default():
    """Denial is the security property; it must not regress."""
    import mcp_client

    forbidden = ("powershell", "shell", "exec", "registry", "reg",
                 "file_write", "file_save", "process_kill", "service")
    leaked = [
        name for name in mcp_client.ALLOWED_TOOLS
        if any(bad in name.lower() for bad in forbidden)
    ]
    assert not leaked, (
        f"dangerous tools present in the allowlist: {leaked}. PowerShell, "
        "registry and file-write access must stay denied by default."
    )


def test_actions_are_risk_gated():
    """Typing and clicking are MEDIUM, not SAFE -- they change the world."""
    import mcp_client

    assert mcp_client.ALLOWED_TOOLS["ui_click"]["risk"] != "SAFE"
    assert mcp_client.ALLOWED_TOOLS["ui_type"]["risk"] != "SAFE"
    assert mcp_client.ALLOWED_TOOLS["ui_snapshot"]["risk"] == "SAFE"
    assert mcp_client.ALLOWED_TOOLS["ui_find"]["risk"] == "SAFE"


# --------------------------------------------------------------------------
# Real server (requires the optional binary)
# --------------------------------------------------------------------------

def _require_binary(client):
    import os

    if not os.path.exists(client.exe_path):
        pytest.xfail(
            f"Windows MCP server binary not installed at {client.exe_path}. "
            "Drop Sbroenne.WindowsMcp.exe in mcp/ (see README) to run this."
        )


def test_server_starts_and_lists_tools(client):
    """Real handshake against the real binary."""
    _require_binary(client)

    started = client.start(timeout_s=20.0)
    assert started, f"server failed to start: {client.get_error_message()}"
    assert client.is_available()

    # Real API: the client caches the server's tool list at handshake and
    # exposes it as OpenAI-style specs. An earlier version of this test called
    # a guessed list_tools() and only discovered the mistake once the binary
    # was actually present -- it had xfailed silently until then.
    specs = client.get_openai_tool_specs()
    assert specs, "server reported no tools"
    names = {s["function"]["name"] for s in specs}
    assert "ui_snapshot" in names, f"expected ui_snapshot, got {sorted(names)}"


def _strip_mcp_data(result):
    """Remove the client's <MCP_DATA> prompt-injection quarantine wrapper."""
    if not isinstance(result, str):
        return None
    if "<MCP_DATA>" in result:
        return result.split("<MCP_DATA>", 1)[1].split("</MCP_DATA>", 1)[0]
    return result


def _extract_element_id(result: str):
    """Pull an elementId out of a ui_find result.

    Measured against server v1.3.28: ui_find returns its matches under the key
    `items` (not `elements` or `results`), and each item carries an opaque
    elementId that ui_type requires.
    """
    body = _strip_mcp_data(result)
    if not body:
        return None
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    for key in ("items", "elements", "results", "matches"):
        bucket = data.get(key)
        if isinstance(bucket, list):
            for item in bucket:
                if isinstance(item, dict):
                    for id_key in ("elementId", "element_id", "id"):
                        if item.get(id_key):
                            return item[id_key]
    for value in data.values():
        if isinstance(value, dict):
            got = _extract_element_id(json.dumps(value))
            if got:
                return got
    return None


def _server_succeeded(result) -> bool:
    """call_tool() returns a *string*. Detect real success.

    The earlier version of this test asserted only `result is not None`, which
    is satisfied by "Blocked: ...". It therefore PASSED VACUOUSLY against a
    call that could not have worked -- the server's `app` tool takes
    `programPath`, not `name`. A test that cannot fail is a comment with a
    runtime cost, so success is now asserted explicitly.
    """
    if not isinstance(result, str):
        return False
    low = result.lower()
    if "blocked" in low or "error" in low or "should i proceed" in low:
        return False
    return '"success":true' in low.replace(" ", "") or "success: true" in low


def test_readonly_tool_runs_against_a_real_window(client):
    """The capability that matters: observe and control a real application.

    Opens Notepad through MCP, finds its text control, types a known string,
    and reads it back to confirm the text actually landed. This is the
    'open Notepad and understand what is happening' scenario, proven end to
    end.

    API notes, all established against the real server v1.3.28:
      * `app` takes `programPath`, not `name`.
      * `window_management` `find` returns a handle; `ui_find` / `ui_type` /
        `ui_read` all REQUIRE that handle, even though `ui_snapshot` works
        without one. Omitting it yields a server-side .NET exception.
      * Notepad's text control reports controlType "Document", not "Edit";
        searching for "Edit" burns a 5 s timeout.
      * `ui_find` returns its results under the key `items`.
    """
    _require_binary(client)

    assert client.start(timeout_s=20.0), client.get_error_message()

    try:
        launched = client.call_tool("app", {"programPath": "notepad.exe"})
        assert _server_succeeded(launched), f"Notepad did not launch: {launched!r}"

        found = client.call_tool("window_management", {"action": "find",
                                                       "processName": "Notepad"})
        assert _server_succeeded(found), f"could not find the Notepad window: {found!r}"
        handle = _extract_window_handle(found)
        assert handle, (
            f"no window handle in the find result; ui_find/ui_type/ui_read all "
            f"require one. Got: {found!r}"
        )
        # The schema is explicit: "Window handle as decimal string". Passing an
        # int makes ui_find throw a bare server-side .NET exception with no
        # diagnostic, which is what cost the first few runs of this test.
        handle_arg = str(handle)

        probe = _UNIQUE_PROBE

        # ui_type requires an elementId from discovery. Discover first.
        discovery = client.call_tool("ui_find", {"windowHandle": handle_arg})
        assert _server_succeeded(discovery), f"ui_find failed: {discovery!r}"
        element_id = _extract_element_id(discovery)
        assert element_id, (
            f"no elementId in the ui_find result; ui_type requires one. "
            f"Got: {discovery!r}"
        )

        typed = client.call_tool("ui_type", {
            "windowHandle": handle_arg,
            "elementId": element_id,
            "text": probe,
        }, bypass_confirmation=True)   # server preview mode asks first by design
        assert _server_succeeded(typed), f"ui_type failed: {typed!r}"

        read_back = client.call_tool("ui_read", {
            "windowHandle": handle_arg,
            "elementId": element_id,
        })
        assert _server_succeeded(read_back), f"ui_read failed: {read_back!r}"
        assert probe in read_back, (
            f"text was not found in Notepad after typing it -- the action "
            f"reported success but did not take effect. Got: {read_back!r}"
        )
    finally:
        try:
            client.call_tool("app", {"programPath": "notepad.exe", "action": "close"})
        except Exception:
            pass  # cleanup only


def test_denied_tool_is_refused_without_calling_the_server(client):
    """A denied tool must never reach the server, binary or not."""
    result = client.call_tool("powershell", {"command": "whoami"})

    assert isinstance(result, str), f"call_tool must return a string, got {type(result)}"
    lowered = result.lower()
    assert any(
        marker in lowered for marker in ("not allowed", "blocked", "denied",
                                         "not permitted", "allowlist", "refused")
    ), f"a denied tool did not produce a clear refusal: {result!r}"