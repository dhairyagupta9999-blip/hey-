# Phase 17: Jev Background Tasks, Watchers & Event-Driven Automation Report

## 1. Executive Summary

Phase 17 elevates Hey Jev from a purely synchronous assistant (`REQUEST -> RESPONSE`) into an autonomous, proactive agent orchestrator (`REQUEST -> MONITOR -> EVENT -> REACT -> VERIFY -> NOTIFY`).

Jev can now continue executing background workloads, monitor file systems, track processes, inspect Git repositories, observe browser and project states, detect semantic and structural changes, fire rule-driven reactive pipelines, and deliver notifications with quiet hours and prioritization.

Critically, all background mechanics are designed for strict safety on low-power dual-core hardware (Intel Core i3-5005U):
- **Audio Thread Protection**: Microsecond-scale voice preemption (`< 10 µs`) immediately yields CPU compute whenever user speech or faster-whisper transcription starts.
- **Dual-Core Budgeting**: Concurrency capped at 1 foreground + 1 background task slot.
- **Loop & Cascade Safety**: Event cascade depth capped at 5, with MD5 event fingerprinting and per-rule cooldowns.
- **User Desktop Session Only**: 100% non-elevated user desktop session; zero Windows Services to avoid Session 0 desktop/audio isolation.

---

## 2. Key Architecture Components Implemented

| Module | Purpose & Core Capabilities |
| :--- | :--- |
| `background_task_model.py` | `BackgroundTaskDefinition`, 11 lifecycle states (`DRAFT`, `READY`, `SCHEDULED`, `RUNNING`, `WAITING_FOR_TRIGGER`, `COMPLETED`, `FAILED`, `CANCELLED`, `BLOCKED`, `PAUSED`, `MISFIRED`), priority tiers, and resource classes. |
| `trigger_model.py` | 14 trigger types (`CRON`, `INTERVAL`, `ON_EVENT`, `FILE_CHANGE`, `PROCESS_EXIT`, `GIT_EVENT`, etc.), configurable debounce intervals, cooldown limits, and payload filtering. |
| `watcher_model.py` | `WatcherDefinition`, `WatcherType` (`FILESYSTEM`, `PROCESS`, `GIT`, `BROWSER`, `MISSION`, `PROJECT`), `WatcherState`, and event emission models. |
| `change_detector.py` | `ChangeDetector` computing diffs for dictionaries, lists, and raw text (`unified_diff`), outputting structured `ChangeReport` with semantic summaries. |
| `resource_governor.py` | Hardware resource management for 2C/4T CPU: concurrency slot acquisition (1 fg, 1 bg), battery state awareness, quiet hours windowing, and instantaneous voice preemption (`set_voice_active`). |
| `notification_manager.py` | Provider-neutral notification dispatch, priority gating, suppression during quiet hours unless high priority, and historical alert ledger. |
| `event_rule_engine.py` | Declarative rule engine matching predicates (`equals`, `contains`, `matches`, `greater_than`, `less_than`, `changed`, `exists`, `not_exists`), cooldown enforcement, and recursion cycle detection (`max_event_chain_depth = 5`). |
| `scheduler_backend.py` | `InProcessSchedulerBackend` using a Python priority queue (heap) with sub-millisecond evaluation, cron/interval recalculation, misfire tracking, and Windows Task Scheduler export adapter. |
| `watchers.py` | Specialized domain observers: `FilesystemWatcher` (with drive root safety), `ProjectWatcher`, `GitWatcher` (100% read-only), `ProcessWatcher`, `BrowserWatcher`, and `MissionWatcher`. |
| `recovery_manager.py` | System crash recovery: audits and recovers stranded `RUNNING` tasks to `READY`, checks watcher health, and performs bounded restarts. |
| `background_status.py` | Structured status snapshots (`BackgroundStatusSnapshot`) and human-readable CLI/TTS summaries of background tasks and watchers. |
| `background_task_manager.py` | Central coordinator orchestrating SQLite WAL persistence, scheduler, rule engine, watchers, governor, and notification dispatch. |

---

## 3. Technology Evaluation (Tech Radar Summary)

