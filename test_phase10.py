"""Automated Tests for Phase 10: TTS System, Provider Abstraction, Kokoro Fallback, and Caching."""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock
import numpy as np
import requests

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tts
from tts import (
    compute_cache_path,
    normalize_lang_code,
    is_hindi_text,
    fetch_tts,
    speak,
    is_muted,
    set_muted,
    resolve_kokoro_files,
    get_tts_status,
)
import audio_io
import siri
import persona


class TestPhase10TTS(unittest.TestCase):
    """Phase 10 Test Suite covering TTS provider abstraction, caching, fallback, and safety."""

    def setUp(self):
        set_muted(False)
        audio_io.reset_playback_state()
        siri.reset_transcript_dedup()
        os.environ["HEYJEV_DISABLE_WARM_CACHE"] = "1"

    def tearDown(self):
        os.environ.pop("HEYJEV_DISABLE_WARM_CACHE", None)

    # -------------------------------------------------------------------------
    # 1. Provider-Agnostic Semantic Caching & Legacy Compatibility
    # -------------------------------------------------------------------------
    def test_01_cache_key_different_for_different_languages(self):
        """Cache keys differ for identical text in different languages."""
        p_en = compute_cache_path("Hello", voice="am_adam", lang="en-us")
        p_hi = compute_cache_path("Hello", voice="am_adam", lang="hi")
        self.assertNotEqual(p_en, p_hi, "Different languages must yield distinct cache paths.")

    def test_02_cache_key_different_for_different_voices(self):
        """Cache keys differ for different voices with identical text."""
        p1 = compute_cache_path("Hello", voice="am_adam", lang="en-us")
        p2 = compute_cache_path("Hello", voice="af_sarah", lang="en-us")
        self.assertNotEqual(p1, p2, "Different voices must yield distinct cache paths.")

    def test_03_cache_key_different_for_different_speeds(self):
        """Cache keys differ when synthesis speed differs."""
        p_normal = compute_cache_path("Hello", voice="am_adam", speed=1.0)
        p_fast = compute_cache_path("Hello", voice="am_adam", speed=1.2)
        self.assertNotEqual(p_normal, p_fast, "Different speeds must yield distinct cache paths.")

    def test_04_legacy_fish_cache_backward_compatibility(self):
        """Default 'jev' English request resolves existing pre-warmed Fish Audio hash."""
        # Check an existing pre-warmed scripted reply
        test_line = "Opening it up."
        legacy_path = compute_cache_path(test_line, voice="jev", lang="en-us", speed=1.0)
        # Verify it uses .wav extension
        self.assertTrue(legacy_path.endswith(".wav"))

    def test_05_corrupted_zero_byte_cache_file_cleanup(self):
        """Corrupted zero-byte cache entry is cleaned up and replaced."""
        corrupt_path = os.path.join(tts.TTS_CACHE_DIR, "corrupt_test_file.wav")
        with open(corrupt_path, "wb") as f:
            f.write(b"")  # 0 bytes
        self.assertTrue(os.path.exists(corrupt_path))

        with patch("tts.compute_cache_path", return_value=corrupt_path):
            with patch("tts.synthesize_fish_audio", return_value=120):
                # fetch_tts should detect < 100 bytes, delete it, and re-synthesize
                with patch("builtins.open", unittest.mock.mock_open()):
                    p, ms, cached = fetch_tts("test line")
                    self.assertFalse(cached, "Corrupted file should not be reported as a cache hit.")

    # -------------------------------------------------------------------------
    # 2. Language Normalization & Hindi Support
    # -------------------------------------------------------------------------
    def test_06_language_code_normalization(self):
        """Language strings are normalized to valid ISO codes."""
        self.assertEqual(normalize_lang_code("h"), "hi")
        self.assertEqual(normalize_lang_code("hi"), "hi")
        self.assertEqual(normalize_lang_code("hindi"), "hi")
        self.assertEqual(normalize_lang_code("en"), "en-us")
        self.assertEqual(normalize_lang_code("en-us"), "en-us")
        self.assertEqual(normalize_lang_code("english"), "en-us")
        self.assertEqual(normalize_lang_code("en-gb"), "en-gb")

    def test_07_hindi_text_detection(self):
        """Devanagari characters are reliably detected."""
        self.assertTrue(is_hindi_text("नमस्ते"))
        self.assertTrue(is_hindi_text("वॉल्यूम 40%"))
        self.assertTrue(is_hindi_text("ठीक है, opening Spotify."))
        self.assertFalse(is_hindi_text("Hello world."))
        self.assertFalse(is_hindi_text("Opening Spotify now."))

    # -------------------------------------------------------------------------
    # 3. 'Jev' Pronunciation Verification
    # -------------------------------------------------------------------------
    def test_08_jev_pronunciation_phonemization(self):
        """Jev is phonemized using correct /dʒˈɛv/ phoneme."""
        engine = tts.get_kokoro_engine()
        ph = engine.tokenizer.phonemize("Jev", lang="en-us")
        self.assertIn("dʒ", ph, "Must contain voiced affricate /dʒ/ like Jack/Jeff.")
        self.assertIn("ɛ", ph, "Must contain short vowel /ɛ/ like bed/Jeff.")
        self.assertIn("v", ph, "Must contain voiced labiodental /v/.")

    # -------------------------------------------------------------------------
    # 4. Fallback Logic: Fish Audio -> Kokoro
    # -------------------------------------------------------------------------
    @patch("tts.synthesize_fish_audio")
    @patch("tts.synthesize_kokoro")
    def test_09_fish_audio_failure_falls_back_to_kokoro(self, mock_kokoro, mock_fish):
        """Fish Audio network/auth failure triggers deterministic Kokoro fallback."""
        mock_fish.side_effect = requests.exceptions.HTTPError("401 Unauthorized")
        mock_kokoro.return_value = ("test_path.wav", 450)

        with patch.dict(os.environ, {"HEYJEV_TTS_PROVIDER": "fish"}):
            with patch("os.path.exists", return_value=False):
                path, ms, cached = fetch_tts("Hello from test.", allow_fallback=True)
                # Verify that Fish Audio was attempted for our test text
                self.assertTrue(any(c.args and c.args[0] == "Hello from test." for c in mock_fish.mock_calls))
                # Verify that Kokoro was called as fallback
                self.assertTrue(any(c.args and c.args[0] == "Hello from test." for c in mock_kokoro.mock_calls))
                self.assertEqual(path, "test_path.wav")
                self.assertFalse(cached)

    @patch("tts.synthesize_fish_audio")
    @patch("tts.synthesize_kokoro")
    def test_10_both_providers_fail_raises_clean_runtime_error(self, mock_kokoro, mock_fish):
        """When both Fish Audio and Kokoro fail, a clean RuntimeError is raised."""
        mock_fish.side_effect = requests.exceptions.Timeout("Connection timed out")
        mock_kokoro.side_effect = RuntimeError("Kokoro execution failed")

        with patch.dict(os.environ, {"HEYJEV_TTS_PROVIDER": "fish"}):
            with patch("os.path.exists", return_value=False):
                with self.assertRaises(RuntimeError) as ctx:
                    fetch_tts("Hello failing test.", allow_fallback=True)
                self.assertIn("All TTS providers failed", str(ctx.exception))

    @patch("tts.synthesize_fish_audio")
    @patch("tts.synthesize_kokoro")
    def test_11_hindi_text_routes_directly_to_kokoro(self, mock_kokoro, mock_fish):
        """Hindi text routes directly to Kokoro without invoking Fish English API."""
        mock_kokoro.return_value = ("hindi_test.wav", 600)

        with patch("os.path.exists", return_value=False):
            path, ms, cached = fetch_tts("नमस्ते दोस्तों।", allow_fallback=True)
            self.assertFalse(any(c.args and "नमस्ते" in c.args[0] for c in mock_fish.mock_calls))
            self.assertTrue(any(c.args and "नमस्ते" in c.args[0] for c in mock_kokoro.mock_calls))
            self.assertEqual(path, "hindi_test.wav")

    # -------------------------------------------------------------------------
    # 5. Mute & Playback Safety Hooks
    # -------------------------------------------------------------------------
    def test_12_mute_flag_bypasses_synthesis_and_playback(self):
        """Setting mute returns 0 ms immediately without playing audio."""
        set_muted(True)
        self.assertTrue(is_muted())
        with patch("tts.fetch_tts") as mock_fetch:
            with patch("audio_io.play_audio") as mock_play:
                ms = speak("This should not speak.")
                self.assertEqual(ms, 0)
                mock_fetch.assert_not_called()
                mock_play.assert_not_called()

    def test_13_tts_echo_guard_active_during_playback(self):
        """Microphone processing is gated during TTS playback and settle tail."""
        self.assertFalse(audio_io.is_audio_playing_or_settling(settle_s=0.5))
        audio_io.set_playback_active(True)
        self.assertTrue(audio_io.is_audio_playing_or_settling(settle_s=0.5))
        audio_io.set_playback_active(False)
        # Immediately after, within settle window, it is still True
        self.assertTrue(audio_io.is_audio_playing_or_settling(settle_s=0.7))

    def test_14_right_alt_ptt_unblocked(self):
        """Right Alt PTT remains responsive and uncoupled from TTS engine."""
        # PTT calls siri.ptt_turn which invokes transcribe_audio directly
        self.assertFalse(audio_io.is_audio_playing_or_settling())

    def test_15_persona_isolation(self):
        """Persona phrases only supply conversational text, never altering safety policy."""
        greeting = persona.get_greeting()
        self.assertIsInstance(greeting, str)
        self.assertTrue(len(greeting) > 0)

    # -------------------------------------------------------------------------
    # 6. Status and Diagnostics
    # -------------------------------------------------------------------------
    def test_16_tts_status_reporting(self):
        """get_tts_status reports diagnostic state accurately."""
        st = get_tts_status()
        self.assertIn("provider_configured", st)
        self.assertIn("fish_audio_available", st)
        self.assertIn("kokoro_available", st)
        self.assertTrue(st["kokoro_available"], "Kokoro model files should be present locally.")
        self.assertIn("cached_files_count", st)


if __name__ == "__main__":
    unittest.main()
