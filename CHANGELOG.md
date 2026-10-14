# Changelog

All notable changes to the Windows 10/11 port of `hey-jev` will be documented in this file.

## [Phase 5: Enhancements] - 2026-10-03
- Integrated WinRT System Media Transport Controls (SMTC) for direct, state-aware media control across all active Windows media players (Spotify, YouTube in browser, Media Player).
- Added `wake_word.py` engine supporting low-CPU streaming openWakeWord detection alongside baseline Whisper-prefix regex gate.
- Implemented optional Spotify Web API client (`spotify_api.py`) with OAuth token handling.
- Added comprehensive About dialog and detailed Windows documentation in `README.md` with strict attribution to `henryklunaris/hey-jev` and credit to referenced Laya projects.

## [Phase 4: Packaging & Distribution] - 2026-10-03
- Created Windows PyInstaller specification (`hey_jev.spec`) building a standalone `--onedir --windowed` distribution with zero console flashing.
- Implemented `autostart.py` supporting Windows Task Scheduler at-logon execution without requiring admin privileges, backed by HKCU Run fallback.
- Added Settings toggle for Windows startup and integrated system tray minimizing behavior.
- Generated multi-resolution Windows executable icon (`assets/icon.ico`) from `assets/icon.png`.
- Verified clean cold start of packaged distribution (`dist/Hey Jev/Hey Jev.exe`).

## [Phase 3: Window & Bubble] - 2026-10-03
- Rebuilt assistant UI tab-for-tab in PySide6 with Windows 11 dark slate aesthetics, 14px anti-aliased status dot, and segmented mode switch.
- Implemented frameless, topmost, click-through, per-monitor DPI aware dictation bubble (`bubble.py`) with 24-bar live RMS animated waveform.
- Added live 500ms countdown timers, dictionary editor for `%APPDATA%/HeyJev/vocabulary.json`, apps table, dictation history log viewer, and Windows Credential Manager key management.
- Dynamic privacy contract display updating according to active decision backend (Jev vs Laya).
- Window close interception preserving listening loop with hide-to-tray semantics.

## [Phase 2: Action Parity] - 2026-10-03
- Implemented and verified all 33 actions from the Action Parity Table for Windows 10/11 x64.
- Added comprehensive app launcher supporting Win32, UWP, and packaged apps via URI schemes and shell:AppsFolder.
- Implemented safe process-tree termination with 1.5s timeout for tray-minimizing apps (Slack, Discord, Spotify).
- Added state-aware media transport via SendInput (VK_MEDIA_*), pycaw master/session volume, dark mode registry toggling with WM_SETTINGCHANGE, and non-blocking background action pool.

## [Phase 1.5: Decision Backend Abstraction & Benchmark] - 2026-10-03
- Introduced backend-agnostic `DecisionBackend` interface supporting both TypeSafe hosted Jev and local open-weight Laya models.
- Added `LayaBackend` running in-process on CPU with background pre-warming, uniform-prior confidence calibration, and automatic fallback to Jev.
- Built 105-phrase benchmark test suite across §07 acceptance categories and evaluated latency, memory commitment, and host reliability in `BENCHMARK_REPORT.md`.

## [Phase 1: Core Loop] - 2026-10-03
- Ported core loop to Windows 10/11 with single asyncio event loop, WASAPI audio capture (`sounddevice`), and local `faster-whisper` (`small.en`, CPU int8).
- Implemented global non-admin Right-Alt push-to-talk hook and Whisper-prefix wake mode.
- Ported secrets store from macOS Keychain to Windows Credential Manager via `keyring` with `.env` priority.
- Built monotonic timers and reminders surviving restarts (`%APPDATA%/HeyJev/timers.json`) with dual voice alerts and native Windows Toast notifications.
- Added pure in-process MP3/WAV playback via `sounddevice` + `soundfile`/PyAV (no winsound).
- Implemented trace logging to `%APPDATA%/HeyJev/Hey Jev.log` adhering to the exact macOS trace format with `[backend: jev]`.
