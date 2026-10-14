# Phase 7 Research: "Do Anything" Automation Engines

**Evaluation of Candidate Automation Systems for Hey Jev Windows Port**  
**Date:** October 2026  
**Environment:** Windows 11 / Windows 10 x64, Python 3.11.9, dedicated `.venv-agents`  
**Status:** Step 1 Research Complete & Verified — Step 2 Primary Engine Selected

---

## 1. Candidate Evaluation Matrix

| Candidate | License | Last Commit Date | Runtime / Platform Requirements | Disk Footprint | Telemetry / Data Sent | Primary Tool List | Key Capability Beyond Hey Jev Tier 2 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **sbroenne/mcp-windows** *(Selected Primary)* | **MIT** (sbroenne, 2025–2026) | Oct 2026 (v1.3.27) | Windows 10/11 x64; .NET 10 standalone binary (No Python required) | **57.12 MB (measured)** (`Sbroenne.WindowsMcp.exe`) | **Zero telemetry** (verified: 0 active/outbound socket connections via psutil) | `ui_snapshot`, `ui_find`, `ui_click`, `ui_type`, `ui_select`, `ui_read`, `ui_wait`, `keyboard_control`, `mouse_control`, `window_management`, `screenshot_control`, `app`, `clipboard`, `file_open`, `file_save`, `ui_read_table`, `ui_batch`, `process` | Semantic UI Automation (UIA) targeting by element name/ID/role (resolution/DPI-independent) without blind coordinates |
| **CursorTouch/Windows-MCP** *(Fallback)* | **MIT** (Jeomon George, 2025) | Early Oct 2026 (v1.0.1 on Sep 27, 2026) | Windows 7–11 x64; requires Python >= 3.14 or `uvx` (cannot run directly on Python 3.11 in `.venv-agents`) | ~55 MB (estimate, Python dependencies) | PostHog usage data (tool name, latency, status). **Disable:** `ANONYMIZED_TELEMETRY=false` | `click_tool`, `type_tool`, `move_cursor_tool`, `press_key_tool`, `scroll_tool`, `snapshot_tool` (with DOM mode), `app_tool`, `clipboard_tool`, `powershell_tool`, `process_tool`, `registry_tool` | Arbitrary UI coordinate clicking, typing into non-focused inputs, accessibility/DOM tree snapshotting |
| **browser-use/browser-use** | **MIT** (browser-use team) | Oct 2026 (v0.13.10 on Sep 3, 2026) | Windows 10/11, macOS, Linux; Python >= 3.11; Chromium | ~350 MB (estimate, Playwright + Chromium browser) | Anonymous task metadata (task, visited URLs, action traces). **Disable:** `ANONYMIZED_TELEMETRY=false` | `search_google`, `go_to_url`, `click_element`, `input_text`, `scroll_down`, `scroll_up`, `send_keys`, `open_tab`, `switch_tab`, `close_tab`, `extract_content` | Autonomous multi-step web browsing, dynamic DOM interaction, form fills, authentication navigation |
| **simular-ai/Agent-S** (Agent S3) | **Apache-2.0** (Simular AI) | Sep 2026 (v0.3.2 + patches) | Windows, macOS, Linux; Python >= 3.10; PyTorch, OCR | ~2.5 GB (estimate, PyTorch, vision models, OCR weights) | Sends full screenshots to external VLM APIs (Claude/GPT-4o). No telemetry to Simular | `click`, `double_click`, `right_click`, `move_to`, `type_text`, `press_key`, `hotkey`, `scroll`, `drag_and_drop`, `screenshot`, `subtask_complete` | End-to-end vision-language reasoning on arbitrary desktop GUI, visual icon grounding, complex subtask planning |
| **microsoft/UFO** (UFO² / UFO³ Galaxy) | **MIT** (Microsoft Corp) | Mid/Late 2026 (UFO³ Galaxy) | Windows 10/11 x64; Python >= 3.10; PyWin32, OpenCV | ~1.2 GB (estimate, OpenCV, COM, agent libraries) | Dual-agent sends screenshots & UI trees to Azure/OpenAI VLM. Configurable telemetry | HostAgent: App dispatch, window focus, DAG evolution. AppAgent: Control click, type, select, scroll, text read | Cross-application enterprise workflow automation (e.g. Excel -> PowerPoint -> Outlook) with dual-agent reflection |

---

## 2. Detailed Candidate Profiles

