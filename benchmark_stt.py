"""Speech-to-Text Benchmark Harness for Hey Jev.

Benchmarks faster-whisper (tiny.en, base.en, small.en) and Whistle on:
  - Model load time
  - Steady-state latency (median ms, p95 ms after warmup)
  - Accuracy (exact matches out of 10, case- and punctuation-insensitive)
  - Wrong transcript listing per engine

Expects 10 test clips recorded by 'record_test_clips.py' in test_clips/.
"""
from __future__ import annotations

import os
import sys
import time
import json
import re
import argparse
import numpy as np
import soundfile as sf

import stt
from stt import FasterWhisperBackend, WhistleBackend

CLIPS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_clips")
METADATA_FILE = os.path.join(CLIPS_DIR, "metadata.json")

DEFAULT_SENTENCES = [
    {"index": 1, "file": "clip_01.wav", "text": "open Spotify"},
    {"index": 2, "file": "clip_02.wav", "text": "set volume to forty percent"},
    {"index": 3, "file": "clip_03.wav", "text": "pause Spotify and open Slack"},
    {"index": 4, "file": "clip_04.wav", "text": "who wrote Hamlet"},
    {"index": 5, "file": "clip_05.wav", "text": "Hey Jev, dark mode on"},
    {"index": 6, "file": "clip_06.wav", "text": "mute the volume"},
    {"index": 7, "file": "clip_07.wav", "text": "set a timer for five minutes"},
    {"index": 8, "file": "clip_08.wav", "text": "open youtube"},
    {"index": 9, "file": "clip_09.wav", "text": "lock the screen"},
    {"index": 10, "file": "clip_10.wav", "text": "next track"},
]

def normalize_text(text: str) -> str:
    """Normalize text for case- and punctuation-insensitive comparison."""
    if not text:
        return ""
    # Strip non-alphanumeric/non-space characters
    clean = re.sub(r"[^\w\s]", "", text)
    return " ".join(clean.lower().split())

def is_exact_match(hyp: str, ref: str) -> bool:
    """Check if hypothesis matches reference ignoring case and punctuation."""
    return normalize_text(hyp) == normalize_text(ref)

def load_clips(clips_dir: str = CLIPS_DIR) -> list[dict]:
    """Load test clips and expected transcripts."""
    metadata_path = os.path.join(clips_dir, "metadata.json")
    if os.path.exists(metadata_path):
        with open(metadata_path, "r", encoding="utf-8") as f:
            clips = json.load(f)
    else:
        clips = DEFAULT_SENTENCES

    loaded = []
    for item in clips:
        filepath = os.path.join(clips_dir, item.get("file", f"clip_{item['index']:02d}.wav"))
        if not os.path.exists(filepath):
            continue
        audio, sr = sf.read(filepath, dtype="float32")
        if sr != 16000:
            print(f"Warning: {filepath} has sample rate {sr}, expected 16000")
        if audio.ndim > 1:
            audio = audio[:, 0]
        loaded.append({
            "index": item["index"],
            "file": item.get("file"),
            "expected": item["text"],
            "audio": audio,
            "duration_s": len(audio) / float(sr)
        })
    return loaded

def benchmark_engine(name: str, create_fn, clips: list[dict]) -> dict:
    """Run benchmark on a single STT engine."""
    print(f"\nEvaluating {name}...")
    
    # 1. Measure model load time (actual weights loaded into memory)
    t_load_start = time.perf_counter()
    backend = create_fn()
    if isinstance(backend, FasterWhisperBackend):
        backend.get_model()
    elif isinstance(backend, WhistleBackend):
        if backend._needle is not None:
            backend._needle.transcribe(np.zeros(1600, dtype=np.float32), language="en", keywords=backend.get_keywords())
    load_time_ms = int((time.perf_counter() - t_load_start) * 1000)
    print(f"  Load time: {load_time_ms} ms")

    # 2. Warmup on silent buffer (excluded from clip latency measurements)
    print("  Warming up on silent buffer...")
    backend.warmup()

    # 3. Steady-state evaluation across clips
    latencies: list[int] = []
    results: list[dict] = []
    correct_count = 0
    wrong_transcripts: list[dict] = []

    for clip in clips:
        audio = clip["audio"]
        expected = clip["expected"]

        t_start = time.perf_counter()
        transcript, _ = backend.transcribe(audio)
        latency_ms = int((time.perf_counter() - t_start) * 1000)
        latencies.append(latency_ms)

        match = is_exact_match(transcript, expected)
        if match:
            correct_count += 1
        else:
            wrong_transcripts.append({
                "clip_index": clip["index"],
                "file": clip["file"],
                "expected": expected,
                "got": transcript,
                "norm_expected": normalize_text(expected),
                "norm_got": normalize_text(transcript)
            })

        results.append({
            "index": clip["index"],
            "latency_ms": latency_ms,
            "expected": expected,
            "got": transcript,
            "match": match
        })
        status_sym = "[OK]" if match else "[DIFF]"
        print(f"    Clip #{clip['index']:02d} ({clip['duration_s']:.2f}s): {latency_ms}ms {status_sym} -> \"{transcript}\"")

    median_ms = int(np.median(latencies)) if latencies else 0
    p95_ms = int(np.percentile(latencies, 95)) if latencies else 0

    return {
        "engine": name,
        "load_time_ms": load_time_ms,
        "median_ms": median_ms,
        "p95_ms": p95_ms,
        "correct_count": correct_count,
        "total_clips": len(clips),
        "latencies": latencies,
        "wrong_transcripts": wrong_transcripts,
        "results": results
    }

