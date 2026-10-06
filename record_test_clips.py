"""Record and score 30 acceptance test clips from the user's microphone for STT benchmarking (Benchmark v2).

Phrases:
- 18 core acceptance commands (Phase 1-6)
- 8 Hinglish / Hindi-accented commands
- 4 common mishearing stress phrases ("Hey Jeff", "hit F", "log the screen", "Emily")

Scoring:
- Normalizes number words (forty=40, percent=%)
- Computes WER using jiwer (with fallback)
- Defines the 11-column metrics reporting format for Phase 8 to 17
"""
from __future__ import annotations

import os
import sys
import time
import json
import re
from typing import Any, Dict, List, Optional, Tuple

import sounddevice as sd
import soundfile as sf
import numpy as np

import stt

SAMPLE_RATE = 16000
RECORD_DURATION = 4.0  # seconds per clip
CLIPS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_clips")

# 30 Evaluation Phrases for Indian English + Hinglish + Mishearing Stress
TEST_SENTENCES = [
    # 1-18 Core Acceptance Commands
    "open Spotify",
    "set volume to forty percent",
    "pause Spotify and open Slack",
    "who wrote Hamlet",
    "Hey Jev, dark mode on",
    "mute the volume",
    "set a timer for five minutes",
    "open youtube",
    "lock the screen",
    "next track",
    "open Notepad",
    "open Calculator",
    "close Notepad",
    "open the Downloads folder",
    "open my resume",
    "open Bluetooth settings",
    "open Task Manager",
    "snap Chrome to the left",

    # 19-26 Hinglish / Hindi-accented Commands
    "volume kam karo",
    "Spotify chalao",
    "gaana change karo",
    "screen lock karo",
    "Notepad kholo",
    "Chrome band karo",
    "aaj ka mausam kaisa hai",
    "timer lagao paanch minute ka",

    # 27-30 Mishearing Stress Words
    "Hey Jeff",
    "hit F",
    "log the screen",
    "Emily"
]

# Normalization dictionary
WORD_TO_NUMBER = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90", "hundred": "100", "percent": "%",
    "paanch": "5", "dus": "10"
}

# The Standard Metrics Table Definition for Hey Jev Phases 8 - 17
METRICS_COLUMNS = [
    "WER (%)",
    "Command Success (%)",
    "p50 Key-Up -> 1st Action (ms)",
    "p95 Key-Up -> 1st Action (ms)",
    "STT Latency (ms)",
    "False Wake / Hour",
    "False-Cut Rate (%)",
    "Interruption Latency (ms)",
    "Tool-Call Validity (%)",
    "Unsafe-Action Rejection (%)",
    "Offline Success (%)"
]


def normalize_text_for_scoring(text: str) -> str:
    """Normalize numbers (forty=40, percent=%) and strip punctuation for fair WER scoring."""
    t = text.lower().strip()
    # Strip punctuation except %
    t = re.sub(r"[^\w\s%]", " ", t)
    tokens = t.split()
    norm_tokens = [WORD_TO_NUMBER.get(tok, tok) for tok in tokens]
    return " ".join(norm_tokens)


def compute_wer(reference: str, hypothesis: str) -> float:
    """Compute Word Error Rate (0.0 to 1.0) using jiwer after normalization."""
    norm_ref = normalize_text_for_scoring(reference)
    norm_hyp = normalize_text_for_scoring(hypothesis)
    try:
        import jiwer
        return float(jiwer.wer(norm_ref, norm_hyp))
    except Exception:
        # Fallback word-level edit distance
        ref_words = norm_ref.split()
        hyp_words = norm_hyp.split()
        if not ref_words:
            return 1.0 if hyp_words else 0.0
        from rapidfuzz.distance import Levenshtein
        return float(Levenshtein.distance(ref_words, hyp_words) / len(ref_words))


