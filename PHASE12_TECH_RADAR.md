# Hey Jev — Phase 12 Technology Radar: Windows Computer Use & UI Automation

**Target Platform:** Windows 10 Pro x64 / Windows 11  
**Hardware Profile:** Intel Core i3-5005U (2 cores / 4 threads @ 2.00 GHz), ~7.93 GB RAM, Intel HD Graphics 5500  
**Phase Objective:** Autonomous Windows Computer Operator via Goal-Directed Agent Harness  
**Date:** October 2026  

---

## 1. Executive Summary

Phase 12 equips Hey Jev with the capability to operate the Windows operating system autonomously. To achieve reliable, low-latency, and safe execution on a dual-core 8 GB CPU, Jev rejects brute-force coordinate-clicking and heavy vision models as the primary interaction mode. Instead, Jev adopts a **Semantic-UI-First** hierarchy:
1. **Windows UI Automation (UIA) & COM Accessibility Tree**
2. **Native Win32 APIs (User32, Shell32, Advapi32)**
3. **Structured PowerShell & Shell Operations**
4. **Selective High-Resolution Screenshot Capture** (only when semantic inspection fails)
5. **Coordinate-Based Interaction** (strictly as last-resort fallback)

Below is the technology evaluation of modern open-source Windows automation projects and frameworks.

---

## 2. In-Depth Evaluation of Candidate Technologies

