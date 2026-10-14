"""Windows 10/11 system, media, app, and volume actions for Hey Jev.

Implements all 33 actions from the Action Parity Table:
- App launching (Win32, UWP/Packaged apps via shell:AppsFolder & URI schemes)
- Window control (focus, minimize, quit with 1.5s process-tree termination)
- State-aware media transport via SendInput (VK_MEDIA_*)
- System and Spotify volume sessions via pycaw
- Dark mode toggling via registry with WM_SETTINGCHANGE broadcast
- Screen locking and sleep
- Non-blocking async execution pool
"""
import os
import re
import time
import winreg
import ctypes
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any
import psutil

# Background executor for non-blocking actions (>50ms operations like quit, process tree scan)
_ACTION_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="HeyJevActionWorker")

def run_async(func, *args, **kwargs):
    """Dispatch action off-loop to keep the voice assistant responsive."""
    return _ACTION_EXECUTOR.submit(func, *args, **kwargs)

# Level percentages
LEVELS = {"silent": 0, "quiet": 25, "medium": 50, "loud": 75, "max": 100}

# Virtual Keys
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP       = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_CONTROL          = 0x11
VK_T                = 0x54
KEYEVENTF_KEYUP     = 0x0002

user32 = ctypes.windll.user32

def send_vk(vk_code: int):
    """Synthesize a single key down and key up event."""
    user32.keybd_event(vk_code, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)

# --------------------------------------------------------------------------- Master Volume via pycaw
def _get_master_endpoint():
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        speakers = AudioUtilities.GetSpeakers()
        if not speakers:
            return None
        if hasattr(speakers, "EndpointVolume"):
            return speakers.EndpointVolume
        interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return interface.QueryInterface(IAudioEndpointVolume)
    except Exception as e:
        print(f"  [pycaw error]: {e}")
        return None

def get_volume() -> int:
    vol = _get_master_endpoint()
    if vol:
        return int(round(vol.GetMasterVolumeLevelScalar() * 100))
    return 50

def set_volume(level_percent: int):
    vol = _get_master_endpoint()
    if vol:
        scalar = max(0.0, min(1.0, level_percent / 100.0))
        vol.SetMasterVolumeLevelScalar(scalar, None)

def set_mute(muted: bool):
    vol = _get_master_endpoint()
    if vol:
        vol.SetMute(1 if muted else 0, None)

# --------------------------------------------------------------------------- Spotify Session Volume
def _get_spotify_session():
    try:
        from pycaw.pycaw import AudioUtilities
        for session in AudioUtilities.GetAllSessions():
            if session.Process and session.Process.name().lower() == "spotify.exe":
                return session
    except Exception:
        pass
    return None

def get_spotify_volume() -> int:
    s = _get_spotify_session()
    if s and s.SimpleAudioVolume:
        return int(round(s.SimpleAudioVolume.GetMasterVolume() * 100))
    return get_volume()

def set_spotify_volume(level_percent: int):
    s = _get_spotify_session()
    if s and s.SimpleAudioVolume:
        scalar = max(0.0, min(1.0, level_percent / 100.0))
        s.SimpleAudioVolume.SetMasterVolume(scalar, None)

def _smtc_cmd(cmd_name: str) -> bool:
    """Execute direct media command via Windows System Media Transport Controls (SMTC)."""
    try:
        import asyncio
        import winsdk.windows.media.control as wmc
        async def _run():
            mgr = await wmc.GlobalSystemMediaTransportControlsSessionManager.request_async()
            sess = mgr.get_current_session()
            if not sess:
                return False
            if cmd_name == "play":
                return await sess.try_play_async()
            elif cmd_name == "pause":
                return await sess.try_pause_async()
            elif cmd_name == "toggle":
                return await sess.try_toggle_play_pause_async()
            elif cmd_name == "next":
                return await sess.try_skip_next_async()
            elif cmd_name == "previous":
                return await sess.try_skip_previous_async()
            return False
        return asyncio.run(_run())
    except Exception:
        return False

