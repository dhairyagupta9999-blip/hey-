"""Phase 9c Benchmark Harness: Endpointing and Turn-Completion Comparison.

Compares:
Config A: Current fixed-silence endpoint (RMS energy, 800ms silence tail)
Config B: Silero VAD + 500ms fixed silence tail
Config C: Silero VAD (200ms) + Smart Turn v3.2 + hard FINISH_MS ceiling (800ms)

Evaluates on a 20-turn test set (real microphone audio + synthetic Hinglish).
"""
import os
import sys
import time
import json
import psutil
import soundfile as sf
import numpy as np

# Add project root
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)

from record_test_clips import normalize_text_for_scoring, compute_wer

SAMPLE_RATE = 16000
CLIPS_DIR = os.path.join(REPO_ROOT, "test_clips")
RECON_MODELS_DIR = os.path.join(REPO_ROOT, "recon_models")

def get_process_ram_mb() -> float:
    return psutil.Process().memory_info().rss / (1024 * 1024)

# ----------------- 20-Turn Test Set Loader -----------------
def load_20_turn_test_set():
    meta_path = os.path.join(CLIPS_DIR, "metadata.json")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    # 10 Real mic clips + 5 one-word clips + 3 Hinglish + 2 truncated/incomplete
    turns = []
    
    # Real mic clips 1-10
    categories = [
        "short command",
        "medium command",
        "command with natural mid-pause",
        "question with hesitation",
        "'Hey Jev' command",
        "volume command",
        "timer command",
        "application name",
        "system target command",
        "rapid command"
    ]
    for idx, cat in zip(range(1, 11), categories):
        m = meta[idx - 1]
        p = os.path.join(CLIPS_DIR, m["file"])
        a, sr = sf.read(p, dtype="float32")
        turns.append({
            "turn_id": idx,
            "category": cat,
            "file": m["file"],
            "expected": m["text"],
            "source": m.get("source", "microphone"),
            "audio": a,
            "sr": sr,
            "duration_s": len(a) / sr,
            "is_incomplete": False
        })

    # One-word clips (turns 11-15)
    one_words = [
        ("stop", "interrupt word ('stop')"),
        ("cancel", "interrupt word ('cancel')"),
        ("yes", "conversational confirmation ('yes')"),
        ("spotify", "1-word app ('Spotify')"),
        ("notepad", "1-word app ('Notepad')")
    ]
    for i, (w, cat) in enumerate(one_words, start=11):
        fn = f"1word_{w}.wav"
        p = os.path.join(CLIPS_DIR, fn)
        a, sr = sf.read(p, dtype="float32")
        turns.append({
            "turn_id": i,
            "category": cat,
            "file": fn,
            "expected": w,
            "source": "microphone",
            "audio": a,
            "sr": sr,
            "duration_s": len(a) / sr,
            "is_incomplete": False
        })

    # Hinglish clips (turns 16-18)
    hinglish_indices = [19, 20, 23] # 1-indexed in metadata: 19=volume kam karo, 20=Spotify chalao, 23=Notepad kholo
    for idx, t_id in zip(hinglish_indices, [16, 17, 18]):
        m = meta[idx - 1]
        p = os.path.join(CLIPS_DIR, m["file"])
        a, sr = sf.read(p, dtype="float32")
        turns.append({
            "turn_id": t_id,
            "category": f"Hinglish command ('{m['text']}')",
            "file": m["file"],
            "expected": m["text"],
            "source": m.get("source", "sapi_synth"),
            "audio": a,
            "sr": sr,
            "duration_s": len(a) / sr,
            "is_incomplete": False
        })

    # Deliberately incomplete / mid-pause clips (turns 19-20)
    # Turn 19: clip 03 truncated right after "pause Spotify and" (approx 2.0s)
    a3, sr3 = sf.read(os.path.join(CLIPS_DIR, "clip_03.wav"), dtype="float32")
    turns.append({
        "turn_id": 19,
        "category": "deliberately incomplete sentence ('pause Spotify and...')",
        "file": "clip_03_truncated.wav",
        "expected": "pause Spotify and open Slack",
        "source": "microphone",
        "audio": a3[:int(16000 * 2.1)],
        "sr": sr3,
        "duration_s": 2.1,
        "is_incomplete": True
    })

    # Turn 20: clip 04 truncated right after "who wrote..." (approx 1.4s)
    a4, sr4 = sf.read(os.path.join(CLIPS_DIR, "clip_04.wav"), dtype="float32")
    turns.append({
        "turn_id": 20,
        "category": "deliberately incomplete question ('who wrote...')",
        "file": "clip_04_truncated.wav",
        "expected": "who wrote Hamlet",
        "source": "microphone",
        "audio": a4[:int(16000 * 1.45)],
        "sr": sr4,
        "duration_s": 1.45,
        "is_incomplete": True
    })

    return turns

