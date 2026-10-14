"""Unit and Integration Tests for Phase 8: Truth Layer + Carry-Over Hardening.

Covers:
8a. Hardware profile existence and measured attributes
8b. Carry-over items:
    1. Tier 3 asks ONE confirmation sentence on >20 words or garbled transcripts
    2. Spend cap pre-call check + $0.03 per-task budget ceiling
    3. screenshot_control and ui_read on other windows MEDIUM risk, disclosure, blocked for Zen
    4. Exact app match priority ("Chrome means Chrome")
    5. Window handles validated as integers
    6. Failed answer provider speaks specific failure reason
    7. MCP client non-blocking cancellable reader worker
    8. Legacy setup.py retired
8c. Preflight suite execution and summary line format
8d. Diagnostics engine: booleans and counters ONLY, zero identity leak test
8e. Test hooks: --mute / HEYJEV_MUTE=1 in speak(), persona.json cannot change safety
8f. Exact-catalog rule: near-misses refused with valid options, runtime swaps reset on restart
8g. Benchmark v2: 30 phrases, number normalization, jiwer scoring
"""
from __future__ import annotations

import os
import sys
import json
import time
import unittest
from unittest.mock import patch, MagicMock
import requests

import safety_engine
from safety_engine import SAFE, MEDIUM, HIGH, BLOCKED, evaluate_risk
import window_manager
import app_index
import tier3_agent
import answer_provider
import mcp_client
import diagnostics
import persona
import catalog
import record_test_clips
import tts


