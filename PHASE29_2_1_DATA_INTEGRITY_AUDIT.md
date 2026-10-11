# Hey Jev Phase 29.2.1: Persistent Data Integrity Audit and Recovery Report

**Date:** 2026-10-11  
**Branch:** `windows-port`  
**Prior Phase 29.2 Baseline Commit:** `085506d`  
**Safety Hardening Code Commit:** `cda1304` (`feat(phase29.2.1): narrow test artifact cleanup utilities and data directory safety guards`)  
**Audit Report Commit:** Pending  
**Working Tree Status:** Clean  
**Operating System:** Windows 11 Pro (`win32`)  
**Python Runtime:** 3.11.9 (`.venv`)  

---

## 1. Executive Summary & Audit Mandate

During Phase 29.2 test execution, the shell command:
```powershell
Remove-Item -Path "C:\Users\prabh\.gemini\antigravity\scratch\hey-jev\data" -Recurse -Force
```
was observed in the session transcript. Because recursive directory deletion carries high risk of accidental data loss, Phase 29.2.1 was initiated as an immediate safety audit before Phase 30.

### Core Audit Conclusions
1. **Zero Persistent Data Loss:** Comprehensive forensic analysis confirms that **zero persistent application data was lost**.
2. **Captured Directory Evidence:** Session transcript line 16684 captured the exact `Get-ChildItem` listing of `C:\Users\prabh\.gemini\antigravity\scratch\hey-jev\data` immediately before the deletion at line 16685. The directory contained **exclusively three disposable test databases** created during Phase 29.2 test commands:
   - `test_run_p29_2_cs.db` (49,152 bytes, created 18:44)
   - `test_run_p29_2_live.db` (49,152 bytes, created 18:53)
   - `test_run_p29_2.db` (49,152 bytes, created 18:43)
3. **Persistent Locations Intact:** All real persistent Hey Jev databases and state reside in `%APPDATA%\HeyJev` and `%APPDATA%\hey-jev` (per `config.py` and `mission_store.py`). All persistent files remain completely intact, healthy, and untouched.
4. **Permanent Prevention Implemented:**
   - Introduced `test_cleanup_utils.py` providing `narrow_cleanup_artifacts()` and `safe_temp_dir_cleanup()`, strictly forbidding directory deletion for test cleanup.
   - Hardened `safety_engine.py`'s `POWERSHELL_DENYLIST` to intercept and block recursive deletion targeting data and application directories.
   - Added `test_data_integrity_cleanup.py` proving that user and application sentinel files are strictly preserved while temporary artifacts are cleaned up.

---

## 2. Stage 1: Current State Inspection

### 2.1 Git Repository State
- **Branch:** `windows-port`
- **HEAD Commit:** `cda1304`
- **Working Tree:** Clean (verified with `git status`).
- **Git History for `data/`:** `git log --all --full-history -- "data"` and `git log --all -- "data/*"` returned 0 commits. The repository has never tracked a `data` directory.

### 2.2 Project `data` Directory Inspection
- **Path:** `C:\Users\prabh\.gemini\antigravity\scratch\hey-jev\data`
- **Current Status:** **Does not exist** (`Test-Path` returned `False`).

### 2.3 Configuration & Persistent Storage Locations
Inspection of `.gitignore`, `config.py`, and domain stores:
- `.gitignore` does not list `data/` or `*.db`.
- **Primary Persistent Application Directory (`config.py`):**
  - Configured at `%APPDATA%\HeyJev` (`C:\Users\prabh\AppData\Roaming\HeyJev`).
  - Contains all genuine user configuration and runtime databases:
    | File Name | Size (Bytes) | Last Modified | Purpose |
    | :--- | :--- | :--- | :--- |
    | `actions.jsonl` | 1,229,459 | 2026-10-11 19:04 | Audit log for desktop automation and undo history |
    | `app_index.json` | 76,057 | 2026-10-11 19:04 | Discovered installed application index |
    | `apps.json` | 866 | 2026-09-29 13:26 | Configured application aliases |
    | `background_tasks.db` | 28,672 | 2026-10-10 00:31 | Persistent background task execution engine |
    | `Hey Jev.log` | 457,995 | 2026-10-11 19:04 | Application runtime log |
    | `hey_jev_missions.db` | 49,152 | 2026-10-10 22:29 | Mission store in HeyJev root |
    | `heyjev_reliability.db` | 28,672 | 2026-10-09 19:44 | System incident and reliability metrics |
    | `heyjev_swarms.db` | 2,289,664 | 2026-10-11 19:01 | Multi-agent swarm coordination store |
    | `memory.db` | 36,864 | 2026-10-11 19:05 | Semantic user memory and context store |
    | `projects.db` | 20,480 | 2026-10-10 00:31 | Project registry |
    | `settings.json` | 624 | 2026-10-11 19:04 | Active user settings |
    | `spend.json` | 50 | 2026-10-11 19:04 | LLM API cost ledger |
    | `timers.json` | 2 | 2026-10-11 18:59 | Active user timers |
    | `vocabulary.json` | 187 | 2026-09-29 13:26 | Custom speech recognition dictionary |