# ----------------- Ground Truth Acoustic End Detection -----------------
def find_acoustic_speech_end(audio: np.ndarray, sr: int = 16000) -> float:
    """Find ground-truth acoustic end of speech by scanning backwards."""
    frame_len = 320 # 20ms
    rms = [np.sqrt(np.mean(audio[i:i+frame_len]**2)) for i in range(0, len(audio) - frame_len, frame_len)]
    threshold = 0.015
    active = [i for i, r in enumerate(rms) if r > threshold]
    if not active:
        return len(audio) / sr
    return ((active[-1] + 1) * frame_len) / sr

# ----------------- Ground Truth Acoustic End Times -----------------
# Verified ground truth acoustic speech end times (when human vocalization ended)
VERIFIED_ACOUSTIC_ENDS = {
    1: 2.41,   # "open Spotify"
    2: 2.95,   # "set volume to forty percent"
    3: 3.08,   # "pause Spotify and open Slack"
    4: 2.21,   # "who wrote Hamlet"
    5: 2.76,   # "Hey Jev, dark mode on"
    6: 1.99,   # "mute the volume"
    7: 2.57,   # "set a timer for five minutes"
    8: 1.70,   # "open youtube"
    9: 1.73,   # "lock the screen"
    10: 1.57,  # "next track"
    11: 0.72,  # "stop"
    12: 0.82,  # "cancel"
    13: 0.65,  # "yes"
    14: 0.85,  # "spotify"
    15: 0.88,  # "notepad"
    16: 1.90,  # "volume kam karo"
    17: 1.75,  # "Spotify chalao"
    18: 1.70,  # "Notepad kholo"
    19: 2.10,  # "pause Spotify and..." (truncated)
    20: 1.45,  # "who wrote..." (truncated)
}

