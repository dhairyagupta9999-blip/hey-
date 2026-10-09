"""Windows voice assistant: hold right Alt or say "Hey Jev", then speak. Jev decides, Fish speaks.

Port of henryklunaris/hey-jev to Windows 10/11 x64.
"""
import os
import re
import sys
import json
import time
import queue
import random
import argparse
import tempfile
import threading
import hashlib
import collections
import numpy as np
import requests

from config import (
    LOG_FILE, USER_APPS_FILE, DEFAULT_APPS_FILE, USER_VOCAB_FILE,
    COMMAND_PROMPT, WAKE_PROMPT, NO_SPEECH_MAX, NAMES, WAKE_WINDOW,
    SAMPLE_RATE, CLIPS_DIR
)
from secrets_store import get_secret
from dictation import Dictation, START as DICTATE_START, paste
from audio_io import Recorder, play_audio, play_chime
from stt import transcribe_audio, warmup_whisper
from tts import speak, fetch_tts, warm_cache, say_line, REPLIES, LEVELS
from timers import (
    parse_duration, parse_reminder, say_duration, short_duration,
    add_timer, timer_snapshot, cancel_timer, start_timer_loop, load_persisted_timers
)
from logger import (
    trace_line, trace_heard, trace_fixed, trace_answers, trace_decision, trace_split_call,
    trace_action, trace_say, trace_fish
)
import actions_win
from wake_word import get_wake_detector, OpenWakeWordDetector

# --------------------------------------------------------------------------- Secrets & Constants
TS_KEY = get_secret("TYPESAFE_API_KEY")
FISH_KEY = get_secret("FISH_AUDIO_API_KEY")
OR_KEY = get_secret("OPENROUTER_API_KEY")
OA_KEY = get_secret("OPENAI_API_KEY")

GATE = 0.65
WAKE = re.compile(rf"(?:(?:^\W*a|\b(?:hey|hi|hay|okay|ok))\W+(?:{NAMES})\b|^\W*(?:{NAMES})\s*,)\W*", re.I)

# App definitions
apps_path = USER_APPS_FILE if os.path.exists(USER_APPS_FILE) else DEFAULT_APPS_FILE
with open(apps_path, "r", encoding="utf-8") as f:
    _apps = json.load(f)

APPS = {k: v if isinstance(v, str) else v["app"] for k, v in _apps.items()}
APP_SAY = {k: v if isinstance(v, str) else v.get("say", v["app"]) for k, v in _apps.items()}
HEARD_AS = [
    (re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(v["heard_as"], key=len, reverse=True))) + r")\b", re.I), APP_SAY[k])
    for k, v in _apps.items() if isinstance(v, dict) and v.get("heard_as")
]
BROWSERS = ("chrome", "brave", "edge", "firefox")
DEFAULT_BROWSER = "chrome"

def reload_all_keys():
    global TS_KEY, FISH_KEY, OR_KEY, OA_KEY
    TS_KEY = get_secret("TYPESAFE_API_KEY")
    FISH_KEY = get_secret("FISH_AUDIO_API_KEY")
    OR_KEY = get_secret("OPENROUTER_API_KEY")
    OA_KEY = get_secret("OPENAI_API_KEY")

# --------------------------------------------------------------------------- Jev Question Battery
QUESTIONS = {
    "category": {"type": "choice", "instructions": "What kind of request is this?",
                 "criteria": {"mac_command": "asks the computer to do something",
                              "information_request": "asks a general knowledge or factual question",
                              "chit_chat": "just talking, greeting, or thanking",
                              "unclear": "garbled, empty, or makes no sense"}},
    "compound": {"type": "noul", "instructions": "Does the request contain more than one distinct action?"},
    "target": {"type": "choice", "instructions": "What is the primary thing being controlled?",
               "criteria": {"app": "an application", "volume": "sound level", "display": "screen appearance or dark mode",
                            "media": "music playback", "system": "locking or sleeping the computer",
                            "timer": "setting, checking, or cancelling a timer or reminder",
                            "browser": "opening a website or a new browser tab"}},
    "app": {"type": "choice", "instructions": "Which app, if any, is named?",
            "criteria": {**{k: None for k in APPS}, "none": None}},
    "app_action": {"type": "choice", "instructions": "What should happen to the app? Every name in the app list is an application, so open or close with one of those names is about the app itself.",
                   "criteria": {"open": "open, launch, or start the app itself", "quit": "quit, close, or kill the app",
                                "hide": "hide the app", "minimise": "minimise the app's windows",
                                "focus": "switch to, show, or bring the app to the front",
                                "none": "the request is about playback, volume, a website, a tab, or something inside the app"}},
    "browser_action": {"type": "choice", "instructions": "What should happen in the web browser, if anything?",
                       "criteria": {"new_tab": "open a new empty tab", "open_site": "go to or open a specific website",
                                    "none": None}},
    "volume_action": {"type": "choice", "instructions": "What should happen to the volume, if anything?",
                      "criteria": {"up": None, "down": None, "mute": None, "unmute": None,
                                   "set": "set to a specific level", "none": None}},
    "volume_scope": {"type": "choice", "instructions": "Which volume should change?",
                     "criteria": {"spotify": "Spotify's own in-app volume when Spotify is explicitly named",
                                  "system": "the computer's overall output volume, including unqualified volume requests"}},
    "volume_level": {"type": "score", "instructions": "If a volume level is asked for, how loud?",
                     "criteria": ["silent", "quiet", "medium", "loud", "max"]},
    "display_action": {"type": "choice", "instructions": "What should happen to dark mode?",
                       "criteria": {"dark_on": None, "dark_off": None, "toggle": None, "none": None}},
    "media_action": {"type": "choice", "instructions": "What should happen to music playback?",
                     "criteria": {"play": None, "pause": None, "next": None, "previous": None, "none": None}},
    "timer_action": {"type": "choice", "instructions": "What should happen with a timer or reminder?",
                     "criteria": {"set": "start a timer or set a reminder", "check": "ask how much time is left",
                                  "cancel": "stop or cancel a timer", "none": None}},
    "system_action": {"type": "choice", "instructions": "What should happen to the computer?",
                      "criteria": {"lock": None, "sleep": None, "none": None}},
}