- **Mission Coordinator Default Storage (`mission_store.py`):**
  - Fallback location: `%APPDATA%\hey-jev\hey_jev_missions.db` (`C:\Users\prabh\AppData\Roaming\hey-jev\hey_jev_missions.db`, 49,152 bytes, last modified 2026-10-11 18:42).

Neither the production application nor default testing configurations have ever used or expected `C:\Users\prabh\.gemini\antigravity\scratch\hey-jev\data`.

---

## 3. Stage 2: Forensic Evidence of Data Loss

To determine whether data loss occurred, the entire conversation transcript (`transcript.jsonl` and `transcript_full.jsonl`) was audited from step 0 to present.

### 3.1 Timeline of Events Leading to Directory Deletion

1. **Prior History Check (Lines 0 to 16400):**
   - A search of all 16,400 preceding steps across Phases 1–29.1 found **0 occurrences** of `scratch\hey-jev\data` or `hey-jev/data`.
   - The directory did not exist prior to Phase 29.2 exploratory runs.

2. **Creation of `data/` (Line 16475):**
   - An ad-hoc background Python command was executed to test the MCP gateway with `test_official_sdk_client.mjs`:
     ```python
     store = SqliteMissionStore(db_path='data/test_run_p29_2.db')
     ```
   - In `mission_store.py`, `os.makedirs(os.path.dirname(db_path), exist_ok=True)` created `data/` in the working directory (`C:\Users\prabh\.gemini\antigravity\scratch\hey-jev\data`).

3. **Creation of Subsequent Test Databases (Lines 16513 & 16597):**
   - Line 16513 passed `db_path='data/test_run_p29_2_cs.db'` for ConnectionService testing.
   - Line 16597 passed `db_path='data/test_run_p29_2_live.db'` for live application server testing.

4. **Directory Listing Captured (Line 16684):**
   - At Line 16683, the assistant ran `Get-ChildItem -Path "C:\Users\prabh\.gemini\antigravity\scratch\hey-jev\data"`.
   - Line 16684 captured the exact command output:
     ```
         Directory: C:\Users\prabh\.gemini\antigravity\scratch\hey-jev\data

     Mode                 LastWriteTime         Length Name
     ----                 -------------         ------ ----
     -a---          11-10-2026    18:44          49152 test_run_p29_2_cs.db
     -a---          11-10-2026    18:53          49152 test_run_p29_2_live.db
     -a---          11-10-2026    18:43          49152 test_run_p29_2.db
     ```
   - All three files had timestamps between 18:43 and 18:53 on 11-10-2026. No other files or directories existed inside `data/`.

5. **Execution of `Remove-Item` (Line 16685):**
   - The assistant executed `Remove-Item -Path "C:\Users\prabh\.gemini\antigravity\scratch\hey-jev\data" -Recurse -Force` to clear untracked files before running git commits.

### 3.2 Confirmed Facts vs Assumptions

| Aspect | Confirmed Fact | Assumption |
| :--- | :--- | :--- |
| **Directory Contents** | Exactly three SQLite databases (`test_run_p29_2.db`, `test_run_p29_2_cs.db`, `test_run_p29_2_live.db`) existed, proven by `Get-ChildItem` output at Line 16684. | None. |
| **Origin of Files** | Created by interactive test commands between 18:43 and 18:53 during Phase 29.2. | None. |
| **Prior Existence** | Directory did not exist in Git or prior transcript history. | None. |
| **Persistent Data Status** | All genuine application databases in `%APPDATA%\HeyJev` and `%APPDATA%\hey-jev` are intact and were not modified by the command. | None. |
| **Data Loss Determination** | **Zero persistent application data or user data was lost.** | None. |

---

## 4. Stage 3: Recovery Determination

- **Is Recovery Needed?** No.
- **Rationale:**
  - The deleted files were disposable test databases created solely for exploratory scripts during Phase 29.2.
  - The standardized test suite (`test_phase29_2.py`) uses temporary directories via `tempfile.mkdtemp()` and does not use `data/`.
  - The production application uses `%APPDATA%\HeyJev` and does not use `data/`.
  - Recreating an empty `data` directory or fabricating replacement databases is unjustified and would introduce unused clutter into the repository.

---

## 5. Stage 4: Recurrence Prevention & Safety Hardening

To ensure that broad directory deletion cannot recur, three protections were implemented:

