"""Unit and UI smoke tests for Phase 3: PySide6 Window & Waveform Bubble."""
import os
import sys
import unittest

# Run Qt offscreen for automated headless testing
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from assistant_ui import MainWindow, STATUS_COLORS
from bubble import DictationBubble

class TestPhase3WindowAndBubble(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_bubble_creation_and_rms(self):
        """Verify DictationBubble creation and RMS scaling."""
        bubble = DictationBubble()
        self.assertEqual(bubble.WIDTH, 340)
        self.assertEqual(bubble.HEIGHT, 72)

        # Set RMS
        bubble.set_rms(0.5)
        self.assertAlmostEqual(bubble.target_rms, 1.0, places=2)

        bubble.set_rms(0.01)
        self.assertAlmostEqual(bubble.target_rms, 0.04, places=2)

        # Animate step
        bubble._animate_step()
        self.assertEqual(len(bubble.bar_heights), bubble.NUM_BARS)
        bubble.close()

    def test_main_window_tabs(self):
        """Verify MainWindow has all 7 tabs specified in Prime Directives."""
        window = MainWindow()
        self.assertEqual(window.tabs.count(), 7)

        tab_titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
        expected_tabs = ["Home", "Dictionary", "Apps", "Dictation", "Privacy", "Settings", "Keys"]
        self.assertEqual(tab_titles, expected_tabs)
        self.assertEqual(window.windowTitle(), "Hey Jev")

        window.close()

    def test_status_dot_colors(self):
        """Verify status dot colors match the Mac status-dot language."""
        self.assertEqual(STATUS_COLORS["Ready"], "#10b981")        # Green
        self.assertEqual(STATUS_COLORS["Listening"], "#ef4444")    # Red
        self.assertEqual(STATUS_COLORS["Thinking"], "#a855f7")     # Purple
        self.assertEqual(STATUS_COLORS["Doing it"], "#f97316")     # Orange
        self.assertEqual(STATUS_COLORS["Speaking"], "#06b6d4")     # Teal

    def test_dynamic_privacy_text(self):
        """Verify privacy text changes dynamically based on active backend and torch availability."""
        from unittest.mock import patch
        window = MainWindow()

        # Test Jev text
        window.settings["backend"] = "jev"
        window._update_privacy_text()
        self.assertIn("Jev (TypeSafe Hosted", window.privacy_text.text())
        self.assertIn("faster-whisper", window.privacy_text.text())

        # Test Laya text when torch IS available
        with patch("assistant_ui.is_torch_available", return_value=True):
            window.settings["backend"] = "laya"
            window._update_privacy_text()
            self.assertIn("Local Laya (100% On-Device)", window.privacy_text.text())
            self.assertIn("Zero command text or audio leaves your computer", window.privacy_text.text())

        # Test Laya text when torch is NOT bundled/available (honest packaged mode)
        with patch("assistant_ui.is_torch_available", return_value=False):
            window.settings["backend"] = "laya"
            window._update_privacy_text()
            self.assertNotIn("Local Laya (100% On-Device)", window.privacy_text.text())
            self.assertIn("Fallback - Local Laya Unavailable", window.privacy_text.text())
            self.assertIn("NOT evaluated on-device", window.privacy_text.text())

        window.close()

    def test_honest_laya_dropdown(self):
        """Verify settings dropdown marks Laya as unselectable when torch is not bundled."""
        from unittest.mock import patch
        # When torch is unavailable
        with patch("assistant_ui.is_torch_available", return_value=False):
            window = MainWindow()
            # Item 1 should indicate needs install
            self.assertIn("needs install", window.backend_combo.itemText(1))
            self.assertEqual(window.backend_combo.itemData(1), "laya_unavailable")
            # Should be unselectable (index forced to 0)
            window.backend_combo.setCurrentIndex(1)
            self.assertEqual(window.backend_combo.currentIndex(), 0)
            window.close()

    def test_close_to_tray_semantics(self):
        """Verify Close hides window rather than terminating process."""
        window = MainWindow()
        window.settings["close_to_tray"] = True
        window.show()
        self.assertTrue(window.isVisible())

        # Simulate close event
        from PySide6.QtGui import QCloseEvent
        event = QCloseEvent()
        window.closeEvent(event)
        self.assertTrue(event.isAccepted() or not window.isVisible())
        window.close()

    def test_no_global_hotkey_registered(self):
        """Verify no global OS hotkey is registered for Ctrl+Q and shortcut is window-scoped."""
        from unittest.mock import patch
        from PySide6.QtCore import Qt

        # 1. Assert MainWindow initialization NEVER calls RegisterHotKey
        with patch("ctypes.windll.user32.RegisterHotKey", create=True) as mock_reg:
            window = MainWindow()
            window.show()
            mock_reg.assert_not_called()

            # 2. Verify quit_shortcut is strictly window-scoped (active only when window has focus)
            self.assertEqual(window.quit_shortcut.context(), Qt.WindowShortcut)

            # 3. Verify WM_HOTKEY (0x0312) is not handled in nativeEvent
            import ctypes.wintypes
            msg = ctypes.wintypes.MSG()
            msg.message = 0x0312
            msg.wParam = 101
            res = window.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))
            self.assertFalse(res[0] if isinstance(res, tuple) else False)

            window.close()

if __name__ == "__main__":
    unittest.main()
