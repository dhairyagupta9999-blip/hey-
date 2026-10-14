"""Monotonic timers and reminders persisted to disk, announced via TTS and Windows Toast."""
import os
import re
import json
import time
import uuid
import threading
import subprocess
from config import TIMERS_FILE

NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "ninety": 90, "couple": 2, "few": 3
}

UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600
}

DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b(\s+and\s+a\s+half)?")

def _digits(text):
    """'twenty five minutes' -> '25 minutes', 'half an hour' -> '30 minutes'."""
    t = re.sub(r"\bhalf an? hour\b", "30 minutes", text.lower())
    t = re.sub(r"\ba couple of\b", "couple", t)
    t = re.sub(r"\b(an?|few|couple)\s+(hours?|minutes?|seconds?)\b", lambda m: f"{NUMBER_WORDS[m[1]]} {m[2]}", t)
    words = t.replace("-", " ").split()
    out, i = [], 0
    while i < len(words):
        w = words[i].strip(",.!?")
        if w in NUMBER_WORDS and w not in ("a", "an", "few", "couple"):
            n = NUMBER_WORDS[w]
            nxt = words[i + 1].strip(",.!?") if i + 1 < len(words) else ""
            if n >= 20 and nxt in NUMBER_WORDS and NUMBER_WORDS[nxt] < 10 and nxt not in ("a", "an"):
                n, i = n + NUMBER_WORDS[nxt], i + 1
            out.append(str(n))
        else:
            out.append(words[i])
        i += 1
    return " ".join(out)


def parse_duration(text):
    """Total seconds mentioned in the sentence, or None."""
    total = 0
    for num, unit, half in DURATION.findall(_digits(text)):
        secs = UNITS[unit]
        total += float(num) * secs + (secs / 2 if half else 0)
    return int(total) or None


def parse_reminder(text):
    """What to remind about: the part after 'to', minus any duration. 'remind me in 5 min to call mum' -> 'call mum'."""
    m = re.search(r"\bto\s+(.+)$", _digits(text))
    if not m:
        return None
    what = DURATION.sub("", m[1])
    what = re.sub(r"\bplease\b", "", what).strip(" .,!?")
    what = re.sub(r"\s*\b(in|for|after)$", "", what).strip(" .,!?")
    return what or None


def say_duration(secs):
    secs = max(0, int(round(secs)))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    parts = [f"{n} {u}{'' if n == 1 else 's'}" for n, u in ((h, "hour"), (m, "minute"), (s, "second")) if n]
    if h or m >= 10:  # skip seconds once it's a long wait
        parts = parts[:2] if h else parts[:1]
    return " and ".join(parts) or "no time"


def short_duration(secs):
    h, rem = divmod(int(secs), 3600)
    m, s = divmod(rem, 60)
    return " ".join(f"{n} {u}" for n, u in ((h, "hr"), (m, "min"), (s, "sec")) if n) or "0 sec"


# --------------------------------------------------------------------------- Windows Toast Notification
def show_windows_toast(title: str, message: str):
    """Display a native Windows Toast notification."""
    def _toast():
        ps_cmd = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$toastXml = [xml]$template.GetXml()
$toastXml.GetElementsByTagName("text")[0].AppendChild($toastXml.CreateTextNode('{title}')) > $null
$toastXml.GetElementsByTagName("text")[1].AppendChild($toastXml.CreateTextNode('{message}')) > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($toastXml.OuterXml)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Hey Jev").Show($toast)
"""
        try:
            subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd],
                           capture_output=True, timeout=5)
        except Exception as e:
            print(f"  [toast error]: {e}")
    threading.Thread(target=_toast, daemon=True).start()


# --------------------------------------------------------------------------- Monotonic Timer Manager
TIMERS = []
TIMERS_LOCK = threading.Lock()


def _save_timers_to_disk():
    data = []
    now_mono = time.monotonic()
    now_wall = time.time()
    for t in TIMERS:
        remaining = max(0.0, t["end_mono"] - now_mono)
        data.append({
            "id": t["id"],
            "secs": t["secs"],
            "label": t["label"],
            "line": t["line"],
            "deadline_wall": now_wall + remaining
        })
    try:
        with open(TIMERS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"  [timers save error]: {e}")


def load_persisted_timers():
    """Load timers from disk on startup and compute monotonic offsets."""
    if not os.path.exists(TIMERS_FILE):
        return
    try:
        with open(TIMERS_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
        now_wall = time.time()
        now_mono = time.monotonic()
        with TIMERS_LOCK:
            for item in items:
                remaining = item.get("deadline_wall", now_wall) - now_wall
                if remaining > 0.5:
                    TIMERS.append({
                        "id": item.get("id", str(uuid.uuid4())),
                        "secs": item["secs"],
                        "label": item.get("label"),
                        "line": item.get("line"),
                        "end_mono": now_mono + remaining
                    })
            TIMERS.sort(key=lambda x: x["end_mono"])
        print(f"  [timers] Restored {len(TIMERS)} active timers from disk")
    except Exception as e:
        print(f"  [timers load error]: {e}")


def add_timer(secs, label=None):
    now_mono = time.monotonic()
    t = {
        "id": str(uuid.uuid4()),
        "end_mono": now_mono + secs,
        "secs": secs,
        "label": label,
        "line": None
    }
    with TIMERS_LOCK:
        TIMERS.append(t)
        TIMERS.sort(key=lambda x: x["end_mono"])
        _save_timers_to_disk()
    return t


def timer_snapshot():
    """(name, seconds left) for each running timer, soonest first."""
    now = time.monotonic()
    with TIMERS_LOCK:
        return [
            ((t["label"] or "").capitalize() or short_duration(t["secs"]) + " timer", max(0.0, t["end_mono"] - now))
            for t in TIMERS
        ]


def cancel_timer(all_timers=False):
    with TIMERS_LOCK:
        if not TIMERS:
            return False
        if all_timers:
            TIMERS.clear()
        else:
            # cancel the one set most recently
            TIMERS.pop()
        _save_timers_to_disk()
    return True


def start_timer_loop(on_done):
    """Monotonic loop firing on_done(t) when a timer expires."""
    def loop():
        while True:
            time.sleep(0.25)
            now = time.monotonic()
            due = []
            with TIMERS_LOCK:
                remaining = []
                for t in TIMERS:
                    if t["end_mono"] <= now:
                        due.append(t)
                    else:
                        remaining.append(t)
                if due:
                    TIMERS[:] = remaining
                    _save_timers_to_disk()
            for t in due:
                try:
                    # Windows toast announcement
                    toast_title = "Hey Jev: Reminder" if t.get("label") else "Hey Jev: Time's up!"
                    toast_msg = t.get("label") or f"{short_duration(t['secs'])} timer is complete"
                    show_windows_toast(toast_title, toast_msg)
                    on_done(t)
                except Exception as e:
                    print(f"  [timer alert error]: {e}")
    threading.Thread(target=loop, daemon=True).start()
