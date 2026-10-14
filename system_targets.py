"""System Targets, Settings URIs, Folders, and Web Navigation (Tier 2).

Resolves and executes:
  - ms-settings: URIs (wifi, bluetooth, display, sound, apps, windowsupdate)
  - Windows Administrative Tools (Task Manager, Control Panel, Device Manager)
  - Standard user folders (Downloads, Documents, Desktop, Pictures, Music, Videos)
  - Websites: direct URLs, known sites map, or fallback web search
"""
from __future__ import annotations

import os
import subprocess
import webbrowser
import re
from typing import Any, Dict, Optional

SETTINGS_PAGES = {
    "wifi": ("ms-settings:network-wifi", "Wi-Fi Settings"),
    "wi-fi": ("ms-settings:network-wifi", "Wi-Fi Settings"),
    "network": ("ms-settings:network", "Network Settings"),
    "internet": ("ms-settings:network", "Network Settings"),
    "bluetooth": ("ms-settings:bluetooth", "Bluetooth Settings"),
    "bluetooth settings": ("ms-settings:bluetooth", "Bluetooth Settings"),
    "display": ("ms-settings:display", "Display Settings"),
    "screen": ("ms-settings:display", "Display Settings"),
    "sound": ("ms-settings:sound", "Sound Settings"),
    "audio": ("ms-settings:sound", "Sound Settings"),
    "apps": ("ms-settings:appsfeatures", "Apps & Features"),
    "installed apps": ("ms-settings:appsfeatures", "Apps & Features"),
    "updates": ("ms-settings:windowsupdate", "Windows Update"),
    "windows update": ("ms-settings:windowsupdate", "Windows Update"),
    "update": ("ms-settings:windowsupdate", "Windows Update"),
    "battery": ("ms-settings:powersleep", "Battery & Power Settings"),
    "power": ("ms-settings:powersleep", "Battery & Power Settings"),
    "storage": ("ms-settings:storagesense", "Storage Settings"),
    "notifications": ("ms-settings:notifications", "Notification Settings"),
    "date and time": ("ms-settings:dateandtime", "Date & Time Settings"),
    "time": ("ms-settings:dateandtime", "Date & Time Settings"),
    "settings": ("ms-settings:", "Windows Settings"),
}

STANDARD_FOLDERS = {
    "downloads": (os.path.expanduser("~/Downloads"), "Downloads folder"),
    "downloads folder": (os.path.expanduser("~/Downloads"), "Downloads folder"),
    "documents": (os.path.expanduser("~/Documents"), "Documents folder"),
    "documents folder": (os.path.expanduser("~/Documents"), "Documents folder"),
    "desktop": (os.path.expanduser("~/Desktop"), "Desktop folder"),
    "desktop folder": (os.path.expanduser("~/Desktop"), "Desktop folder"),
    "pictures": (os.path.expanduser("~/Pictures"), "Pictures folder"),
    "pictures folder": (os.path.expanduser("~/Pictures"), "Pictures folder"),
    "music": (os.path.expanduser("~/Music"), "Music folder"),
    "music folder": (os.path.expanduser("~/Music"), "Music folder"),
    "videos": (os.path.expanduser("~/Videos"), "Videos folder"),
    "videos folder": (os.path.expanduser("~/Videos"), "Videos folder"),
}

SYSTEM_UTILITIES = {
    "task manager": ("taskmgr.exe", "Task Manager"),
    "taskmgr": ("taskmgr.exe", "Task Manager"),
    "control panel": ("control.exe", "Control Panel"),
    "device manager": ("devmgmt.msc", "Device Manager"),
    "device manager console": ("devmgmt.msc", "Device Manager"),
    "disk management": ("diskmgmt.msc", "Disk Management"),
    "resource monitor": ("resmon.exe", "Resource Monitor"),
    "services": ("services.msc", "Services"),
}

KNOWN_SITES = {
    "youtube": "https://www.youtube.com",
    "github": "https://github.com",
    "gmail": "https://mail.google.com",
    "reddit": "https://www.reddit.com",
    "twitter": "https://twitter.com",
    "x": "https://x.com",
    "google": "https://www.google.com",
    "wikipedia": "https://www.wikipedia.org",
    "netflix": "https://www.netflix.com",
    "spotify": "https://open.spotify.com",
    "amazon": "https://www.amazon.com",
    "twitch": "https://www.twitch.tv",
    "discord": "https://discord.com",
    "linkedin": "https://www.linkedin.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
}

URL_REGEX = re.compile(
    r"^(?:https?://)?(?:www\.)?([a-zA-Z0-9-]+\.(?:com|org|net|io|edu|gov|co|ai|dev|app|info|xyz|me|tv|cc))(?:/[^\s]*)?$",
    re.IGNORECASE
)