def split_questions():
    """Duplicate question battery scoped to first and second actions."""
    out = {}
    for slot, word in (("first", "FIRST"), ("second", "SECOND")):
        for k, q in QUESTIONS.items():
            if k in ("category", "compound"):
                continue
            out[f"{slot}_{k}"] = {**q, "instructions": f"Considering ONLY the {word} action the user asks for: {q['instructions']}"}
    return out

SPLIT_QUESTIONS = split_questions()

from backend import DecisionBackend, JevBackend, LayaBackend, get_backend

# --------------------------------------------------------------------------- Decision Layer
def jev(text, questions=None):
    """Call the active decision backend (default 'jev', or 'laya')."""
    backend = get_backend()
    return backend.decide(questions or QUESTIONS, text)

TARGETS = ("app", "volume", "display", "media", "system", "timer", "browser")
SPEAK_FIRST = {"volume_mute", "system_lock", "system_sleep"}

def sub_action(ans, target):
    """Extract (confidence, action_key, arg, reply_key, fmt) for a given target."""
    if target == "app":
        (app, ac), (action, aac) = ans["app"], ans["app_action"]
        if app == "none" or action == "none" or min(ac, aac) < GATE:
            return None
        return (min(ac, aac), f"app_{action}", app, f"app_{action}", {"app": APP_SAY[app]})
    if target == "browser":
        action, conf = ans["browser_action"]
        if action == "none" or conf < GATE:
            return None
        app = ans["app"][0] if ans["app"][0] in BROWSERS and ans["app"][1] >= GATE else None
        return (conf, f"browser_{action}", app, f"browser_{action}", {})

    key = {
        "volume": "volume_action", "display": "display_action", "media": "media_action",
        "system": "system_action", "timer": "timer_action"
    }[target]
    action, conf = ans[key]
    if action == "none" or conf < GATE:
        return None
    lvl = ans["volume_level"][0] if target == "volume" else None
    prefix = target
    if target == "volume":
        scope, scope_conf = ans["volume_scope"]
        named_spotify = ans["app"][0] == "spotify"
        if scope == "spotify" and (scope_conf >= 0.5 or named_spotify):
            prefix = "spotify_volume"
    return (conf, f"{prefix}_{action}", lvl, f"{prefix}_{action}", {"level": lvl})

def pick_action(ans):
    target, tconf = ans["target"]
    a = sub_action(ans, target) if tconf >= 0.5 else None
    if a is None:
        cands = [x for x in (sub_action(ans, t) for t in TARGETS) if x]
        a = max(cands, key=lambda x: x[0]) if cands else None
    return a

def decide(ans):
    cat, cconf = ans["category"]
    if ans["target"][0] == "timer" and ans["target"][1] >= GATE and not ans["compound"][0]:
        t = sub_action(ans, "timer")
        if t:
            return ("actions", [t])
    if cat == "chit_chat" and cconf >= GATE:
        return ("reply", "chit_chat")
    if cat == "information_request" and cconf >= GATE:
        return ("llm", None)
    if cat == "unclear" and cconf >= GATE:
        return ("clarify", None)
    if ans["compound"][0] and ans["compound"][1] >= GATE:
        return ("split", None)
    a = pick_action(ans)
    if a:
        return ("actions", [a])
    return ("llm", None) if cat == "information_request" else ("clarify", None)

def split_actions(text, ans):
    backend = get_backend()
    sans, ms, cost = backend.decide(SPLIT_QUESTIONS, text)
    trace_split_call(backend.name, ms, cost)
    acts = []
    for slot in ("first", "second"):
        half = {k[len(slot) + 1:]: v for k, v in sans.items() if k.startswith(slot + "_")}
        a = pick_action(half)
        if a and (a[1], a[2]) not in [(x[1], x[2]) for x in acts]:
            acts.append(a)
    if len(acts) < 2:
        acts = [a for a in (sub_action(ans, t) for t in TARGETS) if a]
    return acts

