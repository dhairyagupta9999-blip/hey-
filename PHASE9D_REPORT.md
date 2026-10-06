# Phase 9d Report: Wake Word / "Hey Jev" Gate

## Executive Summary

Phase 9d evaluated whether to replace or augment the always-listening Whisper-prefix wake gate with a lightweight dedicated wake-word detector (`dscripka/openWakeWord`), gated by the Silero VAD implemented in Phase 9c.

Empirical evaluation, licensing audit, and architectural validation were completed on the host hardware (Intel Core i3-5005U, 2C/4T @ 2.00GHz, 7.93 GB RAM, Windows 10 Pro x64).

**Key Conclusion:**
- **Licensing Blocker:** Pretrained openWakeWord models and the official training feature datasets (`openwakeword_features` on Hugging Face) are licensed under **CC-BY-NC-SA 4.0** (Non-Commercial, ShareAlike). Shipping these in Hey Jev (MIT) would violate licensing. A commercial-grade custom model is **`[BLOCKED: LICENSING]`** until trained on independently curated, commercially permissive data.
- **Acoustic Reality:** Pretrained models like `hey_jarvis_v0.1.onnx` score only 0.0014 on real user audio (`clip_05.wav`: *"Hey Jev, dark mode on"*), failing to trigger on "Hey Jev".
- **Whisper Baseline Reliability:** The existing faster-whisper prefix gate achieves **100% positive recall** on real user speech, seamlessly handles single-breath compound commands without audio truncation, and benefits from Phase 9c's Silero VAD gating to incur **0.0% background CPU during silence**.
- **Decision:** **CASE B & CASE C (Deferred)**. Production default remains `whisper` (`DEFAULT_WAKEWORD_ENGINE = "whisper"`). `openwakeword` is integrated behind the feature flag `HEYJEV_WAKEWORD_ENGINE=openwakeword` with automatic fallback to Whisper if a custom model is absent.
- **Test Suite Status:** **178 passed** (100% green, +12 new Phase 9d tests, 0 regressions). Preflight: 11 pass, 0 fail, 3 warn.

---

## Detailed Answers to the 19 Explicit Questions

### 1. Did you evaluate openWakeWord? Which version/commit?
**Yes.** Evaluated `openwakeword` version **0.6.0** (installed in `.venv` with `onnxruntime 1.23.2` and `tflite-runtime`).

### 2. What are the licenses of: openWakeWord Python code, the pretrained model files, the custom model training code, and the dataset used to train the official models?
- **openWakeWord Python Code:** **Apache-2.0** (per repository `LICENSE` file).
- **Pretrained Model Files (`hey_jarvis_v0.1.onnx`, `alexa_v0.1.tflite`, etc.):** **CC-BY-NC-SA 4.0** (Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International).
- **Custom Model Training Code (notebooks/scripts):** **Apache-2.0**.
- **Official Training Features Dataset (`openwakeword_features` on Hugging Face):** **CC-BY-NC-SA 4.0**. The dataset comprises precomputed embeddings derived from AudioSet (YouTube clips) and Common Voice, licensed strictly for non-commercial research with share-alike requirements.

### 3. Can a commercial or open-source product legally ship openWakeWord pretrained models under MIT? Under Apache-2.0?
**No.** Pretrained models are under **CC-BY-NC-SA 4.0**.
- CC-BY-NC-SA 4.0 prohibits commercial use (**NC**) and requires derivative works to be distributed under the same license (**SA**).
- Hey Jev is licensed under **MIT** (fully permissive, commercial use permitted). Distributing CC-BY-NC-SA model weights within an MIT distribution creates a direct license incompatibility and legal contamination. Shipping under Apache-2.0 is equally prohibited because Apache-2.0 permits commercial distribution, which CC-BY-NC-SA explicitly forbids.

### 4. What is the licensing status of a custom-trained model produced using openWakeWord's official training notebook?
**Legally Contaminated (CC-BY-NC-SA 4.0).**
The official training workflow relies on precomputed negative and background acoustic features hosted on Hugging Face (`davidscripka/openwakeword_features`), which are explicitly licensed under **CC-BY-NC-SA 4.0**. Because machine learning models trained directly on and regularized by these embeddings constitute derivative works under the ShareAlike clause, the resulting model weights inherit the CC-BY-NC-SA 4.0 restriction.

### 5. Did you find a clean, commercially permissive wake-word alternative? If so, what? If not, what is the path to one?
- **Current Alternatives Surveyed:**
  - `Picovoice Porcupine`: Commercial proprietary license; free tier requires cloud token and enforces monthly user caps. Not open source or offline permissive.
  - `Snowboy`: Discontinued, unmaintained, abandoned C++ codebase with severe Python 3.11 build issues.
  - `PocketSphinx / CMU Sphinx`: BSD-like, but acoustic model accuracy and false-alarm rejection on modern conversational speech are unacceptably poor on Windows.
  - `sherpa-onnx (k2-fsa)`: Apache-2.0 engine, evaluated in Phase 9b. Provides streaming keyword spotting (KWS), but requires training a transducer/Zipformer or embedding model specifically for "Hey Jev".
