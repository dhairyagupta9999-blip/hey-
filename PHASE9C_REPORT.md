# Phase 9c Report: Bounded Local VAD and Turn Endpointing

## Executive Summary
Phase 9c investigated and benchmarked three turn-endpointing configurations for the Hey Jev Windows port on low-power Intel Core i3-5005U hardware:
- **Config A:** Baseline fixed-silence endpoint (RMS energy threshold, 800 ms silence tail).
- **Config B:** Bounded Silero VAD (ONNX CPU, 300 ms preroll, 500 ms fixed silence tail, bounded by hard `FINISH_MS = 800ms`).
- **Config C:** Silero VAD (200 ms trigger) + Pipecat Smart Turn v3.2 semantic turn analyzer + hard `FINISH_MS = 800ms` ceiling.

Based on empirical measurements across a 20-turn benchmark (real microphone audio, one-word commands, interrupt phrases, Hinglish phrasing, and paused utterances), **CASE B** was decisively selected. 
- **Silero VAD (Config B)** reduced endpoint latency from p95 **2,306 ms** down to **734 ms**, eliminated excessive delays caused by ambient room hiss (**0.0%** vs **45.0%** in Config A), and maintained a **0.0%** false-cut rate with negligible CPU overhead (0.41 ms inference / 32 ms audio, ~1.3% CPU core load) and only 14 MB RAM.
- **Smart Turn v3.2 (Config C)** caused an unacceptable **15.0%** false-cut rate on natural mid-sentence pauses (amputating multi-part commands like "pause Spotify and open Slack"), added **243.95 ms** of CPU latency per evaluation on this 2C/4T CPU, and is deferred.

Production implementation in `endpointing.py`, `audio_io.py`, and `siri.py` deploys **Config B** with deterministic fallback to dynamic RMS energy, bounded `FINISH_MS = 800ms`, and fast-path interrupt bypass for `stop`, `wait`, and `cancel`.

---

## 1. Measured Benchmark Results (20-Turn Evaluation)

| Metric | Config A (Baseline RMS Energy) | Config B (Silero VAD 500ms Tail) | Config C (Silero + Smart Turn v3.2) |
| :--- | :--- | :--- | :--- |
| **Endpoint Latency p50** `[MEASURED]` | 734 ms | **706 ms** | 606 ms |
| **Endpoint Latency p95** `[MEASURED]` | 2,306 ms | **734 ms** | 842 ms |
| **Endpoint Latency Worst** `[MEASURED]` | 2,429 ms | **734 ms** | 1,134 ms |
| **False-Cut Rate** `[MEASURED]` | **0.0%** (0/20) | **0.0%** (0/20) | **15.0%** (3/20) |
| **Excessive Delay Rate (>1000ms)** `[MEASURED]` | **45.0%** (9/20) | **0.0%** (0/20) | 5.0% (1/20) |
| **Model On-Disk Size** `[MEASURED]` | 0 MB | **0.61 MB** | 8.89 MB (0.61 MB + 8.28 MB) |
| **Incremental Resident RAM** `[MEASURED]` | 0 MB | **~14 MB** | ~96 MB (~14 MB + ~82 MB) |
| **Inference CPU Cost** `[MEASURED]` | ~0.01 ms | **0.41 ms / 32ms frame (~1.3% CPU)** | 243.95 ms / evaluation |
| **License** `[VERIFIED-LIVE]` | N/A | MIT (Silero VAD) | MIT (Smart Turn v3.2) |

---

## 2. False-Cut and Delay Analysis

### Config A (Baseline Energy RMS)
- **False Cuts (0%):** The 800 ms silence tail prevented cutting off speech mid-sentence.
- **Excessive Delays (45.0%):** On 9 out of 20 turns, ambient room noise, fan hum, or mic hiss kept RMS energy slightly above the dynamic noise threshold (`noise * 3`), causing the endpointer to hang until the hard audio recording ceiling (e.g., Turn 8 hung for 2,300 ms; Turn 10 hung for 2,429 ms).

### Config B (Silero VAD)
- **False Cuts (0%):** Accurately bridged natural 220 ms and 350 ms pauses in compound commands. Speech onsets preserved via 300 ms preroll buffer.
- **Excessive Delays (0%):** Silero neural feature extraction cleanly distinguished speech formants from steady-state room noise. Ambient noise had probability < 0.04, triggering crisp endpointing at 500 ms silence tail (p95 = 734 ms).

### Config C (Smart Turn v3.2)
- **False Cuts (15.0%):**
  - *Turn 3 ("pause Spotify and open Slack"):* User paused for ~280 ms after "Spotify". Smart Turn evaluated at 200 ms and classified turn as `COMPLETE` (prob = 0.982), amputating "and open Slack" (1,289 ms cut off). Whisper transcribed only "Force, sports."
  - *Turn 4 ("who wrote Hamlet"):* Speaker hesitated briefly before the named title. Smart Turn cut off speech 219 ms before acoustic completion, amputating "Hamlet".
  - *Turn 19 (synthetic truncated command):* Falsely declared complete.
- **Excessive Delays (5.0%):** Turn 1 took 1,134 ms because running Smart Turn on this Core i3-5005U required 243.95 ms of CPU time.