# Short browser name -> the application name `start` resolves via App Paths (B2).
BROWSER_EXECUTABLES = {
    "chrome": "chrome",
    "edge": "msedge",
    "firefox": "firefox",
    "brave": "brave",
    "opera": "opera",
}

def is_url(text: str) -> Optional[str]:
    """Check if text is a valid web URL, returning a normalized https:// URL."""
    t = text.strip()
    if t.startswith("http://") or t.startswith("https://"):
        return t
    m = URL_REGEX.match(t)
    if m:
        return f"https://{t}"
    return None

def execute_system_target(res: Dict[str, Any]) -> bool:
    """Execute the action described by a resolved system target."""
    if not res:
        return False
    target_type = res.get("type")
    try:
        if target_type == "setting":
            os.startfile(res["uri"])
            return True
        elif target_type == "utility":
            subprocess.Popen(["cmd", "/c", "start", "", res["cmd"]], shell=False)
            return True
        elif target_type == "folder":
            path = res["path"]
            if os.path.exists(path):
                os.startfile(path)
            else:
                subprocess.Popen(["explorer.exe", path], shell=False)
            return True
        elif target_type in ("website", "search"):
            url = res["url"]
            browser = res.get("browser")
            if browser:
                # B2: honour an explicitly requested browser instead of the
                # system default. `start <app> <url>` resolves through App Paths.
                exe = BROWSER_EXECUTABLES.get(browser)
                if exe:
                    subprocess.Popen(["cmd", "/c", "start", "", exe, url],
                                     shell=False)
                    return True
            webbrowser.open(url)
            return True
    except Exception as exc:
        print(f"  [system_target exec error]: {exc}")
        return False
    return False


def resolve_system_target(text: str, execute: bool = False) -> Optional[Dict[str, Any]]:
    """Resolve spoken target against Settings pages, system utilities, folders, or websites.
    
    If execute is True, executes the corresponding system action immediately.
    """
    t_clean = text.lower().strip()
    # Strip common leading verbs
    t_target = re.sub(r"^(?:open|launch|start|show|go\s+to|bring\s+up)\s+(?:the\s+|my\s+)?", "", t_clean).strip()
    t_target = re.sub(r"\s+please$", "", t_target).strip()

    # 0. Trailing browser qualifier (B2). "open youtube in chrome" was
    #    previously unresolvable: the qualifier stayed glued to the site name
    #    so the KNOWN_SITES lookup below missed entirely, and the utterance
    #    fell through to an ambiguous app-index match. Split it off first and
    #    carry the browser through to execution.
    browser = None
    try:
        from target_extractor import split_browser_qualifier
        t_target, browser = split_browser_qualifier(t_target)
    except Exception:
        browser = None

    # 1. Settings Pages
    for key, (uri, label) in SETTINGS_PAGES.items():
        if t_target == key or t_target == f"{key} settings" or t_target == f"settings for {key}":
            res = {
                "type": "setting",
                "uri": uri,
                "label": label,
                "line": f"Opening {label}."
            }
            if execute:
                execute_system_target(res)
            return res

    # 2. System Utilities
    for key, (cmd, label) in SYSTEM_UTILITIES.items():
        if t_target == key:
            res = {
                "type": "utility",
                "cmd": cmd,
                "label": label,
                "line": f"Opening {label}."
            }
            if execute:
                execute_system_target(res)
            return res

    # 3. Standard Folders
    for key, (path, label) in STANDARD_FOLDERS.items():
        if t_target == key or t_target == f"{key} folder":
            res = {
                "type": "folder",
                "path": path,
                "label": label,
                "line": f"Opening {label}."
            }
            if execute:
                execute_system_target(res)
            return res

    # 4. Websites
    # Direct URL
    normalized_url = is_url(t_target)
    if normalized_url:
        res = {
            "type": "website",
            "url": normalized_url,
            "label": normalized_url,
            "line": f"Opening {normalized_url}."
        }
        if browser:
            res["browser"] = browser
            res["line"] = f"Opening {normalized_url} in {browser.capitalize()}."
        if execute:
            execute_system_target(res)
        return res

    # Known site by bare name
    if t_target in KNOWN_SITES:
        url = KNOWN_SITES[t_target]
        res = {
            "type": "website",
            "url": url,
            "label": t_target.capitalize(),
            "line": f"Opening {t_target.capitalize()}."
        }
        if browser:
            res["browser"] = browser
            res["line"] = f"Opening {t_target.capitalize()} in {browser.capitalize()}."
        if execute:
            execute_system_target(res)
        return res

    # If explicitly phrased as "search for X" or "google X"
    search_m = re.match(r"^(?:search\s+for|google|web\s+search\s+for|search\s+the\s+web\s+for)\s+(.+)$", t_clean)
    if search_m:
        query = search_m.group(1).strip()
        search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        res = {
            "type": "search",
            "url": search_url,
            "query": query,
            "line": f"Searching for {query}."
        }
        if execute:
            execute_system_target(res)
        return res

    return None

