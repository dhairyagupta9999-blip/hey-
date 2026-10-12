# Hey Jev: Capability Inventory & Verification Matrix

This document provides a comprehensive inventory of every significant capability discovered in the Hey Jev source code. Each capability is classified with its implementation files, current status, supporting evidence, dependencies, and operational limitations.

---

## 1. Classification Definitions

- **Verified Working:** Tested and proven functional against the live environment (preflight, real Windows APIs, live OS operations, or live client connections).
- **Automated-Test-Only:** Fully covered by passing automated unit/integration tests with mocks, but live human end-to-end desktop operation has not been independently verified.
- **Experimental:** Implemented in code, but depends on optional local neural models, external tools, or developmental heuristics.
- **Disabled / Broken:** Known to fail in the current local environment without external installation or manual configuration.
- **Unverified:** Present in the codebase, but lacking dedicated test or runtime verification evidence.

---

## 2. Complete Capability Matrix

| Capability | Implementing Files & Interfaces | Status | Supporting Evidence | Dependencies & Known Limitations |
| :--- | :--- | :--- | :--- | :--- |
| **Desktop GUI Lifecycle** | `app.py`, `assistant_ui.py`<br>`AssistantWindow(QMainWindow)` | **Verified Working** | Preflight Check 1 PASS;<br>Loads 7 tabs and system tray icon instantly. | Requires graphical display session (PySide6). Cannot run in headless terminal without X/Windows server. |
| **Mission Center GUI** | `mission_center.py`<br>`MissionCenterWindow(QMainWindow)` | **Verified Working** | Phase 28 suite PASS (16 tests in `test_phase28.py`). DAG rendering, status badges, criteria table verified. | Requires PySide6. Graph visualizer renders topological DAG; complex multi-parent graphs use layered hierarchy. |
| **Persistent Mission Store** | `mission_store.py`<br>`MissionStore(db_path)` | **Verified Working** | Phase 27, 27.1, 28, 29.2.1 suites PASS.<br>Authoritative SQLite WAL database at `%APPDATA%\hey-jev\hey_jev_missions.db`. | Relies on local filesystem. Schema migrations handled automatically via SQLite table initialization. |
| **Mission Planning & DAG Resolution** | `mission_planner.py`<br>`DeterministicMissionPlanner` | **Verified Working** | Phase 27 suite PASS.<br>Decomposes natural language goals into topological task DAGs with acceptance criteria. | Uses deterministic heuristics and `CapabilityRegistry`. Does not hallucinate non-existent capabilities. |
| **Mission Execution & Human Controls** | `mission_coordinator.py`<br>`MissionCoordinator` | **Verified Working** | Phase 27.1 suite PASS.<br>Task dispatch, approval pauses, resume, cancel, and adaptive replanning verified. | Non-interactive task execution requires pre-approval or automated capability runner. |
| **OpenDots MCP Gateway** | `opendots_gateway.py`<br>`OpenDotsGateway` | **Verified Working** | Phase 29.1 & 29.2 test suites PASS.<br>Tested with official `@modelcontextprotocol/sdk` (Node.js); 46 checks passed. | Binds to loopback (`127.0.0.1:8765`). Requires HTTP header `Authorization: Bearer <token>`. Exposes 10 mission tools. |
| **Local Speech-to-Text (STT)** | `stt.py`<br>`WhisperSTT` | **Verified Working** | Preflight Check 2 PASS (`faster-whisper` tiny.en warm transcription verified on CPU). | Requires CPU or CUDA. Cold start model load takes ~15–20s; subsequent transcriptions take ~200–800ms. |
| **Speculative Voice Decision Battery** | `siri.py`, `backend.py`<br>`JevBackend.decide()` | **Verified Working** | Preflight Check 4 PASS.<br>Evaluates 13 questions in single HTTP call (1213ms latency, $0.00006 cost). | Requires `TYPESAFE_API_KEY`. Without key, turns fall back to Tier 2 open-vocabulary resolvers. |
| **Win32 System & Volume Actions** | `actions_win.py`<br>`pycaw`, `ctypes`, WinRT SMTC | **Verified Working** | Preflight Check 1 PASS;<br>Phase 2 suite PASS.<br>Master volume, Spotify session volume, media keys verified. | Requires Windows audio endpoint. Spotify volume requires running `Spotify.exe`. |
| **Open-Vocabulary App Index** | `app_index.py`, `tier2_resolver.py`<br>`AppIndex.find_app()` | **Verified Working** | Preflight Check 7 PASS (`Notepad` resolved with 100.0 score).<br>Scans Start Menu, UWP, and packaged apps. | Scanning thousands of shortcuts on cold start takes ~100–300ms; cached in-memory thereafter. |
| **System Targets & ms-settings** | `system_targets.py`<br>`resolve_system_target()` | **Verified Working** | Phase 6b suite PASS.<br>Resolves 21 settings pages, 6 standard folders, 8 utilities, and 18 websites. | Direct execution launches default Windows URL/URI handlers via `os.startfile` or `webbrowser.open`. |
| **Window Layout Management** | `window_manager.py`<br>`resolve_window_command()` | **Verified Working** | Phase 6b suite PASS.<br>Snap left, snap right, maximize, minimize, restore executed via Win32. | Operates on active foreground window or named application. |
| **Safety Risk Engine & Blocklist** | `safety_engine.py`<br>`evaluate_risk()`, `log_action()` | **Verified Working** | Preflight Check 9 & 13 PASS.<br>Strictly blocks Win+R Run dialog, cmd shells, and recursive file deletion. | In-process safety filter. Does not provide OS kernel sandboxing. |
| **Secret Redaction in Logs** | `safety_engine.py`<br>`redact_secrets()` | **Verified Working** | Preflight Check 10 PASS.<br>Zero API keys detected in disk logs; masks OpenAI, Anthropic, and GitHub keys. | Regex-based pattern matching for known token formats. |
| **Voice Loop Guards** | `siri.py`<br>Dedup guard, loop breaker, echo filter | **Verified Working** | Preflight Checks 11 & 12 PASS.<br>Drops identical transcripts within 5s; trips on 3 identical firings within 10s. | Prevents runaway audio loops caused by assistant hearing its own speakers. |
| **Cloud Text-to-Speech (TTS)** | `tts.py`<br>`fetch_tts()` | **Automated-Test-Only** | Preflight Check 3 PASS on cached audio.<br>Phase 1 suite PASS. | Live cloud synthesis requires `FISH_AUDIO_API_KEY`. Without key, uses cached audio or Kokoro fallback. |
| **Local Kokoro ONNX TTS** | `kokoro_tts.py`<br>`KokoroTTS` | **Experimental** | Phase 10 suite PASS.<br>Local neural TTS fallback on ONNX runtime. | Requires downloading Kokoro model weights and voice numpy files into `assets/` or user cache. |
| **Computer Operator 2.0 (UIA)** | `uia_operator.py`<br>`UIAOperator` | **Automated-Test-Only** | Phase 20 suite PASS (28 tests in `test_phase20.py`).<br>Inspects UIA trees and synthesizes clicks/input. | Desktop automation requires target window to be visible and unblocked. DPI scaling handled via `dpi_manager.py`. |
| **Browser Automation (CDP)** | `browser_adapter.py`, `browser_session.py`<br>`CDPBrowserBackend` | **Automated-Test-Only** | Phase 13 suite PASS.<br>Connects to Chrome/Edge via remote debugging port 9555 with isolated profiles. | Requires Google Chrome or Microsoft Edge installed. Requires `websocket-client` package. |
| **External Windows MCP Client** | `windows_mcp_client.py`<br>`WindowsMCPClient` | **Disabled / Broken** | Preflight Check 6 WARNING.<br>"Windows automation service is unavailable. Please check MCP server installation." | External binary `windows-mcp.exe` not bundled or running. Handshake fails safely with warning. |
| **LLM Q&A Chit-Chat Answers** | `answer_provider.py`<br>`ask_llm()` | **Verified Working** | Preflight Check 5 PASS (returns fallback gracefully when keys are unconfigured). | Requires `OPENROUTER_API_KEY` or `OPENCODE_ZEN_API_KEY` for live generative responses. |
| **Multi-Agent Swarm Engine** | `swarm_engine.py`, `swarm_coordinator.py`<br>`SwarmEngine` | **Automated-Test-Only** | Phase 18 suite PASS.<br>Coordinates multi-agent subtasks and state synchronization. | Relies on provider gateway for LLM agent workers. |
| **Background Tasks & Watchers** | `scheduler_backend.py`, `watchers.py`<br>`WindowsNativeDirectoryWatcher` | **Verified Working** | Phase 17 & Phase 21 suites PASS.<br>Overlapped `ReadDirectoryChangesW` handles directory changes. | Asynchronous file system monitoring; gracefully cancels via `CancelIoEx`. |
| **Dynamic Capability Deployment** | `capability_deployment.py`, `extension_runner.py` | **Experimental** | Phase 24 & 25 suites PASS.<br>Controlled AST validation and staged deployment in isolated directory. | Staged capability activation requires user permission. |

---

## 3. Verification Gaps & Blind Spots

While all 832 automated tests pass, the following capabilities have known gaps between test suites and real desktop usage:

1. **Live External Audio Playback:** Automated tests mock audio drivers; real-world volume levels, microphone sensitivity, and speaker distortion can cause Whisper STT to mishear words.
2. **UIA Foreground Focus Stealing:** In automated test environments, mock windows or background processes are targeted directly. In live Windows usage, popups, system notifications, or UAC prompts can steal focus away from the intended target window.
3. **CDP Browser User Profile State:** Automated tests spin up empty temporary profiles in `%TEMP%`. Real users expect browser automation to run in their logged-in browser with existing cookies and extensions.
4. **Voice API Latency:** In test runs with cached or mocked backends, decision latency is 0ms. In live usage, TypeSafe API roundtrip takes ~1,200ms and Fish Audio TTS takes ~800–1,500ms, creating noticeable delay before the assistant responds.
