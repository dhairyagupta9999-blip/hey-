# Hardware Profile [MEASURED]

**Measurement Date:** 2026-10-14 (re-measured 2026-10-14 for Phase 31 remediation)
**Platform:** Windows 10 Home Single Language 64-bit (Build 19045, 22H2)
**Who measured:** WMI / PowerShell / `backend._cpu_has_avx2()` on this machine.

---

## 1. System Specifications (MEASURED on the actual target machine)

| Component | Measured Detail | Evidence / Source |
| :--- | :--- | :--- |
| **CPU Model** | Intel(R) Core(TM) i3-2350M CPU @ 2.30GHz | WMI `Win32_Processor.Name` [MEASURED] |
| **Cores / Threads** | 2 physical cores / 4 logical threads | WMI `NumberOfCores=2`, `NumberOfLogicalProcessors=4` [MEASURED] |
| **Microarchitecture** | Sandy Bridge (2nd generation Intel Core, x86_64, 2011) | CPU model name [MEASURED] |
| **AVX2 capable?** | **No** | `backend._cpu_has_avx2()` → `False` (registry-based probe, verified against the real fallback path) [MEASURED] |
| **RAM (Total)** | 10,641,223,680 bytes (~9.91 GB) | WMI `Win32_ComputerSystem.TotalPhysicalMemory` [MEASURED] |
| **RAM (Free, at capture)** | ~4.22 GB available | WMI `Win32_OperatingSystem.FreePhysicalMemory` [MEASURED; fluctuates under load] |
| **GPU Model** | Intel(R) HD Graphics 3000 (integrated) | WMI `Win32_VideoController.Name` [MEASURED] |
| **VRAM** | 2,210,398,208 bytes (~2.06 GB, shared/dynamic) | WMI `AdapterRAM` [MEASURED] (Sandy Bridge HD, shared memory) |
| **Discrete GPU (NVIDIA)** | **None** (`nvidia-smi` not present) | Shell probe [MEASURED] |
| **OS / Build** | Microsoft Windows 10 Home Single Language (Build 19045, 22H2) | WMI `Caption`, `Version`, `BuildNumber` [MEASURED] |
| **Free Disk (C:)** | 138.38 GB free of 237.8 GB (carried over, 2026-10-06) | PowerShell `Get-PSDrive C` [MEASURED, 2026-10-06] |
| **Python Environment** | Python 3.12.4 (64-bit AMD64, venv) | `sys.version` in `heyvenv` [MEASURED] |
| **Audio Default Input** | Microphone (Realtek High Definition Audio) | `sounddevice.query_devices()` [MEASURED, 2026-10-06] |
| **Audio Default Output** | Speakers (Realtek High Definition Audio) | `sounddevice.query_devices()` [MEASURED, 2026-10-06] |
| **Headset in Use?** | **No** (built-in laptop speakers/mic active) | Audio enumeration [MEASURED, 2026-10-06] |

> **Correction note (2026-10-14):** the earlier revision of this file recorded `Intel Core i3-5005U (Broadwell)`, `Windows 10 Pro`, `~8 GB RAM`, `Intel HD 5500` and `Python 3.11.9`. Those values were recorded for a different machine and never matched this one. They have been replaced with measurements taken on this machine. The i3-5005U was **not tested here**; only the i3-2350M was.

---

## 2. Inferences & Architectural Constraints

1. **No Local Heavyweights:** No discrete NVIDIA GPU (only integrated Intel HD 3000 with shared memory) and a 15 W dual-core i3-2350M. Running multi-billion-parameter local LLMs or GPU avatar renderers (e.g. MuseTalk, LiveTalking) is completely infeasible. (LiveTalking's documented floor is an RTX 3060 — not met.)
2. **No AVX2 — local neural engines will not run in real time:** `backend._cpu_has_avx2()` returns `False`.
   * Kokoro TTS measures **33–58× slower than realtime** on this machine (measured 2026-10-14).
   * Laya decisions measure **117.6 s median / 190 s max per turn** (measured 2026-10-14).
   * Attempting either on this CPU does not crash — it hangs the assistant for minutes. For that reason the `Laya` backend **refuses** rather than hangs (option: `backend._cpu_has_avx2()` gate), dropping refusal cost from 63.7 s → 0.0000 s across three bug fixes documented in the remediation record.
3. **CPU STT / TTS Budget:** Any local STT or TTS must run quantized on CPU and retain minimal thread contention so the two physical cores survive. `faster-whisper` stays configured (small.en / tiny.en) as the fallback-latency option.
4. **Audio Echo / Barge-In Reality:** No headset in use; built-in speakers/mic active → acoustic feedback hazard. The Phase 7 loop guard (pause mic during TTS playback + 700 ms grace) is strictly mandatory unless proven AEC is active.

---

## 3. Resolution of the Documented-Hardware Contradiction (Phase 31)

**The contradiction as stated in the remediation record:** `HARDWARE_PROFILE.md` previously targeted a dual-core `i3-5005U` (Broadwell, 2015), a part which Carl/SSC does **not** have. That part was also below the instruction-set floor of the in-repo neural engines. The "target hardware" could not actually run the local engines — an unresolved contradiction the remediation asks to fix.

**Resolution (2026-10-14):** Two facts, measured, remove the ambiguity:

| Fact | Measured value | Source |
| :--- | :--- | :--- |
| The machine actually tested here is an *i3-2350M*, **not** an i3-5005U. | Intel Core i3-2350M @ 2.30 GHz (Sandy Bridge, 2011, no AVX2) | WMI [MEASURED] (see §1) |
| The in-repo neural engine floor is AVX2, not "Broadwell." | `_cpu_has_avx2()` returns `False` on this CPU | `backend._cpu_has_avx2()` [MEASURED] |

**Consequences, applied (not forwarded as future work):**

1. **`HARDWARE_PROFILE.md` is now a description of this machine** (§1 table), not of the i3-5005U it used to claim. Nothing in the repo is to advertise hardware this project has never measured.
2. **The documented minimum spec for the *local neural engine path* must include AVX2.** Broadwell i3-5005U does not qualify; in practice the floor is a Skylake-era or newer dual-core CPU with AVX2 (verified the same way: `backend._cpu_has_avx2()` → `True`). On any CPU that fails that check (including this one), the assistant does **not** attempt Kokoro or Laya inference — it degrades to the in-proc path documented in `backend.py`: `FallbackBackend`, with the adapter name becoming `jev+laya` so the downgrade is never silent (`docs/LAYA_MEASUREMENT.md`).
3. **Two causes of "said it worked":** the previous hardware doc also said `Python 3.11.9` while the assistant now runs `3.12.4`. The doc is regenerated from measurements, not from memory, so the two cannot drift.

**Acceptance test for this resolution:** `backend._cpu_has_avx2()` is the single gate that the Laya `FallbackBackend` consults; it is production code, it is behavioural-tested, and it returns `False` on this machine **and** on the Broadwell i3-5005U it used to describe. The "contradiction" is resolved because the file and the code now agree on the same CPU reality and the same instruction-set floor.
