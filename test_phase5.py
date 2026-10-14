"""Phase 5 Test Suite: SMTC media transport, openWakeWord, and Spotify Web API enhancements."""
import os
import sys
import unittest
import numpy as np

class TestPhase5Enhancements(unittest.TestCase):
    def test_01_smtc_media_transport(self):
        """Verify WinRT SMTC media integration functions cleanly."""
        import actions_win
        # Check audio playing status (should return a bool)
        playing = actions_win.is_audio_playing()
        self.assertIsInstance(playing, bool)

        # Execute media actions (state-aware, should not throw)
        try:
            actions_win.media_play()
            actions_win.media_pause()
            actions_win.media_next()
            actions_win.media_previous()
        except Exception as e:
            self.fail(f"SMTC media action failed with exception: {e}")

    def test_02_whisper_prefix_detector(self):
        """Verify baseline Whisper prefix regex detector."""
        from wake_word import WhisperPrefixDetector
        det = WhisperPrefixDetector()

        # Hits
        self.assertIsNotNone(det.detect_utterance("hey jev open spotify"))
        self.assertIsNotNone(det.detect_utterance("hi jev what time is it"))
        self.assertIsNotNone(det.detect_utterance("okay jev volume up"))
        self.assertIsNotNone(det.detect_utterance("jev, pause"))

        # Misses
        self.assertIsNone(det.detect_utterance("open spotify"))
        self.assertIsNone(det.detect_utterance("who wrote hamlet"))
        self.assertIsNone(det.detect_utterance("hey google"))

    def test_03_openwakeword_detector(self):
        """Verify openWakeWord detector initialization and audio chunk processing."""
        from wake_word import OpenWakeWordDetector, get_wake_detector
        det = get_wake_detector(backend="openwakeword")
        self.assertIsNotNone(det)

        # Feed 1280 samples of silence (80ms at 16kHz)
        silence = np.zeros(1280, dtype=np.float32)
        detected, name, score = det.feed_audio(silence)
        self.assertIsInstance(detected, bool)
        self.assertFalse(detected) # Silence should not trigger wake

    def test_04_spotify_web_api_client(self):
        """Verify Spotify Web API client interface and unconfigured safety."""
        from spotify_api import SpotifyWebApiClient
        client = SpotifyWebApiClient()
        # When unconfigured, should report False safely and not crash
        self.assertFalse(client.is_configured)
        self.assertFalse(client.play())
        self.assertFalse(client.pause())
        self.assertFalse(client.next_track())
        self.assertFalse(client.previous_track())
        self.assertFalse(client.set_volume(50))
        self.assertIsNone(client.get_playback_state())

    def test_05_siri_wake_argument_support(self):
        """Verify siri.py parses wake arguments."""
        import argparse
        import siri
        # Ensure run_voice_assistant accepts wake_backend
        import inspect
        sig = inspect.signature(siri.run_voice_assistant)
        self.assertIn("wake_backend", sig.parameters)
        self.assertIn("wake_model", sig.parameters)

if __name__ == "__main__":
    unittest.main()
