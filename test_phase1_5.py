"""Unit tests for Phase 1.5: Decision Backend Abstraction (Jev + Laya)."""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from backend import DecisionBackend, JevBackend, LayaBackend, get_backend
from siri import QUESTIONS, SPLIT_QUESTIONS, jev

class TestDecisionBackendAbstraction(unittest.TestCase):

    def test_backend_interface(self):
        """Verify DecisionBackend interface attributes."""
        jev_b = JevBackend()
        self.assertEqual(jev_b.name, "jev")
        self.assertEqual(jev_b.default_gate, 0.65)

        laya_b = LayaBackend()
        self.assertEqual(laya_b.name, "laya")
        self.assertEqual(laya_b.default_gate, 0.45)

    def test_confidence_calibration(self):
        """Verify uniform-prior probability calibration formula."""
        laya_b = LayaBackend()
        # N = 4 choices. If raw_prob = 0.25 (pure random guess), calibrated = 0.0
        self.assertAlmostEqual(laya_b.calibrate_confidence(0.25, 4), 0.0, places=4)

        # N = 4 choices. If raw_prob = 1.0 (certainty), calibrated = 1.0
        self.assertAlmostEqual(laya_b.calibrate_confidence(1.0, 4), 1.0, places=4)

        # N = 4 choices. If raw_prob = 0.70, (4 * 0.70 - 1) / 3 = 1.8 / 3 = 0.60
        self.assertAlmostEqual(laya_b.calibrate_confidence(0.70, 4), 0.60, places=4)

        # N = 2 choices. If raw_prob = 0.50 (coin toss), calibrated = 0.0
        self.assertAlmostEqual(laya_b.calibrate_confidence(0.50, 2), 0.0, places=4)

    def test_backend_resolution_default(self):
        """Verify get_backend defaults to Jev."""
        old_env = os.environ.get("HEYJEV_BACKEND")
        try:
            if "HEYJEV_BACKEND" in os.environ:
                del os.environ["HEYJEV_BACKEND"]
            backend = get_backend()
            self.assertEqual(backend.name, "jev")
        finally:
            if old_env:
                os.environ["HEYJEV_BACKEND"] = old_env

    def test_backend_resolution_env(self):
        """Verify get_backend respects HEYJEV_BACKEND."""
        old_env = os.environ.get("HEYJEV_BACKEND")
        try:
            os.environ["HEYJEV_BACKEND"] = "laya"
            backend = get_backend()
            self.assertEqual(backend.name, "laya")

            os.environ["HEYJEV_BACKEND"] = "jev"
            backend = get_backend()
            self.assertEqual(backend.name, "jev")

            os.environ["HEYJEV_BACKEND"] = "non_existent_engine"
            backend = get_backend()
            self.assertEqual(backend.name, "jev")  # Fallback
        finally:
            if old_env:
                os.environ["HEYJEV_BACKEND"] = old_env

    def test_laya_load_failure_fallback(self):
        """Verify Laya fails gracefully and falls back to Jev when checkpoint is invalid."""
        laya_b = LayaBackend(checkpoint="definitely/non-existent-checkpoint-xyz-999", fallback_to_jev=True)
        mock_jev = MagicMock()
        mock_jev.decide.return_value = ({"category": ("mac_command", 0.9)}, 150, 0.00004)
        laya_b._jev_fallback = mock_jev

        q = {"category": {"type": "choice", "instructions": "test"}}
        ans, lat, cost = laya_b.decide(q, "open slack")

        # Verify fallback was invoked
        mock_jev.decide.assert_called_once_with(q, "open slack")
        self.assertIn("category", ans)

    def test_laya_no_fallback_raises(self):
        """Verify Laya raises error if fallback is disabled."""
        laya_b = LayaBackend(checkpoint="definitely/non-existent-checkpoint-xyz-999", fallback_to_jev=False)
        q = {"category": {"type": "choice", "instructions": "test"}}
        with self.assertRaises(RuntimeError):
            laya_b.decide(q, "open slack")

    def test_question_battery_schema(self):
        """Verify question battery conforms to required structure."""
        self.assertIn("category", QUESTIONS)
        self.assertIn("compound", QUESTIONS)
        self.assertIn("target", QUESTIONS)
        self.assertIn("app", QUESTIONS)
        self.assertIn("app_action", QUESTIONS)

        self.assertIn("first_target", SPLIT_QUESTIONS)
        self.assertIn("second_target", SPLIT_QUESTIONS)

if __name__ == "__main__":
    unittest.main()
