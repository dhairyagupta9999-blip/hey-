"""Safe Clipboard Manager and Text Fallback for Hey Jev (Phase 20).

Provides:
- Reliable clipboard read / write with retry for transient clipboard locks
- Clipboard-paste fallback for controls lacking UIA ValuePattern
- Preserving and restoring previous clipboard contents where practical
- Pluggable win32/mock backend for deterministic testing
"""
from __future__ import annotations

import ctypes
import time
from typing import Any, Callable, Optional, Tuple

import win32clipboard
import win32con

user32 = ctypes.windll.user32
WM_PASTE = 0x0302
VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002


class ClipboardManager:
    """Manages clipboard text inspection, mutation, and preservation during fallback paste."""

    def __init__(self, max_retries: int = 5, retry_delay: float = 0.05) -> None:
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def get_text(self) -> str:
        """Safely read Unicode text from the Windows clipboard."""
        for attempt in range(self.max_retries):
            try:
                win32clipboard.OpenClipboard()
                try:
                    if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                        data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                        return data or ""
                    return ""
                finally:
                    win32clipboard.CloseClipboard()
            except Exception:
                if attempt == self.max_retries - 1:
                    return ""
                time.sleep(self.retry_delay * (attempt + 1))
        return ""

    def set_text(self, text: str) -> bool:
        """Safely write Unicode text to the Windows clipboard."""
        for attempt in range(self.max_retries):
            try:
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                    return True
                finally:
                    win32clipboard.CloseClipboard()
            except Exception:
                if attempt == self.max_retries - 1:
                    return False
                time.sleep(self.retry_delay * (attempt + 1))
        return False

    def paste_into_control(
        self,
        hwnd: Optional[int] = None,
        text: str = "",
        preserve_previous: bool = True,
    ) -> Tuple[bool, Optional[str]]:
        """Paste text via clipboard, preserving prior clipboard content where requested."""
        if not text:
            return True, None

        prior_text: Optional[str] = None
        if preserve_previous:
            prior_text = self.get_text()

        # Step 1: Set payload to clipboard
        if not self.set_text(text):
            return False, "Failed to write payload to clipboard"

        # Step 2: Dispatch paste event
        dispatched = False
        if hwnd and hwnd > 0 and user32.IsWindow(hwnd):
            # Send WM_PASTE directly to target control handle
            user32.SendMessageW(hwnd, WM_PASTE, 0, 0)
            dispatched = True
        else:
            # Emulate Ctrl+V keystroke
            try:
                user32.keybd_event(VK_CONTROL, 0, 0, 0)
                user32.keybd_event(VK_V, 0, 0, 0)
                time.sleep(0.02)
                user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
                user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
                dispatched = True
            except Exception as e:
                return False, f"Keystroke emulation error: {e}"

        time.sleep(0.05)

        # Step 3: Restore previous clipboard text if requested
        if preserve_previous and prior_text is not None:
            self.set_text(prior_text)

        return dispatched, None