- **Clean Path Forward:**
  Use the Apache-2.0 `openwakeword` architecture or `sherpa-onnx` KWS, but train the custom "Hey Jev" acoustic model exclusively using:
  1. Permissively licensed public domain speech datasets (e.g. LibriSpeech under CC-BY 4.0 / Public Domain).
  2. Synthetically generated TTS variations (e.g. Piper / Kokoro permissive voices).
  3. Commercially clean environmental noise backgrounds (e.g. FreeSound CC-0 or MUSAN).
  Zero reliance on the CC-BY-NC-SA `openwakeword_features` dataset.

### 6. Is a custom "Hey Jev" model available?
- **Status:** **`[BLOCKED: LICENSING]`**.
- **Blocker Details:**
  1. **Licensing:** The upstream precomputed feature pipeline is CC-BY-NC-SA 4.0. Training an MIT-clean model requires gathering and computing ~50 GB of commercial-grade background features from scratch.
  2. **Compute & Environment:** Generating multi-speaker synthetic variations of "Hey Jev" with room impulse responses and training an ONNX acoustic model requires a GPU workstation; the local dual-core host (Intel i3-5005U) lacks CUDA and cannot feasibly train acoustic models within acceptable timeframes.
  3. **Data:** No pre-existing clean open-source dataset exists specifically for the phrase "Hey Jev".

### 7. What are the measured benchmark results for the Whisper-prefix baseline?
Measured across 34 clips (15 real mic recordings + 19 synthetic test clips, total 144.8s audio):
- **Positive Recall:** **100.0%** (7/7 positive clips detected, including real user audio `clip_05.wav`).
- **False Accept Rate on Negatives:** **7.4%** (2/27). Both false accepts occurred on "Hey Jeff" clips (`synth_hey_jeff_01.wav` and `synth_hey_jeev_01.wav`), which is by design due to intentional phonetic tolerance in `config.NAMES` (`jeff`, `jeev`).
- **False Accepts/Hour on Silence:** **0.0** (0 false accepts on 60-second pure silence test `silence_60s.wav`).
- **Latency:**
  - p50 STT Latency: **10,488 ms**
  - p95 STT Latency: **13,120 ms**
  - Worst Latency: **15,290 ms**
- **CPU Cost:** ~100% of 2 CPU threads during the ~10s transcription window; **0.0% idle CPU** during silence due to Silero VAD gating.

### 8. What are the measured benchmark results for openWakeWord?
Measured using `hey_jarvis_v0.1.onnx` as engine benchmark:
- **Positive Recall (on target phrase):** **80.0%** (4/5 at threshold 0.50; 100.0% at threshold <= 0.25).
- **False Accept Rate on Negatives:** **0.0%** (0/29 across all conversational phrases, commands, and silence).
- **False Accepts/Hour on Silence:** **0.0** (0 triggers across `silence_60s.wav`).
- **Latency Per Step:** **5.77 ms** per 80ms audio chunk (1280 samples at 16kHz).
- **CPU During Speech:** **7.2% to 9.0%** of a single core (~3.6% to 4.5% of total CPU).
- **Idle CPU with VAD Gating:** **0.0%** (silence is gated by RMS/Silero before entering model prediction).
- **Incremental RAM:** **156.11 MB** RSS (Total resident: 170.13 MB).

### 9. Did you evaluate on real user audio (clip_05.wav)? What did each engine do on that clip?
**Yes.** Evaluated on `test_clips/clip_05.wav` (real microphone recording of user saying *"Hey Jev, dark mode on"*):
- **Whisper Baseline:**
  - Transcribed text: `"Hey Jev, dark mode on."`
  - Wake Regex Match: **Matched** (`m.group() = "Hey Jev,"`, command extracted = `"dark mode on"`).
  - STT Latency: 10,848 ms.
  - Result: **Detected (PASS)**.
- **openWakeWord Engine Benchmark (`hey_jarvis_v0.1.onnx`):**
  - Max Prediction Score: **0.0014** (far below 0.50 threshold).
  - Result: **Not Detected (FAIL)**.
  - *Observation:* Confirms that generic acoustic models cannot substitute for an authentic, trained "Hey Jev" model.

### 10. Did you sweep detection thresholds (0.10 to 0.90)? What is the optimal threshold for openWakeWord on your test set?
**Yes.** Full sweep conducted from 0.10 to 0.90 in increments of 0.05:
| Threshold | Positive Recall | False Accepts | False Accept Rate | Trigger Latency (p50) |
|:---:|:---:|:---:|:---:|:---:|
| 0.10 | 100.0% | 0 | 0.0% | 800 ms |
| 0.15 | 100.0% | 0 | 0.0% | 800 ms |
| 0.20 | 100.0% | 0 | 0.0% | 800 ms |
| 0.25 | 100.0% | 0 | 0.0% | 880 ms |
| 0.30 - 0.90 | 80.0% | 0 | 0.0% | 880 ms |

**Optimal Threshold:** **0.25** offers the highest operating point with **100% positive recall** and **0.0% false accepts** across the benchmark dataset. For production deployment of any future custom model, **0.50** remains the recommended default to maximize noise rejection in unstructured acoustic environments.