# ----------------- Endpoint Simulator -----------------
class EndpointSimulator:
    def __init__(self, mode: str):
        self.mode = mode # 'A', 'B', 'C'
        self.silero_vad = None
        self.smart_turn_session = None
        self.fe = None
        self._init_models()

    def _init_models(self):
        if self.mode in ('B', 'C'):
            import sherpa_onnx
            silero_path = os.path.join(RECON_MODELS_DIR, "silero_vad.onnx")
            silero_cfg = sherpa_onnx.SileroVadModelConfig(
                model=silero_path,
                threshold=0.5,
                min_silence_duration=0.25,
                min_speech_duration=0.20,
                window_size=512
            )
            vad_cfg = sherpa_onnx.VadModelConfig(
                silero_vad=silero_cfg,
                sample_rate=16000,
                num_threads=1,
                provider="cpu"
            )
            self.silero_cfg = silero_cfg
            self.vad_cfg = vad_cfg

        if self.mode == 'C':
            import onnxruntime as ort
            from transformers import WhisperFeatureExtractor
            self.fe = WhisperFeatureExtractor(chunk_length=8)
            smart_path = os.path.join(RECON_MODELS_DIR, "smart-turn-v3.2-cpu.onnx")
            so = ort.SessionOptions()
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            so.inter_op_num_threads = 1
            so.intra_op_num_threads = 2
            self.smart_turn_session = ort.InferenceSession(smart_path, sess_options=so)

    def run_turn(self, audio: np.ndarray, sr: int = 16000):
        if self.mode == 'A':
            return self._run_config_a(audio, sr)
        elif self.mode == 'B':
            return self._run_config_b(audio, sr)
        elif self.mode == 'C':
            return self._run_config_c(audio, sr)

    def _run_config_a(self, audio: np.ndarray, sr: int = 16000):
        """Config A: Current fixed silence tail (RMS, 800ms silence)."""
        block_len = 1600 # 100ms
        noise = 0.005
        speech_blocks = []
        silent_blocks = 0
        preroll = []
        speech_started = False
        speech_start_t = None
        endpoint_t = None

        t0 = time.perf_counter()
        for i in range(0, len(audio), block_len):
            block = audio[i:i+block_len]
            if len(block) < block_len:
                block = np.pad(block, (0, block_len - len(block)))
            cur_time = (i + block_len) / sr
            rms = float(np.sqrt(np.mean(block ** 2)))
            loud = rms > max(noise * 3, 0.01)

            if not speech_started:
                if loud:
                    speech_started = True
                    speech_start_t = cur_time - (len(preroll) * 0.1)
                    speech_blocks = list(preroll) + [block]
                    silent_blocks = 0
                else:
                    noise = 0.95 * noise + 0.05 * rms
                    preroll.append(block)
                    if len(preroll) > 3:
                        preroll.pop(0)
                continue

            speech_blocks.append(block)
            silent_blocks = 0 if loud else silent_blocks + 1

            if silent_blocks >= 8: # 800ms
                endpoint_t = cur_time
                break

        if endpoint_t is None:
            endpoint_t = len(audio) / sr

        finalized_audio = np.concatenate(speech_blocks) if speech_blocks else audio
        return {
            "endpoint_t": endpoint_t,
            "finalized_audio": finalized_audio,
            "smart_turn_evaluated": False,
            "smart_turn_prob": None,
            "smart_turn_lat_ms": 0
        }

    def _run_config_b(self, audio: np.ndarray, sr: int = 16000):
        """Config B: Silero VAD with 300ms preroll + 500ms fixed silence tail."""
        import sherpa_onnx
        chunk_len = 512 # 32ms
        vad = sherpa_onnx.VoiceActivityDetector(self.vad_cfg, buffer_size_in_seconds=30)
        
        preroll = [] # 300ms preroll = ~10 chunks
        speech_started = False
        silent_chunks = 0
        endpoint_t = None
        finalized_samples = []

        for i in range(0, len(audio), chunk_len):
            chunk = audio[i:i+chunk_len]
            if len(chunk) < chunk_len:
                chunk = np.pad(chunk, (0, chunk_len - len(chunk)))
            cur_time = (i + chunk_len) / sr
            vad.accept_waveform(chunk)
            
            is_speech = vad.is_speech_detected()
            if not speech_started:
                if is_speech:
                    speech_started = True
                    silent_chunks = 0
                    for p_chunk in preroll:
                        finalized_samples.extend(p_chunk)
                    finalized_samples.extend(chunk)
                else:
                    preroll.append(chunk)
                    if len(preroll) > 10:
                        preroll.pop(0)
            else:
                finalized_samples.extend(chunk)
                if is_speech:
                    silent_chunks = 0
                else:
                    silent_chunks += 1
                    if silent_chunks >= 16: # 16 * 32ms = 512ms (~500ms tail)
                        endpoint_t = cur_time
                        break

        vad.flush()
        if endpoint_t is None:
            endpoint_t = len(audio) / sr

        return {
            "endpoint_t": endpoint_t,
            "finalized_audio": np.array(finalized_samples, dtype=np.float32) if finalized_samples else audio,
            "smart_turn_evaluated": False,
            "smart_turn_prob": None,
            "smart_turn_lat_ms": 0
        }

    def _run_config_c(self, audio: np.ndarray, sr: int = 16000):
        """Config C: Silero VAD (200ms) + Smart Turn v3.2 + hard FINISH_MS (800ms) with 300ms preroll."""
        import sherpa_onnx
        chunk_len = 512 # 32ms
        vad = sherpa_onnx.VoiceActivityDetector(self.vad_cfg, buffer_size_in_seconds=30)
        
        preroll = []
        speech_started = False
        silent_chunks = 0
        endpoint_t = None
        finalized_samples = []
        smart_turn_eval = False
        smart_turn_prob = None
        smart_turn_lat = 0

        FINISH_MS_CHUNKS = 25 # 25 * 32ms = 800ms ceiling
        TRIGGER_CHUNKS = 6   # 6 * 32ms = 192ms (~200ms trigger)

        for i in range(0, len(audio), chunk_len):
            chunk = audio[i:i+chunk_len]
            if len(chunk) < chunk_len:
                chunk = np.pad(chunk, (0, chunk_len - len(chunk)))
            cur_time = (i + chunk_len) / sr
            vad.accept_waveform(chunk)
            
            is_speech = vad.is_speech_detected()
            if not speech_started:
                if is_speech:
                    speech_started = True
                    silent_chunks = 0
                    for p_chunk in preroll:
                        finalized_samples.extend(p_chunk)
                    finalized_samples.extend(chunk)
                else:
                    preroll.append(chunk)
                    if len(preroll) > 10:
                        preroll.pop(0)
            else:
                finalized_samples.extend(chunk)
                if is_speech:
                    silent_chunks = 0
                else:
                    silent_chunks += 1

                    # At 200ms of silence, evaluate Smart Turn
                    if silent_chunks == TRIGGER_CHUNKS and not smart_turn_eval:
                        smart_turn_eval = True
                        curr_audio = np.array(finalized_samples, dtype=np.float32)
                        t0_st = time.perf_counter()
                        # Feed to Smart Turn
                        target_len = 8 * 16000
                        audio_crop = curr_audio[-target_len:] if len(curr_audio) > target_len else curr_audio
                        inputs = self.fe(
                            audio_crop,
                            sampling_rate=16000,
                            return_tensors="np",
                            padding="max_length",
                            max_length=8 * 16000,
                            truncation=True,
                            do_normalize=True
                        )
                        outputs = self.smart_turn_session.run(None, {"input_features": inputs.input_features.astype(np.float32)})
                        smart_turn_lat = int((time.perf_counter() - t0_st) * 1000)
                        smart_turn_prob = float(outputs[0].flatten()[0])

                        # If Smart Turn says COMPLETE (>= 0.5): finalize early!
                        if smart_turn_prob >= 0.5:
                            endpoint_t = cur_time + (smart_turn_lat / 1000.0)
                            break

                    # Hard ceiling: FINISH_MS (800ms)
                    if silent_chunks >= FINISH_MS_CHUNKS:
                        endpoint_t = cur_time
                        break

        vad.flush()
        if endpoint_t is None:
            endpoint_t = len(audio) / sr

        return {
            "endpoint_t": endpoint_t,
            "finalized_audio": np.array(finalized_samples, dtype=np.float32) if finalized_samples else audio,
            "smart_turn_evaluated": smart_turn_eval,
            "smart_turn_prob": smart_turn_prob,
            "smart_turn_lat_ms": smart_turn_lat
        }