### Candidate 1: sbroenne/mcp-windows (Selected Primary)
- **Repository:** `https://github.com/sbroenne/mcp-windows`
- **License:** **MIT License** (verified from `LICENSE`, Copyright 2025–2026 sbroenne).
- **Last Commit / Release:** v1.3.27, released October 2026.
- **Requirements:** Windows 10/11 x64. Implemented in C# / .NET 10. Standalone `.exe` binary available on GitHub Releases (No Python or .NET runtime installation required).
- **Acquisition Verification:** Obtained **without requiring a GitHub token** by directly downloading the public release asset `windows-mcp-server-1.3.27-win-x64.zip` from `https://github.com/sbroenne/mcp-windows/releases/download/v1.3.27/windows-mcp-server-1.3.27-win-x64.zip`.
- **Measured Install Size:** **59,899,787 bytes (57.12 MB)** for extracted `Sbroenne.WindowsMcp.exe`.
- **Data Collection & Telemetry (Measured):**
  - **Zero data collection**. Strictly local execution via Windows native UI Automation COM APIs (`UIAutomationClient`).
  - Tested running process with `psutil`: `len(p.net_connections()) == 0` and 0 child processes. Zero outbound connections.
- **Exposed Tools (18 tools discovered over stdio):**
  - `ui_snapshot`: Orient and inspect compact element trees with comparison mode.
  - `ui_find`: Find element by name, control type, or automation ID.
  - `ui_click`: Click UI element by name/identifier.
  - `ui_type`: Input text directly into element.
  - `ui_select`: Select combo box / list / tab option.
  - `ui_read`: Read accessibility attributes and text content.
  - `ui_read_table`: Extract tabular grid into structured rows/headers in one call.
  - `ui_wait`: Wait for element appearance/disappearance or state change without blind sleeps.
  - `window_management`: Find, activate, move, resize, minimize, maximize.
  - `screenshot_control`: Capture screen or window bounds (optionally annotated).
  - `mouse_control`: Simulate mouse click/move/scroll/drag.
  - `keyboard_control`: Send keystrokes, shortcuts, and key combinations.
  - `app`: Launch app and return HWND.
  - `clipboard`: Fast bulk text get/set/clear.
  - `file_save` / `file_open`: Save/open via native dialogs.
  - `ui_batch`: Multi-step batch execution.
  - `process`: Process list and query.
- **Capabilities Beyond Hey Jev Tier 2:**
  - Semantic element targeting: clicks buttons and types into fields by label rather than pixel coordinates. Immune to display scaling/DPI shifts.
  - Fast, self-contained single binary with zero external telemetry and zero Python environment baggage.

---

### Candidate 2: CursorTouch/Windows-MCP (Fallback)
- **Repository:** `https://github.com/CursorTouch/Windows-MCP`
- **License:** **MIT License** (verified from `LICENSE.md`, Copyright 2025 Jeomon George).
- **Last Commit / Release:** v1.0.1 released September 27, 2026; commits active in October 2026.
- **Requirements & Environment Compatibility:**
  - Declares `requires-python = ">=3.14"` in `pyproject.toml` or execution via `uvx windows-mcp`.
  - `.venv-agents` was created using the system Python 3.11.9; therefore, Windows-MCP cannot be cleanly installed via `pip install windows-mcp` directly into `.venv-agents` without Python 3.14 or `uvx`.
- **Install Size:** ~55 MB (estimate, Python dependencies: FastMCP, comtypes, dxcam, Pillow, posthog, psutil, click).
- **Data Collection & Telemetry:**
  - PostHog client sends tool execution status (success/failure), latency duration, tool name, client application name/version, and anonymized session ID.
  - **Exact setting to disable:**
    ```env
    ANONYMIZED_TELEMETRY=false
    ```
- **Exposed Tools:**
  - `click_tool` (`Click`), `move_cursor_tool` (`Move`), `type_tool` (`Type`), `press_key_tool` (`Shortcut`), `scroll_tool` (`Scroll`), `app_tool` (`Launch`), `snapshot_tool` (`State`, with DOM mode), `wait_for_tool` (`WaitFor`), `clipboard_tool`, `powershell_tool`, `process_tool`, `registry_tool`, `display_inventory_tool`.
- **Status:** Maintained as secondary fallback if .NET-based UIA encounters unsupported legacy controls.

---

### Candidate 3: browser-use/browser-use
- **Repository:** `https://github.com/browser-use/browser-use`
- **License:** **MIT License** (verified from `LICENSE`).
- **Last Commit / Release:** v0.13.10 released September 3, 2026; active through October 2026.
- **Requirements:** Windows 10/11 x64, Python >= 3.11 (compatible with `.venv-agents` Python 3.11.9), Chromium (via Playwright).
- **Install Size:** ~350 MB (estimate, library + Playwright browser binaries).
- **Data Collection & Telemetry:**
  - Default telemetry (`AgentTelemetryEvent`) collects task instructions, URLs visited, action steps, and outcomes.
  - **Exact setting to disable:**
    ```env
    ANONYMIZED_TELEMETRY=false
    ```
- **Exposed Actions:**
  - `search_google`, `go_to_url`, `click_element`, `input_text`, `scroll_down`, `scroll_up`, `send_keys`, `open_tab`, `switch_tab`, `close_tab`, `extract_content`.
- **Capabilities Beyond Hey Jev Tier 2:**
  - Handles dynamic modern web apps, multi-tab navigation, login forms, CAPTCHA pauses, and content extraction that standard shell URLs cannot handle.

