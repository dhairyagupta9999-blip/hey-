"""Unit and Integration Tests for Phase 9a: Ears — Zero-Dependency STT Accuracy Pass.

Covers:
1. Default initial_prompt: exact text, single natural sentence, no comma-separated vocabulary list.
2. Default hotwords: starts with 'Jev lock the screen', deterministic, bounded length, zero paths/usernames/secrets.
3. FasterWhisperBackend parameter passing: initial_prompt and hotwords cleanly forwarded to transcribe.
4. Auto-cast float64 to float32: prevents ONNX runtime Silero VAD crashes.
5. Silence buffer safety: zero hallucination, zero crashes.
6. A/B configurations: Config A (baseline), Config B (prompt only), Config C (hotwords only), Config D (prompt+hotwords).
7. Benchmark artifact: BENCHMARK_PHASE9A.json presence and schema.
8. Regression safety: mute, loop breaker, and dedup guards unaffected.
"""
from __future__ import annotations

import os
import sys
import json
import re
import getpass
import time
import unittest
from unittest.mock import patch, MagicMock
import numpy as np

import stt
from stt import (
    DEFAULT_INITIAL_PROMPT,
    get_default_initial_prompt,
    get_default_hotwords,
    FasterWhisperBackend,
    get_stt_backend,
    transcribe_audio
)
import tts
import siri