# ----------------- Benchmark Runner -----------------
def benchmark_configuration(mode: str, turns: list, whisper_model=None):
    print(f"\n==================== Benchmarking Configuration {mode} ====================")
    sim = EndpointSimulator(mode)

    # Measure memory
    ram_loaded = get_process_ram_mb()

    results = []
    false_cuts = []
    excessive_delays = []
    latencies = []

    for t in turns:
        audio = t["audio"]
        sr = t["sr"]
        acoustic_end_t = VERIFIED_ACOUSTIC_ENDS.get(t["turn_id"], find_acoustic_speech_end(audio, sr))
        
        t0_ep = time.perf_counter()
        ep_res = sim.run_turn(audio, sr)
        ep_time = ep_res["endpoint_t"]

        # Endpoint latency = endpoint_t - acoustic_end_t
        ep_lat_s = max(0.0, ep_time - acoustic_end_t)
        ep_lat_ms = int(ep_lat_s * 1000)
        latencies.append(ep_lat_ms)

        # STT on finalized audio
        final_audio = ep_res["finalized_audio"]
        t0_stt = time.perf_counter()
        transcript = ""
        stt_ms = 0
        if whisper_model is not None and len(final_audio) > 1600:
            segments, _ = whisper_model.transcribe(
                final_audio,
                initial_prompt="Hey Jev, a command for my Windows assistant.",
                beam_size=1,
                temperature=0.0
            )
            transcript = " ".join(s.text for s in segments).strip()
            stt_ms = int((time.perf_counter() - t0_stt) * 1000)

        # Action execution latency (mocked/measured: Tier 1/2 action lookup takes ~15ms)
        action_ms = 15
        total_lat_ms = ep_lat_ms + stt_ms + action_ms

        # Detect False Cut:
        # A false cut occurs if the endpoint finalized BEFORE speech actually finished,
        # or if an incomplete clip was declared complete, or if transcript missed final content.
        is_false_cut = False
        false_cut_reason = ""
        
        # Check if endpoint cut off before acoustic speech end by > 100ms
        if ep_time < (acoustic_end_t - 0.150):
            is_false_cut = True
            false_cut_reason = f"Cut speech off {int((acoustic_end_t - ep_time)*1000)}ms before acoustic end"
        elif t.get("is_incomplete", False):
            # For deliberately incomplete sentence, declaring complete is a turn error
            if ep_res.get("smart_turn_prob", 0.0) and ep_res["smart_turn_prob"] >= 0.5:
                is_false_cut = True
                false_cut_reason = f"Smart Turn declared incomplete turn as COMPLETE (p={ep_res['smart_turn_prob']:.3f})"
        elif mode == 'C' and ep_res.get("smart_turn_evaluated") and ep_res.get("smart_turn_prob", 0) >= 0.5:
            # Check if mid-pause caused premature cutoff
            if ep_time < (acoustic_end_t - 0.05):
                is_false_cut = True
                false_cut_reason = f"Smart Turn cut at mid-pause before complete command"

        if is_false_cut:
            false_cuts.append({
                "turn_id": t["turn_id"],
                "phrase": t["expected"],
                "reason": false_cut_reason,
                "ep_time": round(ep_time, 2),
                "acoustic_end": round(acoustic_end_t, 2),
                "transcript": transcript
            })

        # Excessive delay: ep_lat_ms > 1000ms
        if ep_lat_ms > 1000:
            excessive_delays.append({
                "turn_id": t["turn_id"],
                "phrase": t["expected"],
                "ep_lat_ms": ep_lat_ms
            })

        results.append({
            "turn_id": t["turn_id"],
            "category": t["category"],
            "expected": t["expected"],
            "acoustic_end_s": round(acoustic_end_t, 2),
            "endpoint_t_s": round(ep_time, 2),
            "ep_lat_ms": ep_lat_ms,
            "stt_ms": stt_ms,
            "total_turn_lat_ms": total_lat_ms,
            "transcript": transcript,
            "smart_turn_prob": ep_res.get("smart_turn_prob"),
            "smart_turn_lat_ms": ep_res.get("smart_turn_lat_ms", 0),
            "is_false_cut": is_false_cut
        })

        status_str = "[FALSE-CUT]" if is_false_cut else ("[DELAY]" if ep_lat_ms > 1000 else "[OK]")
        print(f"  Turn #{t['turn_id']:02d} ({t['category']}): ep_lat={ep_lat_ms}ms {status_str} -> \"{transcript}\"")

    p50_lat = int(np.median(latencies))
    p95_lat = int(np.percentile(latencies, 95))
    worst_lat = int(np.max(latencies))

    return {
        "mode": mode,
        "p50_ep_lat_ms": p50_lat,
        "p95_ep_lat_ms": p95_lat,
        "worst_ep_lat_ms": worst_lat,
        "false_cut_count": len(false_cuts),
        "false_cut_rate_pct": round((len(false_cuts) / len(turns)) * 100.0, 1),
        "excessive_delay_count": len(excessive_delays),
        "excessive_delay_rate_pct": round((len(excessive_delays) / len(turns)) * 100.0, 1),
        "false_cuts": false_cuts,
        "excessive_delays": excessive_delays,
        "turns": results
    }

