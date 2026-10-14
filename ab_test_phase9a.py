"""A/B Testing Harness for Phase 9a: Faster-Whisper Initial Prompt & Hotwords.

Tests 4 configurations across:
1. 30 Benchmark Clips (Clips 1-10 real microphone, Clips 11-30 SAPI synthesized)
2. Silence clips (1.0s and 3.0s) for hallucination testing
3. 1-word clips ("Spotify", "Notepad", "stop", "yes", "cancel") for hallucination / false insertion of 'Jev'

Configurations:
- Config A: Baseline current faster-whisper configuration (no prompt, no hotwords)
- Config B: initial_prompt only ("Hey Jev, a command for my Windows assistant.")
- Config C: hotwords only ("Jev lock the screen" + app index)
- Config D: initial_prompt + hotwords
"""
import os
import sys
import time
import json
import re
import getpass
import numpy as np
import soundfile as sf
import scipy.signal
import win32com.client
from faster_whisper import WhisperModel

import stt
import record_test_clips
from record_test_clips import normalize_text_for_scoring, compute_wer

CLIPS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_clips")
SAMPLE_RATE = 16000

INITIAL_PROMPT_TEXT = "Hey Jev, a command for my Windows assistant."

def generate_default_hotwords() -> str:
    base_phrase = "Jev lock the screen"
    try:
        import app_index
        idx = app_index.get_app_index()
        curr_user = getpass.getuser().lower()
        candidates = []
        for app in idx.apps:
            p = app.get("priority", 0)
            name = app.get("name", "").strip()
            if p >= 80 or name.lower() in ("notepad", "calculator", "task manager", "settings", "command prompt"):
                if re.match(r"^[A-Za-z0-9][A-Za-z0-9 ]{1,24}$", name):
                    n_low = name.lower()
                    if curr_user not in n_low and not any(ext in n_low for ext in (".exe", ".lnk", ".bat", "\\", "/", ":")):
                        candidates.append(name)
        unique_sorted = sorted(list(dict.fromkeys(candidates)))[:15]
        if unique_sorted:
            return f"{base_phrase} " + " ".join(unique_sorted)
        return base_phrase
    except Exception:
        return base_phrase

def load_or_create_1word_clips() -> dict:
    words = ["Spotify", "Notepad", "stop", "yes", "cancel"]
    clips = {}
    speaker = win32com.client.Dispatch("SAPI.SpVoice")
    filestream = win32com.client.Dispatch("SAPI.SpFileStream")
    temp_path = os.path.join(CLIPS_DIR, "temp_1word.wav")
    
    for w in words:
        path = os.path.join(CLIPS_DIR, f"1word_{w.lower()}.wav")
        if os.path.exists(path):
            audio, sr = sf.read(path, dtype="float32")
            clips[w] = audio
        else:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            filestream.Open(temp_path, 3, False)
            speaker.AudioOutputStream = filestream
            speaker.Speak(w)
            filestream.Close()
            data, sr = sf.read(temp_path, dtype="float32")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            num_target = int(len(data) * 16000 / sr)
            resampled = scipy.signal.resample(data, num_target).astype(np.float32)
            sf.write(path, resampled, 16000)
            clips[w] = resampled
    return clips

