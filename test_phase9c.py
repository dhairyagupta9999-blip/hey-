"""Automated Tests for Phase 9c: Bounded Local VAD and Turn Endpointing."""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock
import numpy as np

# Ensure project root in sys.path
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
TESTS_DIR = os.path.join(REPO_ROOT, "tests")
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

from model_assets import requires_kokoro, requires_silero_vad, requires_whisper_model

import endpointing
from endpointing import (
    SileroVad,
    TurnEndpointDetector,
    is_interrupt_phrase,
    VAD_FRAME_SIZE,
    FRAME_DURATION_MS,
    VAD_SILENCE_MS,
    FINISH_MS,
    FALLBACK_SILENCE_MS,
    MIN_SPEECH_MS,
    PREROLL_MS,
)
import audio_io
import siri


class TestPhase9cEndpointing(unittest.TestCase):
    """Phase 9c Test Suite covering Silero VAD, endpointing transitions, fallbacks, and safety."""

    def setUp(self):
        # Reset any playback state
        audio_io.reset_playback_state()
        siri.reset_transcript_dedup()

    # -------------------------------------------------------------------------
    # 1. Silero VAD Engine & Model Availability
    # -------------------------------------------------------------------------
    @requires_silero_vad()
    def test_01_silero_vad_initialization_and_availability(self):
        """SileroVad initializes ONNX session and handles inference cleanly."""
        vad = SileroVad()
        self.assertTrue(vad.available, "Silero VAD model should be found and loaded locally via ONNX Runtime.")
        
        # Test 512-sample silent frame
        silent_frame = np.zeros(512, dtype=np.float32)
        is_sp, prob = vad.is_speech(silent_frame)
        self.assertFalse(is_sp)
        self.assertLess(prob, 0.5)

        # Reset states
        vad.reset()
        self.assertEqual(vad._h.shape, (2, 1, 64))
        self.assertEqual(vad._c.shape, (2, 1, 64))

    # -------------------------------------------------------------------------
    # 2. State Machine Transitions (IDLE -> SPEECH -> TRAILING_SILENCE -> SPEECH)
    # -------------------------------------------------------------------------
    def test_02_vad_speech_detection_and_state_transitions(self):
        """Detector transitions cleanly through IDLE, SPEECH, and TRAILING_SILENCE states."""
        detector = TurnEndpointDetector(use_neural_vad=False)  # Test deterministic logic
        self.assertEqual(detector.state, TurnEndpointDetector.STATE_IDLE)
        self.assertFalse(detector.is_speech_active())

        # Feed 1 frame of loud synthetic audio (speech onset)
        loud_frame = np.ones(512, dtype=np.float32) * 0.5
        res = detector.process_frame(loud_frame)
        self.assertIsNone(res)
        self.assertEqual(detector.state, TurnEndpointDetector.STATE_SPEECH)
        self.assertTrue(detector.is_speech_active())

        # Feed 1 frame of silence -> transitions to TRAILING_SILENCE
        silent_frame = np.zeros(512, dtype=np.float32)
        res = detector.process_frame(silent_frame)
        self.assertIsNone(res)
        self.assertEqual(detector.state, TurnEndpointDetector.STATE_TRAILING_SILENCE)
        self.assertTrue(detector.is_speech_active())

        # Feed loud frame again (natural mid-sentence pause bridged) -> transitions back to SPEECH
        res = detector.process_frame(loud_frame)
        self.assertIsNone(res)
        self.assertEqual(detector.state, TurnEndpointDetector.STATE_SPEECH)
        self.assertEqual(detector.consecutive_silent_frames, 0)

    # -------------------------------------------------------------------------
    # 3. Finalization after Silence Tail (VAD_SILENCE_MS = 500ms)
    # -------------------------------------------------------------------------
    def test_03_vad_finalization_after_silence_tail(self):
        """Utterance finalizes and yields audio buffer after silence tail is reached."""
        detector = TurnEndpointDetector(use_neural_vad=False)
        loud_frame = np.ones(512, dtype=np.float32) * 0.5
        silent_frame = np.zeros(512, dtype=np.float32)

        # Feed 10 speech frames (10 * 32ms = 320ms, exceeds MIN_SPEECH_MS 250ms)
        for _ in range(10):
            res = detector.process_frame(loud_frame)
            self.assertIsNone(res)

        # Feed silence frames up to FALLBACK_SILENCE_MS (800ms / 32ms = 25 frames)
        finalized = None
        for _ in range(25):
            res = detector.process_frame(silent_frame)
            if res is not None:
                finalized = res
                break

        self.assertIsNotNone(finalized)
        self.assertIsInstance(finalized, np.ndarray)
        self.assertGreater(len(finalized), 0)
        self.assertEqual(detector.state, TurnEndpointDetector.STATE_IDLE)
        self.assertFalse(detector.is_speech_active())

    # -------------------------------------------------------------------------
    # 4. Hard Finish Ceiling Bounds Endpoint (FINISH_MS = 800ms)
    # -------------------------------------------------------------------------
    def test_04_finish_ms_hard_ceiling_bounds_endpoint(self):
        """Hard FINISH_MS ceiling strictly forces finalization regardless of config."""
        detector = TurnEndpointDetector(use_neural_vad=False, finish_ms=800)
        loud_frame = np.ones(512, dtype=np.float32) * 0.5
        silent_frame = np.zeros(512, dtype=np.float32)

        # 10 speech frames
        for _ in range(10):
            detector.process_frame(loud_frame)

        # Exactly 25 silent frames (25 * 32ms = 800ms)
        for i in range(24):
            self.assertIsNone(detector.process_frame(silent_frame))
        
        # 25th silent frame must trigger finalization
        finalized = detector.process_frame(silent_frame)
        self.assertIsNotNone(finalized)

    # -------------------------------------------------------------------------
    # 5. Minimum Speech Duration Discards Transient Clicks (<250ms)
    # -------------------------------------------------------------------------
    def test_05_minimum_speech_duration_discards_transient_clicks(self):
        """Short transient noises (< 250ms) are discarded and never trigger transcription."""
        detector = TurnEndpointDetector(use_neural_vad=False)
        loud_frame = np.ones(512, dtype=np.float32) * 0.5
        silent_frame = np.zeros(512, dtype=np.float32)

        # Feed only 2 speech frames (64ms, far below 250ms)
        detector.process_frame(loud_frame)
        detector.process_frame(loud_frame)

        # Feed 25 silence frames
        for _ in range(25):
            res = detector.process_frame(silent_frame)
            self.assertIsNone(res, "Transient burst must NOT finalize into an utterance segment.")

        # Must have cleanly reset to IDLE
        self.assertEqual(detector.state, TurnEndpointDetector.STATE_IDLE)
        self.assertFalse(detector.is_speech_active())

    # -------------------------------------------------------------------------
    # 6. Smart Turn COMPLETE Trigger Shortens Turn Finalization
    # -------------------------------------------------------------------------
    def test_06_smart_turn_complete_shortens_turn_endpoint(self):
        """Pluggable Smart Turn hook predicting COMPLETE finalizes turn early at 200ms pause."""
        mock_smart_turn = MagicMock()
        mock_smart_turn.predict_turn_complete.return_value = True

        detector = TurnEndpointDetector(use_neural_vad=False, smart_turn_analyzer=mock_smart_turn)
        loud_frame = np.ones(512, dtype=np.float32) * 0.5
        silent_frame = np.zeros(512, dtype=np.float32)

        # 10 speech frames
        for _ in range(10):
            detector.process_frame(loud_frame)

        # Feed 6 silent frames (~192ms)
        finalized = None
        for _ in range(6):
            res = detector.process_frame(silent_frame)
            if res is not None:
                finalized = res
                break

        # Should have finalized early via Smart Turn!
        self.assertIsNotNone(finalized)
        mock_smart_turn.predict_turn_complete.assert_called_once()
        self.assertEqual(detector.state, TurnEndpointDetector.STATE_IDLE)

    # -------------------------------------------------------------------------
    # 7. Smart Turn CONTINUE Waits for Full Silence Tail
    # -------------------------------------------------------------------------
    def test_07_smart_turn_continue_waits_for_silence_tail(self):
        """Smart Turn predicting CONTINUE does not finalize early; waits for tail."""
        mock_smart_turn = MagicMock()
        mock_smart_turn.predict_turn_complete.return_value = False

        detector = TurnEndpointDetector(use_neural_vad=False, smart_turn_analyzer=mock_smart_turn)
        loud_frame = np.ones(512, dtype=np.float32) * 0.5
        silent_frame = np.zeros(512, dtype=np.float32)

        for _ in range(10):
            detector.process_frame(loud_frame)

        # 6 silent frames
        for _ in range(6):
            self.assertIsNone(detector.process_frame(silent_frame))

        mock_smart_turn.predict_turn_complete.assert_called_once()
        # Still in TRAILING_SILENCE because Smart Turn said CONTINUE
        self.assertEqual(detector.state, TurnEndpointDetector.STATE_TRAILING_SILENCE)

    # -------------------------------------------------------------------------
    # 8. Smart Turn Crashing / Unavailable Degrades Gracefully
    # -------------------------------------------------------------------------
    def test_08_smart_turn_unavailable_or_crashing_fallback(self):
        """Smart Turn failure or crash does not hang; degrades to fixed silence tail."""
        mock_smart_turn = MagicMock()
        mock_smart_turn.predict_turn_complete.side_effect = RuntimeError("ONNX crash")

        detector = TurnEndpointDetector(use_neural_vad=False, smart_turn_analyzer=mock_smart_turn)
        loud_frame = np.ones(512, dtype=np.float32) * 0.5
        silent_frame = np.zeros(512, dtype=np.float32)

        for _ in range(10):
            detector.process_frame(loud_frame)

        # Feed silence all the way through 25 frames
        finalized = None
        for _ in range(25):
            res = detector.process_frame(silent_frame)
            if res is not None:
                finalized = res
                break

        # Degraded cleanly to fixed silence endpoint
        self.assertIsNotNone(finalized)
        self.assertEqual(detector.state, TurnEndpointDetector.STATE_IDLE)

    # -------------------------------------------------------------------------
    # 9. VAD Unavailable Deterministic Dynamic Energy Fallback
    # -------------------------------------------------------------------------
    def test_09_vad_unavailable_deterministic_rms_fallback(self):
        """When neural VAD is unavailable, system falls back to dynamic RMS energy."""
        detector = TurnEndpointDetector(use_neural_vad=False)
        self.assertFalse(detector.has_neural_vad)

        # Noise adaptation in IDLE
        low_noise = np.ones(512, dtype=np.float32) * 0.002
        detector.process_frame(low_noise)
        self.assertEqual(detector.state, TurnEndpointDetector.STATE_IDLE)

        # Loud frame triggers speech
        loud_frame = np.ones(512, dtype=np.float32) * 0.4
        detector.process_frame(loud_frame)
        self.assertEqual(detector.state, TurnEndpointDetector.STATE_SPEECH)

    # -------------------------------------------------------------------------
    # 10. process_audio Slices Arbitrary Chunk Sizes (e.g. 1600 WASAPI blocks)
    # -------------------------------------------------------------------------
    def test_10_process_audio_slices_arbitrary_chunk_sizes(self):
        """Recorder.process_audio handles 1600-sample WASAPI blocks seamlessly."""
        detector = TurnEndpointDetector(use_neural_vad=False)
        block_1600_loud = np.ones(1600, dtype=np.float32) * 0.5
        block_1600_silent = np.zeros(1600, dtype=np.float32)

        # Feed 3 loud 1600 blocks (4800 samples = 300ms speech)
        for _ in range(3):
            self.assertIsNone(detector.process_audio(block_1600_loud))

        self.assertTrue(detector.is_speech_active())

        # Feed silent 1600 blocks until finalization (8 blocks = 800ms)
        finalized = None
        for _ in range(10):
            res = detector.process_audio(block_1600_silent)
            if res is not None:
                finalized = res
                break

        self.assertIsNotNone(finalized)
        self.assertFalse(detector.is_speech_active())

    # -------------------------------------------------------------------------
    # 11. Fast-Path Interrupt Phrases (stop, wait, cancel)
    # -------------------------------------------------------------------------
    def test_11_fast_path_interrupt_phrases_stop_and_wait(self):
        """Interrupt phrases ('stop', 'wait', 'cancel', etc.) match and bypass decision gates."""
        self.assertTrue(is_interrupt_phrase("stop"))
        self.assertTrue(is_interrupt_phrase("STOP"))
        self.assertTrue(is_interrupt_phrase("wait"))
        self.assertTrue(is_interrupt_phrase("cancel"))
        self.assertTrue(is_interrupt_phrase("stop that"))
        self.assertTrue(is_interrupt_phrase("wait a second"))
        self.assertTrue(is_interrupt_phrase("hold on"))
        self.assertTrue(is_interrupt_phrase("halt"))
        self.assertTrue(is_interrupt_phrase("abort"))

        # Non-interrupt phrases
        self.assertFalse(is_interrupt_phrase("open notepad"))
        self.assertFalse(is_interrupt_phrase("stop the music in spotify"))

        # Verify fast-path in siri.handle()
        with patch("safety_engine.get_confirmation_manager") as mock_cm, \
             patch("safety_engine.request_global_cancel") as mock_cancel, \
             patch("siri.say") as mock_say:
            cm_instance = MagicMock()
            cm_instance.cancel_pending.return_value = (True, "Cancelled action.")
            mock_cm.return_value = cm_instance

            siri.handle("wait a second")
            cm_instance.cancel_pending.assert_called_once()
            mock_cancel.assert_called_once()
            mock_say.assert_called_once_with("Cancelled action.", None)

    # -------------------------------------------------------------------------
    # 12. Loop Breaker and Duplicate Transcript Interaction
    # -------------------------------------------------------------------------
    def test_12_loop_breaker_and_duplicate_transcript_interaction(self):
        """Transcript deduplication and loop breaker remain fully functional."""
        with patch("siri.say") as mock_say:
            # First transcript
            siri.handle("stop")
            self.assertEqual(mock_say.call_count, 1)

            # Duplicate transcript within 5s is dropped by Guard b
            siri.handle("stop")
            self.assertEqual(mock_say.call_count, 1)  # Dropped!

    # -------------------------------------------------------------------------
    # 13. TTS Echo Protection and Recorder Clearing
    # -------------------------------------------------------------------------
    @patch("audio_io.is_audio_playing_or_settling", return_value=True)
    def test_13_tts_echo_protection_and_recorder_clearing(self, mock_settling):
        """TTS or chime playback immediately resets and drops mic capture in Recorder."""
        rec = audio_io.Recorder(use_vad=False)
        rec.wake = True
        rec.paused = False

        # Simulate incoming audio during TTS settle
        loud_input = np.ones((1600, 1), dtype=np.float32)
        rec._cb(loud_input, 1600, None, 0)

        # Buffer must be completely empty
        self.assertEqual(rec.segments.qsize(), 0)
        self.assertFalse(rec.detector.is_speech_active())

    # -------------------------------------------------------------------------
    # 14. Dictation Mode Max Utterance Duration
    # -------------------------------------------------------------------------
    def test_14_dictation_mode_allows_longer_utterances(self):
        """Dictation mode sets max utterance duration ceiling to 30,000ms."""
        detector = TurnEndpointDetector(use_neural_vad=False)
        self.assertEqual(detector.max_utterance_ms, 15000)

        detector.set_dictating(True)
        self.assertEqual(detector.max_utterance_ms, 30000)

        detector.set_dictating(False)
        self.assertEqual(detector.max_utterance_ms, 15000)


if __name__ == "__main__":
    unittest.main()
