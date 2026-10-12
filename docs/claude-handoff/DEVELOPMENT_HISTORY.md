# Hey Jev: Complete Development History (Phases 1–29.2.1)

This document reconstructs the complete engineering evolution of Hey Jev from its initial Windows port through Phase 29.2.1, based on verified Git commits, repository reports, and architectural decisions.

---

## 1. Project Genesis & The macOS Origins

Hey Jev originated as a macOS-based voice assistant (`JevSiri`) that evaluated speculative voice questions against a remote TypeSafe API decision model. In late 2025, the project began a comprehensive migration to Windows 10/11 x64 under branch `windows-port`.

Remnants of this macOS heritage (such as references to `Finder`, `Safari`, `Terminal`, and `Mimestream` in `apps.json`) still exist in the repository as legacy aliases alongside their Windows equivalents (`Explorer`, `Edge`, `Windows Terminal`, `Outlook`).

---

## 2. Chronological Phase Breakdown

### Epoch 1: Windows Port Baseline & Core Desktop Experience (Phases 1–5)

- **Phase 1: Windows Core Voice Loop Port**
  - *Commit:* `c708227` (`feat(core-loop): port core voice loop to Windows 10/11 x64 (Phase 1)`)
  - Replaced macOS `say` and `afplay` with `sounddevice` and `soundfile`.
  - Ported global Push-to-Talk (PTT) hotkey to Windows using `keyboard` on Right Alt.
  - Integrated `faster-whisper` for local CPU speech recognition.

- **Phase 1.5: Decision Backend Abstraction**
  - *Commit:* `c64ccb3` (`feat(backend): introduce DecisionBackend abstraction (Jev + Laya) and benchmark`)
  - Created `DecisionBackend` interface in `backend.py`.
  - Added support for both remote `JevBackend` (TypeSafe API) and local `LayaBackend` (ONNX model).

- **Phase 2: Windows Action Parity Table**
  - *Commit:* `eed4f6a` (`feat(actions): complete Windows action parity table and non-blocking executor`)
  - Implemented 33 discrete Windows desktop actions in `actions_win.py`.
  - Replaced AppleScript/macOS APIs with Win32 APIs, `pycaw` audio volume sessions, and `ctypes`.
  - Introduced non-blocking `ThreadPoolExecutor` for operations taking >50ms.

- **Phase 3: Native PySide6 Desktop GUI**
  - *Commit:* `ee09f22` (`feat(ui): PySide6 7-tab native window and floating waveform bubble`)
  - Built full native Qt interface (`assistant_ui.py`) with 7 tabs.
  - Added floating audio waveform bubble and real-time status transitions.

- **Phase 4: Packaging & Tray Lifecycle**
  - *Commit:* `113a6f7` (`feat(packaging): PyInstaller one-dir build, Task Scheduler autostart, and tray lifecycle`)
  - Implemented PyInstaller one-dir build specifications.
  - Added Windows Task Scheduler autostart entry and system tray integration via `pystray`.

- **Phase 5: Media Controls & Neural Wake-Word**
  - *Commit:* `43e7d78` (`feat(enhancements): SMTC media transport, openWakeWord engine, and Spotify Web API`)
  - Upgraded media transport to WinRT System Media Transport Controls (SMTC).
  - Integrated `openWakeWord` neural wake detector as an alternative to Whisper VAD.

---

### Epoch 2: Open-Vocabulary Intelligence & Truth Layer (Phases 6–10)

- **Phase 6: Open-Vocabulary Tier 2 Resolvers & Safety Engine**
  - *Commits:* `e9693ee` (6a), `1506315` (6b), `15d0178` (6c), `3c44d1e` (6), `68e95c7` (6d), `1706355` (6e)
  - Created Tier 2 open-vocabulary local resolver (`tier2_resolver.py`).
  - Added `app_index.py` scanning Start Menu and `shell:AppsFolder` with `RapidFuzz` matching.
  - Added `system_targets.py` (`ms-settings:` URIs, folders, websites) and `window_manager.py` (Snap Assist).
  - Built `safety_engine.py` with 5 risk tiers, action audit log (`actions.jsonl`), and `undo_manager.py`.

- **Phase 7: Windows MCP Client Research & Hardening**
  - *Commits:* `6124a74`, `c4841d9`, `02babfa`
  - Researched desktop automation engines.
  - Implemented Tier 3 Windows MCP client with allowlist and UI bypass safety checks.