# --------------------------------------------------------------------------- LLM Fallback (OpenRouter Haiku)
LLM_MODEL = "anthropic/claude-haiku-4.5"

def ask_llm(text):
    """Answer questions using configured answer provider (OpenRouter or OpenCode Zen)."""
    import answer_provider
    reply, lat_ms, cost, prov, model = answer_provider.ask_answer(text)
    return reply, lat_ms, cost, prov, model

# --------------------------------------------------------------------------- Windows Actions Mapping
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
}

def run_browser_action(action, key, text):
    if action == "browser_new_tab":
        actions_win.browser_new_tab(key)
        return "browser_new_tab", {}
    # Find url
    t = re.sub(r"\s+dot\s+", ".", text, flags=re.I)
    m = re.search(r"\b((?:[\w-]+\.)+(?:com|co\.uk|org|net|io|ai|dev|tv|app|me|uk|gov|edu)(?:/\S*)?)", t, re.I)
    if m:
        url = "https://" + m[1].lower().rstrip(".,!?")
        actions_win.open_url(url, key)
        site_name = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
        return "browser_open_site", {"site": site_name}
    # Check known sites by name without domain extension
    for site, url in KNOWN_SITES.items():
        if re.search(rf"\b{re.escape(site)}\b", t, re.I):
            actions_win.open_url(url, key)
            return "browser_open_site", {"site": site}
    return "browser_no_site", {}

def run_timer_action(action, text):
    if action == "timer_set":
        secs = parse_duration(text)
        if not secs:
            return ("timer_unclear", {})
        label = parse_reminder(text)
        add_timer(secs, label)
        return ("reminder_set" if label else "timer_set", {})
    if action == "timer_check":
        snaps = timer_snapshot()
        if not snaps:
            return ("timer_none", {})
        return ("timer_check", {"left": say_duration(snaps[0][1])})
    if action == "timer_cancel":
        all_cancel = bool(re.search(r"\ball\b", text.lower()))
        success = cancel_timer(all_timers=all_cancel)
        return ("timers_cancel" if all_cancel else "timer_cancel", {}) if success else ("timer_none", {})
    return ("timer_none", {})

ACTIONS = {
    "app_open": lambda a: actions_win.launch_app(APPS[a]),
    "app_quit": lambda a: actions_win.run_async(actions_win.quit_app, APPS[a]),
    "app_hide": lambda a: actions_win.minimize_app(APPS[a]),
    "app_minimise": lambda a: actions_win.minimize_app(APPS[a]),
    "app_focus": lambda a: actions_win.focus_app(APPS[a]),
    "volume_up": lambda _: actions_win.set_volume(min(100, actions_win.get_volume() + 20)),
    "volume_down": lambda _: actions_win.set_volume(max(0, actions_win.get_volume() - 20)),
    "volume_mute": lambda _: actions_win.set_mute(True),
    "volume_unmute": lambda _: actions_win.set_mute(False),
    "volume_set": lambda lvl: actions_win.set_volume(LEVELS.get(lvl, 50)),
    "spotify_volume_up": lambda _: actions_win.set_spotify_volume(min(100, actions_win.get_spotify_volume() + 20)),
    "spotify_volume_down": lambda _: actions_win.set_spotify_volume(max(0, actions_win.get_spotify_volume() - 20)),
    "spotify_volume_mute": lambda _: actions_win.set_spotify_volume(0),
    "spotify_volume_unmute": lambda _: actions_win.set_spotify_volume(50),
    "spotify_volume_set": lambda lvl: actions_win.set_spotify_volume(LEVELS.get(lvl, 50)),
    "display_dark_on": lambda _: actions_win.set_dark_mode(True),
    "display_dark_off": lambda _: actions_win.set_dark_mode(False),
    "display_toggle": lambda _: actions_win.toggle_dark_mode(),
    "media_play": lambda _: actions_win.media_play(),
    "media_pause": lambda _: actions_win.media_pause(),
    "media_next": lambda _: actions_win.media_next(),
    "media_previous": lambda _: actions_win.media_previous(),
    "system_lock": lambda _: actions_win.lock_screen(),
    "system_sleep": lambda _: actions_win.sleep_computer(),
}

# --------------------------------------------------------------------------- One Turn Execution
misses = 0

def fix_names(text):
    for rx, name in HEARD_AS:
        text = rx.sub(name, text)
    return text

def emit(notify, state, detail=""):
    if notify:
        notify(state, detail)

# --------------------------------------------------------------------------- Loop & Echo Guards
# Guard b: Ignore identical transcript within 5 seconds
_LAST_TRANSCRIPT = ""
_LAST_TRANSCRIPT_TIME = 0.0
_TRANSCRIPT_DEDUP_WINDOW_S = 5.0