def run_ab_benchmarks():
    print("=" * 80)
    print(" PHASE 9A: FASTER-WHISPER A/B BENCHMARK SUITE")
    print("=" * 80)
    
    # Measure cold load
    print("\n[1/5] Measuring Cold Model Load Time...")
    t0_cold = time.perf_counter()
    model = WhisperModel("tiny.en", device="cpu", compute_type="int8", cpu_threads=stt.get_cpu_threads())
    cold_load_ms = int((time.perf_counter() - t0_cold) * 1000)
    print(f"Cold Model Load Latency: {cold_load_ms} ms")
    
    # Warmup
    print("Warming up model...")
    t0_warmup = time.perf_counter()
    warm_buf = np.zeros(SAMPLE_RATE, dtype=np.float32)
    list(model.transcribe(warm_buf, language="en", beam_size=1, vad_filter=True)[0])
    warmup_ms = int((time.perf_counter() - t0_warmup) * 1000)
    print(f"Warmup Latency: {warmup_ms} ms")
    
    hotwords_str = generate_default_hotwords()
    print(f"\nConstructed hotwords ({len(hotwords_str)} chars):\n  \"{hotwords_str}\"")
    print(f"Initial prompt:\n  \"{INITIAL_PROMPT_TEXT}\"")
    
    # Load 30 benchmark clips
    meta_path = os.path.join(CLIPS_DIR, "metadata.json")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
        
    clips_data = []
    for item in meta:
        fpath = os.path.join(CLIPS_DIR, item["file"])
        audio, sr = sf.read(fpath, dtype="float32")
        clips_data.append({
            "index": item["index"],
            "file": item["file"],
            "expected": item["text"],
            "source": item.get("source", "microphone" if item["index"] <= 10 else "sapi_synth"),
            "audio": audio
        })
    print(f"\nLoaded {len(clips_data)} benchmark clips.")

    # Configurations
    configs = [
        ("Config A (Baseline)", None, None),
        ("Config B (Prompt only)", INITIAL_PROMPT_TEXT, None),
        ("Config C (Hotwords only)", None, hotwords_str),
        ("Config D (Prompt + Hotwords)", INITIAL_PROMPT_TEXT, hotwords_str),
    ]

    config_results = {}

    for cfg_name, init_prompt, hotwords in configs:
        print(f"\n" + "-" * 70)
        print(f" Running {cfg_name}...")
        print("-" * 70)
        
        latencies = []
        wers = []
        exact_matches = 0
        keyword_hits = 0
        keyword_totals = 0
        transcripts = []
        jev_insertions = 0
        
        for clip in clips_data:
            audio = clip["audio"]
            expected = clip["expected"]
            trimmed = stt.trim_silence(audio)
            if trimmed.dtype != np.float32:
                trimmed = trimmed.astype(np.float32)
                
            t_start = time.perf_counter()
            kwargs = {
                "language": "en",
                "beam_size": 1,
                "best_of": 1,
                "vad_filter": True,
                "without_timestamps": True,
                "condition_on_previous_text": False
            }
            if init_prompt is not None:
                kwargs["initial_prompt"] = init_prompt
            if hotwords is not None:
                kwargs["hotwords"] = hotwords
                
            segs, _ = model.transcribe(trimmed, **kwargs)
            res_text = " ".join(s.text.strip() for s in segs).strip()
            lat_ms = int((time.perf_counter() - t_start) * 1000)
            latencies.append(lat_ms)
            
            # Scoring
            wer = compute_wer(expected, res_text)
            wers.append(wer)
            
            # Exact command match
            norm_exp = normalize_text_for_scoring(expected)
            norm_got = normalize_text_for_scoring(res_text)
            if norm_exp == norm_got:
                exact_matches += 1
                
            # Keyword hits: extract content words (>3 chars) from expected
            content_words = [w for w in norm_exp.split() if len(w) > 3 and w not in ("with", "from", "that", "this")]
            for kw in content_words:
                keyword_totals += 1
                if kw in norm_got:
                    keyword_hits += 1
                    
            # Check false insertion of "jev"
            if "jev" in norm_got and "jev" not in norm_exp:
                jev_insertions += 1
                
            transcripts.append({
                "index": clip["index"],
                "expected": expected,
                "got": res_text,
                "wer": wer,
                "lat_ms": lat_ms,
                "source": clip["source"]
            })
            
        avg_wer = float(np.mean(wers)) * 100.0
        kw_acc = (keyword_hits / keyword_totals * 100.0) if keyword_totals else 100.0
        cmd_acc = (exact_matches / len(clips_data)) * 100.0
        median_lat = int(np.median(latencies))
        p95_lat = int(np.percentile(latencies, 95))
        
        # Hallucination tests
        # 1. Silence clips (1.0s and 3.0s zeros)
        silence_1s = np.zeros(SAMPLE_RATE, dtype=np.float32)
        silence_3s = np.zeros(SAMPLE_RATE * 3, dtype=np.float32)
        segs_s1, _ = model.transcribe(silence_1s, **kwargs)
        s1_out = " ".join(s.text.strip() for s in segs_s1).strip()
        segs_s3, _ = model.transcribe(silence_3s, **kwargs)
        s3_out = " ".join(s.text.strip() for s in segs_s3).strip()
        
        # 2. 1-word clips
        one_word_clips = load_or_create_1word_clips()
        one_word_results = {}
        for w, w_audio in one_word_clips.items():
            trimmed_w = stt.trim_silence(w_audio)
            if trimmed_w.dtype != np.float32:
                trimmed_w = trimmed_w.astype(np.float32)
            segs_w, _ = model.transcribe(trimmed_w, **kwargs)
            w_out = " ".join(s.text.strip() for s in segs_w).strip()
            one_word_results[w] = w_out
            if "jev" in normalize_text_for_scoring(w_out):
                jev_insertions += 1
                
        config_results[cfg_name] = {
            "avg_wer_pct": avg_wer,
            "kw_acc_pct": kw_acc,
            "cmd_acc_pct": cmd_acc,
            "exact_matches": exact_matches,
            "total_clips": len(clips_data),
            "median_lat_ms": median_lat,
            "p95_lat_ms": p95_lat,
            "false_jev_insertions": jev_insertions,
            "silence_1s_hallucination": s1_out,
            "silence_3s_hallucination": s3_out,
            "one_word_outputs": one_word_results,
            "transcripts": transcripts
        }
        
        print(f"Results for {cfg_name}:")
        print(f"  WER: {avg_wer:.2f}% | Keyword Acc: {kw_acc:.1f}% | Command Acc: {cmd_acc:.1f}% ({exact_matches}/{len(clips_data)})")
        print(f"  Median Latency: {median_lat} ms | p95: {p95_lat} ms")
        print(f"  False 'Jev' Insertions: {jev_insertions}")
        print(f"  Silence 1s: '{s1_out}' | Silence 3s: '{s3_out}'")
        print(f"  1-word outputs: {one_word_results}")
        
    # Write machine-readable artifact
    artifact_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "BENCHMARK_PHASE9A.json")
    with open(artifact_path, "w", encoding="utf-8") as f:
        json.dump({
            "cold_load_ms": cold_load_ms,
            "warmup_ms": warmup_ms,
            "configs": {k: {sub_k: sub_v for sub_k, sub_v in v.items() if sub_k != "transcripts"} for k, v in config_results.items()}
        }, f, indent=2)
    print(f"\nArtifact saved to: {artifact_path}")
    
    return cold_load_ms, warmup_ms, config_results

if __name__ == "__main__":
    run_ab_benchmarks()