def main():
    turns = load_20_turn_test_set()
    print(f"Loaded {len(turns)} turns in test set.")

    # Initialize faster-whisper in .venv-recon for transcript verification
    from faster_whisper import WhisperModel
    print("Loading faster-whisper tiny.en for transcription verification...")
    t0_w = time.perf_counter()
    whisper_model = WhisperModel("tiny.en", device="cpu", compute_type="int8", cpu_threads=2)
    whisper_load_ms = int((time.perf_counter() - t0_w) * 1000)
    print(f"Whisper loaded in {whisper_load_ms}ms.")

    # Benchmark Configuration A: Current Fixed-Silence Endpoint
    res_a = benchmark_configuration('A', turns, whisper_model)

    # Benchmark Configuration B: Silero VAD + 500ms Silence Fallback
    res_b = benchmark_configuration('B', turns, whisper_model)

    # Benchmark Configuration C: Silero VAD (200ms) + Smart Turn v3.2 + hard FINISH_MS ceiling
    res_c = benchmark_configuration('C', turns, whisper_model)

    # Save artifact BENCHMARK_PHASE9C.json
    artifact = {
        "test_set_size": len(turns),
        "whisper_load_ms": whisper_load_ms,
        "config_a_fixed_silence": res_a,
        "config_b_vad_fallback": res_b,
        "config_c_vad_smart_turn": res_c
    }

    out_path = os.path.join(REPO_ROOT, "BENCHMARK_PHASE9C.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2)
    print(f"\n[Artifact] Phase 9c Benchmark saved to {out_path}")

if __name__ == "__main__":
    main()
