# Hardware Profile [MEASURED]

**Measurement Date:** 2026-10-06  
**Platform:** Windows 10 Pro 64-bit (Build 19045, 22H2)

---

## 1. System Specifications

| Component | Measured Detail | Evidence / Source |
| :--- | :--- | :--- |
| **CPU Model** | Intel(R) Core(TM) i3-5005U @ 2.00GHz | WMI `Win32_Processor` [MEASURED] |
| **Cores / Threads** | 2 physical cores / 4 logical threads | WMI `NumberOfCores=2`, `NumberOfLogicalProcessors=4` [MEASURED] |
| **CPU Architecture** | 5th-Gen Intel Core (Broadwell, 14nm, x86_64) | WMI [MEASURED] |
| **RAM (Total)** | 8,282,368 KB (7.90 GB visible / 8 GB physical) | WMI `Win32_OperatingSystem.TotalVisibleMemorySize` [MEASURED] |
| **RAM (Free)** | ~1.35 – 1.42 GB available | WMI `FreePhysicalMemory` [MEASURED] |
| **GPU Model** | Intel(R) HD Graphics 5500 (Integrated) | WMI `Win32_VideoController` [MEASURED] |
| **VRAM** | 1,073,741,824 bytes (1,024 MB shared/dynamic) | WMI `AdapterRAM` [MEASURED] |
| **Discrete GPU (NVIDIA)** | **None** (`nvidia-smi` not present) | Shell probe [MEASURED] |
| **OS / Build** | Microsoft Windows 10 Pro 64-bit (Build 19045) | WMI `Caption`, `Version`, `BuildNumber` [MEASURED] |
| **Free Disk (C:)** | 138.38 GB free of 237.8 GB | PowerShell `Get-PSDrive C` [MEASURED] |
| **Python Environment** | Python 3.11.9 (64-bit AMD64, MSC v.1938) | `sys.version` in `.venv` [MEASURED] |
| **Audio Default Input** | Microphone (Realtek High Definition Audio) | `sounddevice.query_devices()` [MEASURED] |
| **Audio Default Output** | Speakers (Realtek High Definition Audio) | `sounddevice.query_devices()` [MEASURED] |
| **Headset in Use?** | **No** (Built-in laptop speakers/mic active) | Audio enumeration index [1, 3] [MEASURED] |

---

## 2. Inferences & Architectural Constraints

1. **No Local Heavyweights:** Because there is **no discrete NVIDIA GPU** (only integrated Intel HD 5500 with shared memory) and CPU is a 15W dual-core i3-5005U, running multi-billion parameter local LLMs or GPU avatars (e.g. MuseTalk, LiveTalking) is completely infeasible.
2. **CPU STT / TTS Budget:** Any local STT or TTS must run quantized on CPU (e.g., int8 ONNX / Sherpa-onnx / Kokoro-onnx / faster-whisper tiny.en or base.en) and maintain minimal thread contention so the dual-core CPU does not choke.
3. **Audio Echo / Barge-In Reality:** Because **no headset is in use** and built-in speakers/mic are active, acoustic feedback is a real hazard. The Phase 7 loop guard (pause microphone during TTS playback + 700 ms grace) is strictly mandatory unless proven AEC is active.
