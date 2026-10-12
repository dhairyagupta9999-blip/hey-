# Hey Jev: Security, Privacy & Safety Architecture

This document describes the safety boundaries, access controls, secrets management, and privacy policies enforced across the Hey Jev codebase.

---

## 1. Operating System Permission Reality

> [!CAUTION]
> **No Kernel or Container Sandboxing:**
> Hey Jev runs directly on the host Windows operating system as the currently logged-in user (`C:\Users\prabh`).
> - There is **NO** Docker container, hypervisor, or OS-level sandbox isolating the Python process.
> - The application inherits full read and write permissions to the user's home directory, files, registry keys, and local network.
> - Any child process spawned by Hey Jev (`subprocess.Popen`) executes with the current user's privileges.
> - Application safety controls described below are **software-level policy filters**, not operating system security boundaries.

---

## 2. Safety Engine & Risk Classification

All actions passing through the assistant or mission engines are evaluated by `safety_engine.py`.

### 2.1 Five-Tier Risk Classification
The risk engine classifies every action into one of five risk tiers:

| Risk Tier | Criteria & Examples | Execution Policy |
| :--- | :--- | :--- |
| **SAFE** | Volume adjustments, media playback (play/pause/next), reading window titles, clock/timer queries. | Auto-approved; executes immediately. |
| **LOW** | Launching known applications (`Notepad`, `Spotify`, `Calculator`), window minimize/maximize/snap. | Auto-approved; logged to audit ledger. |
| **MEDIUM** | Closing running applications, file searches, switching active windows, reading document text. | Requires validation or logged confirmation. |
| **HIGH** | `close_all` (closing all windows), opening dangerous file extensions (`.exe`, `.bat`, `.ps1`), running external scripts. | **Mandatory Interactive User Confirmation** via voice or GUI modal. |
| **CRITICAL** | System-altering commands, process tree termination, registry edits, network configuration changes. | **Strictly Gated**; blocked by default unless explicit multi-step human confirmation is provided. |

### 2.2 Hardcoded Command Denylist
`safety_engine.py` maintains an immutable denylist that rejects dangerous terminal and PowerShell patterns:
- Blocked administrative shells: `powershell.exe -Command`, `cmd.exe /c` executing unapproved shell scripts.
- Blocked destructive operations:
  - Recursive directory deletion: `Remove-Item -Recurse -Force`, `rmdir /s /q`, `del /s /q`.
  - Disk formatting & partition manipulation: `format`, `diskpart`, `chkdsk /f`.
  - Registry deletion: `reg delete`, `Remove-ItemProperty`.
  - Dangerous Windows utilities: Win+R Run dialog execution.

### 2.3 Interactive Confirmation Manager
When an action is classified as `HIGH` or `CRITICAL`:
1. `ConfirmationManager.request_high_risk_confirmation(action, message, exec_fn)` is called.
2. The assistant suspends execution and states the confirmation request (e.g., *"This action will close all open windows. Should I proceed?"*).
3. A strict 8-second timeout is armed.
4. If the user explicitly confirms (*"yes"*, *"proceed"*, *"do it"*), the action executes.
5. If the user says *"no"*, *"cancel"*, or if the timeout expires without an affirmative answer, the action is aborted.

---

## 3. Data Protection & File Deletion Safety

### 3.1 Persistent Application Data Protection
Following the Phase 29.2.1 data integrity audit, `safety_engine.py` was hardened with specific path denylists protecting:
- `%APPDATA%\HeyJev`
- `%APPDATA%\hey-jev`
- Repository data directories (`./data`)

Any command attempting recursive deletion targeting these directories is immediately rejected with a `SecurityViolationError`.

### 3.2 Safe Test Artifact Cleanup
Automated test scripts must never invoke raw PowerShell deletion commands. All test cleanup operations must use `test_cleanup_utils.py`, which restricts file deletion strictly to matching disposable temporary files (e.g. `test_run_*.db`).

---

## 4. Secrets Management & Credential Storage

### 4.1 Storage Architecture
Hey Jev employs a two-tier secrets management strategy defined in `secrets_store.py`:
1. **Windows Credential Manager (Production):**
   - API keys are stored encrypted within Windows Credential Manager under the service name `com.jevsiri.keys` via the `keyring` library (`keyring>=24.3.0`).
   - Protected by Windows DPAPI (Data Protection API) tied to the user's Windows login.
2. **Local `.env` File (Development Fallback):**
   - Developer keys can be placed in a `.env` file in the repository root.
   - The `.env` file is explicitly ignored in `.gitignore` and must **NEVER** be committed to source control.
   - Precedence: A value in `.env` overrides Windows Credential Manager for rapid development iteration.

### 4.2 Managed Secrets
- `TYPESAFE_API_KEY`: Required for speculative voice intent evaluation (Tier 1).
- `FISH_AUDIO_API_KEY`: Required for cloud neural text-to-speech synthesis.
- `OPENROUTER_API_KEY`: Required for general Q&A chit-chat answers via Haiku.
- `OPENCODE_ZEN_API_KEY` / `OPENCODE_API_KEY`: Optional alternative for Q&A and coding tasks.
- `OPENAI_API_KEY`: Optional alternative for GPT-4o-mini answers and dictation.

### 4.3 Automated Secret Redaction in Logs
`safety_engine.py:redact_secrets(text)` automatically sanitizes all log outputs, error messages, and trace lines before writing to disk:
- Detects OpenAI/Anthropic/OpenRouter key patterns: `sk-[a-zA-Z0-9_\-]{20,}` -> `[REDACTED_API_KEY]`
- Detects GitHub tokens: `ghp_[a-zA-Z0-9]{20,}` -> `[REDACTED_TOKEN]`
- Masks raw bearer tokens in HTTP headers.
- Preflight Check 10 independently scans disk logs on startup to ensure zero credentials exist on disk.

---

## 5. Network Boundaries & Interoperability Security

### 5.1 OpenDots MCP Gateway (`opendots_gateway.py`)
- **Loopback Binding:** The HTTP server binds exclusively to `127.0.0.1`. It does not listen on `0.0.0.0` or external network adapters.
- **Bearer Token Authentication:** Every incoming HTTP request must include:
  `Authorization: Bearer <token>` matching `JEV_OPENDOTS_BEARER_TOKEN`. Requests with missing or invalid tokens are rejected immediately with HTTP 401.
- **Concurrency & Rate Limiting:** The gateway enforces a strict connection cap (`JEV_OPENDOTS_MAX_CONCURRENCY`, default 8) and input payload size limits (1MB maximum) to prevent Denial of Service.
- **Protocol Compliance:** Fully implements the Model Context Protocol (MCP) Streamable HTTP transport standard (Protocol version `2025-03-26`).

---

## 6. Audit Logging & Undo Ledger

### 6.1 Security Audit Ledger (`actions.jsonl`)
- Every executed action across Tier 1, Tier 2, and Tier 3 is recorded to an append-only JSON Lines file located at `%APPDATA%\HeyJev\actions.jsonl`.
- Each record contains: timestamp, tier, action name, arguments, risk level, status (`success` or `failed`), and execution latency.

### 6.2 Undo Stack (`undo_manager.py`)
- Reversible actions (such as volume changes, window positioning, or file moves) push inverse operations onto the `UndoManager` stack.
- Saying `"undo"` or clicking Undo reverts the most recent reversible action.