- **Phase 8: Truth Verification Layer & Preflight Suite**
  - *Commit:* `1be1b3e` (`feat(phase8): truth layer, hardware profile, preflight checks, and carry-over hardening`)
  - Established `preflight.py` truth suite verifying real hardware, STT warmup, TTS cache, and bug checks without mocks.
  - Enforced the principle: *"Never claim a feature works without live environment proof."*

- **Phase 9: STT Accuracy, VAD & Gated Wake-Word**
  - *Commits:* `560b2dd` (9a), `65f942c` (9b), `fadba01` (9c), `9b9f599` (9d)
  - Tuned Whisper accuracy using domain-specific `INITIAL_PROMPT` and hotword biasing.
  - Evaluated streaming STT candidates in reconnaissance report `PHASE9B_RECON.md`.
  - Implemented bounded VAD and gated turn completion.

- **Phase 10: Local Neural TTS Fallback**
  - *Commit:* `cfa0131` (`feat(phase10): add kokoro local tts fallback`)
  - Added `kokoro_tts.py` using Kokoro ONNX model for offline speech synthesis when Fish Audio cloud keys are absent.

---

### Epoch 3: Agent Harness, Multi-Agent Swarms & Persistent Memory (Phases 11–16)

- **Phase 11: Agent Harness Foundation**
  - *Commit:* `e3ecce7` (`feat(phase11): add jev agent harness foundation`)
  - Introduced unified agent execution contracts (`agent_adapter.py`, `agent_task.py`, `agent_result.py`).

- **Phase 12: Windows Computer Operator & Desktop Tools**
  - *Commit:* `7447f67` (`feat(phase12): add windows computer operator and tools`)
  - Built `windows_computer_adapter.py` and `windows_computer_tools.py` for keyboard, mouse, and window manipulation.

- **Phase 13: Browser Operator & Research Engine**
  - *Commit:* `861c02b` (`feat(phase13): add browser operator and research engine`)
  - Implemented native Chrome/Edge CDP automation (`browser_adapter.py`, `browser_session.py`).
  - Added evidence-based research engine (`research_engine.py`) with untrusted data quarantining.

- **Phase 14: Multi-Agent Orchestration**
  - *Commit:* `fab27b4` (`feat(phase14): add multi-agent orchestration and agent collaboration`)
  - Implemented agent team collaboration, handoffs, and consensus protocols.

- **Phase 15: Skill System & Dynamic Tool Registry**
  - *Commit:* `bb77b61` (`feat(phase15): add skill system and dynamic tool registry`)
  - Built `tool_registry.py` and `skill_executor.py` for registering and matching callable tools.

- **Phase 16: Persistent Contextual Memory & Project Registry**
  - *Commit:* `254529a` (`feat(phase16): add persistent memory and project awareness`)
  - Added SQLite WAL stores for contextual memories (`memory.db`) and registered developer codebases (`projects.db`).

---

### Epoch 4: Event-Driven Automation, Computer Operator 2.0 & Reliability (Phases 17–21)

- **Phase 17: Background Tasks & System Watchers**
  - *Commit:* `c6f0e2e` (`feat(phase17): add background tasks, watchers, and event-driven automation`)
  - Built `scheduler_backend.py` backed by `background_tasks.db` and initial file/process watchers.

- **Phase 18: Agent Swarm & Provider Gateway**
  - *Commit:* `9e7035a` (`feat(phase18): add real agent swarm and provider gateway`)
  - Added multi-agent swarm engine (`swarm_engine.py`) and unified LLM provider gateway (`provider_gateway.py`).

- **Phase 19: Reliability, Verification & State Checkpoints**
  - *Commits:* `ea4c730`, `39f497b`
  - Added `reliability_manager.py` with SHA-256 state checkpoints, auto-rollback, and directory undo.

- **Phase 20: Computer Operator 2.0 (UI Automation-First)**
  - *Commits:* `ad289e7`, `8da62c7`
  - Upgraded desktop automation to `pywinauto` UIA backend (`uia_operator.py`). Added DPI scaling awareness (`dpi_manager.py`).

- **Phase 21: Native Directory Watchers & Proactive Heartbeat**
  - *Commits:* `8a7ef44`, `50fa46f`, `08190d2`, `19cc5e6`
  - Replaced polling with Windows overlapped `ReadDirectoryChangesW` (`WindowsNativeDirectoryWatcher`).
  - Added proactive background health engine (`proactive_heartbeat.py`).

---

