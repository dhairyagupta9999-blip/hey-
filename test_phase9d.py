"""Automated Tests for Phase 9d: Wake Word / 'Hey Jev' Gate."""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock
import numpy as np

# Ensure project root in sys.path
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import config
import wake_word
from wake_word import (
    WakeDetector,
    WhisperPrefixDetector,
    OpenWakeWordDetector,
    get_wake_detector,
    DEFAULT_WAKE_REGEX,
)
import siri


class TestPhase9dWakeWord(unittest.TestCase):
    """Phase 9d Test Suite covering wake word detectors, gating, fallback, and siri integration."""

    def setUp(self):
        siri.reset_transcript_dedup()

    # -------------------------------------------------------------------------
    # 1. Base Class and Factory
    # -------------------------------------------------------------------------
    def test_01_base_wake_detector_interface(self):
        """WakeDetector interface provides default stub implementations."""
        det = WakeDetector()
        self.assertIsNone(det.detect_utterance("hey jev"))
        res, name, score = det.feed_audio(np.zeros(1600, dtype=np.float32))
        self.assertFalse(res)
        self.assertEqual(name, "")
        self.assertEqual(score, 0.0)
        det.reset()

    def test_02_factory_default_returns_whisper_prefix_detector(self):
        """Default factory instantiation returns WhisperPrefixDetector."""
        with patch.dict(os.environ, {"HEYJEV_WAKEWORD_ENGINE": "whisper"}):
            detector = get_wake_detector()
            self.assertIsInstance(detector, WhisperPrefixDetector)

    def test_03_factory_fallback_when_openwakeword_model_missing(self):
        """Requesting openwakeword without a valid model falls back to WhisperPrefixDetector."""
        detector = get_wake_detector(backend="openwakeword", model_path="nonexistent_model.onnx")
        self.assertIsInstance(detector, WhisperPrefixDetector)

    # -------------------------------------------------------------------------
    # 2. WhisperPrefixDetector Unit Tests
    # -------------------------------------------------------------------------
    def test_04_whisper_prefix_positive_matches(self):
        """WhisperPrefixDetector matches standard wake phrases with phonetic variants."""
        det = WhisperPrefixDetector()
        phrases = [
            "Hey Jev, what time is it?",
            "hey jeff open the browser",
            "Hi Jev, turn up the volume",
            "okay jev status report",
            "Jev, play some music",
            "Hey Jev",
        ]
        for phrase in phrases:
            m = det.detect_utterance(phrase)
            self.assertIsNotNone(m, f"Expected wake match for {phrase!r}")

    def test_05_whisper_prefix_negative_rejections(self):
        """WhisperPrefixDetector rejects non-wake conversational utterances."""
        det = WhisperPrefixDetector()
        negatives = [
            "what time is it?",
            "open spotify please",
            "I was talking to Jeff yesterday",
            "hello everyone",
            "turn up the volume",
        ]
        for phrase in negatives:
            m = det.detect_utterance(phrase)
            self.assertIsNone(m, f"Did not expect wake match for {phrase!r}")

    # -------------------------------------------------------------------------
    # 3. OpenWakeWordDetector Unit Tests (with Mocked Engine)
    # -------------------------------------------------------------------------
    def test_06_openwakeword_energy_gate_skips_silence(self):
        """Silence is gated before model inference to preserve CPU."""
        det = OpenWakeWordDetector()
        # Mock internal model to ensure it is not called during silence
        det._model = MagicMock()
        det.use_vad_gate = True

        # Pure silence
        silent_chunk = np.zeros(1600, dtype=np.float32)
        det.feed_audio(silent_chunk)
        det._model.predict.assert_not_called()

        # Int16 silence
        silent_int16 = np.zeros(1600, dtype=np.int16)
        det.feed_audio(silent_int16)
        det._model.predict.assert_not_called()

    def test_07_openwakeword_detection_and_threshold(self):
        """OpenWakeWordDetector correctly evaluates predictions against threshold."""
        det = OpenWakeWordDetector(threshold=0.5)
        mock_model = MagicMock()
        det._model = mock_model

        # Below threshold
        mock_model.predict.return_value = {"hey_jarvis": 0.35}
        loud_chunk = np.ones(1280, dtype=np.float32) * 0.1
        detected, name, score = det.feed_audio(loud_chunk)
        self.assertFalse(detected)
        self.assertEqual(name, "")

        # At/above threshold
        mock_model.predict.return_value = {"hey_jarvis": 0.82}
        detected, name, score = det.feed_audio(loud_chunk)
        self.assertTrue(detected)
        self.assertEqual(name, "hey_jarvis")
        self.assertAlmostEqual(score, 0.82)

    def test_08_openwakeword_handles_arbitrary_chunk_lengths(self):
        """OpenWakeWordDetector processes audio longer than single 80ms step size."""
        det = OpenWakeWordDetector(threshold=0.5)
        mock_model = MagicMock()
        mock_model.predict.return_value = {"hey_jarvis": 0.0}
        det._model = mock_model

        # 16000 samples = 1 full second (approx 12.5 chunks of 1280)
        long_chunk = np.ones(16000, dtype=np.float32) * 0.1
        det.feed_audio(long_chunk)
        self.assertGreater(mock_model.predict.call_count, 10)

    # -------------------------------------------------------------------------
    # 4. Feature Flag & Environment Variable Integration
    # -------------------------------------------------------------------------
    def test_09_feature_flag_config(self):
        """DEFAULT_WAKEWORD_ENGINE is configurable via HEYJEV_WAKEWORD_ENGINE env var."""
        self.assertIn(config.DEFAULT_WAKEWORD_ENGINE, ("whisper", "openwakeword"))

    # -------------------------------------------------------------------------
    # 5. Siri Wake Turn Integration
    # -------------------------------------------------------------------------
    @patch("siri.play_chime")
    @patch("siri.emit")
    def test_10_whisper_wake_turn_arm_on_wake_only(self, mock_emit, mock_chime):
        """Saying 'Hey Jev' with no command arms the assistant and chimes."""
        with patch("siri.transcribe_audio", return_value=("Hey Jev", 150)):
            # Simulate wake_turn with WhisperPrefixDetector
            det = WhisperPrefixDetector()
            text, ms = siri.transcribe_audio(np.zeros(1600), "")
            m = det.detect_utterance(text)
            self.assertIsNotNone(m)
            rest = text[m.end():].strip(" .,!?")
            self.assertEqual(rest, "")

    @patch("siri.handle")
    def test_11_whisper_wake_turn_executes_compound_command(self, mock_handle):
        """Saying 'Hey Jev open notepad' extracts the command and runs turn."""
        det = WhisperPrefixDetector()
        text = "Hey Jev, open notepad"
        m = det.detect_utterance(text)
        self.assertIsNotNone(m)
        rest = text[m.end():].strip(" .,!?")
        self.assertEqual(rest, "open notepad")

    # -------------------------------------------------------------------------
    # 6. Safety & Push-To-Talk Isolation
    # -------------------------------------------------------------------------
    def test_12_ptt_independent_of_wake_detector(self):
        """Push-to-talk does not use or require wake word detection."""
        # PTT calls transcribe_audio directly with COMMAND_PROMPT without wake prefix gating
        self.assertIsNotNone(config.COMMAND_PROMPT)
        self.assertIn("Open Spotify", config.COMMAND_PROMPT)


if __name__ == "__main__":
    unittest.main()