def check_and_update_transcript_dedup(text: str, current_time: float = None) -> bool:
    """Return True if transcript should be ignored as duplicate within 5s, else False."""
    global _LAST_TRANSCRIPT, _LAST_TRANSCRIPT_TIME
    if current_time is None:
        current_time = time.time()
    t_norm = text.lower().strip()
    if not t_norm:
        return False
    if t_norm == _LAST_TRANSCRIPT and (current_time - _LAST_TRANSCRIPT_TIME) < _TRANSCRIPT_DEDUP_WINDOW_S:
        return True
    _LAST_TRANSCRIPT = t_norm
    _LAST_TRANSCRIPT_TIME = current_time
    return False

def reset_transcript_dedup():
    global _LAST_TRANSCRIPT, _LAST_TRANSCRIPT_TIME
    _LAST_TRANSCRIPT = ""
    _LAST_TRANSCRIPT_TIME = 0.0


# Guard c: Loop breaker (same action 3 times within 10 seconds)
_ACTION_FIRING_HISTORY = collections.deque(maxlen=30)  # (action_key, timestamp)
_LOOP_BREAKER_WINDOW_S = 10.0
_LOOP_BREAKER_THRESHOLD = 3
_DISABLE_WAKE_FN = None

def set_disable_wake_fn(fn):
    global _DISABLE_WAKE_FN
    _DISABLE_WAKE_FN = fn

def disable_wake_mode():
    global _DISABLE_WAKE_FN
    if callable(_DISABLE_WAKE_FN):
        try:
            _DISABLE_WAKE_FN()
        except Exception as e:
            print(f"  [loop breaker] error disabling wake mode: {e}")

def record_and_check_action_loop(action_key: str, current_time: float = None) -> bool:
    """Check if the same action has fired 3 times within 10 seconds.
    
    Returns True if a loop is detected (i.e. this would be the 3rd firing in 10s),
    indicating execution must halt.
    """
    global _ACTION_FIRING_HISTORY
    if current_time is None:
        current_time = time.time()
    cutoff = current_time - _LOOP_BREAKER_WINDOW_S
    while _ACTION_FIRING_HISTORY and _ACTION_FIRING_HISTORY[0][1] < cutoff:
        _ACTION_FIRING_HISTORY.popleft()
    count = sum(1 for k, t in _ACTION_FIRING_HISTORY if k == action_key)
    _ACTION_FIRING_HISTORY.append((action_key, current_time))
    return (count + 1) >= _LOOP_BREAKER_THRESHOLD

def reset_action_loop_history():
    global _ACTION_FIRING_HISTORY
    _ACTION_FIRING_HISTORY.clear()

def trigger_loop_breaker(notify=None):
    msg = "I think I'm looping, stopping now"
    print(f"\n  [loop guard c] {msg}. Turning off wake mode.")
    trace_line(f"  [loop guard c] loop breaker triggered: turning wake mode off")
    disable_wake_mode()
    say(msg, notify)
    emit(notify, "Ready", msg)


# Guard d: Ignore transcripts matching assistant's own recent spoken replies
_RECENT_ASSISTANT_REPLIES = collections.deque(maxlen=20)  # (normalized_text, timestamp)
_RECENT_REPLIES_WINDOW_S = 15.0

def record_spoken_reply(line: str, timestamp: float = None):
    if timestamp is None:
        timestamp = time.time()
    clean = re.sub(r"\[.*?\]", "", line).lower().strip(" .,!?")
    if clean:
        _RECENT_ASSISTANT_REPLIES.append((clean, timestamp))

def is_matching_recent_reply(transcript: str, current_time: float = None) -> bool:
    """Check if transcript matches any reply spoken by the assistant in recent window."""
    if current_time is None:
        current_time = time.time()
    t_clean = re.sub(r"\[.*?\]", "", transcript).lower().strip(" .,!?")
    if not t_clean:
        return False
    cutoff = current_time - _RECENT_REPLIES_WINDOW_S
    for reply_clean, t_reply in list(_RECENT_ASSISTANT_REPLIES):
        if t_reply < cutoff:
            continue
        # Exact match
        if t_clean == reply_clean:
            return True
        # Substring match (for phrases of reasonable length, avoiding single words like yes/no)
        if len(t_clean) >= 6 and len(reply_clean) >= 6 and t_clean not in ("yes", "no"):
            if t_clean in reply_clean or reply_clean in t_clean:
                return True
        # Fuzzy match
        try:
            from rapidfuzz import fuzz
            if fuzz.ratio(t_clean, reply_clean) >= 80:
                return True
        except ImportError:
            pass
    return False

def reset_spoken_replies():
    global _RECENT_ASSISTANT_REPLIES
    _RECENT_ASSISTANT_REPLIES.clear()


def say(line, notify=None):
    trace_say(line)
    emit(notify, "Speaking", line)
    record_spoken_reply(line)
    try:
        tts_ms = speak(line)
        trace_fish(tts_ms)
    except Exception:
        pass

