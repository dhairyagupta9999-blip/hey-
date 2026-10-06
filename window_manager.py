"""Window Management Controller for Hey Jev (Tier 2).

Provides programmatic window manipulation by name:
  - Focus / bring to front
  - Minimize
  - Maximize
  - Restore
  - Snap left / snap right
"""
from __future__ import annotations

import re
import ctypes
from ctypes import wintypes
from typing import Any, Dict, List, Optional, Tuple

import win32gui
import win32con
import win32process
import psutil

user32 = ctypes.windll.user32

def get_work_area() -> Tuple[int, int, int, int]:
    """Get the usable desktop working area (excluding taskbar): (left, top, width, height)."""
    rect = wintypes.RECT()
    # SPI_GETWORKAREA = 0x0030 (48)
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    return (rect.left, rect.top, w, h)

def find_window_by_name(query: str) -> Optional[int]:
    """Find top-level window handle matching query by title or process name."""
    q = query.lower().strip()
    matching_hwnds: List[Tuple[int, str]] = []

    def enum_cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and not win32gui.GetParent(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title and title not in ("Program Manager", "Hey Jev"):
                title_lower = title.lower()
                # 1. Match title substring
                if q in title_lower:
                    matching_hwnds.append((hwnd, title))
                    return True
                # 2. Match process name
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    p = psutil.Process(pid)
                    p_name = p.name().lower().replace(".exe", "")
                    if q in p_name or p_name in q:
                        matching_hwnds.append((hwnd, title))
                        return True
                except Exception:
                    pass
        return True

    try:
        win32gui.EnumWindows(enum_cb, None)
    except Exception:
        pass

    if matching_hwnds:
        return validate_hwnd(matching_hwnds[0][0])
    return None

def validate_hwnd(hwnd: Any) -> Optional[int]:
    """Validate that hwnd is a valid positive integer window handle."""
    if isinstance(hwnd, int) and not isinstance(hwnd, bool):
        if hwnd > 0:
            return hwnd
    elif isinstance(hwnd, str) and hwnd.strip().isdigit():
        val = int(hwnd.strip())
        if val > 0:
            return val
    return None


def focus_hwnd(hwnd: Any) -> bool:
    """Restore and bring window handle to front."""
    val = validate_hwnd(hwnd)
    if not val:
        return False
    try:
        win32gui.ShowWindow(val, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(val)
        return True
    except Exception:
        return False

def snap_hwnd(hwnd: Any, side: str = "left") -> bool:
    """Snap window handle to left or right half."""
    val = validate_hwnd(hwnd)
    if not val:
        return False
    try:
        left, top, work_w, work_h = get_work_area()
        half_w = work_w // 2
        win32gui.ShowWindow(val, win32con.SW_RESTORE)
        if side.lower() == "left":
            x, y, w, h = left, top, half_w, work_h
        else:
            x, y, w, h = left + half_w, top, half_w, work_h
        user32.MoveWindow(val, x, y, w, h, True)
        win32gui.SetForegroundWindow(val)
        return True
    except Exception:
        return False

def focus_window(name: str) -> bool:
    """Restore and bring window to front."""
    hwnd = find_window_by_name(name)
    if not hwnd:
        return False
    return focus_hwnd(hwnd)

def minimize_window(name: str) -> bool:
    """Minimize window."""
    hwnd = find_window_by_name(name)
    if not hwnd:
        return False
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        return True
    except Exception:
        return False

def maximize_window(name: str) -> bool:
    """Maximize window."""
    hwnd = find_window_by_name(name)
    if not hwnd:
        return False
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        return True
    except Exception:
        return False

def restore_window(name: str) -> bool:
    """Restore window to normal size and position."""
    hwnd = find_window_by_name(name)
    if not hwnd:
        return False
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        return True
    except Exception:
        return False

def snap_window(name: str, side: str = "left") -> bool:
    """Snap window to the left or right half of the primary monitor."""
    hwnd = find_window_by_name(name)
    if not hwnd:
        return False
    try:
        left, top, work_w, work_h = get_work_area()
        half_w = work_w // 2

        # Restore first if maximized
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        if side.lower() == "left":
            x, y, w, h = left, top, half_w, work_h
        else:
            x, y, w, h = left + half_w, top, half_w, work_h

        # Move and resize window
        user32.MoveWindow(hwnd, x, y, w, h, True)
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False

SNAP_REGEX = re.compile(
    r"^(?:snap|dock)\s+(?:the\s+)?(.+?)\s+(?:to\s+(?:the\s+)?)?(left|right)$",
    re.IGNORECASE
)
WINDOW_ACTION_REGEX = re.compile(
    r"^(minimize|maximize|restore|focus|bring\s+up)\s+(?:the\s+)?(.+?)$",
    re.IGNORECASE
)

def resolve_window_command(text: str) -> Optional[Dict[str, Any]]:
    """Parse and execute window layout/state commands."""
    t = text.strip()

    # Snap left / right
    m_snap = SNAP_REGEX.match(t)
    if m_snap:
        target = m_snap.group(1).strip()
        side = m_snap.group(2).lower().strip()
        success = snap_window(target, side)
        return {
            "action": f"snap_{side}",
            "target": target,
            "success": success,
            "line": f"Snapped {target} to the {side}." if success else f"Couldn't find window for {target}."
        }

    # Minimize / Maximize / Restore / Focus
    m_act = WINDOW_ACTION_REGEX.match(t)
    if m_act:
        act = m_act.group(1).lower().strip()
        target = m_act.group(2).strip()

        if act == "minimize":
            ok = minimize_window(target)
            return {"action": "minimize", "target": target, "success": ok, "line": f"Minimized {target}." if ok else f"Couldn't find window for {target}."}
        elif act == "maximize":
            ok = maximize_window(target)
            return {"action": "maximize", "target": target, "success": ok, "line": f"Maximized {target}." if ok else f"Couldn't find window for {target}."}
        elif act == "restore":
            ok = restore_window(target)
            return {"action": "restore", "target": target, "success": ok, "line": f"Restored {target}." if ok else f"Couldn't find window for {target}."}
        elif act in ("focus", "bring up"):
            ok = focus_window(target)
            return {"action": "focus", "target": target, "success": ok, "line": f"Focused {target}." if ok else f"Couldn't find window for {target}."}

    return None
