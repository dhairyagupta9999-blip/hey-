# Hey Jev: Known Issues, User Bug Reports & Diagnostic Pathways

This document catalogs the primary real-world user issues encountered during personal operation of Hey Jev. Each issue is analyzed with separation between **User Reports**, **Demonstrated Code Facts**, **Unverified Hypotheses**, and **Diagnostic Pathways**.

---

## 1. Issue: Slow Calculator Launching Latency

### 1.1 The User Report
When the user asks Hey Jev to open Calculator (e.g., `"Hey Jev, open Calculator"` or holding Right Alt and saying `"open Calculator"`), there is a noticeable multi-second delay before the Calculator application window appears on screen.

### 1.2 Demonstrated Code Facts
1. **Omission from `apps.json`:**
   Inspection of `apps.json` shows entries for `spotify`, `slack`, `chrome`, `brave`, `safari`, `vscode`, `finder`, `messages`, `notes`, `whatsapp`, `mail`, `calendar`, `terminal`, `zoom`, `cursor`, `mimestream`, `discord`, `notion`, and `claude_code`.
   **`calculator` and `calc` are completely missing from `apps.json`.**
2. **Impact on Decision Routing in `siri.py`:**
   - In `siri.py`, Tier 1 options for the `app` question in the TypeSafe API battery are dynamically populated from `apps.json`.
   - Because `calculator` is not in `apps.json`, the remote TypeSafe decision model cannot select `calculator` as a recognized app.
   - If the request does not trigger `is_tier2_direct_match` (for example, if phrased as `"calculator"`, `"bring up calculator"`, or if the phonetic transcript is slightly imprecise), the command is sent to the remote TypeSafe API.
   - The remote API returns `app: none` -> `kind: clarify`.
   - Only after receiving this response does `siri.py` fall back to Tier 2 (`resolve_tier2`) at line 605.
   - **Cost of this fallback:** The user pays a 1,200ms+ network roundtrip latency penalty before local resolution even begins.
3. **Execution Mechanism in `actions_win.py`:**
   - In `actions_win.py`, `KNOWN_APP_LAUNCHERS["calculator"] = ["calc.exe"]`.
   - Line 267 executes: `subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)`.
   - On Windows 10/11, `calc.exe` is a lightweight proxy stub that delegates to the modern UWP package `CalculatorApp.exe`. Spawning `cmd.exe` to spawn `calc.exe` to trigger UWP package initialization incurs process-spawning overhead.

### 1.3 Unverified Hypotheses
- **Hypothesis A:** If `extract_target("open calculator")` succeeds, `is_tier2_direct_match` returns `True`, routing immediately to `tier2_resolver.py`. If this occurs, the delay is caused entirely by Start Menu / UWP scanning in `app_index.py` and `cmd /c start calc.exe` execution.
- **Hypothesis B:** If STT produces extraneous punctuation or filler words (e.g., `"open Calculator, please."`), `is_tier2_direct_match` fails, forcing the 1,200ms remote TypeSafe API roundtrip before falling back to Tier 2.

### 1.4 Diagnostic Entrypoints
To isolate the exact source of latency:
1. Run a timed Python test measuring the components:
   ```powershell
   .venv\Scripts\python.exe -c "import time, subprocess, app_index; t0 = time.perf_counter(); app_index.get_app_index().find_app('calculator'); print('AppIndex search:', time.perf_counter() - t0); t1 = time.perf_counter(); subprocess.Popen(['cmd', '/c', 'start', '', 'calc.exe']); print('cmd start:', time.perf_counter() - t1)"
   ```
2. Inspect `actions.jsonl` in `%APPDATA%\HeyJev\` to see whether the logged latency is under Tier 1 or Tier 2.
3. Trace `siri.py:handle` timing logs (`trace_line`).

---

## 2. Issue: Failed YouTube Opening

### 2.1 The User Report
When the user asks Hey Jev to open YouTube (e.g., `"open YouTube"`, `"open youtube in chrome"`, or `"play music on youtube"`), either nothing happens, the browser opens to the wrong place, or an error is reported.

### 2.2 Demonstrated Code Facts
1. **Target Mapping in `system_targets.py`:**
   - In `system_targets.py`:
     ```python
     KNOWN_SITES = {
         "youtube": "https://www.youtube.com",
         ...
     }
     ```
   - For a bare command like `"open youtube"`, `system_targets.resolve_system_target("open youtube")` resolves to `{"type": "website", "url": "https://www.youtube.com"}`.
   - Line 124 executes: `webbrowser.open(res["url"])`.
   - `webbrowser.open()` delegates to Windows default protocol handler. If the Windows default browser registration is corrupted, non-standard, or blocked by a background process, `webbrowser.open` returns silently without launching.
2. **Compound Phrasing Gaps:**
   - If the user says `"open youtube in chrome"`, `target_extractor.extract_target("open youtube in chrome")` extracts `target = "youtube in chrome"`.
   - `"youtube in chrome"` is **NOT** in `KNOWN_SITES`.
   - It is not a raw URL (`is_url` returns `None`).
   - `resolve_system_target` returns `None`.
   - It falls back to `app_index.find_app("youtube in chrome")`, which fails to find any installed application.
   - It falls back to `file_finder.find_files("youtube in chrome")`, which finds no files.
   - It finally falls back to Tier 3 agent (`tier3_agent.py`) or reports clarification.
3. **Headless Execution in `browser_adapter.py`:**
   - When Tier 3 or mission planning invokes `BrowserAgent`:
     ```python
     self.manager.start(headless=task.context.get("headless", True))
     ```
   - `headless` defaults to `True`!
   - If `BrowserAgent` executes a navigation goal, it starts Chrome/Edge in `--headless=new` mode in the background. The user sees no browser window open on screen, creating the appearance of complete failure.

### 2.3 Unverified Hypotheses
- **Hypothesis A:** Users phrased the request with a browser qualifier (e.g. `"in Chrome"` or `"on Edge"`), causing target extraction to miss the bare site name.
- **Hypothesis B:** When Tier 3 was triggered, it ran headless CDP automation instead of launching the user's visible desktop browser.
- **Hypothesis C:** Windows default URL association for `https://` failed to launch or was swallowed by an unresponsive browser process.