def is_tier2_direct_match(text: str) -> bool:
    """Check if transcript matches open/close/switch/find/window/system targets
    with a target that Jev's fixed battery cannot resolve.
    """
    import window_manager
    import system_targets
    import target_extractor

    t = text.strip()

    # 1. Target extraction: if it's an app in APPS or HEARD_AS, Jev handles it (Tier 1)
    action, target = target_extractor.extract_target(t)
    if action in ("open", "close", "focus") and target:
        tgt_lower = target.lower()
        if tgt_lower in APPS:
            return False
        for rx, _ in HEARD_AS:
            if rx.search(tgt_lower):
                return False

    # 2. Window management commands (snap left/right, minimize, maximize, restore)
    if window_manager.WINDOW_ACTION_REGEX.match(t) or window_manager.SNAP_REGEX.match(t):
        return True

    # 3. System targets (settings URIs, folders, administrative tools, direct URLs)
    sys_res = system_targets.resolve_system_target(t, execute=False)
    if sys_res:
        # Exclude if it resolved to a known site that is an app in APPS (e.g. spotify)
        if sys_res.get("type") == "website" and sys_res.get("label", "").lower() in APPS:
            return False
        return True

    # 4. Special commands: close_all, refresh_apps
    if action in ("close_all", "refresh_apps"):
        return True

    # 5. Open/close/focus with a target not in APPS
    if action and target:
        return True

    return False

def handle_tier2_result(t2_res, notify=None):
    global misses
    misses = 0
    status = t2_res.get("status")

    if status == "needs_confirmation":
        from safety_engine import get_confirmation_manager
        cm = get_confirmation_manager()
        action = t2_res.get("action", "unknown")
        message = t2_res.get("message", "This action requires confirmation.")
        exec_fn = t2_res.get("execute_fn", lambda: None)
        cm.request_high_risk_confirmation(action, message, exec_fn, timeout_s=8.0)
        say(message, notify)
        emit(notify, "Awaiting confirmation", message)
        return

    line = t2_res.get("line") or t2_res.get("message", "")
    if line:
        say(line, notify)
        emit(notify, "Ready", line)