### 1. `pywinauto` (Windows UI Automation & Win32 Python Binding)
* **Repository / URL:** [https://github.com/pywinauto/pywinauto](https://github.com/pywinauto/pywinauto)
* **License:** BSD 3-Clause
* **Maintenance / Activity:** Mature, stable, widely adopted in Windows automation.
* **Windows Compatibility:** 100% native (supports Windows 7 through 11).
* **Python Compatibility:** Python 3.8 – 3.12+ (already present in Hey Jev `.venv` as v0.6.9).
* **Dependency Footprint:** Minimal (~15 MB, pure Python + `comtypes` / `pywin32`).
* **CPU / RAM Requirements:** Very low (<30 MB RAM per inspection, negligible CPU idle).
* **Capabilities:**
  * Semantic UI tree navigation (`backend="uia"`)
  * Control finding by name, control type, automation ID, class name, role
  * Direct invocation of UIA control patterns (`InvokePattern`, `ValuePattern`, `SelectionPattern`)
  * Native Win32 window management (`backend="win32"`)
* **Security & Privilege:** Runs in user space; standard UAC boundaries apply.
* **Offline Capability:** 100% offline; zero external network calls.
* **Suitability for Hey Jev:** **Optimal.** Gives Jev semantic access to Windows buttons, menus, edit fields, and lists without requiring slow screenshot inference.
* **Decision:** **ADOPT** (Core Semantic Engine).

---

### 2. `sbroenne/mcp-windows` (Windows Automation MCP Server)
* **Repository / URL:** [https://github.com/sbroenne/mcp-windows](https://github.com/sbroenne/mcp-windows)
* **License:** MIT
* **Maintenance / Activity:** Active; created specifically for LLM tool use via Model Context Protocol.
* **Windows Compatibility:** Windows 10/11.
* **Python Compatibility:** Standalone binary / Python wrapper.
* **Dependency Footprint:** Low to Moderate.
* **CPU / RAM Requirements:** ~40 MB RAM.
* **Capabilities:**
  * Application control by name
  * UI control clicking and typing via UI Automation
  * Window management and system settings
* **Security & Privilege:** Exposes broad automation endpoints over stdio JSON-RPC. Must be gated by Jev's `safety_engine.py`.
* **Suitability for Hey Jev:** High as an optional external MCP backend. However, running an extra persistent process on a dual-core machine incurs unnecessary IPC overhead when native python bindings exist.
* **Decision:** **STUDY & SUPPORT VIA ADAPTER BOUNDARY** (Optional MCP Backend, Native First).

---

### 3. `sandraschi/windows-computer-use-mcp`
* **Repository / URL:** [https://github.com/sandraschi/windows-computer-use-mcp](https://github.com/sandraschi/windows-computer-use-mcp)
* **License:** MIT
* **Maintenance / Activity:** Active community development.
* **Windows Compatibility:** Windows 10/11.
* **Python Compatibility:** Python 3.10+.
* **Dependency Footprint:** High (Pulls Tesseract OCR, OpenCV, PyAutoGUI, Web server dashboard).
* **CPU / RAM Requirements:** High (>450 MB RAM, heavy CPU spikes during OCR and frame template matching).
* **Capabilities:** Mouse/keyboard simulation, screenshot OCR, template matching, mission dashboard.
* **Security & Privilege:** Unrestricted mouse movement and click generation without foreground window validation.
* **Suitability for Hey Jev:** Low for local execution on Intel i3-5005U. OpenCV and Tesseract OCR would severely degrade voice responsiveness and Kokoro TTS speech synthesis.
* **Decision:** **REJECT for Core / STUDY for Vision Fallback Concepts.**

---

### 4. `AIDotNet/Windows-MCP.Net`
* **Repository / URL:** [https://github.com/AIDotNet/Windows-MCP.Net](https://github.com/AIDotNet/Windows-MCP.Net)
* **License:** MIT
* **Maintenance / Activity:** Moderate.
* **Windows Compatibility:** Windows 11 / 10 x64.
* **Python Compatibility:** None (Requires .NET 10 runtime SDK).
* **Dependency Footprint:** Heavy (Requires .NET runtime installation).
* **CPU / RAM Requirements:** High (>200 MB RAM).
* **Suitability for Hey Jev:** Unsuitable. Hey Jev is a Python-native application packaged with PyInstaller; adding a .NET 10 dependency introduces immense distribution friction.
* **Decision:** **REJECT.**

---

### 5. Native Win32 API (`ctypes` + `pywin32`)
* **Technology:** Microsoft Win32 Subsystem (`user32.dll`, `shell32.dll`, `advapi32.dll`, `gdi32.dll`)
* **License:** Built-in Windows OS APIs.
* **Maintenance:** Permanent OS standard.
* **Dependency Footprint:** Zero additional packages (uses Python `ctypes` and existing `pywin32`).
* **CPU / RAM Requirements:** Negligible (<5 ms execution latency, near-zero RAM).
* **Capabilities:**
  * Exact window enumeration (`EnumWindows`, `GetWindowText`, `GetForegroundWindow`)
  * Window state control (`ShowWindow`, `SetForegroundWindow`, `MoveWindow`)
  * System parameters & wallpaper (`SystemParametersInfoW` with `SPI_SETDESKWALLPAPER`)
  * Process lifecycle (`TerminateProcess`, `OpenProcess`, `GetWindowThreadProcessId`)
* **Suitability for Hey Jev:** **Essential.** Provides instantaneous, deterministic operations for window focus, wallpaper changes, and system settings.
* **Decision:** **ADOPT** (Core System & Settings Layer).

---

### 6. Windows PowerShell 5.1 / 7 (Structured Subprocess Tooling)
* **Technology:** Microsoft Windows PowerShell
* **License:** Built-in OS Component (Windows PowerShell 5.1) / MIT (PowerShell 7).
* **Dependency Footprint:** Zero (pre-installed on every Windows 10/11 system).
* **Capabilities:**
  * Structured filesystem queries (`Get-ChildItem`, `Sort-Object`)
  * Process introspection (`Get-Process`)
  * Registry and configuration modifications
* **Security Concerns:** PowerShell can execute arbitrary malicious scripts.
* **Mitigation in Hey Jev:** Strictly gated by `safety_engine.py` policy checks, timeout enforcement (15s ceiling), non-interactive `-NoProfile` execution, and complete audit trail recording.
* **Decision:** **ADOPT** (Structured Execution Layer).

---

### 7. Screenshot Capture (`Pillow` / `PIL.ImageGrab` & Win32 GDI)
* **Technology:** `PIL.ImageGrab` / GDI BitBlt
* **License:** Historical Permission Notice and Disclaimer (HPND).
* **Dependency Footprint:** Zero (Pillow is already installed in `.venv`).
* **CPU / RAM Requirements:** ~15 MB per captured frame, ~35 ms capture time on Intel HD 5500.
* **Capabilities:** Instantaneous desktop screen capture, cropping, saving to disk artifact.
* **Guideline for Jev:** Used **selectively on demand** for verification and fallback, never in continuous 30fps loops.
* **Decision:** **ADOPT** (Selective Visual Verification Layer).

---

## 3. Technology Comparison Matrix

| Technology | Category | License | Memory Overhead | Latency | Semantic UI? | Hardware Feasibility (2C/8GB) | Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **pywinauto (UIA)** | Python Library | BSD-3 | ~25 MB | 20–80 ms | **Yes (Full UIA)** | Excellent | **ADOPT** |
| **Win32 APIs (ctypes)** | Native OS | N/A | <2 MB | <5 ms | **Yes (Handles)** | Flawless | **ADOPT** |
| **PowerShell (Gated)** | System Tool | OS Standard | ~35 MB | 150–350 ms | **Yes (Objects)** | Good | **ADOPT** |
| **Pillow (ImageGrab)** | Python Library | HPND | ~15 MB | ~35 ms | No (Pixels) | Good (Selective) | **ADOPT** |
| **sbroenne/mcp-windows** | MCP Server | MIT | ~40 MB | 50–120 ms | Yes | Moderate (IPC) | **STUDY (Adapter)** |
| **sandraschi/comp-use** | Python Framework | MIT | >450 MB | 800–2500 ms | Partial (OCR) | Poor (CPU bound) | **REJECT** |
| **AIDotNet (.NET 10)** | .NET Server | MIT | >200 MB | 100–300 ms | Yes | Unusable in PyEnv | **REJECT** |

---

## 4. Architectural Selection for Hey Jev

Hey Jev chooses a **Native-First, Semantic-Driven Architecture**:
1. **`WindowsComputerTools`** wraps `pywinauto` (UIA), `ctypes` (Win32), `Pillow` (screenshots), and gated PowerShell into a unified, provider-neutral toolkit.
2. **`WindowsComputerAdapter`** binds these tools to the Phase 11 `AgentAdapter` interface.
3. Every mutating action strictly implements the **OBSERVE → DECIDE → ACT → OBSERVE AGAIN → VERIFY** lifecycle.
4. Concurrency is strictly bounded (`max_local_agents = 1`), preventing CPU starvation on the dual-core target PC.