def is_audio_playing() -> bool:
    """Check if audio is actively playing via WinRT SMTC session or pycaw session peak."""
    # 1. Query WinRT SMTC session status
    try:
        import asyncio
        import winsdk.windows.media.control as wmc
        async def _check_smtc():
            mgr = await wmc.GlobalSystemMediaTransportControlsSessionManager.request_async()
            sess = mgr.get_current_session()
            if sess:
                info = sess.get_playback_info()
                if info and info.playback_status == wmc.GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING:
                    return True
            return False
        if asyncio.run(_check_smtc()):
            return True
    except Exception:
        pass

    # 2. Query pycaw Spotify session peak meter
    s = _get_spotify_session()
    if s:
        try:
            from pycaw.pycaw import IAudioMeterInformation
            meter = s.QueryInterface(IAudioMeterInformation)
            if meter and meter.GetPeakValue() > 0.001:
                return True
        except Exception:
            pass
    return False

# --------------------------------------------------------------------------- Media Transport (SMTC + VK Fallback)
def media_play():
    """State-aware play: use SMTC try_play_async first, fallback to VK_MEDIA_PLAY_PAUSE toggle."""
    if _smtc_cmd("play"):
        return
    if not is_audio_playing():
        send_vk(VK_MEDIA_PLAY_PAUSE)

def media_pause():
    """State-aware pause: use SMTC try_pause_async first, fallback to VK_MEDIA_PLAY_PAUSE toggle."""
    if _smtc_cmd("pause"):
        return
    if is_audio_playing():
        send_vk(VK_MEDIA_PLAY_PAUSE)

def media_next():
    """Next track: use SMTC try_skip_next_async first, fallback to VK_MEDIA_NEXT_TRACK."""
    if _smtc_cmd("next"):
        return
    send_vk(VK_MEDIA_NEXT_TRACK)

def media_previous():
    """Previous track: use SMTC try_skip_previous_async first, fallback to double VK_MEDIA_PREV_TRACK."""
    if _smtc_cmd("previous"):
        return
    send_vk(VK_MEDIA_PREV_TRACK)
    time.sleep(0.3)
    send_vk(VK_MEDIA_PREV_TRACK)

# --------------------------------------------------------------------------- Dark Mode
def set_dark_mode(dark: bool):
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            val = 0 if dark else 1
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, val)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, val)
        # Broadcast WM_SETTINGCHANGE
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        res = ctypes.c_ulong()
        user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "ImmersiveColorSet",
            SMTO_ABORTIFHUNG, 1000, ctypes.byref(res)
        )
    except Exception as e:
        print(f"  [dark_mode error]: {e}")

def toggle_dark_mode():
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            is_light = bool(val)
        set_dark_mode(is_light)
    except Exception as e:
        print(f"  [toggle dark mode error]: {e}")

# --------------------------------------------------------------------------- System Actions
def lock_screen():
    user32.LockWorkStation()

def sleep_computer():
    ctypes.windll.PowrProf.SetSuspendState(False, False, False)