def handle(text, stt_ms=None, notify=None, quiet=False):
    global misses
    trace_heard(text, stt_ms)
    fixed = fix_names(text)
    if fixed != text:
        trace_fixed(fixed)
        text = fixed

    if not text.strip():
        emit(notify, "Ready", "Didn't catch anything")
        return

    # Guard b: Ignore duplicate transcript within 5 seconds
    if check_and_update_transcript_dedup(text):
        print(f"  [loop guard b] Ignoring duplicate transcript within 5.0s: {text!r}")
        trace_line(f"  [loop guard b] dropped identical transcript: {text!r}")
        return

    # Guard d: Ignore transcript matching recent assistant spoken replies
    if is_matching_recent_reply(text):
        print(f"  [loop guard d] Ignoring transcript matching recent assistant reply: {text!r}")
        trace_line(f"  [loop guard d] dropped reply echo: {text!r}")
        return

    t_clean = text.lower().strip()

    # 0. Diagnostics Hook
    if t_clean in ("diag", "diagnostics", "system status", "health check"):
        import diagnostics
        diag_text = diagnostics.format_diagnostics_text()
        print(f"\n{diag_text}\n")
        d = diagnostics.get_diagnostics_dict()
        line = f"Diagnostics: listening {d['listening']}, spend {d['today_spent_usd']} dollars."
        say(line, notify)
        emit(notify, "Ready", line)
        return

    # 1. Safety Hook: Stop / cancel / wait fast-path interrupt commands
    from endpointing import is_interrupt_phrase
    if is_interrupt_phrase(text):
        from safety_engine import get_confirmation_manager, request_global_cancel
        cm = get_confirmation_manager()
        cancelled, msg = cm.cancel_pending(f"user said {t_clean}")
        request_global_cancel(f"user said {t_clean}")
        if t_clean in ("wait", "wait a second", "hold on"):
            line = msg if cancelled else "Holding on."
        else:
            line = msg if cancelled else "Stopped."
        say(line, notify)
        emit(notify, "Ready", line)
        return

    # 2. Safety Hook: Undo command
    if t_clean in ("undo", "undo that", "undo previous action", "revert"):
        from safety_engine import perform_undo
        ok, msg = perform_undo()
        say(msg, notify)
        emit(notify, "Ready", msg)
        return

    # 3. Safety Hook: User response to in-flight confirmation
    from safety_engine import get_confirmation_manager
    cm = get_confirmation_manager()
    if cm.pending_confirmation:
        handled, msg = cm.respond(text)
        if handled:
            say(msg, notify)
            emit(notify, "Ready", msg)
            return

    # 4. Direct Tier 2 Routing for open-vocabulary targets outside Jev's battery
    if is_tier2_direct_match(text):
        from tier2_resolver import resolve_tier2
        t2_res = resolve_tier2(text)
        if t2_res is not None:
            action_key = f"tier2:{t2_res.get('action')}:{t2_res.get('target')}"
            if record_and_check_action_loop(action_key):
                trigger_loop_breaker(notify)
                return
            handle_tier2_result(t2_res, notify)
            return

    emit(notify, "Thinking", text)
    backend = get_backend()
    active_gate = getattr(backend, "default_gate", GATE)
    try:
        ans, dec_ms, cost = backend.decide(QUESTIONS, text)
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 401:
            err_msg = "Invalid TypeSafe API key (401). Check your keys in .env or Settings."
            print(f"\n  [auth error]: {err_msg}")
            emit(notify, "Something went wrong", err_msg)
            return
        raise
    trace_answers(ans, active_gate)
    trace_decision(backend.name, dec_ms, cost)

    kind, payload = decide(ans)
    if kind == "split":
        payload = split_actions(text, ans)
        kind = "actions" if payload else "clarify"

    # Tier 2 fallback resolution if Tier 1 gates didn't pass or returned app: none
    t2_applicable = (kind == "clarify") or (
        kind == "actions" and any(p[1].startswith("app_") and p[2] == "none" for p in payload)
    )
    if t2_applicable:
        from tier2_resolver import resolve_tier2
        t2_res = resolve_tier2(text)
        if t2_res is not None:
            action_key = f"tier2:{t2_res.get('action')}:{t2_res.get('target')}"
            if record_and_check_action_loop(action_key):
                trigger_loop_breaker(notify)
                return
            handle_tier2_result(t2_res, notify)
            return

    # Tier 3 (AI Agent) hook if enabled
    try:
        from assistant_ui import get_settings
        tier3_enabled = get_settings().get("tier3_enabled", False)
    except Exception:
        tier3_enabled = False

    if tier3_enabled and (kind == "clarify" or kind == "unclear"):
        action_key = f"tier3:{text.strip()}"
        if record_and_check_action_loop(action_key):
            trigger_loop_breaker(notify)
            return
        try:
            import tier3_agent
            t3_res = tier3_agent.run_tier3_agent(text)
            if t3_res is not None:
                misses = 0
                line = t3_res.get("line") or t3_res.get("message", "")
                if line:
                    say(line, notify)
                    emit(notify, "Ready", line)
                return
        except Exception as exc:
            print(f"  [tier 3 error]: {exc}")

    if kind == "clarify" and quiet:
        emit(notify, "Ready", "Didn't catch that")
        return

    if kind == "clarify":
        misses += 1
        line = say_line("give_up") if misses >= 2 else say_line("clarify")
        if misses >= 2:
            misses = 0
    else:
        misses = 0
        if kind == "reply":
            if payload == "chit_chat":
                import answer_provider
                prov = answer_provider.get_configured_provider()
                zen_key = answer_provider.get_opencode_zen_key()
                or_key = answer_provider.get_openrouter_key()
                if (prov == "opencode_zen" and zen_key) or (prov == "openrouter" and or_key):
                    res = ask_llm(text)
                    if len(res) == 5:
                        line, llm_ms, llm_cost, ans_prov, ans_model = res
                    else:
                        line, llm_ms, llm_cost = res
                        ans_prov, ans_model = "openrouter", LLM_MODEL
                    trace_line(f"  answer {ans_prov} {ans_model} {llm_ms}ms  ${llm_cost:.5f}")
                else:
                    line = say_line(payload)
            else:
                line = say_line(payload)
        elif kind == "llm":
            res = ask_llm(text)
            if len(res) == 5:
                line, llm_ms, llm_cost, ans_prov, ans_model = res
            else:
                line, llm_ms, llm_cost = res
                ans_prov, ans_model = "openrouter", LLM_MODEL
            trace_line(f"  answer {ans_prov} {ans_model} {llm_ms}ms  ${llm_cost:.5f}")
        else:
            default_line = lambda: say_line(payload[0][3], **payload[0][4]) if len(payload) == 1 else say_line("compound_done")
            speak_first = any(a[1] in SPEAK_FIRST or (a[1].endswith("volume_set") and a[2] == "silent") for a in payload)
            if speak_first:
                line = default_line()
                say(line, notify)

            done, timer_reply = 0, None
            for _, action, arg, _, _ in payload:
                action_key = f"tier1:{action}:{arg}"
                if record_and_check_action_loop(action_key):
                    trigger_loop_breaker(notify)
                    return
                try:
                    emit(notify, "Doing it", text)
                    if action.startswith("timer_"):
                        timer_reply = run_timer_action(action, text)
                    elif action.startswith("browser_"):
                        timer_reply = run_browser_action(action, arg, text)
                    else:
                        ACTIONS[action](arg)
                    trace_action(action, arg)
                    done += 1
                except Exception as e:
                    print(f"  action failed: {action} {e}")

            if speak_first:
                emit(notify, "Ready", line)
                return
            if not done:
                line = say_line("unsupported")
            elif timer_reply and len(payload) == 1:
                line = say_line(timer_reply[0], **timer_reply[1])
            else:
                line = default_line()

    say(line, notify)
    emit(notify, "Ready", line)

# --------------------------------------------------------------------------- Voice Assistant Runner
def ready_text(wake):
    return "Say \u201cHey Jev\u201d and your command" if wake else "Hold right Alt to talk"