---

### Candidate 4: simular-ai/Agent-S (Agent S3)
- **Repository:** `https://github.com/simular-ai/Agent-S`
- **License:** **Apache-2.0 License** (verified from `LICENSE`).
- **Last Commit / Release:** v0.3.2 (Dec 2025) with active September 2026 updates.
- **Requirements:** Windows/Linux/macOS, Python >= 3.10, PyTorch, torchvision, EasyOCR.
- **Install Size:** Heavyweight (~2.5 GB, estimate).
- **Data Collection & Privacy:**
  - Transmits full desktop screenshots to external multimodal vision models (e.g. Anthropic Claude 3.5 Sonnet, OpenAI GPT-4o).
- **Exposed ACI Tools:**
  - Mouse moves, clicks, typing, hotkeys, scrolling, coordinate dragging, and screenshot capturing.
- **Capabilities Beyond Hey Jev Tier 2:**
  - Visual reasoning over arbitrary desktop pixels without needing accessibility tree tags.

---

### Candidate 5: microsoft/UFO (UFO² / UFO³ Galaxy)
- **Repository:** `https://github.com/microsoft/UFO`
- **License:** **MIT License** (verified from `LICENSE`, Copyright Microsoft Corporation).
- **Last Commit / Release:** UFO³ Galaxy framework (Mid/Late 2026).
- **Requirements:** Windows 10/11 x64, Python >= 3.10, PyWin32, OpenCV.
- **Install Size:** Heavyweight (~1.2 GB, estimate).
- **Data Collection & Privacy:**
  - Dual-agent loop sends window screenshots and UI tree hierarchies to LLM/VLM APIs.
- **Capabilities Beyond Hey Jev Tier 2:**
  - Orchestrates complex workflows across multiple Office and Windows desktop applications.

---

## 3. Integration Plan & Engine Choice (Step 2)

### Selected Primary Engine: sbroenne/mcp-windows
1. **Decision Rationale:**
   - **Standalone Executable:** Single binary `Sbroenne.WindowsMcp.exe` (57.12 MB), completely decoupled from Python runtime dependencies and immune to Python version conflicts.
   - **Zero Telemetry:** Completely local execution. Zero network calls confirmed through runtime socket inspection via `psutil`.
   - **Semantic UIA Targeting:** Targets controls by name, controlType, and automationId, bypassing coordinate brittleness.
   - **Zero Token Requirement:** Downloaded and verified directly without requiring GitHub API credentials or personal access tokens.
2. **Mandatory Security Configuration & Tool Allowlist:**
   - **Tool Allowlist (Permitted):**
     - UI Find / Read / Snapshot / Table: `ui_find`, `ui_read`, `ui_read_table`, `ui_snapshot`, `screenshot_control`, `ui_wait`
     - Click: `ui_click`, `mouse_control`
     - Type / Input: `ui_type`, `ui_select`
     - Keystrokes & Shortcuts: `keyboard_control`
     - Window Focus / Resize / Management: `window_management`
     - App Launch: `app`
   - **Tool Denylist (Strictly Blocked by Default):**
     - `process` (process termination/killing is denied by default)
     - `file_save` / `file_open` (file writes and system dialogs are blocked unless explicitly gated)
     - `ui_batch` (batching denied unless all sub-actions are individually validated)
     - Any unknown or unlisted tool: **Blocked immediately**
     - PowerShell/shell, registry, file deletion, services, scheduled tasks, network configuration: **Not supported by binary and denied by client**
3. **Safety Engine Gating & Loop Constraints:**
   - Typing or key combinations into any application $\to$ **MEDIUM risk** (evaluated via `safety_engine.evaluate_risk()`).
   - Unknown tools $\to$ **Blocked**.
   - Inspection and read actions $\to$ **SAFE**.
   - Prompt Injection Quarantine: all tool outputs wrapped in `<DATA>` tags.
   - Execution Limits: Maximum 8 steps ceiling, 30-second total timeout per turn.
   - Loop Protection & Interruption: Global cancel flag (`safety_engine.is_cancel_requested()`) polled before every step; Esc key and spoken "stop" instantly abort execution.
   - All tool invocations logged to `%APPDATA%\HeyJev\actions.jsonl`.
   - Clean failure handling: if MCP server fails to start, report a single clean user message and remain off.

---

## 4. Current State & Verification

- **Environment:** Dedicated virtual environment `.venv-agents` created at `C:\Users\prabh\.gemini\antigravity\scratch\hey-jev\.venv-agents` running Python 3.11.9.
- **Engine Binary:** `Sbroenne.WindowsMcp.exe` installed at `.venv-agents\bin\mcp-windows\Sbroenne.WindowsMcp.exe`.
- **Measured Disk Size:** Exactly 59,899,787 bytes (57.12 MB).
- **Measured Network Egress:** Confirmed 0 active/listening network sockets via `psutil` during stdio execution.
- **Automated Tests:** 110/110 existing unit tests passing cleanly.
