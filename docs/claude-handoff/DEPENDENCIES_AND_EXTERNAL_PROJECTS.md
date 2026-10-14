# Hey Jev: Dependencies & External Projects

This document catalogs the external software dependencies, runtime packages, hardware requirements, and external repositories associated with Hey Jev.

---

## 1. Python Runtime & Environment

- **Python Version:** Python 3.11 or 3.12 (64-bit on Windows; 3.12.4 tested)
- **Virtual Environment Path:** `C:\Users\prabh\.gemini\antigravity\scratch\hey-jev\.venv`
- **Virtualenv Executables:**
  - Python: `.venv\Scripts\python.exe`
  - Pip: `.venv\Scripts\pip.exe`
  - Pytest: `.venv\Scripts\pytest.exe`

---

## 2. Core Dependencies Analysis

### 2.1 Dependencies in `requirements.txt`

| Package | Version Spec | Purpose in Hey Jev | Verified Installed Version |
| :--- | :--- | :--- | :--- |
| `requests` | `>=2.32` | HTTP client for remote TypeSafe API and OpenRouter | 2.34.2 |
| `python-dotenv` | `>=1.0` | Loading `.env` file configuration and secrets | 1.2.4 |
| `numpy` | `>=2.0` | Audio buffer processing for STT and audio playback | 2.4.6 |
| `sounddevice` | `>=0.5` | Microphone recording and audio playback | 0.5.6 |
| `soundfile` | `>=0.13` | Audio file I/O (.wav encoding/decoding) | 0.14.0 |
| `faster-whisper` | `>=1.2` | Local fast speech-to-text on CPU/GPU | 1.2.1 |
| `keyboard` | `>=0.13.5` | Global low-level hotkey hooks (Right Alt PTT) | 0.13.5 |
| `keyring` | `>=24.3.0` | Windows Credential Manager integration | 25.7.0 |
| `psutil` | `>=5.9.0` | Process tree inspection, window discovery, and killing | 7.2.2 |
| `pycaw` | `>=20240210` | Core Windows Audio Endpoint Volume sessions | 20260927 |
| `pywinauto` | `>=0.6.8` | UI Automation (UIA) desktop inspection | 0.6.9 |
| `PySide6` | `>=6.6.0` | Native Qt6 desktop GUI (7 tabs, Mission Center) | 6.11.2 |
| `pystray` | `>=0.19.5` | System tray icon and minimize-to-tray lifecycle | 0.19.5 |
| `pillow` | `>=10.0.0` | Image processing for tray icons and waveform | 12.3.0 |
| `pyinstaller` | `>=6.0.0` | Desktop binary freezing and packaging | 6.22.3 |
| `winsdk` | `>=1.0.0b10` | WinRT API bindings for System Media Transport Controls | 1.0.0b10 |
| `openwakeword` | `>=0.6.0` | Local neural wake-word detection engine | 0.6.0 |
| `laya` | `>=0.3.24` | Speculative local decision model integration | 0.3.24 |

---

### 2.2 Critical Packages Installed in `.venv` but Missing from `requirements.txt`

The following packages are currently installed in the development virtual environment and used by code or tests, but were not recorded in the original `requirements.txt`:

1. **`pytest` (v9.1.1):**
   - **Usage:** Essential test runner for all 46 test files (832 automated tests).
   - **Recommendation:** Add to `requirements.txt` or a `requirements-dev.txt`.
2. **`websocket-client` (v1.9.2):**
   - **Usage:** Used by `browser_session.py` to establish WebSocket connections to Chrome/Edge DevTools Protocol (CDP) on port 9555.
3. **`RapidFuzz` (v3.14.6):**
   - **Usage:** Used by `app_index.py` for high-performance fuzzy matching of application names.
4. **`kokoro-onnx` (v0.6.1) & `onnxruntime` (v1.30.0):**
   - **Usage:** Used by `kokoro_tts.py` for offline neural text-to-speech fallback.
5. **`pywin32` (v312):**
   - **Usage:** Used for native Win32 API calls (`win32gui`, `win32con`, `win32process`, `win32api`).
6. **`Send2Trash` (v2.1.0):**
   - **Usage:** Used by `file_finder.py` for safe file deletion without permanent loss.

---

## 3. External Projects & Ecosystem Relationships

### 3.1 OpenDots External Workspace
- **Upstream Repository:** `https://github.com/CopilotKit/OpenDots`
- **Local Clone Location:** `C:\Users\prabh\.gemini\antigravity\scratch\OpenDots`
- **Architectural Boundary:**
  - OpenDots is an external Next.js/CopilotKit desktop and browser workspace.
  - OpenDots is **NOT** a dependency of Hey Jev, and Hey Jev does **NOT** run inside OpenDots.
  - Hey Jev acts as the authoritative agent engine and exposes a Model Context Protocol (MCP) Streamable HTTP gateway (`opendots_gateway.py` on `127.0.0.1:8765`).
  - OpenDots connects to Hey Jev as an optional MCP client to create, inspect, and execute missions.
  - Compatibility was verified in Phase 29.2 using the official `@modelcontextprotocol/sdk` (Node.js).

### 3.2 External Windows MCP Server (`windows-mcp`)
- Referenced in `windows_mcp_client.py` and Preflight Check 6.
- Represents an external Windows UI automation MCP binary.
- If not running, Hey Jev falls back safely to its native `uia_operator.py` (Computer Operator 2.0) and Win32 actions.

---

## 4. Hardware & Operating System Requirements

- **OS:** Windows 10 (Build 19041+) or Windows 11 x64.
- **CPU:** 4+ cores recommended (Whisper STT uses 2 threads for CPU inference).
- **RAM:** Minimum 8 GB (16 GB recommended when running PySide6 GUI + Whisper model in memory).
- **Audio:** Functioning microphone and speakers.
- **Network:** Internet access required for Fish Audio TTS, TypeSafe API, and OpenRouter chit-chat. Offline mode functions for local App Index, Win32 actions, UIA, and mission planning.