def run_voice_assistant(notify=None, controls=None, mode="ptt", mic="", wake_backend="whisper", wake_model=None):
    print("loading whisper...")
    emit(notify, "Starting", "Loading Whisper\u2026")
    warmup_whisper()
    rec = Recorder(mic)
    busy = threading.Lock()
    armed_until = [0.0]
    dictation = Dictation(NAMES, lambda: (OA_KEY, OR_KEY), SAMPLE_RATE)
    late_timers = []
    wake_detector = get_wake_detector(wake_backend, wake_model)

    def transcribe(audio, prompt, drop_noise=False):
        return transcribe_audio(audio, prompt, drop_noise)

    def run_turn(text, stt_ms, quiet=False):
        with busy:
            rec.paused = True
            try:
                if DICTATE_START.match(text.strip()):
                    start_dictation()
                else:
                    handle(text, stt_ms, notify, quiet)
            except Exception as exc:
                print(f"\n  turn failed: {exc}")
                emit(notify, "Something went wrong", str(exc))
                time.sleep(2)
                emit(notify, "Ready", ready_text(rec.wake))
            finally:
                time.sleep(0.7)
                rec.paused = False

    def start_dictation():
        print(f"\n> dictation started")
        if not (OA_KEY or OR_KEY):
            say("Add an OpenRouter or OpenAI key first.", notify)
            emit(notify, "Ready", "Dictation needs an OpenRouter or OpenAI key")
            return
        emit(notify, "Dictating", "Say \u201cstop transcribing\u201d when you\u2019re done")
        play_chime("pop")
        dictation.start()
        rec.dictating = True
        rec.sync_mic()

    def dictate_turn(audio):
        heard, _ = transcribe(audio, None, drop_noise=True)
        print(f"  (dictating: {heard!r})")
        if dictation.add(audio, heard):
            finish_dictation()

    def finish_dictation():
        rec.dictating = False
        rec.sync_mic()
        emit(notify, "Finishing", "Writing it up\u2026")
        play_chime("pop")
        try:
            text, failed, total = dictation.finish()
            print(f"  dictation: {text!r}")
            if failed:
                emit(notify, "Something went wrong", f"{failed} of {total} parts failed")
            elif text:
                paste(text)
                emit(notify, "Ready", f"Pasted {len(text.split())} words")
            else:
                emit(notify, "Ready", "Didn't catch anything to paste")
        except Exception as exc:
            print(f"  dictation failed: {exc}")
            emit(notify, "Something went wrong", str(exc))

    def ptt_turn(audio):
        emit(notify, "Transcribing", "Working out what you said\u2026")
        try:
            audio_secs = len(audio) / float(SAMPLE_RATE) if len(audio) else 0.0
            text, ms = transcribe(audio, COMMAND_PROMPT)
            print(f"  [stt] {ms}ms ({audio_secs:.2f}s audio)")
        except Exception as exc:
            emit(notify, "Something went wrong", str(exc))
            return
        run_turn(text, ms)

    def wake_loop():
        while True:
            try:
                audio = rec.segments.get(timeout=1)
            except queue.Empty:
                if dictation.timed_out():
                    finish_dictation()
                if armed_until[0] and time.time() > armed_until[0]:
                    armed_until[0] = 0
                    emit(notify, "Ready", ready_text(rec.wake))
                continue
            from audio_io import is_audio_playing_or_settling
            if is_audio_playing_or_settling():
                continue
            try:
                if rec.dictating:
                    dictate_turn(audio)
                elif rec.wake and not busy.locked():
                    wake_turn(audio)
            except Exception as exc:
                print(f"\n  wake turn failed: {exc}")

    def wake_turn(audio):
        if isinstance(wake_detector, OpenWakeWordDetector) and wake_detector.is_available:
            detected, name, score = wake_detector.feed_audio(audio)
            if detected:
                print(f"\n  (openwakeword detected: {name} score={score:.2f})")
                audio_secs = len(audio) / float(SAMPLE_RATE) if len(audio) else 0.0
                if audio_secs > 1.2:
                    text, ms = transcribe(audio, COMMAND_PROMPT, drop_noise=True)
                    print(f"  [stt] {ms}ms ({audio_secs:.2f}s audio)")
                    m = WAKE.search(text)
                    rest = text[m.end():].strip(" .,!?") if m else text.strip(" .,!?")
                    if rest:
                        armed_until[0] = 0
                        run_turn(rest, ms, quiet=False)
                        return
                with busy:
                    rec.paused = True
                    try:
                        play_chime("wake")
                        time.sleep(0.2)
                    finally:
                        rec.paused = False
                armed_until[0] = time.time() + WAKE_WINDOW
                emit(notify, "Listening", "Go ahead\u2026")
                return
            elif armed_until[0] and time.time() < armed_until[0]:
                armed_until[0] = 0
                audio_secs = len(audio) / float(SAMPLE_RATE) if len(audio) else 0.0
                text, ms = transcribe(audio, COMMAND_PROMPT, drop_noise=True)
                print(f"  [stt] {ms}ms ({audio_secs:.2f}s audio)")
                if text.strip():
                    run_turn(text, ms, quiet=True)
                return
            return

        audio_secs = len(audio) / float(SAMPLE_RATE) if len(audio) else 0.0
        text, ms = transcribe(audio, WAKE_PROMPT, drop_noise=True)
        print(f"  [stt] {ms}ms ({audio_secs:.2f}s audio)")
        m = wake_detector.detect_utterance(text)
        if m:
            print(f"\n  (wake: {text!r})")
            rest = text[m.end():].strip(" .,!?")
            if rest:
                armed_until[0] = 0
                run_turn(rest, ms, quiet=bool(text[:m.start()].strip(" .,!?")))
            else:
                with busy:
                    rec.paused = True
                    try:
                        play_chime("wake")
                        time.sleep(0.2)
                    finally:
                        rec.paused = False
                armed_until[0] = time.time() + WAKE_WINDOW
                emit(notify, "Listening", "Go ahead\u2026")
        elif armed_until[0] and time.time() < armed_until[0]:
            armed_until[0] = 0
            run_turn(text, ms, quiet=True)

    def set_mode(new):
        rec.wake = (new == "wake")
        rec.sync_mic()
        armed_until[0] = 0
        print(f"\n[mode: {'always listening' if rec.wake else 'hold right Alt'}]")
        if rec.dictating:
            emit(notify, "Dictating", "Say \u201cstop transcribing\u201d when you\u2019re done")
        elif not busy.locked():
            emit(notify, "Ready", ready_text(rec.wake))

    def start_recording():
        if not rec.wake and not rec.on and not rec.dictating and not busy.locked():
            rec.start()
            print("\n[listening]", end="", flush=True)
            emit(notify, "Listening", "Release right Alt when you\u2019re done")

    def stop_recording():
        if rec.on:
            audio = rec.stop()
            if len(audio) > SAMPLE_RATE * 0.3:
                threading.Thread(target=ptt_turn, args=(audio,), daemon=True).start()

    def timer_done(t):
        with busy:
            rec.paused = True
            try:
                emit(notify, "Time's up", t.get("label") or "Timer finished")
                play_chime("timer")
                line = say_line("reminder_done", label=t["label"]) if t.get("label") else say_line("timer_done")
                say(line, notify)
            finally:
                time.sleep(0.7)
                rec.paused = False
        emit(notify, "Ready", ready_text(rec.wake))

    # Initialize subsystems
    load_persisted_timers()
    start_timer_loop(timer_done)
    threading.Thread(target=warm_cache, args=(APP_SAY,), daemon=True).start()
    threading.Thread(target=wake_loop, daemon=True).start()
    set_disable_wake_fn(lambda: set_mode("ptt"))
    set_mode(mode)

    # Initialize runtime background automation fleet
    try:
        from runtime_manager import start_runtime
        start_runtime()
    except Exception as exc:
        print(f"  [runtime_manager warning]: {exc}")

    print("ready. ctrl+c to quit.")

    # Hotkey hook for push to talk
    from hotkey import PTTListener
    ptt = PTTListener(key="right alt", on_press=start_recording, on_release=stop_recording)
    ptt.start()

    if controls is not None:
        while True:
            command = controls.get()
            if isinstance(command, tuple) and command[0] == "mode":
                set_mode(command[1])
            elif isinstance(command, tuple) and command[0] == "mic":
                rec.set_device(command[1])
            elif command == "press":
                start_recording()
            elif command == "release":
                stop_recording()

    # Block until KeyboardInterrupt
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        ptt.stop()
        try:
            from runtime_manager import stop_runtime
            stop_runtime()
        except Exception:
            pass
        print("\nExiting Hey Jev.")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", help="skip the mic, run one turn on this transcript")
    ap.add_argument("--wake", action="store_true", help="always listening, say \"Hey Jev\" instead of holding Alt")
    ap.add_argument("--ui", action="store_true", help="launch graphical interface")
    ap.add_argument("--wake-backend", choices=["whisper", "openwakeword"], default="whisper", help="wake engine (whisper or openwakeword)")
    ap.add_argument("--wake-model", help="path to custom openWakeWord model (.onnx / .tflite)")
    ap.add_argument("--mute", action="store_true", help="mute speech audio output (sets HEYJEV_MUTE=1)")
    args = ap.parse_args()

    if args.mute:
        os.environ["HEYJEV_MUTE"] = "1"

    if args.ui:
        from assistant_ui import run_app
        run_app()
        return

    if not TS_KEY or not FISH_KEY:
        print("Note: TYPESAFE_API_KEY and FISH_AUDIO_API_KEY should be set in .env or Windows Credential Manager.")

    if args.text:
        handle(args.text)
        return

    run_voice_assistant(
        mode="wake" if args.wake else "ptt",
        wake_backend=args.wake_backend,
        wake_model=args.wake_model
    )

if __name__ == "__main__":
    main()
