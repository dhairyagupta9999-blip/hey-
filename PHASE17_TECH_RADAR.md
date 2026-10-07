# Phase 17: Technology Radar & Architecture Decisions
## Background Tasks, Watchers, and Event-Driven Automation

**Date**: 2026-10-07  
**Platform**: Windows 10 Pro x64 (Intel Core i3-5005U, 2C/4T @ 2.00 GHz, ~7.93 GB RAM, Python 3.11.9)  
**System State**: Phase 16 Complete (`254529a`), 321 passing tests, clean tree.  

---

## 1. Executive Evaluation Matrix

| Domain | Technology / Candidate | Status | Primary Rationale & Constraints |
| :--- | :--- | :--- | :--- |
| **Scheduler** | **Python In-Process Priority Scheduler** | **ADOPT** | Zero new dependencies, microsecond latency, thread-safe, integrates directly with `EventBus` & `TaskStorage`, <100 KB memory footprint. |
| **Scheduler** | **Windows Task Scheduler (`schtasks.exe` wrapper)** | **STUDY / SELECTIVE** | Native OS persistence across reboots. Restricted strictly to non-elevated user tasks with prefix `HeyJev_`. Headless-only (Session 0 limitations for GUI). |
| **Scheduler** | **APScheduler** | **DEFER** | Adds 5+ third-party dependencies (`tzlocal`, `pytz`), async event loop bloat, and memory overhead without functional benefits over in-process priority heap + SQLite. |
| **Lifecycle** | **Windows Service (`winsvc` / SCM)** | **REJECT** | Runs in Session 0; completely isolated from interactive desktop (cannot click UI, capture mic, play TTS). Requires administrator privileges. |
| **FS Watcher**| **Native `ReadDirectoryChangesW` (`pywin32`)** | **ADOPT** | High-performance Windows filesystem notifications. `pywin32` 312 is already installed. Zero polling overhead, <0.01% idle CPU. |
| **FS Watcher**| **`watchdog`** | **STUDY / ADAPTER** | Popular Python library. Abstracted behind `FilesystemWatcherBackend`. Not installed currently in `.venv`; native `pywin32` is preferred to avoid extra package weight. |
| **Process** | **`psutil` Process Monitor** | **ADOPT** | `psutil` 7.2.2 is already installed. Fast non-intrusive PID/name polling, zero kernel drivers, user-space execution. |
| **Git** | **Read-Only `GitInspector` Poller** | **ADOPT** | Reuses Phase 16 read-only Git status inspector (`status --porcelain`, `log -1`). Zero auto-commits, debounced by `.git/` change events. |
| **Browser** | **Page / DOM / URL Watcher** | **ADOPT** | Light event monitoring (URL, DOM status, download folder). Strictly prohibits continuous screenshotting or OCR loops. |
| **Mission** | **EventBus Mission Lifecycle Hook** | **ADOPT** | Native event chaining: `mission.completed` -> event rule -> review skill -> verifier. |

---

## 2. In-Depth Component Analysis

### 2.1 Background Execution & Schedulers

#### Candidate A: Python In-Process Priority Scheduler (Adopted as Primary Engine)
* **License**: Python Software Foundation (built-in).
* **RAM / CPU Impact**: Negligible (<100 KB RAM, <0.05% CPU).
* **Reliability & Concurrency**: Backed by Python standard library `threading.Thread`, priority queue (`heapq`), and condition variables.
* **Restart Recovery**: Rehydrates pending/scheduled tasks from persistent SQLite (`background_tasks` table) upon Jev startup.
* **Desktop Interaction**: Runs directly in the interactive user desktop session, enabling GUI actions (via Phase 12 computer operator) when authorized.