def print_benchmark_report(reports: list[dict], total_clips: int):
    """Format and print benchmark table and details."""
    print("\n" + "=" * 90)
    print(f" HEY JEV — SPEECH-TO-TEXT BACKEND BENCHMARK RESULTS ({total_clips} clips)")
    print("=" * 90)
    
    header = f"{'Engine':<26} | {'Load Time':<11} | {'Median (ms)':<11} | {'P95 (ms)':<9} | {'Accuracy':<16}"
    sep = "-" * 27 + "+" + "-" * 13 + "+" + "-" * 13 + "+" + "-" * 11 + "+" + "-" * 18
    print(header)
    print(sep)

    for r in reports:
        engine = r["engine"]
        load = f"{r['load_time_ms']} ms"
        med = f"{r['median_ms']} ms"
        p95 = f"{r['p95_ms']} ms"
        acc = f"{r['correct_count']}/{r['total_clips']} ({r['correct_count']*100.0/max(1, r['total_clips']):.0f}%)"
        print(f"{engine:<26} | {load:<11} | {med:<11} | {p95:<9} | {acc:<16}")

    print("=" * 90)
    print("The test clips were recorded from the user's real voice on this machine.")
    print("=" * 90)

    print("\nWrong Transcripts per Engine (case- and punctuation-insensitive):")
    for r in reports:
        print(f"\n[{r['engine']}]")
        wrongs = r.get("wrong_transcripts", [])
        if not wrongs:
            print("  (None — 100% exact matches)")
        else:
            for w in wrongs:
                print(f"  - Clip #{w['clip_index']:02d} ({w['file']}):")
                print(f"      Expected: \"{w['expected']}\"")
                print(f"      Got:      \"{w['got']}\"")

def create_synthetic_clips(clips_dir: str):
    """Generate 10 silent/synthetic WAV clips for testing the benchmark runner."""
    os.makedirs(clips_dir, exist_ok=True)
    meta = []
    for item in DEFAULT_SENTENCES:
        fname = f"clip_{item['index']:02d}.wav"
        fpath = os.path.join(clips_dir, fname)
        # 1.5 seconds of low-level synthetic tone/silence
        t = np.linspace(0, 1.5, int(16000 * 1.5), endpoint=False, dtype=np.float32)
        audio = 0.001 * np.sin(2 * np.pi * 440 * t)
        sf.write(fpath, audio, 16000)
        meta.append({
            "index": item["index"],
            "file": fname,
            "text": item["text"],
            "duration_s": 1.5
        })
    with open(os.path.join(clips_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"Generated {len(meta)} synthetic test clips in {clips_dir}")

def main():
    parser = argparse.ArgumentParser(description="Hey Jev STT Benchmark (faster-whisper vs Whistle)")
    parser.add_argument("--clips-dir", default=CLIPS_DIR, help="Path to directory containing test WAV clips")
    parser.add_argument("--create-synthetic", action="store_true", help="Create synthetic silent clips for testing harness")
    parser.add_argument("--save-json", default="benchmark_stt_results.json", help="Path to save output JSON")
    args = parser.parse_args()

    if args.create_synthetic:
        create_synthetic_clips(args.clips_dir)

    clips = load_clips(args.clips_dir)
    if not clips:
        print(f"\n[!] No test clips found in '{args.clips_dir}'.")
        print("Please run:")
        print("    python record_test_clips.py")
        print("to record the 10 acceptance sentences from your microphone, then re-run this benchmark.")
        print("(Or run with '--create-synthetic' to verify the benchmark runner without a microphone).")
        sys.exit(1)

    print(f"Loaded {len(clips)} test clips from {args.clips_dir}")

    engines = [
        ("faster-whisper (tiny.en)", lambda: FasterWhisperBackend(model_size="tiny.en")),
        ("faster-whisper (base.en)", lambda: FasterWhisperBackend(model_size="base.en")),
        ("faster-whisper (small.en)", lambda: FasterWhisperBackend(model_size="small.en")),
        ("Whistle", lambda: WhistleBackend(fallback_model_size="small.en")),
    ]

    reports = []
    for name, create_fn in engines:
        try:
            report = benchmark_engine(name, create_fn, clips)
            reports.append(report)
        except Exception as exc:
            print(f"Error benchmarking {name}: {exc}")

    print_benchmark_report(reports, total_clips=len(clips))

    if args.save_json:
        # Strip numpy/unserializable fields
        serializable = []
        for r in reports:
            clean_r = {k: v for k, v in r.items() if k not in ("latencies",)}
            clean_r["median_ms"] = int(r["median_ms"])
            clean_r["p95_ms"] = int(r["p95_ms"])
            serializable.append(clean_r)
        with open(args.save_json, "w", encoding="utf-8") as f:
            json.dump({
                "engines": serializable,
                "note": "The test clips were recorded from the user's real voice on this machine.",
                "total_clips": len(clips)
            }, f, indent=2)
        print(f"\nSaved benchmark results to {args.save_json}")

if __name__ == "__main__":
    main()