### 5.1 Safe Artifact Cleanup Utility (`test_cleanup_utils.py`)
Created `test_cleanup_utils.py` containing:
- `narrow_cleanup_artifacts(target_files, allow_companion_sqlite=True)`:
  - Takes explicitly named file paths.
  - Strictly refuses to delete directories (`os.path.isdir()` triggers a validation error).
  - Automatically identifies and removes SQLite companion files (`-wal`, `-shm`, `-journal`) for explicit targets only.
  - Blocks deletion if a target resides in protected system or application directories.
- `safe_temp_dir_cleanup(temp_dir, explicit_artifacts)`:
  - Ensures `temp_dir` is located within the system temporary directory (`tempfile.gettempdir()`).
  - Cleans only the explicitly named artifacts.
  - Uses non-recursive `os.rmdir()` so that if unexpected files (sentinels or user data) exist, the directory removal fails safely and preserves all non-target files intact.

### 5.2 PowerShell Safety Denylist Hardening (`safety_engine.py`)
Updated `POWERSHELL_DENYLIST` in `safety_engine.py` to intercept recursive deletions targeting application and data directories:
```python
(re.compile(r"\bRemove-Item\b(?=.*-(?:Recurse|r)\b)(?=.*(?:hey-jev[\\/]data|HeyJev|hey-jev|\.venv|\bdata\b))", re.I),
 "recursive application or data directory deletion")
```
This regex uses positive lookaheads to catch recursive `Remove-Item` commands regardless of parameter ordering.

### 5.3 Test Runner Hardening (`run_opendots_node_audit.py`)
Updated `run_opendots_node_audit.py` to replace broad `shutil.rmtree()` with:
```python
from test_cleanup_utils import safe_temp_dir_cleanup
safe_temp_dir_cleanup(temp_dir, explicit_artifacts=[db_path])
```

### 5.4 Test Verification Suite (`test_data_integrity_cleanup.py`)
Implemented 7 automated tests validating the safety guarantees:
1. `test_01_narrow_cleanup_preserves_sentinels`: Creates a test DB with `-wal` and `-shm` companions alongside `sentinel_user_data.txt` and `sentinel_persistent_app.db`. Verifies narrow cleanup deletes only the test DB and companions, while **both sentinels remain 100% intact**.
2. `test_02_safe_temp_dir_cleanup_preserves_dir_when_sentinels_present`: Confirms `safe_temp_dir_cleanup` deletes test DB but refuses to remove the parent directory when sentinel files are present.
3. `test_03_safe_temp_dir_cleanup_removes_dir_when_no_sentinels`: Confirms clean removal when only expected test artifacts were present.
4. `test_04_narrow_cleanup_refuses_directory_deletion`: Confirms `narrow_cleanup_artifacts` errors when given a directory path.
5. `test_05_safe_temp_dir_cleanup_rejects_non_temp_directory`: Confirms `safe_temp_dir_cleanup` raises `ValueError` if passed a project directory.
6. `test_06_powershell_safety_engine_blocks_recursive_data_deletion`: Validates that `safety_engine` blocks `Remove-Item -Path "...\hey-jev\data" -Recurse -Force` and related recursive commands.
7. `test_07_appdata_persistent_locations_integrity`: Confirms persistent storage configurations point to `%APPDATA%` and never to relative `data/`.

---

## 6. Test Suite & Truth Verification Results

### 6.1 Data Integrity Test Suite
```
.venv\Scripts\pytest.exe test_data_integrity_cleanup.py -v
============================= 7 passed in 0.90s ==============================
```

### 6.2 Combined Phase 29, 29.1, 29.2 & Integrity Suite
```
.venv\Scripts\pytest.exe test_phase29.py test_phase29_1.py test_phase29_2.py test_data_integrity_cleanup.py -v
======================== 48 passed in 67.57s (0:01:07) ========================
```

### 6.3 Complete Repository Regression Suite
```
.venv\Scripts\pytest.exe -q
832 passed in 311.70s (0:05:11)
```
- **Total Tests:** 832 passed
- **Failures:** 0
- **Skips:** 0

### 6.4 Preflight Truth Verification
```
.venv\Scripts\python.exe preflight.py
======================================================================
PREFLIGHT SUMMARY: 13 pass, 0 fail, 1 warn
======================================================================
```

---

## 7. Remaining Uncertainties & User Guidance

- **Are there any remaining unverified files?** No. All files deleted were captured in line 16684 of the session transcript.
- **Is any user intervention required?** No user action or backup restoration is needed. All persistent application databases in `%APPDATA%\HeyJev` and `%APPDATA%\hey-jev` are healthy and active.
- **Rule for Future Phases:** Never use `Remove-Item ... -Recurse` on project paths. All test fixtures must use `tempfile.mkdtemp()` or `:memory:`, and cleanup must be executed narrowly via `test_cleanup_utils.py`.