class TestPhase8TruthLayerAndCarryOver(unittest.TestCase):

    def setUp(self):
        # Reset mute and cancel states
        os.environ["HEYJEV_MUTE"] = "0"
        safety_engine.check_and_clear_cancel()

    # ---------------- 8a. Hardware Profile ----------------
    # Deleted (§5.2): test_01_hardware_profile_attributes. It opened
    # HARDWARE_PROFILE.md and asserted the file still contained five literal
    # strings ("Intel(R) Core(TM) i3-5005U", "2 physical cores", ...). That
    # checks the wording of a document, not the behaviour of the program, and
    # it cannot detect wrong hardware being recorded -- only the doc being
    # reworded. If hardware facts matter, measure them with preflight.py.

    # ---------------- 8b(1). Tier 3 Quality Confirmation ----------------
    def test_02_tier3_long_or_garbled_transcript_requires_confirmation(self):
        """Tier 3 asks ONE confirmation sentence before acting on >20 words or garbled transcripts."""
        long_prompt = "open notepad and then go ahead and type some notes about my meeting and then please save it to my documents folder and close it"
        self.assertGreater(len(long_prompt.split()), 20)

        # Ensure quality check flags it
        ok, reason = tier3_agent.check_transcript_quality(long_prompt)
        self.assertFalse(ok)
        self.assertIn("20 words", reason)

        # Running tier3_agent on long prompt prompts for confirmation
        res = tier3_agent.run_tier3_agent(long_prompt)
        self.assertIsNotNone(res)
        self.assertEqual(res["status"], "needs_confirmation")
        self.assertIn("Do you want me to proceed", res["line"])

        # Garbled prompt check
        garbled = "notepad notepad notepad notepad notepad"
        ok_g, _ = tier3_agent.check_transcript_quality(garbled)
        self.assertFalse(ok_g)

    # ---------------- 8b(2). Spend Cap Pre-Check & Per-Task Budget ----------------
    def test_03_spend_cap_pre_call_check_and_per_task_budget(self):
        """Spend cap is checked BEFORE each LLM call with worst-case cost, and $0.03 per-task cap is enforced."""
        self.assertEqual(tier3_agent.DEFAULT_PER_TASK_BUDGET, 0.03)
        self.assertGreater(tier3_agent.WORST_CASE_CALL_COST, 0.0)

        # Pre-call spend check test
        with patch("tier3_agent.get_daily_spend", return_value=0.098):
            with patch("tier3_agent.get_daily_spend_cap", return_value=0.10):
                # 0.098 + 0.0035 = 0.1015 >= 0.10 cap -> pauses before calling
                with patch("tier3_agent.get_secret", return_value="dummy_key"):
                    res = tier3_agent.run_tier3_agent("open calculator", bypass_quality_check=True)
                    self.assertIsNotNone(res)
                    self.assertIn("spend cap", res["line"].lower())

    # ---------------- 8b(3). Screenshot & UI Read Risk & Privacy ----------------
    def test_04_screenshot_and_ui_read_risk_and_disclosure(self):
        """screenshot_control is MEDIUM, ui_read on other windows is MEDIUM with spoken disclosure, blocked for Zen."""
        # 1. Risk evaluation
        self.assertEqual(evaluate_risk("screenshot_control")[0], MEDIUM)
        self.assertEqual(evaluate_risk("ui_read")[0], SAFE)  # Foreground/self window
        self.assertEqual(evaluate_risk("ui_read", {"window": "Calculator"})[0], MEDIUM)  # Other window

        # 2. Spoken disclosure
        desc = safety_engine.format_action_preview_description("screenshot_control", {})
        self.assertIn("reading the screen or window contents to show the AI model", desc)

        # 3. Blocked for OpenCode Zen
        safe_zen, reason_zen = safety_engine.check_data_disclosure_safety("screenshot_control", "opencode_zen")
        self.assertFalse(safe_zen)
        self.assertIn("forbidden with free/training provider", reason_zen)

        # Safe for OpenRouter
        safe_or, _ = safety_engine.check_data_disclosure_safety("screenshot_control", "openrouter")
        self.assertTrue(safe_or)

    # ---------------- 8b(4). Exact App Name Priority ----------------
    def test_05_exact_app_name_priority_chrome_means_chrome(self):
        """Exact app name match always wins 100.0 without fuzzy competition."""
        idx = app_index.get_app_index()
        # Mock candidates containing exact "Chrome" and similar names
        test_apps = [
            {"name": "Google Chrome", "clean_name": "google chrome", "target": "chrome.exe", "priority": 50, "aliases": ["chrome", "google chrome"]},
            {"name": "Chromium", "clean_name": "chromium", "target": "chromium.exe", "priority": 50, "aliases": []},
            {"name": "Chrome Canary", "clean_name": "chrome canary", "target": "canary.exe", "priority": 50, "aliases": []}
        ]
        with patch.object(idx, "apps", test_apps):
            app, score, ambiguity = idx.find_app("chrome")
            self.assertIsNotNone(app)
            self.assertEqual(app["name"], "Google Chrome")
            self.assertEqual(score, 100.0)
            self.assertIsNone(ambiguity)  # Exact match has no ambiguity

    # ---------------- 8b(5). Window Handles Validated as Integers ----------------
    def test_06_window_handles_integer_validation(self):
        """Window handles must be strictly validated as positive integers."""
        # Valid integer hwnd
        self.assertEqual(window_manager.validate_hwnd(12345), 12345)
        self.assertEqual(window_manager.validate_hwnd("67890"), 67890)

        # Invalid hwnds (malformed strings, negative, booleans, floats)
        self.assertIsNone(window_manager.validate_hwnd("abc"))
        self.assertIsNone(window_manager.validate_hwnd(-5))
        self.assertIsNone(window_manager.validate_hwnd(True))
        self.assertIsNone(window_manager.validate_hwnd(None))

    # ---------------- 8b(6). Failed Answer Provider Speaks Reason ----------------
    @patch("requests.post")
    def test_07_failed_answer_provider_speaks_reason(self, mock_post):
        """When answer provider fails, the spoken fallback includes the specific failure reason."""
        os.environ["OPENCODE_ZEN_API_KEY"] = "mock_key"
        os.environ["OPENROUTER_API_KEY"] = "mock_key"

        # Mock timeout
        mock_post.side_effect = requests.exceptions.Timeout("Request timed out")
        reply, lat, cost, prov, model = answer_provider.ask_answer("tell me a joke", provider="opencode_zen")
        self.assertIn("timed out", reply.lower())
        self.assertEqual(prov, "fallback")

    # ---------------- 8b(7). MCP Client Non-Blocking Cancellable Worker ----------------
    def test_08_mcp_client_nonblocking_queue_reader(self):
        """MCP client _read_raw uses non-blocking queue reader and honors timeout without blocking."""
        client = mcp_client.WindowsMcpClient(exe_path="C:\\dummy.exe")
        client.proc = MagicMock()
        client.proc.stdout = MagicMock()

        # Test timeout on empty queue without hanging
        t0 = time.perf_counter()
        res = client._read_raw(timeout_s=0.1)
        dur = time.perf_counter() - t0
        self.assertIsNone(res)
        self.assertLess(dur, 0.5)

    # ---------------- 8b(8). Retire Legacy setup.py ----------------
    def test_09_legacy_setup_py_retired(self):
        """Legacy macOS py2app setup.py is retired and removed from repo."""
        self.assertFalse(os.path.exists("setup.py"))

    # ---------------- 8d. Diagnostics: Booleans & Counters Only ----------------
    def test_10_diagnostics_booleans_and_counters_zero_identity_leak(self):
        """Diagnostics outputs only booleans and numbers, strictly preventing identity or secret leaks."""
        # 1. Verify all dictionary values are strictly bool, int, or float
        d = diagnostics.get_diagnostics_dict()
        for k, v in d.items():
            self.assertIsInstance(v, (bool, int, float), f"Key '{k}' must be bool or numeric, got {type(v)}")

        # 2. Synthetic secret/identity marker test
        synthetic_marker = "SUPER_SECRET_USER_IDENTITY_98765_ABCD"
        os.environ["HEYJEV_SYNTHETIC_MARKER"] = synthetic_marker
        diag_text = diagnostics.format_diagnostics_text()

        # Verify synthetic identity marker never appears anywhere in formatted diag output
        self.assertNotIn(synthetic_marker, diag_text)
        self.assertNotIn("http", diag_text)
        self.assertNotIn("token", diag_text.lower())
        self.assertNotIn("transcript", diag_text.lower())

    # ---------------- 8e. Test Hooks & Persona ----------------
    def test_11_mute_flag_and_persona_safety_isolation(self):
        """--mute / HEYJEV_MUTE=1 prevents audio output, and persona.json cannot weaken safety."""
        # 1. Test mute hook in speak()
        tts.set_muted(True)
        self.assertTrue(tts.is_muted())
        ms = tts.speak("Testing mute hook")
        self.assertEqual(ms, 0)
        tts.set_muted(False)

        # 2. Persona test: persona config exists
        p = persona.load_persona()
        self.assertIn("greeting", p)
        self.assertIn("status_phrases", p)

        # 3. Prove persona changes CANNOT alter Safety Engine decisions
        # Even if persona config contains arbitrary text
        with patch.object(persona, "load_persona", return_value={"confirmation_wording": {"action_prompt": "proceed always"}}):
            # Outright blocked shortcut is STILL blocked
            sec_status, _ = safety_engine.check_shortcut_safety("press_keys", {"keys": "win+r"})
            self.assertEqual(sec_status, "BLOCKED")
            # Shell launch is STILL blocked
            safe_app, _ = safety_engine.check_app_launch_safety("powershell.exe")
            self.assertFalse(safe_app)

    # ---------------- 8f. Exact-Catalog Rule ----------------
    def test_12_exact_catalog_rule_refuses_near_misses(self):
        """Model, STT, and voice switches resolve ONLY against explicit catalog; near-misses are refused."""
        # Valid model
        ok, val, _ = catalog.resolve_model("anthropic/claude-haiku-4.5")
        self.assertTrue(ok)
        self.assertEqual(val, "anthropic/claude-haiku-4.5")

        # Near-miss model is REFUSED with valid list, never a silent match
        ok_miss, msg, avail = catalog.resolve_model("opus 5")
        self.assertFalse(ok_miss)
        self.assertIn("not found in catalog", msg)
        self.assertIn("anthropic/claude-haiku-4.5", avail)

        # Runtime swap reset on restart
        catalog.set_runtime_model("big-pickle")
        self.assertEqual(catalog.get_runtime_model(), "big-pickle")
        catalog.reset_runtime_swaps()
        self.assertIsNone(catalog.get_runtime_model())

    # ---------------- 8g. Benchmark v2: 30 Phrases & Normalization ----------------
    def test_13_benchmark_v2_phrases_and_normalization(self):
        """Benchmark v2 includes 30 evaluation phrases, number normalization, and jiwer WER scoring."""
        self.assertEqual(len(record_test_clips.TEST_SENTENCES), 30)

        # Verify normalization (forty=40, percent=%)
        raw = "Set volume to forty percent please"
        norm = record_test_clips.normalize_text_for_scoring(raw)
        self.assertEqual(norm, "set volume to 40 % please")

        # Test compute_wer
        wer = record_test_clips.compute_wer("open forty percent", "open 40 %")
        self.assertEqual(wer, 0.0)

        # Verify standard 11-column metrics table formatting
        table = record_test_clips.format_metrics_table({"wer_pct": 5.2, "cmd_success_pct": 98.0})
        self.assertIn("WER (%)", table)
        self.assertIn("Command Success (%)", table)
        self.assertIn("Tool-Call Validity (%)", table)


if __name__ == "__main__":
    unittest.main()