#### Candidate B: Windows Task Scheduler (`schtasks.exe`) (Study / Selective Adopt)
* **License**: Microsoft Windows Component.
* **RAM / CPU Impact**: Managed entirely by OS Task Scheduler (`svchost.exe`).
* **Capabilities**: Can wake machine or trigger tasks at user login, boot, or scheduled times even when Jev UI is completely closed.
* **Security & Sandboxing**:
  * Must never require elevation or run as `SYSTEM`.
  * Must use dedicated naming prefix: `HeyJev_<task_id>`.
  * Cannot perform interactive UI automation if launched in a non-interactive token.
  * Explicit cleanup on deletion to avoid lingering orphaned tasks.

#### Candidate C: APScheduler (Deferred)
* **License**: MIT.
* **Reasoning**: APScheduler is an external dependency that requires external time-zone databases (`tzdata`, `pytz`). For an edge assistant running on a dual-core 8 GB laptop, our native SQLite-backed in-process priority scheduler gives deterministic behavior with zero bloat.

#### Candidate D: Windows Services (Rejected)
* **Reasoning**: Since Windows Vista, services run in isolated Session 0. Interactive desktop applications, mouse movements, screen captures, and user microphone audio cannot be accessed from Session 0 without insecure privilege escalation hacks. Rejected entirely.

---

### 2.2 Filesystem & Digital Environment Watchers

#### Candidate A: Windows `ReadDirectoryChangesW` via `pywin32` (Adopted)
* **License**: PSF / Pythonwin.
* **Status**: `pywin32` version 312 is already installed and verified in `.venv`.
* **Performance**: Directly registers with Windows NT kernel directory change notifications. Generates notifications for file additions, modifications, deletions, and renames with zero polling.
* **Safety Controls**:
  * Bound to user-specified directories (`max_depth <= 3`).
  * Rejects root drive monitoring (`C:\`, `C:\Windows`).
  * Debounced with configurable burst window (e.g. 500ms) to combine multiple file writes into one logical event.

#### Candidate B: Process Watching via `psutil` (Adopted)
* **Status**: `psutil` 7.2.2 is already installed in `.venv`.
* **Capabilities**: Inspects specific target PIDs or process names (e.g. `"Antigravity.exe"`, `"pytest.exe"`).
* **Safety**: Does not globally monitor all system activity; tracks only explicit user-configured targets.

#### Candidate C: Read-Only Git Watcher (Adopted)
* **Capabilities**: Combines filesystem debouncing on the `.git/` directory with Phase 16's read-only `GitInspector`.
* **Safety**: Strictly read-only; never commits or modifies working copy.

---

### 2.3 Event Rule Engine & Loop Safety

To avoid runaway feedback loops (`event -> task -> event -> task -> infinite loop`):
1. **Deterministic Predicates**: Rules evaluate simple, explainable condition checks (`equals`, `contains`, `matches`, `greater_than`, `less_than`, `changed`, `exists`, `not_exists`).
2. **Debouncing & Cooldown**: Every watcher and rule supports a minimum cooldown (e.g. 60 seconds) and event fingerprint deduplication.
3. **Chain Depth Bounding**: Maximum event-to-mission cascading depth is strictly clamped (`max_event_chain_depth = 5`).
4. **Action Policy Gating**: Triggered tasks must pass existing `ToolExecutor` and `TaskPermission` gates; scheduled execution does not grant perpetual authorization.

---

### 2.4 Resource Governor & Dual-Core Safety Profile

Target Machine: **Intel Core i3-5005U (2 cores / 4 threads @ 2.00 GHz), ~7.93 GB RAM**.
* **Foreground Reservation**: Foreground voice interaction (wake-word detection, Whisper STT, Kokoro TTS) always holds top CPU priority.
* **Background Yielding**:
  * When microphone detects speech or wake word triggers, `ResourceGovernor` immediately suspends or defers background agent tasks.
  * Local background agent concurrency slots: maximum **1 slot** for LIGHT/NORMAL tasks; **0 slots** during active voice turns.
  * Heavy workloads (LLM inference, OCR, video processing) are marked `ResourceClass.HEAVY` and queued until the user is idle.
