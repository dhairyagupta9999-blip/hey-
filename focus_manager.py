"""Safe Window Focus and Foreground Manager for Hey Jev (Phase 20).

Provides:
- Safe window activation without violent thread-attach bypasses
- Handling minimized/iconic windows by properly restoring them before activation
- Independent verification of the actual foreground window handle
- Explicit focus-denial detection and assistance-required signaling
- Pluggable Win32 API adapter allowing fully isolated test mocks
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import time
from typing import Any, Callable, Dict, Optional

user32 = ctypes.windll.user32
SW_RESTORE = 9
GA_ROOT = 2


@dataclass
class FocusResult:
    """Detailed result of a window focus attempt."""
    success: bool
    target_hwnd: int
    foreground_hwnd: Optional[int] = None
    target_title: str = ""
    foreground_title: str = ""
    denied: bool = False
    restored: bool = False
    assistance_needed: bool = False
    duration_ms: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "target_hwnd": self.target_hwnd,
            "foreground_hwnd": self.foreground_hwnd,
            "target_title": self.target_title,
            "foreground_title": self.foreground_title,
            "denied": self.denied,
            "restored": self.restored,
            "assistance_needed": self.assistance_needed,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


class SafeFocusManager:
    """Safely manages window focus with independent post-activation verification."""

    def __init__(
        self,
        is_window_fn: Optional[Callable[[int], bool]] = None,
        is_iconic_fn: Optional[Callable[[int], bool]] = None,
        show_window_fn: Optional[Callable[[int, int], bool]] = None,
        set_foreground_fn: Optional[Callable[[int], bool]] = None,
        get_foreground_fn: Optional[Callable[[], int]] = None,
        get_title_fn: Optional[Callable[[int], str]] = None,
        get_ancestor_fn: Optional[Callable[[int, int], int]] = None,
    ) -> None:
        self._is_window = is_window_fn or self._default_is_window
        self._is_iconic = is_iconic_fn or self._default_is_iconic
        self._show_window = show_window_fn or self._default_show_window
        self._set_foreground = set_foreground_fn or self._default_set_foreground
        self._get_foreground = get_foreground_fn or self._default_get_foreground
        self._get_title = get_title_fn or self._default_get_title
        self._get_ancestor = get_ancestor_fn or self._default_get_ancestor

    # -------------------------------------------------------------------------
    # Win32 Defaults
    # -------------------------------------------------------------------------

    @staticmethod
    def _default_is_window(hwnd: int) -> bool:
        return bool(user32.IsWindow(hwnd))

    @staticmethod
    def _default_is_iconic(hwnd: int) -> bool:
        return bool(user32.IsIconic(hwnd))

    @staticmethod
    def _default_show_window(hwnd: int, cmd: int) -> bool:
        return bool(user32.ShowWindow(hwnd, cmd))

    @staticmethod
    def _default_set_foreground(hwnd: int) -> bool:
        return bool(user32.SetForegroundWindow(hwnd))

    @staticmethod
    def _default_get_foreground() -> int:
        return int(user32.GetForegroundWindow())

    @staticmethod
    def _default_get_title(hwnd: int) -> str:
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    @staticmethod
    def _default_get_ancestor(hwnd: int, ga_flags: int) -> int:
        return int(user32.GetAncestor(hwnd, ga_flags))

    # -------------------------------------------------------------------------
    # Focus Activation
    # -------------------------------------------------------------------------

    def activate_window(self, hwnd: int, timeout: float = 0.3) -> FocusResult:
        """Activate and verify target window focus.

        - Restores minimized windows.
        - Calls SetForegroundWindow.
        - Polls and independently verifies actual foreground window.
        - Refuses violent focus-stealing bypasses if Windows denies foreground rights.
        """
        start = time.time()
        target_title = self._get_title(hwnd) if hwnd > 0 else ""

        # Step 1: Validate window handle
        if not hwnd or hwnd <= 0 or not self._is_window(hwnd):
            return FocusResult(
                success=False,
                target_hwnd=hwnd,
                target_title=target_title,
                error=f"Invalid or non-existent window handle: {hwnd}",
                duration_ms=round((time.time() - start) * 1000.0, 2),
            )

        # Step 2: Check if already focused
        current_fg = self._get_foreground()
        if current_fg == hwnd or (current_fg > 0 and self._get_ancestor(current_fg, GA_ROOT) == hwnd):
            return FocusResult(
                success=True,
                target_hwnd=hwnd,
                foreground_hwnd=current_fg,
                target_title=target_title,
                foreground_title=self._get_title(current_fg),
                duration_ms=round((time.time() - start) * 1000.0, 2),
            )

        # Step 3: Restore if minimized
        was_restored = False
        if self._is_iconic(hwnd):
            self._show_window(hwnd, SW_RESTORE)
            was_restored = True
            time.sleep(0.05)

        # Step 4: Attempt activation
        self._set_foreground(hwnd)

        # Step 5: Bounded polling to independently verify actual foreground window
        deadline = time.time() + max(0.05, timeout)
        while time.time() < deadline:
            fg = self._get_foreground()
            if fg == hwnd or (fg > 0 and self._get_ancestor(fg, GA_ROOT) == hwnd):
                return FocusResult(
                    success=True,
                    target_hwnd=hwnd,
                    foreground_hwnd=fg,
                    target_title=target_title,
                    foreground_title=self._get_title(fg),
                    restored=was_restored,
                    duration_ms=round((time.time() - start) * 1000.0, 2),
                )
            time.sleep(0.03)

        # Step 6: Focus Denied by Windows (e.g. ForegroundLockTimeout or background restriction)
        actual_fg = self._get_foreground()
        actual_title = self._get_title(actual_fg) if actual_fg > 0 else ""
        return FocusResult(
            success=False,
            target_hwnd=hwnd,
            foreground_hwnd=actual_fg,
            target_title=target_title,
            foreground_title=actual_title,
            denied=True,
            restored=was_restored,
            assistance_needed=True,
            error=f"FOCUS_DENIED: Windows refused foreground activation. Active window remains: '{actual_title}' (hwnd: {actual_fg})",
            duration_ms=round((time.time() - start) * 1000.0, 2),
        )

    def is_foreground(self, hwnd: int) -> bool:
        """Check whether the specified hwnd is currently the active foreground window."""
        if not hwnd or hwnd <= 0:
            return False
        fg = self._get_foreground()
        return fg == hwnd or (fg > 0 and self._get_ancestor(fg, GA_ROOT) == hwnd)
