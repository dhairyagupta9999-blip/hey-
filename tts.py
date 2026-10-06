"""Fish Audio S2.1 Pro TTS synthesis, persistent disk caching, and pre-warming."""
import os
import time
import hashlib
import random
import threading
import requests
from config import TTS_CACHE_DIR
from secrets_store import get_secret
from audio_io import play_audio

VOICE_ID = "9a9cf47702da476aa4629e2506d4a857"

def say_line(key: str, **fmt) -> str:
    """Pick a random reply line for this key and format placeholders."""
    return random.choice(REPLIES[key]).format(**fmt)

# Scripted lines and replies
REPLIES = {
    "app_open": ["[chuckling] There you go.", "Opening it up.", "[cheerful] Here you go."],
    "app_quit": ["{app}'s gone.", "[sighing] Closing {app}. Good riddance.", "Done, {app} is closed."],
    "app_hide": ["{app}'s hidden.", "[chuckling] Out of sight, {app}."],
    "app_minimise": ["Minimised {app}.", "{app}'s tucked away."],
    "app_focus": ["Here's {app}.", "[cheerful] Switching to {app}."],
    "browser_new_tab": ["New tab's open.", "[cheerful] Fresh tab for you."],
    "browser_open_site": ["Opening {site}.", "[cheerful] Here's {site}."],
    "browser_no_site": ["[clear throat] Which website?"],
    "volume_up": ["Louder it is.", "[cheerful] Turning it up.", "Up we go."],
    "volume_down": ["Bringing it down.", "[sighing] A little quieter.", "Turning it down."],
    "volume_mute": ["[sighing] Muting. Finally some quiet.", "Muted.", "Shh. Muted."],
    "volume_unmute": ["Sound's back.", "[cheerful] Unmuted.", "And we're back."],
    "volume_set": ["Set to {level}.", "Volume's {level} now."],
    "spotify_volume_up": ["Turning Spotify up.", "[cheerful] Spotify's louder."],
    "spotify_volume_down": ["Turning Spotify down.", "Spotify's a little quieter."],
    "spotify_volume_mute": ["Spotify's muted.", "[sighing] Muting Spotify."],
    "spotify_volume_unmute": ["Spotify's sound is back.", "[cheerful] Spotify's unmuted."],
    "spotify_volume_set": ["Spotify's set to {level}.", "Set Spotify to {level}."],
    "display_dark_on": ["[chuckling] Lights off.", "Dark mode on.", "Going dark."],
    "display_dark_off": ["[cheerful] Let there be light.", "Dark mode off.", "Back to light."],
    "display_toggle": ["Flipped it.", "There, switched."],
    "media_play": ["[cheerful] Playing.", "Putting the music on.", "Here we go."],
    "media_pause": ["Paused.", "[sighing] Pausing. Take your time.", "Holding it there."],
    "media_next": ["Skipping.", "[chuckling] Not a fan? Next one.", "Next track."],
    "media_previous": ["Going back one.", "Previous track.", "[chuckling] Again? Sure."],
    "system_lock": ["Locking up. See you soon.", "Locked.", "Screen's locked."],
    "system_sleep": ["Good night.", "Sleeping now.", "[sighing] Finally, a nap."],
    "info": ["[chuckling] That's a question, not a command. I'll get a brain for that soon.",
             "[sighing] I can't answer that one yet."],
    "chit_chat": ["[chuckling] Hi. Give me something to do.", "[cheerful] Hey. I'm listening."],
    "compound_done": ["[chuckling] Done, both of them.", "[cheerful] All done.", "Both sorted."],
    "wake": ["Yes?", "[cheerful] Mm-hm?", "I'm listening."],
    "clarify": ["[clear throat] Sorry, say that again?", "Hm, one more time?"],
    "give_up": ["[sighing] I'm not sure what you mean. Try saying it differently?"],
    "timer_set": ["[cheerful] Timer's set.", "On it. I'll let you know.", "Done, counting down."],
    "reminder_set": ["Got it, I'll remind you.", "[cheerful] Sure, I'll give you a shout."],
    "timer_check": ["{left} left.", "You've got {left} to go."],
    "timer_cancel": ["Timer cancelled.", "[sighing] Fine, no timer then."],
    "timers_cancel": ["All timers cancelled.", "Cleared them all."],
    "timer_none": ["[chuckling] There's no timer running."],
    "timer_unclear": ["[clear throat] How long for?"],
    "timer_done": ["[cheerful] Time's up!", "[chuckling] Ding ding, time's up."],
    "reminder_done": ["[cheerful] Hey, just a reminder: {label}.", "Reminder: {label}."],
    "unsupported": ["[chuckling] I know what you want, I just can't do that one yet."],
}

LEVELS = {"silent": 0, "quiet": 25, "medium": 50, "loud": 75, "max": 100}


def fetch_tts(text: str) -> tuple[str, int, bool]:
    """Fetch or return cached wav path for this line. Returns (path, ms, cached)."""
    os.makedirs(TTS_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(TTS_CACHE_DIR, hashlib.sha1(f"{VOICE_ID}|{text}".encode()).hexdigest() + ".wav")
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        return cache_path, 0, True

    fish_key = get_secret("FISH_AUDIO_API_KEY")
    if not fish_key:
        raise RuntimeError("Missing FISH_AUDIO_API_KEY")

    t0 = time.time()
    resp = requests.post(
        "https://api.fish.audio/v1/tts",
        headers={"Authorization": f"Bearer {fish_key}", "model": "s2.1-pro-free"},
        json={"text": text, "reference_id": VOICE_ID, "format": "wav"},
        timeout=60,
    )
    resp.raise_for_status()
    with open(cache_path, "wb") as f:
        f.write(resp.content)
    latency_ms = int((time.time() - t0) * 1000)
    return cache_path, latency_ms, False


def is_muted() -> bool:
    """Check if speech audio output is muted via environment variable or CLI flag."""
    return os.getenv("HEYJEV_MUTE", "0") in ("1", "true", "yes", "True")


def set_muted(muted: bool) -> None:
    """Set global mute state."""
    os.environ["HEYJEV_MUTE"] = "1" if muted else "0"


def speak(text: str) -> int:
    """Synthesize (or fetch from cache) and play audio line. Returns tts latency ms."""
    if is_muted():
        # Centralized mute hook: Nothing can route around speak()
        return 0
    path, ms, cached = fetch_tts(text)
    play_audio(path, blocking=True)
    return ms


def all_scripted_lines(apps_say=None):
    """Generate all known scripted lines with placeholders filled for pre-warming."""
    apps = list(apps_say.values()) if apps_say else ["Spotify", "Slack", "Chrome", "VS Code"]
    for key, lines in REPLIES.items():
        for line in lines:
            if "{app}" in line:
                for app_name in apps:
                    yield line.format(app=app_name)
            elif "{level}" in line:
                for lvl in LEVELS:
                    yield line.format(level=lvl)
            elif "{" not in line:
                yield line


def warm_cache(apps_say=None):
    """Pre-render all scripted dialogue in the background on startup."""
    fish_key = get_secret("FISH_AUDIO_API_KEY")
    if not fish_key:
        return
    count = 0
    for line in all_scripted_lines(apps_say):
        try:
            _, _, cached = fetch_tts(line)
            if not cached:
                count += 1
        except Exception as e:
            pass
    if count:
        print(f"  [tts] Cached {count} new reply lines")