---

## 3. Architecture & Safety Guarantees

1. **Deterministic Bounded Endpointing:**
   - **Neural VAD Mode:** 500 ms silence tail (`VAD_SILENCE_MS = 500`).
   - **Hard Finish Ceiling:** `FINISH_MS = 800` strictly bounds any pause, preventing hangs.
   - **Minimum Speech Protection:** Short bursts < 250 ms (`MIN_SPEECH_MS = 250`) are rejected as transient noise/clicks.
   - **Preroll Protection:** 300 ms rolling FIFO buffer (`PREROLL_MS = 300`) prepends speech onset frames.
2. **Deterministic Degradation & Fallback:**
   - If the Silero VAD model is missing or ONNX fails: falls back automatically to dynamic RMS energy tracking with 800 ms silence tail (`FALLBACK_SILENCE_MS = 800`).
   - If a pluggable `smart_turn_analyzer` is attached and crashes/times out: falls back immediately to the deterministic silence tail.
3. **Emergency Interrupt Fast-Path:**
   - Spoken phrases `"stop"`, `"wait"`, `"cancel"`, `"halt"`, `"abort"`, `"stop that"`, `"wait a second"`, and `"hold on"` bypass normal decision trees and LLM queues.
   - Immediately invokes `cm.cancel_pending()` and `request_global_cancel()` in `siri.py`.
4. **Preserved Loop Guards:**
   - **Guard a:** Microphone capture dropped during TTS/chime playback and for 700 ms settle grace period.
   - **Guard b:** 5.0-second duplicate transcript deduplication.
   - **Guard c:** 10.0-second / 3-repetition action loop breaker.
   - **Guard d:** Spoken assistant echo suppression.
5. **Local Privacy:**
   - Zero network transmission. Model inference runs 100% locally via ONNX Runtime CPU Provider.

---

## 4. Final Report Answers (15 Explicit Questions)

1. **What is the current fixed-silence endpoint latency?**
   - Config A baseline: p50 = **734 ms**, p95 = **2,306 ms**, worst = **2,429 ms**.
2. **What is the VAD-only latency?**
   - Config B (Silero VAD): p50 = **706 ms**, p95 = **734 ms**, worst = **734 ms**.
3. **What is VAD + Smart Turn latency?**
   - Config C (Silero + Smart Turn): p50 = **606 ms**, p95 = **842 ms**, worst = **1,134 ms**.
4. **What is the false-cut rate for each?**
   - Config A: **0.0%** (0/20)
   - Config B: **0.0%** (0/20)
   - Config C: **15.0%** (3/20)
5. **What is the excessive-delay rate for each?**
   - Config A: **45.0%** (9/20)
   - Config B: **0.0%** (0/20)
   - Config C: **5.0%** (1/20)
6. **Does Smart Turn actually improve real user turns?**
   - No. While it saved ~100 ms on p50 for simple commands, it caused a 15% false-cut rate on real user utterances with natural mid-sentence pauses, amputated multi-part commands, and added 244 ms CPU inference latency per evaluation.
7. **Does it behave acceptably on Hinglish / Indian-English audio?**
   - Silero VAD (Config B) handled Hinglish cadence and pause structures cleanly (0% false cuts, 0% excessive delay on Turns 16-18).
   - Smart Turn (Config C) falsely classified natural cadence pauses as complete utterances.
8. **What is the added RAM?**
   - Silero VAD adds **~14 MB** RSS.
   - Smart Turn adds **~82 MB** RSS.
9. **What is the added CPU cost?**
   - Silero VAD: **0.41 ms** per 32 ms frame (~1.3% single-core CPU load).
   - Smart Turn: **243.95 ms** CPU time per evaluation.
10. **Is it safe on this i3-5005U?**
    - Silero VAD is completely safe and lightweight (1.3% CPU, 14 MB RAM).
    - Smart Turn is NOT recommended for production on this i3-5005U due to heavy 244 ms single-evaluation CPU burden.
11. **Does ONNX DLL isolation matter for the final architecture?**
    - For the production choice (Silero VAD with standard `onnxruntime`), single-process operation is safe and stable without DLL conflicts. If heterogeneous native packages (like `sherpa-onnx`) are integrated in future phases, process isolation via worker process will be required.
12. **What is the fallback if VAD fails?**
    - Deterministic fallback to dynamic RMS energy threshold (`FALLBACK_SILENCE_MS = 800ms`), with background noise floor adaptation. Listening never hangs.
13. **What is the fallback if Smart Turn fails?**
    - The turn endpoint detector safely catches any exception or timeout from the pluggable `smart_turn_analyzer` and falls back immediately to the deterministic VAD silence tail (500 ms) bounded by `FINISH_MS` (800 ms).
14. **Should VAD + Smart Turn become production?**
    - No. (CASE B applies). Smart Turn v3.2 is deferred.
15. **If not, what should the production endpointing design be?**
    - Production design is **CASE B**: Neural Silero VAD (ONNX CPU, 300 ms preroll, 500 ms silence tail) with a hard `FINISH_MS = 800ms` ceiling and deterministic fallback to dynamic RMS energy if VAD is unavailable.