### 11. Which decision tree branch (Case A, B, C, or D) applies? Justify with data.
**CASE B & CASE C (Deferred)** applies:
- *Why not Case A?* Case A requires shipping openWakeWord as default. This is precluded because (a) official pretrained models are CC-BY-NC-SA 4.0, (b) pretrained models do not recognize "Hey Jev" on real audio (`clip_05.wav` scored 0.0014), and (c) an MIT-clean custom model is `[BLOCKED: LICENSING]`.
- *Why Case B & C?* The openWakeWord inference engine demonstrates exceptional computational efficiency (5.77 ms step latency, ~7.2% single-core CPU, 0% idle CPU with VAD). Therefore, the engine is fully integrated and tested behind the feature flag `HEYJEV_WAKEWORD_ENGINE=openwakeword` (Case B), while leaving `whisper` as the production default until a clean, commercially permissive custom model is trained (Case C).

### 12. What is the production default wake-word engine after Phase 9d?
**`whisper`** (`DEFAULT_WAKEWORD_ENGINE = "whisper"` in `config.py`).
Whisper-prefix detection remains the active production wake gate.

### 13. If openWakeWord is enabled, what happens when silence is fed? (Does Silero VAD gate it? What is the idle CPU?)
When silence is fed:
1. `OpenWakeWordDetector.feed_audio()` checks frame energy (RMS < 0.005) and Silero VAD speech probability.
2. Silent frames are discarded immediately without invoking ONNX/TFLite model prediction.
3. Measured idle CPU is **0.0%**.

### 14. If openWakeWord fires, how does the command get handed off to faster-whisper? Is any audio lost? How is audio loss prevented?
Single-breath command handoff was implemented in `siri.py:wake_turn(audio)`:
- When `openwakeword` fires on an audio segment:
  - If the segment duration is **> 1.2 seconds**, it indicates a compound utterance spoken in a single breath (e.g. *"Hey Jev, open Spotify"*). The entire audio buffer is passed directly to `transcribe(audio, COMMAND_PROMPT)`, the wake prefix is stripped, and the command is executed immediately (`run_turn(rest, ms)`).
  - If the segment duration is **<= 1.2 seconds**, the user only said the wake phrase. A chime is played, and the assistant arms for `WAKE_WINDOW` (10 seconds) to receive the subsequent command.
- **Zero audio loss:** Because the recorder's VAD segment captures the full utterance from speech onset (including 300ms preroll), no command syllables are dropped.

### 15. How does push-to-talk interact with the wake-word engine?
**Completely independent.**
- Push-to-talk (Right Alt) bypasses all wake-word detectors.
- Pressing Right Alt directly records the user's speech and routes it to `transcribe_audio(audio, COMMAND_PROMPT)` and `handle(text)`.
- The wake-word engine is neither invoked nor required during PTT.

### 16. How does the wake-word engine interact with TTS echo protection and the action loop breaker?
- **TTS Echo Protection:** When speech or chime audio is actively playing or settling (`audio_io.is_audio_playing_or_settling()`), the `wake_loop` skips audio segments, preventing the assistant from triggering on its own voice.
- **Action Loop Breaker:** If spurious or repeated detections occur, `record_and_check_action_loop()` tracks identical actions within a 10-second window. At 3 identical actions, the loop breaker trips, halts execution, and speaks a warning.
- **Transcript Deduplication:** `siri.is_duplicate_transcript()` suppresses identical transcripts received within 5 seconds.

### 17. What is the full test suite result? (number passed, failed, skipped)
- **Result:** **178 passed**, **0 failed**, **0 skipped** in 160.07s.
- Baseline of 166 tests passed + 12 new automated tests in `test_phase9d.py`. Zero regressions.

### 18. What is the preflight check result?
- **Result:** **11 passed, 0 failed, 3 warnings**.
- Preflight confirmed:
  - Configuration and components accessible.
  - STT engine warm and functional (faster-whisper tiny.en, 1.0s clip transcribed).
  - TTS engine functional with warm cache.
  - Tier 2 resolution functional ('Notepad' score=100.0).
  - Safety engine strictly blocks Run-dialog and Win+R.
  - Secret scan passed: 0 API keys in logs.
  - Loop breaker, dedup guard, exact app match, and shell blocking verified on real PC.

### 19. What should Phase 10 (TTS — Fish Audio / local Kokoro pass) do regarding wake-word interaction?
Phase 10 must:
1. **Maintain Mute & Barge-In Isolation:** Ensure Fish Audio and local Kokoro synthesize audio via `audio_io.play_audio()`, which properly asserts `is_audio_playing_or_settling()`.
2. **Prevent Acoustic Self-Triggering:** Keep the 300ms post-speech settle tail active so neither Whisper nor openWakeWord latches onto Kokoro output.
3. **Keep CPU Footprint Bounded:** Kokoro ONNX synthesis should run with constrained CPU thread count (e.g. 2 threads) so it does not starve VAD or STT inference.
4. **Preserve PTT Priority:** Allow PTT activation (Right Alt) to immediately interrupt/stop active TTS playback.