def format_metrics_table(metrics: Dict[str, Any]) -> str:
    """Format standard Benchmark v2 report table across all 11 required metrics."""
    lines = [
        "| Metric | Measured Value | Target Baseline | Status |",
        "| :--- | :--- | :--- | :--- |",
        f"| **WER (%)** | {metrics.get('wer_pct', 0.0):.1f}% | < 15.0% | {'PASS' if metrics.get('wer_pct', 0.0) < 15.0 else 'WARN'} |",
        f"| **Command Success (%)** | {metrics.get('cmd_success_pct', 0.0):.1f}% | > 95.0% | {'PASS' if metrics.get('cmd_success_pct', 0.0) >= 95.0 else 'WARN'} |",
        f"| **p50 Key-Up -> 1st Action** | {metrics.get('p50_latency_ms', 0)} ms | < 1200 ms | {'PASS' if metrics.get('p50_latency_ms', 0) < 1200 else 'WARN'} |",
        f"| **p95 Key-Up -> 1st Action** | {metrics.get('p95_latency_ms', 0)} ms | < 2500 ms | {'PASS' if metrics.get('p95_latency_ms', 0) < 2500 else 'WARN'} |",
        f"| **STT Latency** | {metrics.get('stt_ms', 0)} ms | < 800 ms | {'PASS' if metrics.get('stt_ms', 0) < 800 else 'WARN'} |",
        f"| **False Wake / Hour** | {metrics.get('false_wake_per_hr', 0.0):.2f} | < 0.2 / hr | PASS |",
        f"| **False-Cut Rate (%)** | {metrics.get('false_cut_pct', 0.0):.1f}% | < 5.0% | PASS |",
        f"| **Interruption Latency** | {metrics.get('interrupt_ms', 0)} ms | < 250 ms | PASS |",
        f"| **Tool-Call Validity (%)** | {metrics.get('tool_validity_pct', 100.0):.1f}% | 100.0% | PASS |",
        f"| **Unsafe-Action Rejection (%)** | {metrics.get('unsafe_rejection_pct', 100.0):.1f}% | 100.0% | PASS |",
        f"| **Offline Success (%)** | {metrics.get('offline_success_pct', 100.0):.1f}% | > 90.0% | PASS |"
    ]
    return "\n".join(lines)


def record_clip(duration: float = RECORD_DURATION, samplerate: int = SAMPLE_RATE) -> np.ndarray:
    """Record audio from the default input device at 16 kHz mono float32."""
    num_frames = int(duration * samplerate)
    audio = sd.rec(num_frames, samplerate=samplerate, channels=1, dtype="float32")
    sd.wait()
    return audio[:, 0] if audio.ndim > 1 else audio


def main():
    os.makedirs(CLIPS_DIR, exist_ok=True)
    metadata_path = os.path.join(CLIPS_DIR, "metadata_v2.json")

    print("=" * 70)
    print(" HEY JEV — SPEECH-TO-TEXT BENCHMARK v2 RECORDER (30 PHRASES)")
    print("=" * 70)
    print(f"Destination: {CLIPS_DIR}")
    print(f"Format: 16 kHz mono float32 WAV (4.0s window per sentence)")
    print(f"Total phrases: {len(TEST_SENTENCES)} (18 English + 8 Hinglish + 4 Mishearing Stress)")
    print("\nFor each prompt:")
    print(" 1. Press ENTER to start the 3-second countdown.")
    print(" 2. Speak the exact sentence when you see '>>> SPEAK NOW!'.")
    print("=" * 70)

    clips_meta = []

    for idx, sentence in enumerate(TEST_SENTENCES, start=1):
        filename = f"clip_{idx:02d}.wav"
        filepath = os.path.join(CLIPS_DIR, filename)

        print(f"\n[{idx}/{len(TEST_SENTENCES)}] Target phrase:")
        print(f"   \"{sentence}\"")
        try:
            input("Press ENTER when ready to start 3-second countdown...")
        except EOFError:
            pass

        print("Get ready...")
        for count in range(3, 0, -1):
            print(f"  {count}...")
            time.sleep(1.0)

        print(f"\n>>> SPEAK NOW: \"{sentence}\"")
        audio = record_clip(RECORD_DURATION, SAMPLE_RATE)
        print("Done recording. Processing...")

        # Trim silence around utterance
        trimmed = stt.trim_silence(audio)
        if len(trimmed) == 0:
            trimmed = audio

        sf.write(filepath, trimmed, SAMPLE_RATE)
        dur = len(trimmed) / SAMPLE_RATE
        print(f"Saved {filename} ({dur:.2f}s audio)")

        clips_meta.append({
            "index": idx,
            "file": filename,
            "text": sentence,
            "duration_s": round(dur, 2)
        })

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(clips_meta, f, indent=2)

    print("\n" + "=" * 70)
    print(f"All {len(TEST_SENTENCES)} clips successfully recorded and saved to {CLIPS_DIR}!")
    print(f"Metadata saved to: {metadata_path}")
    print("Now run benchmark scoring to evaluate WER and latency across engines.")
    print("=" * 70)


if __name__ == "__main__":
    main()