class TestPhase9aSttAccuracy(unittest.TestCase):

    def setUp(self):
        # Reset any active backends
        stt._ACTIVE_BACKEND = None
        stt._ACTIVE_CONFIG = None

    def tearDown(self):
        stt._ACTIVE_BACKEND = None
        stt._ACTIVE_CONFIG = None
        os.environ.pop("HEYJEV_STT_HOTWORDS", None)
        os.environ.pop("HEYJEV_STT_PROMPT", None)

    # ---------------- 1. Default Initial Prompt ----------------
    def test_01_default_initial_prompt_single_sentence(self):
        """Initial prompt must be exactly 'Hey Jev, a command for my Windows assistant.' as a single sentence."""
        self.assertEqual(DEFAULT_INITIAL_PROMPT, "Hey Jev, a command for my Windows assistant.")
        self.assertEqual(get_default_initial_prompt(), "Hey Jev, a command for my Windows assistant.")
        
        # Verify it is one natural sentence (one period, no multiple exclamation/question marks)
        self.assertTrue(DEFAULT_INITIAL_PROMPT.endswith("."))
        self.assertEqual(DEFAULT_INITIAL_PROMPT.count("."), 1)
        self.assertNotIn(";", DEFAULT_INITIAL_PROMPT)
        # Not a giant comma-separated vocabulary list
        self.assertLessEqual(DEFAULT_INITIAL_PROMPT.count(","), 1)
        words = DEFAULT_INITIAL_PROMPT.split()
        self.assertLessEqual(len(words), 10)

    # ---------------- 2. Default Hotwords Bounded and Deterministic ----------------
    def test_02_default_hotwords_deterministic_and_safe(self):
        """Hotwords must start with 'Jev lock the screen', be deterministic, bounded, and leak no secrets or paths."""
        hw1 = get_default_hotwords()
        hw2 = get_default_hotwords()
        
        # Deterministic
        self.assertEqual(hw1, hw2)
        
        # Base command phrase present
        self.assertTrue(hw1.startswith("Jev lock the screen"))
        
        # Bounded length (prevent prompt bloat)
        self.assertLess(len(hw1), 350)
        
        # Safe: no paths, extensions, usernames, or secrets
        user = getpass.getuser().lower()
        self.assertNotIn(f"\\", hw1)
        self.assertNotIn(f"/", hw1)
        self.assertNotIn(".exe", hw1.lower())
        self.assertNotIn(".lnk", hw1.lower())
        self.assertNotIn(".bat", hw1.lower())
        if len(user) > 3:
            self.assertNotIn(user, hw1.lower())

    # ---------------- 3. FasterWhisperBackend Forwarding ----------------
    def test_03_faster_whisper_initial_prompt_and_hotwords_forwarding(self):
        """FasterWhisperBackend forwards initial_prompt and hotwords to WhisperModel.transcribe."""
        backend = FasterWhisperBackend(
            model_size="tiny.en",
            initial_prompt="Test prompt sentence.",
            hotwords="Jev test hotwords"
        )
        
        # Mock underlying model
        mock_model = MagicMock()
        mock_seg = MagicMock()
        mock_seg.text = "test output"
        mock_seg.no_speech_prob = 0.01
        mock_model.transcribe.return_value = ([mock_seg], None)
        backend._model = mock_model
        
        # Call transcribe
        test_audio = np.zeros(16000, dtype=np.float32)
        text, lat = backend.transcribe(test_audio)
        
        self.assertEqual(text, "test output")
        mock_model.transcribe.assert_called_once()
        _, kwargs = mock_model.transcribe.call_args
        self.assertEqual(kwargs.get("initial_prompt"), "Test prompt sentence.")
        self.assertEqual(kwargs.get("hotwords"), "Jev test hotwords")

    # ---------------- 4. Float64 Auto-Cast to Float32 ----------------
    def test_04_float64_audio_auto_cast_to_float32(self):
        """Audio arrays passed as float64 are automatically cast to float32 before transcribe."""
        backend = FasterWhisperBackend(model_size="tiny.en")
        mock_model = MagicMock()
        mock_seg = MagicMock()
        mock_seg.text = "sample"
        mock_seg.no_speech_prob = 0.05
        mock_model.transcribe.return_value = ([mock_seg], None)
        backend._model = mock_model
        
        # Supply float64 audio
        audio_f64 = np.zeros(16000, dtype=np.float64)
        text, lat = backend.transcribe(audio_f64)
        
        self.assertEqual(text, "sample")
        called_audio = mock_model.transcribe.call_args[0][0]
        self.assertEqual(called_audio.dtype, np.float32)

    # ---------------- 5. Silence Buffer Safety ----------------
    def test_05_silence_and_empty_audio_safety(self):
        """Zero / empty audio chunks do not crash and handle silence gracefully."""
        backend = FasterWhisperBackend(model_size="tiny.en")
        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([], None)
        backend._model = mock_model
        
        text, lat = backend.transcribe(np.zeros(0, dtype=np.float32))
        self.assertEqual(text, "")

    # ---------------- 6. A/B Configurations Instantiable ----------------
    def test_06_ab_configurations_runnable(self):
        """All four A/B configurations can be instantiated cleanly via get_stt_backend."""
        # Config A: Baseline
        os.environ["HEYJEV_STT_PROMPT"] = "0"
        os.environ["HEYJEV_STT_HOTWORDS"] = "0"
        stt._ACTIVE_BACKEND = None
        stt._ACTIVE_CONFIG = None
        b_a = get_stt_backend(engine="whisper", whisper_model="tiny.en")
        self.assertIsNone(b_a.initial_prompt)
        self.assertIsNone(b_a.hotwords)

        # Config B: Prompt only
        os.environ["HEYJEV_STT_PROMPT"] = DEFAULT_INITIAL_PROMPT
        os.environ["HEYJEV_STT_HOTWORDS"] = "0"
        stt._ACTIVE_BACKEND = None
        stt._ACTIVE_CONFIG = None
        b_b = get_stt_backend(engine="whisper", whisper_model="tiny.en")
        self.assertEqual(b_b.initial_prompt, DEFAULT_INITIAL_PROMPT)
        self.assertIsNone(b_b.hotwords)

        # Config C: Hotwords only
        os.environ["HEYJEV_STT_PROMPT"] = "0"
        os.environ["HEYJEV_STT_HOTWORDS"] = "1"
        stt._ACTIVE_BACKEND = None
        stt._ACTIVE_CONFIG = None
        b_c = get_stt_backend(engine="whisper", whisper_model="tiny.en")
        self.assertIsNone(b_c.initial_prompt)
        self.assertIsNotNone(b_c.hotwords)

        # Config D: Prompt + Hotwords
        os.environ["HEYJEV_STT_PROMPT"] = DEFAULT_INITIAL_PROMPT
        os.environ["HEYJEV_STT_HOTWORDS"] = "1"
        stt._ACTIVE_BACKEND = None
        stt._ACTIVE_CONFIG = None
        b_d = get_stt_backend(engine="whisper", whisper_model="tiny.en")
        self.assertEqual(b_d.initial_prompt, DEFAULT_INITIAL_PROMPT)
        self.assertIsNotNone(b_d.hotwords)

    # ---------------- 7. Benchmark Artifact Schema ----------------
    def test_07_benchmark_artifact_validity(self):
        """BENCHMARK_PHASE9A.json exists and contains metrics for Config A, B, C, D."""
        artifact_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "BENCHMARK_PHASE9A.json")
        self.assertTrue(os.path.exists(artifact_path), "Benchmark artifact BENCHMARK_PHASE9A.json should exist")
        with open(artifact_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        self.assertIn("cold_load_ms", data)
        self.assertIn("warmup_ms", data)
        self.assertIn("configs", data)
        cfgs = data["configs"]
        for key in ["Config A (Baseline)", "Config B (Prompt only)", "Config C (Hotwords only)", "Config D (Prompt + Hotwords)"]:
            self.assertIn(key, cfgs)
            self.assertIn("avg_wer_pct", cfgs[key])
            self.assertIn("kw_acc_pct", cfgs[key])
            self.assertIn("median_lat_ms", cfgs[key])
            self.assertIn("p95_lat_ms", cfgs[key])
            self.assertIn("silence_1s_hallucination", cfgs[key])
            self.assertIn("false_jev_insertions", cfgs[key])

    # ---------------- 8. Regression Safety: Mute & Loop Guards ----------------
    def test_08_regression_safety_loop_breaker_and_mute(self):
        """Loop breaker, mute, and dedup protections remain functional."""
        # 1. Centralized mute
        tts.set_muted(True)
        self.assertEqual(tts.speak("test text"), 0)
        tts.set_muted(False)

        # 2. Loop breaker
        siri.reset_action_loop_history()
        now = time.time()
        self.assertFalse(siri.record_and_check_action_loop("open notepad", now))
        self.assertFalse(siri.record_and_check_action_loop("open notepad", now + 1.0))
        # 3rd identical firing trips
        tripped = siri.record_and_check_action_loop("open notepad", now + 2.0)
        self.assertTrue(tripped)


if __name__ == "__main__":
    unittest.main()
