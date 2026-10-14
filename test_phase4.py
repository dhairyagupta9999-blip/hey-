"""Phase 4 Test Suite: Packaging, Autostart, and Distribution on Windows 10/11 x64."""
import os
import sys
import unittest
import ast

class TestPhase4Packaging(unittest.TestCase):
    def test_01_spec_file_syntax(self):
        """Validate hey_jev.spec parses as Python.

        The three `assertIn("Analysis"/"COLLECT"/"Hey Jev", code)` assertions
        that used to live here were deleted (§5.2): they only proved the file
        still contained three literal strings, which says nothing about whether
        the program works. The parse below is kept because a spec that does not
        compile genuinely cannot be built.
        """
        spec_path = os.path.join(os.path.dirname(__file__), "hey_jev.spec")
        self.assertTrue(os.path.exists(spec_path), "hey_jev.spec must exist")
        with open(spec_path, "r", encoding="utf-8") as f:
            code = f.read()
        parsed = ast.parse(code)  # raises SyntaxError if the spec is malformed
        self.assertIsNotNone(parsed)

    def test_02_icon_ico_generated(self):
        """Verify Windows icon .ico is present with multi-resolution format."""
        ico_path = os.path.join(os.path.dirname(__file__), "assets", "icon.ico")
        self.assertTrue(os.path.exists(ico_path), "assets/icon.ico must exist")
        self.assertGreater(os.path.getsize(ico_path), 1000)

    def test_03_autostart_cycle(self):
        """Verify autostart enable/query/disable cycle."""
        import autostart
        # Ensure clean initial state
        autostart.disable_autostart()
        self.assertFalse(autostart.is_autostart_enabled())

        # Enable autostart
        ok, msg = autostart.enable_autostart()
        self.assertTrue(ok, f"Enable autostart failed: {msg}")
        self.assertTrue(autostart.is_autostart_enabled(), "Autostart should report enabled")

        # Disable autostart
        ok2, msg2 = autostart.disable_autostart()
        self.assertTrue(ok2, f"Disable autostart failed: {msg2}")
        self.assertFalse(autostart.is_autostart_enabled(), "Autostart should report disabled")

    def test_04_target_command_generation(self):
        """Verify get_target_command handles source vs frozen correctly."""
        import autostart
        cmd = autostart.get_target_command()
        self.assertTrue(cmd.startswith('"'))
        self.assertTrue(cmd.endswith('"'))
        self.assertIn("app.py", cmd)

        custom = autostart.get_target_command("C:\\Test\\Hey Jev.exe")
        self.assertEqual(custom, '"C:\\Test\\Hey Jev.exe"')

    # Deleted (§5.2): test_05_app_entrypoint_imports and
    # test_06_ui_autostart_toggle_integrated. Both opened a source file and
    # asserted it contained a literal string ("from assistant_ui import
    # run_app", "chk_autostart", "_on_autostart_toggled"). That establishes
    # only that a grep still matches -- it cannot fail because the program is
    # broken, only because somebody renamed a symbol or reworded a line.
    # Behavioural coverage for the autostart path is in test_03 and test_04.

if __name__ == "__main__":
    unittest.main()