As detailed in `PHASE17_TECH_RADAR.md`:
* **Adopted (Scheduler)**: **In-Process Heap Priority Queue**. Sub-millisecond evaluation (mean 10.9 µs), zero daemon dependencies, seamless integration with Python event loop.
* **Adopted (Observation)**: **Polling + Polling Directory Observation & psutil**. Safe on Windows, robust across network mounts, bounded overhead on dual-core CPU.
* **Adopted (Git Inspection)**: **Read-Only GitInspector**. Reuses Phase 16 read-only CLI inspect calls with strict 3.0s timeouts and zero repo mutation risk.
* **Deferred**: **Celery / Redis / RabbitMQ**. Heavyweight background brokers that consume 150–300 MB of RAM and add external process dependencies.
* **Deferred**: **Windows Services**. Running as a Windows Service places processes into Session 0, which breaks desktop UI automation and microphone/speaker audio access.

---

## 4. Benchmark Performance Metrics

Benchmarked on Windows 10 Pro x64 (Intel Core i3-5005U, 2C/4T @ 2.00 GHz, ~7.93 GB RAM):

| Benchmark Operation | Sample Count | p50 Latency | p95 Latency | Mean Latency |
| :--- | :--- | :--- | :--- | :--- |
| **Task Registration (SQLite WAL)** | 50 | **14.07 ms** | **21.61 ms** | 15.59 ms |
| **Scheduler Step Evaluation** | 100 | **0.0091 ms** (9.1 µs) | **0.0199 ms** | **0.0109 ms** (10.9 µs) |
| **Event Rule Engine Matching** | 100 | **1.13 ms** | **2.55 ms** | 1.39 ms |
| **Change Detector (Dict Diff)** | 100 | **0.32 ms** | **0.63 ms** | 0.35 ms |
| **Change Detector (Text Diff)** | 100 | **11.32 ms** | **13.89 ms** | 11.35 ms |
| **Resource Governor Slot Acquisition** | 100 | **0.0486 ms** | **0.0693 ms** | 2.19 ms |
| **Voice Preemption Toggle** | 100 | **0.0088 ms** (8.8 µs) | **0.0107 ms** | **0.0096 ms** (9.6 µs) |

* **Memory Footprint**:
  * Traced RAM Delta: **1.57 MB**
  * Peak Memory Overhead: **1.60 MB** (negligible memory pressure on 8 GB system)

---

## 5. Verification of the 5 Required Demonstrations

All 5 Phase 17 demonstrations were implemented and verified in `test_phase17.py`:

1. **Folder Watcher Pipeline** (`test_demo_1_folder_watch_to_task`):
   * `FilesystemWatcher` detects newly created file `download_test.zip`.
   * `EventRuleEngine` triggers reactive task `"organize_download"`.
   * Action executes, verifies destination file existence, and emits a user notification.
2. **Project Watcher Health Degradation** (`test_demo_2_project_watcher_health_degradation`):
   * `ProjectWatcher` observes repo state change from healthy to broken (`is_working = False`).
   * Rule triggers `"alert_build_break"` task, notifying user of build failure with failure details.
3. **Scheduled Browser Check with Change Detection** (`test_demo_3_scheduled_browser_change_detector`):
   * Scheduled task checks page content; `ChangeDetector` identifies text alterations.
   * Emits notification with concise change summary.
4. **Mission Watcher Multi-Agent Review** (`test_demo_4_mission_watcher_review_agent`):
   * `MissionWatcher` detects mission transition to `COMPLETED`.
   * Automatically triggers review rule, executing quality review agent task and notifying user.
5. **Background Research Memory Comparison** (`test_demo_5_background_research_memory_diff`):
   * Background task performs topic research and queries existing Phase 16 `SqliteMemoryStore`.
   * `ChangeDetector` verifies new facts against stored knowledge and notifies user on novel insights.

---

## 6. Full Test Suite & Preflight Status

* **Full Pytest Suite**: **343 passed, 0 failed, 0 skipped** (2m 31s).
  * Phase 16 Baseline: 321 tests.
  * Phase 17 Additions: 22 tests (`test_phase17.py`).
  * Regressions: **0**.
* **Preflight Truth Verification**: **12 pass, 0 fail, 2 warn**.
  * Secret scan passed: zero API keys detected in disk logs.
  * Loop breaker, dedup guard, and security protections fully verified.
* **Security & Safety Guarantees**:
  * Safe Directory Enforcement: Rejects filesystem roots (`C:\`, `D:\`, `/`) to prevent runaway scans.
  * Secret Sanitization: Logs and notifications scrub API keys and auth tokens.
  * Audio Thread Preemption: Zero latency interference with Whisper STT or wake word recognition.