### 2.4 Diagnostic Entrypoints
1. Test target extraction behavior:
   ```powershell
   .venv\Scripts\python.exe -c "import system_targets, target_extractor; print(target_extractor.extract_target('open youtube in chrome')); print(system_targets.resolve_system_target('open youtube in chrome'))"
   ```
2. Verify behavior of `webbrowser.open("https://www.youtube.com")` in the current terminal environment.
3. Check `browser_adapter.py` invocation logs in `%APPDATA%\HeyJev\Hey Jev.log`.

---

## 3. Issue: Unreliable Recognition, Audio Drops & Repeated Requests

### 3.1 The User Report
Spoken voice interaction can feel inconsistent:
- The assistant sometimes does not hear the user or drops commands.
- Commands are occasionally misheard (acoustic confusion).
- Repeating a command immediately after a failure causes the assistant to ignore it.
- In some situations, the assistant appears to respond to its own speech or repeat itself.

### 3.2 Demonstrated Code Facts
1. **Whisper `tiny.en` Model Limitations:**
   - `stt.py` uses `faster-whisper` running the `tiny.en` model on CPU.
   - `tiny.en` is optimized for low CPU usage and fast transcription, but has a higher word error rate (WER) on accents, background ambient noise, or short single-word commands than larger models (`base.en` or `small.en`).
   - Whisper has well-known hallucinations on ambient noise (e.g., outputting `"Thank you."`, `"Bye."`, or repeating the prompt text).
2. **Duplicate Transcript Suppression (Guard b):**
   - Line 515 of `siri.py`:
     ```python
     if check_and_update_transcript_dedup(text):
         print(f"  [loop guard b] Ignoring duplicate transcript within 5.0s: {text!r}")
         return
     ```
   - If a user speaks a command (e.g., `"open calculator"`), the action fails or lags, and the user repeats `"open calculator"` within 5 seconds, **Guard b silently drops the second command**. The user perceives this as the assistant going deaf or ignoring them.
3. **Audio Settling & Speaker Feedback Guard (Guard d):**
   - `is_audio_playing_or_settling()` in `audio_io.py` and `is_matching_recent_reply()` in `siri.py` were added specifically to prevent the microphone from hearing assistant speech through the speakers.
   - If the audio settling window (typically 0.7s) is active when the user starts speaking, the start of their utterance is clipped, resulting in dropped or partial transcriptions.

### 3.3 Unverified Hypotheses
- **Hypothesis A:** Hardware microphone input level or Windows audio enhancements (noise suppression / echo cancellation) are causing audio clipping before `sounddevice` receives the buffer.
- **Hypothesis B:** The 5.0-second dedup window in Guard b is excessively long for interactive voice correction, where users naturally repeat failed commands within 2–3 seconds.

### 3.4 Diagnostic Entrypoints
1. Inspect `%APPDATA%\HeyJev\Hey Jev.log` for lines containing:
   - `[loop guard b] dropped identical transcript:`
   - `[loop guard d] dropped reply echo:`
   - `[stt]` transcription latency and raw heard text.
2. Run `record_test_clips.py` to record sample user audio and evaluate Whisper transcription accuracy locally.
3. Test adjusting `DEDUP_WINDOW` in `siri.py` or inspect if `tiny.en` can be safely upgraded to `base.en` without violating latency thresholds.

---

## 4. Guidance for Claude Code When Fixing These Issues

1. **Do Not Rewrite Working Systems:** The multi-tier architecture is solid and passes 832 regression tests. Narrow, targeted fixes are required.
2. **Trace First:** Add debug logging to `siri.py:handle` to measure exact stage latencies before modifying routing logic.
3. **Preserve Safety:** Never disable loop guards entirely. If adjusting Guard b dedup timing, keep at least a 1.5–2.0 second window to prevent genuine speaker echo loops.
4. **Benchmark Verification:** Every fix must be verified against `preflight.py` and `pytest` to prevent regressions.
