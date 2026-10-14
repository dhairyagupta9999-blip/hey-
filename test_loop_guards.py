"""Unit tests for Tier 2 execution fix and 4 loop-prevention guards (Guards a, b, c, d)."""
import time
import unittest
from unittest.mock import patch, MagicMock

import system_targets
import audio_io
import siri


class TestLoopGuards(unittest.TestCase):
    def setUp(self):
        siri.reset_transcript_dedup()
        siri.reset_action_loop_history()
        siri.reset_spoken_replies()
        audio_io.reset_playback_state()

    def tearDown(self):
        siri.reset_transcript_dedup()
        siri.reset_action_loop_history()
        siri.reset_spoken_replies()
        audio_io.reset_playback_state()

    # -------------------------------------------------------------------------
    # Bug Fix: Probe has no side-effects
    # -------------------------------------------------------------------------
    @patch("os.startfile")
    def test_01_system_targets_probe_has_no_side_effects(self, mock_startfile):
        """resolve_system_target with execute=False resolves target without executing."""
        res = system_targets.resolve_system_target("open Bluetooth settings", execute=False)
        self.assertIsNotNone(res)
        self.assertEqual(res["type"], "setting")
        self.assertEqual(res["label"], "Bluetooth Settings")
        mock_startfile.assert_not_called()

        # siri.is_tier2_direct_match probe also causes zero side effects
        matched = siri.is_tier2_direct_match("open Bluetooth settings")
        self.assertTrue(matched)
        mock_startfile.assert_not_called()

        # Explicit execution calls os.startfile
        executed = system_targets.execute_system_target(res)
        self.assertTrue(executed)
        mock_startfile.assert_called_once_with(res["uri"])

    # -------------------------------------------------------------------------
    # Guard a: Pause microphone processing while TTS or chimes play, and for 700 ms after
    # -------------------------------------------------------------------------
    def test_02_guard_a_playback_and_settling_window(self):
        """is_audio_playing_or_settling returns True during playback and for 700ms after."""
        self.assertFalse(audio_io.is_audio_playing_or_settling(settle_s=0.7))

        # Audio starts playing
        audio_io.set_playback_active(True)
        self.assertTrue(audio_io.is_audio_playing_or_settling(settle_s=0.7))

        # Audio finishes playing
        audio_io.set_playback_active(False)

        # Within 700ms settling window -> still True
        self.assertTrue(audio_io.is_audio_playing_or_settling(settle_s=0.7))

        # Mocking time passing beyond 700ms
        audio_io._LAST_PLAYBACK_END_TIME = time.time() - 0.75
        self.assertFalse(audio_io.is_audio_playing_or_settling(settle_s=0.7))

    @patch("audio_io.is_audio_playing_or_settling", return_value=True)
    def test_03_guard_a_recorder_cb_drops_mic_input_when_settling(self, mock_settling):
        """Recorder._cb immediately discards mic frames when audio is playing or settling."""
        import numpy as np
        rec = audio_io.Recorder()
        dummy_indata = np.ones((1600, 1), dtype="float32")
        rec.wake = True
        rec.paused = False

        rec._cb(dummy_indata, 1600, None, 0)
        # Frames should NOT be accumulated
        self.assertEqual(len(rec.frames), 0)
        self.assertEqual(rec.segments.qsize(), 0)

    # -------------------------------------------------------------------------
    # Guard b: Ignore identical transcript within 5 seconds
    # -------------------------------------------------------------------------
    def test_04_guard_b_dedup_identical_transcript_within_300ms(self):
        """Repeats within 300ms are ignored; accepted once the window passes.

        B3 changed this window from 5.0s to 0.3s. The old value also swallowed
        a user retrying a command that visibly failed, which is a correction,
        not an echo -- echo is Guard d's job. The normalization coverage below
        is unchanged and remains the point of this test.
        """
        t0 = 1000.0
        siri.reset_transcript_dedup()

        # First transcript accepted
        ignored = siri.check_and_update_transcript_dedup("open Bluetooth settings", current_time=t0)
        self.assertFalse(ignored)

        # Same transcript 100ms later -> ignored (retrigger / doubled VAD segment)
        ignored = siri.check_and_update_transcript_dedup("open Bluetooth settings", current_time=t0 + 0.1)
        self.assertTrue(ignored)

        # Case & whitespace normalization: "  OPEN bluetooth settings  " -> still duplicate
        ignored = siri.check_and_update_transcript_dedup("  OPEN bluetooth settings  ", current_time=t0 + 0.2)
        self.assertTrue(ignored)

        # Different transcript inside the window -> accepted
        ignored = siri.check_and_update_transcript_dedup("open Task Manager", current_time=t0 + 0.2)
        self.assertFalse(ignored)

        # A human retry one second later -> accepted (this is the B3 fix; the
        # 5s window used to drop this).
        ignored = siri.check_and_update_transcript_dedup("open Bluetooth settings", current_time=t0 + 1.0)
        self.assertFalse(ignored)

    @patch("siri.say")
    @patch("siri.is_tier2_direct_match", return_value=True)
    @patch("tier2_resolver.resolve_tier2")
    def test_05_guard_b_handle_drops_immediate_repeat(self, mock_resolve_tier2, mock_direct_match, mock_say):
        """siri.handle ignores duplicate spoken turn within 5s."""
        mock_resolve_tier2.return_value = {
            "tier": 2, "status": "done", "action": "system_setting",
            "target": "Bluetooth Settings", "line": "Opening Bluetooth Settings."
        }

        # First turn
        siri.handle("open Bluetooth settings")
        self.assertEqual(mock_resolve_tier2.call_count, 1)

        # Second turn immediately after -> dropped by Guard b
        siri.handle("open Bluetooth settings")
        self.assertEqual(mock_resolve_tier2.call_count, 1)

    # -------------------------------------------------------------------------
    # Guard c: Loop breaker (3 times within 10 seconds)
    # -------------------------------------------------------------------------
    def test_06_guard_c_loop_breaker_triggers_on_third_action(self):
        """Action firing 3 times in 10s triggers loop breaker and stops execution."""
        t0 = 5000.0
        action_key = "tier2:system_setting:Bluetooth Settings"

        # 1st firing -> ok
        self.assertFalse(siri.record_and_check_action_loop(action_key, current_time=t0))
        # 2nd firing -> ok
        self.assertFalse(siri.record_and_check_action_loop(action_key, current_time=t0 + 3.0))
        # 3rd firing within 10s -> LOOP DETECTED
        self.assertTrue(siri.record_and_check_action_loop(action_key, current_time=t0 + 6.0))

    def test_07_guard_c_loop_breaker_window_pruning(self):
        """Firings spaced beyond 10s do not trigger the loop breaker."""
        t0 = 5000.0
        action_key = "tier2:system_setting:Bluetooth Settings"

        # 1st firing at t=0
        self.assertFalse(siri.record_and_check_action_loop(action_key, current_time=t0))
        # 2nd firing at t=6
        self.assertFalse(siri.record_and_check_action_loop(action_key, current_time=t0 + 6.0))
        # 3rd firing at t=11 (>10s from 1st firing) -> 1st is pruned, only 2 events in window
        self.assertFalse(siri.record_and_check_action_loop(action_key, current_time=t0 + 11.0))

    @patch("siri.say")
    def test_08_guard_c_trigger_loop_breaker_disables_wake_mode(self, mock_say):
        """trigger_loop_breaker speaks stopping phrase and calls disable wake mode."""
        disable_mock = MagicMock()
        siri.set_disable_wake_fn(disable_mock)

        siri.trigger_loop_breaker()

        mock_say.assert_called_with("I think I'm looping, stopping now", None)
        disable_mock.assert_called_once()

    # -------------------------------------------------------------------------
    # Guard d: Ignore transcripts matching assistant's own recent spoken replies
    # -------------------------------------------------------------------------
    def test_09_guard_d_echo_cancellation_detection(self):
        """Transcripts matching assistant replies within 15s are detected and ignored."""
        t0 = 2000.0
        siri.record_spoken_reply("[cheerful] Opening Bluetooth Settings.", timestamp=t0)

        # Exact match
        self.assertTrue(siri.is_matching_recent_reply("Opening Bluetooth Settings", current_time=t0 + 1.0))

        # Substring / partial match (e.g. microphone heard end of TTS)
        self.assertTrue(siri.is_matching_recent_reply("Bluetooth Settings", current_time=t0 + 2.0))

        # Unrelated speech is not ignored
        self.assertFalse(siri.is_matching_recent_reply("open Spotify", current_time=t0 + 2.0))

        # Matches after 15s window has elapsed are no longer ignored
        self.assertFalse(siri.is_matching_recent_reply("Opening Bluetooth Settings", current_time=t0 + 16.0))

    @patch("siri.say")
    @patch("tier2_resolver.resolve_tier2")
    def test_10_guard_d_handle_drops_reply_echo(self, mock_resolve_tier2, mock_say):
        """siri.handle discards a transcript that echoes assistant's recent spoken reply."""
        # Assistant just said:
        siri.record_spoken_reply("Opening Bluetooth Settings.")

        # Microphone picks up the speaker echo:
        siri.handle("Opening Bluetooth Settings")

        # resolve_tier2 should NEVER be called
        mock_resolve_tier2.assert_not_called()


if __name__ == "__main__":
    unittest.main()