### Epoch 5: Capability Acquisition, Extensions & Autonomous Maintenance (Phases 22–26)

- **Phase 22: Capability Discovery & Workflow Composition**
  - *Commits:* `6cc6903`, `c0f8278`
  - Added intelligent tool matching and multi-step workflow composition (`workflow_engine.py`).

- **Phase 23: Capability Acquisition & Extension Proposals**
  - *Commit:* `1f6e69b` (`feat(phase23): safe capability acquisition, integration discovery and extension proposals`)
  - Structured acquisition pipeline generating extension proposals for missing capabilities.

- **Phase 24: Staged Extension Construction & Boundary Hardening**
  - *Commits:* `a8181fd`, `cd7ce97`
  - Built AST-based security validator and staged activation sandbox (`extension_runner.py`).

- **Phase 25: Capability Deployment & Lifecycle Recovery**
  - *Commits:* `f9838a6`, `4bea329`
  - Added versioning, rollback, and deployment concurrency locks (`capability_deployment.py`).

- **Phase 26: Autonomous Maintenance & Self-Healing**
  - *Commits:* `c9cd28a`, `766d0d9`, `66d0379`, `949ab10`
  - Built self-diagnosis and safe autonomous repair engine (`maintenance_manager.py`).

---

### Epoch 6: Persistent Missions, Mission Center & OpenDots MCP Gateway (Phases 27–29.2.1)

- **Phase 27: Persistent Goal-Driven Missions (System 3)**
  - *Commits:* `7d36c22`, `ce8f79c`, `d162fc4`, `5f57a5f`
  - Created `MissionCoordinator`, `DeterministicMissionPlanner`, and `MissionStore`.
  - Built persistent SQLite WAL storage at `%APPDATA%\hey-jev\hey_jev_missions.db`.
  - Implemented DAG dependency resolution, approval checkpoints, and crash recovery.

- **Phase 28: Mission Center GUI & Live Progress**
  - *Commits:* `af1732e`, `bd295e4`
  - Built native PySide6 `MissionCenterWindow` (`mission_center.py`).
  - Added visual DAG canvas, acceptance criteria table, and live human control buttons.

- **Phase 29: OpenDots Interoperability Gateway**
  - *Commits:* `5beb8ea`, `236c04f`
  - Built `opendots_gateway.py` implementing MCP Streamable HTTP transport on `127.0.0.1:8765`.
  - Exposed 10 mission management tools to external MCP clients like OpenDots.

- **Phase 29.1: Gateway Security & Transport Hardening**
  - *Commits:* `3e77179`, `e77ce3e`
  - Hardened gateway with mandatory Bearer token authentication, rate limiting, and request sanitization.

- **Phase 29.2: Official OpenDots MCP SDK Verification**
  - *Commits:* `203c8eb`, `085506d`
  - Verified gateway against real `@modelcontextprotocol/sdk` (Node.js); 46 passing checks proved complete transport and tool compatibility.

- **Phase 29.2.1: Persistent Data Integrity Audit & Recovery**
  - *Commits:* `cda1304`, `f8d815a`
  - Investigated transcript deletion commands; proved zero loss of persistent user databases.
  - Implemented `test_cleanup_utils.py` for safe disposal of temporary test databases.
  - Added safety engine denylist blocking recursive deletion of persistent data folders.

- **Manual Test Guide Documentation**
  - *Commit:* `f74aa08` (`docs: manual acceptance testing guide for Hey Jev`)
  - Produced comprehensive step-by-step human acceptance testing instructions (`MANUAL_TEST_GUIDE.md`).

---

## 3. Key Lessons Learned & Architectural Pivots

1. **Local vs. Cloud Latency:** Speculative cloud decision models (TypeSafe API) provide high reasoning accuracy for voice, but introduce 1,200ms+ roundtrip latency. For clear app commands (e.g. "open notepad"), deterministic regex/fuzzy local matching must precede remote API calls.
2. **Audio Acoustic Feedback:** Early builds suffered from assistant "self-hearing" loops where the microphone picked up assistant speech from speakers. This was resolved via `audio_io.is_audio_playing_or_settling()` and loop breakers in `siri.py`.
3. **Data Safety Gating:** Running autonomous agents on Windows requires strict PowerShell command denylists to prevent accidental recursive deletion (`Remove-Item -Recurse -Force`).
4. **Test Isolation:** SQLite test databases must use temporary paths created via `test_cleanup_utils.py` rather than mutating production databases in `%APPDATA%`.
