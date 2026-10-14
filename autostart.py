"""Windows autostart manager for Hey Jev.

Uses Windows Task Scheduler "At log on" (schtasks.exe) without requiring
administrator privileges. Includes HKCU Run registry fallback if Task Scheduler
creation is blocked by local machine policy.
"""
import os
import sys
import subprocess
import winreg
from typing import Tuple, Optional

TASK_NAME = "HeyJev"
REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

def get_target_command(target_path: Optional[str] = None) -> str:
    """Determine the command string to execute at logon."""
    if target_path:
        return f'"{os.path.abspath(target_path)}"'
    
    # If packaged via PyInstaller / Nuitka:
    if getattr(sys, "frozen", False):
        return f'"{os.path.abspath(sys.executable)}"'
    
    # Running from Python source:
    app_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
    python_exe = sys.executable
    # On Windows, pythonw.exe avoids console window popup
    pythonw = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
    if os.path.exists(pythonw):
        python_exe = pythonw
    return f'"{python_exe}" "{app_py}"'

def is_autostart_enabled() -> bool:
    """Check whether Hey Jev is configured to start on logon."""
    # 1. Check Task Scheduler
    try:
        res = subprocess.run(
            ["schtasks.exe", "/Query", "/TN", TASK_NAME],
            capture_output=True,
            text=True,
            timeout=5
        )
        if res.returncode == 0:
            return True
    except Exception:
        pass

    # 2. Check HKCU Run registry key fallback
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, TASK_NAME)
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return False

def enable_autostart(target_path: Optional[str] = None) -> Tuple[bool, str]:
    """Enable autostart at user logon via Task Scheduler (or HKCU Run registry)."""
    cmd = get_target_command(target_path)
    
    # 1. Try Task Scheduler (ONLOGON trigger, current non-admin user context)
    try:
        res = subprocess.run(
            ["schtasks.exe", "/Create", "/TN", TASK_NAME, "/TR", cmd, "/SC", "ONLOGON", "/F"],
            capture_output=True,
            text=True,
            timeout=8
        )
        if res.returncode == 0:
            return True, f"Configured Task Scheduler logon task '{TASK_NAME}'"
    except Exception as e:
        pass

    # 2. Fallback to HKCU Run key
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, TASK_NAME, 0, winreg.REG_SZ, cmd)
        return True, f"Configured HKCU Run registry autostart for '{TASK_NAME}'"
    except Exception as e:
        return False, f"Failed to configure autostart: {e}"

def disable_autostart() -> Tuple[bool, str]:
    """Remove Hey Jev from logon startup."""
    msgs = []
    success = False

    # 1. Remove Task Scheduler task
    try:
        res = subprocess.run(
            ["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if res.returncode == 0:
            msgs.append("Removed Task Scheduler task")
            success = True
    except Exception:
        pass

    # 2. Remove HKCU Run key
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, TASK_NAME)
            msgs.append("Removed HKCU Run registry value")
            success = True
    except FileNotFoundError:
        # Not in registry
        pass
    except Exception as e:
        msgs.append(f"Registry error: {e}")

    if not msgs and not is_autostart_enabled():
        return True, "Autostart was not enabled"
    return (True if success or not is_autostart_enabled() else False), "; ".join(msgs)