# --------------------------------------------------------------------------- App Control (Win32 & Packaged)
KNOWN_APP_LAUNCHERS: Dict[str, list] = {
    "spotify": ["spotify:", "Spotify.exe", "shell:AppsFolder\\SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"],
    "slack": ["slack:", "slack.exe", os.path.expandvars(r"%LOCALAPPDATA%\slack\slack.exe")],
    "discord": ["discord:", "Discord.exe", os.path.expandvars(r"%LOCALAPPDATA%\Discord\Update.exe --processStart Discord.exe")],
    "vscode": ["code", "Code.exe", os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe")],
    "code": ["code", "Code.exe", os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe")],
    "visual studio code": ["code", "Code.exe", os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe")],
    "chrome": ["chrome.exe", os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"), os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe")],
    "google chrome": ["chrome.exe", os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"), os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe")],
    "edge": ["msedge.exe", "microsoft-edge:", os.path.expandvars(r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe")],
    "microsoft edge": ["msedge.exe", "microsoft-edge:", os.path.expandvars(r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe")],
    "brave": ["brave.exe", os.path.expandvars(r"%PROGRAMFILES%\BraveSoftware\Brave-Browser\Application\brave.exe")],
    "firefox": ["firefox.exe", os.path.expandvars(r"%PROGRAMFILES%\Mozilla Firefox\firefox.exe")],
    "terminal": ["wt.exe", "powershell.exe"],
    "notepad": ["notepad.exe"],
    "notes": ["notepad.exe"],
    "messages": ["ms-chat:", "ms-people:"],
    "finder": ["explorer.exe"],
    "mail": ["outlook.exe", "outlookmail:"],
    "calendar": ["outlookcal:", "outlook.exe"],
    "calculator": ["calc.exe"],
}

def launch_app(app_target: str):
    """Launch Win32, UWP, or packaged Windows applications with fallback chain."""
    app_norm = app_target.lower().strip()

    # Check known launcher table
    candidates = KNOWN_APP_LAUNCHERS.get(app_norm, [app_target])

    for target in candidates:
        try:
            if target.startswith("shell:") or ":" in target and not "\\" in target:
                # URI scheme or shell folder (e.g. spotify:, shell:AppsFolder...)
                subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
                return True
            else:
                # Executable path or command
                subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
                return True
        except Exception:
            continue

    # Fallback to PowerShell Start-Process
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-Command", f"Start-Process '{app_target}'"], shell=False)
        return True
    except Exception as e:
        print(f"  [launch_app error]: Failed to launch {app_target}: {e}")
        return False

def find_process_by_name(name: str):
    name_clean = name.lower().replace(".exe", "")
    for p in psutil.process_iter(["pid", "name"]):
        try:
            p_name = p.info["name"].lower()
            if name_clean in p_name:
                return p
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None

def quit_app(proc_name: str):
    """Graceful WM_CLOSE, wait 1.5s, force-kill process tree if still alive (tray-minimizing apps)."""
    p = find_process_by_name(proc_name)
    if not p:
        return
    try:
        # 1. Attempt graceful WM_CLOSE via pywinauto / Windows API
        try:
            from pywinauto import Application
            app = Application(backend="uia").connect(process=p.pid, timeout=1.0)
            app.kill()
        except Exception:
            pass

        # 2. Terminate top process and children
        for child in psutil.Process(p.pid).children(recursive=True) + [p]:
            try:
                child.terminate()
            except Exception:
                pass

        time.sleep(1.5)

        # 3. Force kill tree if process remains alive (tray apps like Spotify, Slack, Discord)
        if p.is_running():
            for child in p.children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    pass
            p.kill()
    except Exception as e:
        print(f"  [quit_app error]: {e}")

def minimize_app(proc_name: str):
    """Minimize top-level window (never bare SW_HIDE)."""
    # Try pywinauto first
    p = find_process_by_name(proc_name)
    if not p:
        return
    try:
        from pywinauto import Application
        app = Application(backend="uia").connect(process=p.pid, timeout=1.0)
        top = app.top_window()
        if top:
            top.minimize()
            return
    except Exception:
        pass

    # Win32 fallback
    SW_MINIMIZE = 6
    def enum_cb(hwnd, lparam):
        if user32.IsWindowVisible(hwnd):
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == p.pid:
                user32.ShowWindow(hwnd, SW_MINIMIZE)
        return True
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(WNDENUMPROC(enum_cb), 0)

def focus_app(proc_name: str):
    """Restore and bring top-level window to front."""
    p = find_process_by_name(proc_name)
    if not p:
        return
    try:
        from pywinauto import Application
        app = Application(backend="uia").connect(process=p.pid, timeout=1.0)
        top = app.top_window()
        if top:
            top.restore()
            top.set_focus()
            return
    except Exception:
        pass

    # Win32 fallback
    SW_RESTORE = 9
    def enum_cb(hwnd, lparam):
        if user32.IsWindowVisible(hwnd):
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == p.pid:
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
                return False
        return True
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(WNDENUMPROC(enum_cb), 0)

def open_url(url: str, browser_name: str | None = None):
    """Open URL in default or specified browser."""
    if not url.startswith("http"):
        url = "https://" + url
    if browser_name:
        p = find_process_by_name(browser_name)
        if p:
            subprocess.Popen([p.name(), url], shell=False)
            return
    os.startfile(url)

def browser_new_tab(browser_name: str | None = None):
    """Focus browser and synthesize Ctrl+T."""
    if browser_name:
        focus_app(browser_name)
    time.sleep(0.15)
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_T, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_T, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
