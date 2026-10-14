"""Unit tests for Phase 1 Core Loop on Windows."""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
TESTS_DIR = os.path.join(REPO_ROOT, "tests")
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

from model_assets import requires_kokoro, requires_silero_vad, requires_whisper_model

import time
import json

from config import (
    APPDATA_DIR, LOG_FILE, TIMERS_FILE, WAKE_CHIME, DICTATE_CHIME, TIMER_CHIME
)
from secrets_store import get_secret, missing_secrets, save_secret, SERVICE
import audio_io
import timers
import logger
import actions_win

def test_paths_and_appdata():
    assert os.path.exists(APPDATA_DIR)
    assert os.path.exists(WAKE_CHIME)
    assert os.path.exists(DICTATE_CHIME)
    assert os.path.exists(TIMER_CHIME)

def test_audio_loader():
    # Test loading generated sound assets via soundfile/av
    data, sr = audio_io.load_audio_file(WAKE_CHIME)
    assert len(data) > 0
    assert sr > 0
    assert data.dtype.kind == "f"  # float array

def test_timer_durations_and_reminders():
    # Test duration parser
    assert timers.parse_duration("five minutes") == 300
    assert timers.parse_duration("half an hour") == 1800
    assert timers.parse_duration("45 seconds") == 45
    assert timers.parse_duration("2 hours and a half") == 9000

    # Test reminder extraction
    rem = timers.parse_reminder("remind me in 10 minutes to take out the trash")
    assert rem == "take out the trash"

    rem2 = timers.parse_reminder("remind me to call Mom in two hours")
    assert rem2 == "call mom"

def test_monotonic_timers_persistence():
    # Add a 10s timer
    t = timers.add_timer(10, label="Test Timer")
    assert t["secs"] == 10
    assert t["label"] == "Test Timer"

    snaps = timers.timer_snapshot()
    assert len(snaps) >= 1
    assert any("Test timer" in s[0] for s in snaps)

    # Check persistence on disk
    assert os.path.exists(TIMERS_FILE)
    with open(TIMERS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert any(item["label"] == "Test Timer" for item in data)

    # Cancel timer
    timers.cancel_timer(all_timers=True)
    assert len(timers.timer_snapshot()) == 0

def test_logger_trace():
    test_phrase = "test turn for Operation Nightingale"
    logger.trace_heard(test_phrase, stt_ms=120)
    logger.trace_answers({"category": ("mac_command", 0.95)}, gate=0.65)
    logger.trace_decision("jev", 310, 0.000042)
    logger.trace_action("volume_up", None)
    logger.trace_say("Louder it is.")
    logger.trace_fish(0)

    assert os.path.exists(LOG_FILE)
    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    assert test_phrase in content
    assert "mac_command" in content
    assert "[backend: jev]" in content
    assert "fish cached" in content

def test_volume_actions():
    vol = actions_win.get_volume()
    assert isinstance(vol, int)
    assert 0 <= vol <= 100

def test_stt_trim_silence_and_threads():
    import stt
    import numpy as np
    threads = stt.get_cpu_threads()
    assert 1 <= threads <= 4

    sr = 16000
    t = np.linspace(0, 0.5, int(sr * 0.5), dtype=np.float32)
    tone = 0.5 * np.sin(2 * np.pi * 440 * t)
    audio = np.concatenate([np.zeros(sr, dtype=np.float32), tone, np.zeros(sr, dtype=np.float32)])
    trimmed = stt.trim_silence(audio)
    assert len(trimmed) < len(audio)
    assert len(trimmed) >= len(tone)

@requires_whisper_model()
def test_stt_backend_resolution_and_fallback():
    import stt
    # 1. FasterWhisperBackend resolution
    bw = stt.get_stt_backend(engine="whisper", whisper_model="tiny.en")
    assert isinstance(bw, stt.FasterWhisperBackend)
    assert bw.model_size == "tiny.en"

    # 2. WhistleBackend resolution & keywords
    bwh = stt.get_stt_backend(engine="whistle", whisper_model="tiny.en")
    assert isinstance(bwh, stt.WhistleBackend)
    kws = bwh.get_keywords()
    assert "Jev" in kws
    assert "Hey Jev" in kws

    # 3. Whistle fallback when engine is uninitialized/fails
    bwh._needle = None
    import numpy as np
    text, lat = bwh.transcribe(np.zeros(16000, dtype=np.float32))
    assert isinstance(text, str)
    assert isinstance(lat, int)

def test_stt_benchmark_normalization_and_matching():
    import benchmark_stt
    assert benchmark_stt.normalize_text("Open Spotify.") == "open spotify"
    assert benchmark_stt.normalize_text("  HEY JEV, Dark Mode on!  ") == "hey jev dark mode on"
    assert benchmark_stt.is_exact_match("open Spotify.", "open spotify")
    assert benchmark_stt.is_exact_match("who wrote hamlet", "Who wrote Hamlet?")
    assert not benchmark_stt.is_exact_match("open slack", "open spotify")

if __name__ == "__main__":
    print("Running Phase 1 validation tests...")
    test_paths_and_appdata()
    print("  [PASS] Paths and AppData directories")
    test_audio_loader()
    print("  [PASS] Audio loader (MP3/WAV)")
    test_timer_durations_and_reminders()
    print("  [PASS] Timer duration and reminder regex")
    test_monotonic_timers_persistence()
    print("  [PASS] Monotonic timer and disk persistence")
    test_logger_trace()
    print("  [PASS] Trace logging to Hey Jev.log")
    test_volume_actions()
    print("  [PASS] Windows volume endpoint via pycaw")
    test_stt_trim_silence_and_threads()
    print("  [PASS] STT silence trimming and CPU thread limit")
    test_stt_backend_resolution_and_fallback()
    print("  [PASS] STT backend resolution and Whistle fallback")
    test_stt_benchmark_normalization_and_matching()
    print("  [PASS] STT benchmark normalization and matching")
    print("\nALL PHASE 1 TESTS PASSED!")

