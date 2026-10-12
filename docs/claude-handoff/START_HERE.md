# Hey Jev: Claude Code Handoff — START HERE

Welcome to **Hey Jev**. This repository contains a production-grade, voice-first Windows 10/11 AI desktop assistant and autonomous agent engine developed across 29 architectural phases.

This document is your entrypoint. Read it before inspecting code, modifying files, or executing tasks.

---

## 1. Quick Repository Facts

- **Operating System:** Windows 10 / Windows 11 x64 (Native Windows, not WSL)
- **Repository Root:** `C:\Users\prabh\.gemini\antigravity\scratch\hey-jev`
- **Active Git Branch:** `windows-port`
- **Base Commit for Handoff:** Commit `f74aa08` (50 commits ahead of `origin/windows-port`)
- **Python Runtime:** Python 3.11.9 in virtual environment `.venv`
- **Virtualenv Python:** `.venv\Scripts\python.exe`
- **Virtualenv Pytest:** `.venv\Scripts\pytest.exe`
- **Automated Regression Suite:** 832 passed, 0 failed, 0 skipped (verified on current checkout)
- **Preflight Truth Suite:** 13 pass, 0 fail, 1 warning (verified on current checkout)
- **Working Tree State:** Clean (no untracked files, no uncommitted changes)

---

## 2. Core Philosophy & Rules of Engagement

1. **Zero Hallucination:** Never claim a feature is operational simply because automated unit tests pass. Automated tests use mocks for external services; real desktop operation involves Windows UI Automation, Win32 APIs, microphone hardware, and audio playback.
2. **Preserve User Data & State:** All persistent user data resides in `%APPDATA%\HeyJev` and `%APPDATA%\hey-jev`. **NEVER** run recursive deletion on data directories or wipe local databases.
3. **Reproduce Before Fixing:** When investigating user-reported issues (such as slow Calculator launching or YouTube opening issues), write a reproduction script or trace the exact execution path first.
4. **Inspect Before Editing:** Read the real source code before proposing changes. Hey Jev has an established architecture across 29 phases; do not invent competing architectures.
5. **Secret Hygiene:** Never commit credentials or API keys. Secrets belong in Windows Credential Manager (`keyring` under `com.jevsiri.keys`) or a local, git-ignored `.env` file.

Detailed operating rules are defined in [CLAUDE_WORKING_RULES.md](file:///C:/Users/prabh/.gemini/antigravity/scratch/hey-jev/docs/claude-handoff/CLAUDE_WORKING_RULES.md).

---

## 3. Verified Startup Commands

All commands must be executed using the repository virtual environment from the repository root:

### Desktop Graphical Application (Primary)
Launches the native PySide6 7-tab assistant window, system tray icon, and background task engine:
```powershell
.venv\Scripts\python.exe app.py
```
*(Alternative CLI launcher: `.venv\Scripts\python.exe siri.py --ui`)*

### Headless Voice Daemon / CLI
Runs the background voice loop (push-to-talk via Right Alt or wake-word):
```powershell
.venv\Scripts\python.exe siri.py
```

### Preflight Truth Verification
Runs live hardware and subsystem verification against the actual PC environment:
```powershell
.venv\Scripts\python.exe preflight.py
```

### Full Automated Regression Suite
Runs all 46 test suites (832 tests):
```powershell
.venv\Scripts\pytest.exe -q
```

### OpenDots MCP Interoperability Gateway
Starts the standalone Model Context Protocol (MCP) Streamable HTTP gateway on loopback:
```powershell
.venv\Scripts\python.exe opendots_gateway.py --host 127.0.0.1 --port 8765
```

---

## 4. Handoff Documentation Index

Read the documentation set in the following recommended order:

| Document | Purpose |
| :--- | :--- |
| **[START_HERE.md](file:///C:/Users/prabh/.gemini/antigravity/scratch/hey-jev/docs/claude-handoff/START_HERE.md)** | This document: Quick start, verified commands, and orientation. |
| **[PROJECT_VISION.md](file:///C:/Users/prabh/.gemini/antigravity/scratch/hey-jev/docs/claude-handoff/PROJECT_VISION.md)** | The product philosophy, two-brain / three-tier architecture, and intended UX. |
| **[CURRENT_STATE.md](file:///C:/Users/prabh/.gemini/antigravity/scratch/hey-jev/docs/claude-handoff/CURRENT_STATE.md)** | Exact Git state, test baseline, verified entry points, and immediate next actions. |
| **[ARCHITECTURE.md](file:///C:/Users/prabh/.gemini/antigravity/scratch/hey-jev/docs/claude-handoff/ARCHITECTURE.md)** | Code-grounded architecture: Voice, decision tiers, UIA, missions, and storage. |
| **[CAPABILITY_INVENTORY.md](file:///C:/Users/prabh/.gemini/antigravity/scratch/hey-jev/docs/claude-handoff/CAPABILITY_INVENTORY.md)** | Complete registry of capabilities categorized by verification status. |
| **[DEVELOPMENT_HISTORY.md](file:///C:/Users/prabh/.gemini/antigravity/scratch/hey-jev/docs/claude-handoff/DEVELOPMENT_HISTORY.md)** | Historical evolution across Phases 1–29.2.1 reconstructed from Git commits. |
| **[KNOWN_ISSUES.md](file:///C:/Users/prabh/.gemini/antigravity/scratch/hey-jev/docs/claude-handoff/KNOWN_ISSUES.md)** | User-reported problems (Calculator latency, YouTube opening, STT accuracy) & diagnostics. |
| **[TESTING_AND_VALIDATION.md](file:///C:/Users/prabh/.gemini/antigravity/scratch/hey-jev/docs/claude-handoff/TESTING_AND_VALIDATION.md)** | Test execution, preflight checks, manual acceptance guide, and test gaps. |
| **[SECURITY_AND_PRIVACY.md](file:///C:/Users/prabh/.gemini/antigravity/scratch/hey-jev/docs/claude-handoff/SECURITY_AND_PRIVACY.md)** | Safety boundaries, approval gates, secret redaction, and OS permission reality. |
| **[DEPENDENCIES_AND_EXTERNAL_PROJECTS.md](file:///C:/Users/prabh/.gemini/antigravity/scratch/hey-jev/docs/claude-handoff/DEPENDENCIES_AND_EXTERNAL_PROJECTS.md)** | External dependencies, OpenDots relationship, and runtime prerequisites. |
| **[CLAUDE_WORKING_RULES.md](file:///C:/Users/prabh/.gemini/antigravity/scratch/hey-jev/docs/claude-handoff/CLAUDE_WORKING_RULES.md)** | Mandatory guidelines, safety constraints, and quality requirements. |

---

## 5. First Steps for Claude Code

When taking over work on this repository:
1. Run `git status` to verify you are on branch `windows-port` with a clean tree.
2. Review **[CURRENT_STATE.md](file:///C:/Users/prabh/.gemini/antigravity/scratch/hey-jev/docs/claude-handoff/CURRENT_STATE.md)** to understand the latest changes.
3. Review **[KNOWN_ISSUES.md](file:///C:/Users/prabh/.gemini/antigravity/scratch/hey-jev/docs/claude-handoff/KNOWN_ISSUES.md)** before attempting any bug fixes.
4. **DO NOT** begin a new feature phase (Phase 30) until instructed. The immediate priority is stability, latency reduction, and verifying real desktop user experience.
