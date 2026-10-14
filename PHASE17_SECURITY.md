# Phase 17: Background Automation & Event Watcher Security Architecture

## 1. Executive Summary

Phase 17 introduces background tasks, filesystem and process watchers, priority scheduling, and event-driven automation to Hey Jev on Windows.
Because autonomous background execution presents unique security and stability challenges, Phase 17 implements strict architectural safety boundaries:

1. **Non-Elevated User Desktop Execution**: Zero Windows Services; zero Session 0 isolation issues; strictly non-elevated user context.
2. **Path Containment & Drive Root Rejection**: Watching system directories (`C:\Windows`, `C:\Program Files`) or root drives (`C:\`, `/`) is strictly rejected by policy.
3. **Audio Preemption & Concurrency Gating**: Foreground voice and wake-word interactions have absolute priority over background execution slots.
4. **Infinite Event Loop Prevention**: Event chains are capped at depth 5, burst events are deduplicated with fingerprint hashing, and cooldowns prevent runaway event cascades.
5. **Credential & Secret Protection**: Memory and notification stores scrub credentials and API tokens.

---

## 2. Security Boundaries & Threat Model

| Threat / Risk Vector | Mitigating Control | Implementation |
| :--- | :--- | :--- |
| **Runaway Filesystem Indexing** | Recursive traversal depth capped at 3; root drive and OS directories blocked. | `FilesystemWatcher._is_safe_target()` raises `ValueError` on root drives. |
| **Infinite Event Cascades** | Strict chain depth bounding; MD5 fingerprint deduplication; cooldown enforcement. | `EventRuleEngine.max_event_chain_depth = 5`, fingerprint cache, `can_fire()` checks. |
| **Audio Stutter / CPU Starvation** | Background task preemption upon voice/wake detection; concurrency slots (1 FG, 1 BG). | `ResourceGovernor.set_voice_active(True)` pauses background slots instantly. |
| **Credential / Token Leakage** | All memory inserts and notifications scrub API keys, bearer tokens, and passwords. | `memory_model.sanitize_content()` and regex redactors. |
| **Session 0 Desktop Isolation** | In-process priority scheduler and user-session execution instead of Windows Service. | `InProcessSchedulerBackend` runs in user session with full GUI and audio visibility. |
| **Orphaned Crash Tasks** | Startup reconciliation resets stranded `RUNNING` tasks to `READY` or `FAILED`. | `RecoveryManager.reconcile_startup_tasks()`. |

---

## 3. Path Safety Policy

The `FilesystemWatcher` enforces path safety before beginning directory observation:

```python
def _is_safe_target(self, target: str) -> bool:
    if not target or not target.strip():
        return False
    norm = os.path.abspath(target)
    drive, tail = os.path.splitdrive(norm)
    if tail in ("\\", "/", ""):
        return False  # Rejects C:\ or D:\ root drives
    forbidden = {
        os.path.abspath(os.environ.get("SystemRoot", "C:\\Windows")),
        os.path.abspath(os.environ.get("ProgramFiles", "C:\\Program Files")),
    }
    return norm not in forbidden
```

Any attempt to watch an entire disk or OS core triggers an immediate rejection.

---

## 4. Loop Safety & Recursion Limits

Event rules can trigger tasks, and tasks can emit events. To prevent infinite recursion:
- Each event carries an internal `_chain_depth` counter.
- If `_chain_depth >= max_event_chain_depth (5)`, the event rule engine drops further actions.
- Event deduplication caches MD5 hashes of `(event_type, task_id, data)` within 200 ms to collapse burst signals into single executions.
- Per-rule `cooldown_sec` prevents tight trigger loops.

---

## 5. Dual-Core CPU & Audio Priority

On the dual-core Intel Core i3-5005U:
- Audio capture and faster-whisper transcription must never be starved.
- When voice or wake activity is detected, `governor.set_voice_active(True)` locks background slots to 0.
- Running background tasks yield or defer heavy execution until the voice turn concludes.
- Quiet hours (11:00 PM to 7:00 AM) automatically suppress non-critical notifications.
