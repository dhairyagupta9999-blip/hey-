"""DPI Awareness and High-DPI Coordinate Manager for Hey Jev (Phase 20).

Provides:
- Per-Monitor V2 DPI awareness initialization via SetProcessDpiAwarenessContext
- Inspection of existing awareness state without crashing on established contexts
- Querying per-window, per-monitor, and system DPI scaling
- Bidirectional logical <-> physical coordinate transformations
- Center point calculations and bounding rectangle validation
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from enum import IntEnum
import os
import sys
from typing import Any, Dict, Optional, Tuple

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Win32 DPI Awareness Context Constants
DPI_AWARENESS_CONTEXT_UNAWARE = ctypes.c_void_p(-1)
DPI_AWARENESS_CONTEXT_SYSTEM_AWARE = ctypes.c_void_p(-2)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = ctypes.c_void_p(-3)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
DPI_AWARENESS_CONTEXT_UNAWARE_GDISCALED = ctypes.c_void_p(-5)


class DpiAwarenessLevel(IntEnum):
    """Win32 DPI_AWARENESS enumeration."""
    INVALID = -1
    UNAWARE = 0
    SYSTEM_AWARE = 1
    PER_MONITOR_AWARE = 2
    PER_MONITOR_AWARE_V2 = 3


class DpiManager:
    """Manages process DPI awareness, monitor scaling, and coordinate translation."""

    _initialized: bool = False
    _awareness_level: DpiAwarenessLevel = DpiAwarenessLevel.INVALID
    _init_status: Dict[str, Any] = {}

    @classmethod
    def init_dpi_awareness(cls) -> Dict[str, Any]:
        """Initialize Per-Monitor V2 DPI awareness early.

        Inspects the existing awareness state and safely handles an already-established
        context without raising or crashing.
        """
        if cls._initialized:
            return cls._init_status

        status: Dict[str, Any] = {
            "requested": "PER_MONITOR_AWARE_V2",
            "success": False,
            "already_established": False,
            "awareness_level": DpiAwarenessLevel.INVALID.name,
            "error_code": 0,
            "method": "SetProcessDpiAwarenessContext",
        }

        # Check if SetProcessDpiAwarenessContext is available (Win10 Creators Update 1703+)
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            try:
                # Attempt to set Per-Monitor V2
                res = user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
                err = ctypes.GetLastError()

                if res:
                    status["success"] = True
                    status["awareness_level"] = DpiAwarenessLevel.PER_MONITOR_AWARE_V2.name
                    cls._awareness_level = DpiAwarenessLevel.PER_MONITOR_AWARE_V2
                else:
                    status["error_code"] = err
                    # Error 5 is ERROR_ACCESS_DENIED: Context was already established earlier
                    if err == 5:
                        status["already_established"] = True
                        status["success"] = True
                        # Inspect the current awareness state
                        cls._awareness_level = cls.get_current_awareness()
                        status["awareness_level"] = cls._awareness_level.name
                    else:
                        cls._awareness_level = cls.get_current_awareness()
                        status["awareness_level"] = cls._awareness_level.name
            except Exception as e:
                status["error"] = str(e)
                cls._awareness_level = DpiAwarenessLevel.UNAWARE
                status["awareness_level"] = cls._awareness_level.name
        else:
            # Fallback for older Windows using shcore.SetProcessDpiAwareness
            try:
                shcore = ctypes.windll.shcore
                if hasattr(shcore, "SetProcessDpiAwareness"):
                    res = shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
                    status["method"] = "SetProcessDpiAwareness"
                    status["success"] = (res == 0)
                    cls._awareness_level = DpiAwarenessLevel.PER_MONITOR_AWARE
                    status["awareness_level"] = cls._awareness_level.name
            except Exception as e:
                status["error"] = str(e)
                cls._awareness_level = DpiAwarenessLevel.UNAWARE
                status["awareness_level"] = cls._awareness_level.name

        cls._initialized = True
        cls._init_status = status
        return status

    @classmethod
    def get_current_awareness(cls) -> DpiAwarenessLevel:
        """Inspect the active process DPI awareness level."""
        if hasattr(user32, "GetDpiAwarenessContextForProcess") and hasattr(user32, "GetAwarenessFromDpiAwarenessContext"):
            try:
                current_process = kernel32.GetCurrentProcess()
                ctx = user32.GetDpiAwarenessContextForProcess(current_process)
                if ctx:
                    val = user32.GetAwarenessFromDpiAwarenessContext(ctx)
                    if val == 2:
                        return DpiAwarenessLevel.PER_MONITOR_AWARE_V2
                    elif val == 1:
                        return DpiAwarenessLevel.SYSTEM_AWARE
                    elif val == 0:
                        return DpiAwarenessLevel.UNAWARE
            except Exception:
                pass
        return DpiAwarenessLevel.UNAWARE

    @classmethod
    def get_window_dpi(cls, hwnd: Optional[int] = None) -> int:
        """Get the DPI for a specific window handle, or desktop default (96 DPI = 100%)."""
        if hwnd and hwnd > 0 and hasattr(user32, "GetDpiForWindow"):
            try:
                dpi = user32.GetDpiForWindow(hwnd)
                if dpi > 0:
                    return dpi
            except Exception:
                pass

        if hasattr(user32, "GetDpiForSystem"):
            try:
                dpi = user32.GetDpiForSystem()
                if dpi > 0:
                    return dpi
            except Exception:
                pass

        # Default standard 96 DPI
        return 96

    @classmethod
    def get_scale_factor(cls, hwnd: Optional[int] = None) -> float:
        """Calculate the scaling multiplier (e.g. 1.0 for 100%, 1.25 for 125%, 1.5 for 150%)."""
        return cls.get_window_dpi(hwnd) / 96.0

    @classmethod
    def logical_to_physical_point(cls, x: int, y: int, hwnd: Optional[int] = None) -> Tuple[int, int]:
        """Convert logical client coordinates to physical screen coordinates."""
        scale = cls.get_scale_factor(hwnd)
        return int(round(x * scale)), int(round(y * scale))

    @classmethod
    def physical_to_logical_point(cls, x: int, y: int, hwnd: Optional[int] = None) -> Tuple[int, int]:
        """Convert physical screen coordinates to logical client coordinates."""
        scale = cls.get_scale_factor(hwnd)
        if scale == 0:
            return x, y
        return int(round(x / scale)), int(round(y / scale))

    @classmethod
    def rect_to_center_point(cls, rect: Dict[str, int]) -> Tuple[int, int]:
        """Compute the geometric center (cx, cy) of a bounding rectangle."""
        left = rect.get("left", 0)
        top = rect.get("top", 0)
        right = rect.get("right", left)
        bottom = rect.get("bottom", top)
        cx = left + (right - left) // 2
        cy = top + (bottom - top) // 2
        return cx, cy

    @classmethod
    def is_rect_valid(cls, rect: Dict[str, int]) -> bool:
        """Check whether bounding rectangle has positive area."""
        left = rect.get("left", 0)
        top = rect.get("top", 0)
        right = rect.get("right", 0)
        bottom = rect.get("bottom", 0)
        return (right > left) and (bottom > top)
