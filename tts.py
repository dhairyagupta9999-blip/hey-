"""Voice Output (TTS) abstraction for Hey Jev on Windows.

Supports:
1. Primary: Fish Audio S2.1 Pro TTS (cloud, high-fidelity conversational voice).
2. Fallback: Kokoro-ONNX (100% local, offline, Apache-2.0 model, CPU-optimized FP32/INT8).
3. Provider-Agnostic Disk Cache:
   - Reusable across providers keyed by semantic parameters (text, voice, language, speed).
   - Backward-compatible with existing pre-warmed Fish Audio cache files.
4. Echo Guard & Playback Isolation:
   - Routes 100% of audio through audio_io.play_audio() with playback state tracking
     and settle-tail protection.
"""
from __future__ import annotations

import os
import sys
import time
import hashlib
import random
import threading
from typing import Optional, Tuple, Dict, Any, List
import requests

from config import TTS_CACHE_DIR, APPDATA_DIR, REPO_ROOT
from secrets_store import get_secret
from audio_io import play_audio

# Default Fish Audio configuration
FISH_VOICE_ID = "9a9cf47702da476aa4629e2506d4a857"
DEFAULT_TTS_PROVIDER = os.environ.get("HEYJEV_TTS_PROVIDER", "fish")

# Default Kokoro voices
DEFAULT_KOKORO_VOICE_EN = "am_adam"
DEFAULT_KOKORO_VOICE_HI = "hm_omega"
DEFAULT_KOKORO_SPEED = 1.0

# Scripted lines and replies
REPLIES: Dict[str, List[str]] = {
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


def say_line(key: str, **fmt) -> str:
    """Pick a random reply line for this key and format placeholders."""
    return random.choice(REPLIES[key]).format(**fmt)


def normalize_lang_code(lang: Optional[str]) -> str:
    """Normalize language identifier to standard ISO codes recognized by espeak."""
    if not lang:
        return "en-us"
    l = lang.lower().strip()
    if l in ("h", "hi", "hin", "hindi"):
        return "hi"
    if l in ("en", "eng", "en-us", "english"):
        return "en-us"
    if l in ("en-gb", "gb", "british"):
        return "en-gb"
    return l


def is_hindi_text(text: str) -> bool:
    """Check if string contains Devanagari script."""
    return any("\u0900" <= ch <= "\u097F" for ch in text)


# ---------------------------------------------------------------------------
# Provider-Agnostic Semantic Caching
# ---------------------------------------------------------------------------
def compute_cache_path(
    text: str,
    voice: str = "jev",
    lang: str = "en-us",
    speed: float = 1.0
) -> str:
    """Compute deterministic, provider-agnostic cache file path.
    
    Preserves backward compatibility: if voice is default 'jev', lang is English,
    and speed is 1.0, checks the legacy Fish Audio hash file first so pre-warmed
    assets are reused seamlessly.
    """
    os.makedirs(TTS_CACHE_DIR, exist_ok=True)
    norm_text = text.strip()
    norm_voice = (voice or "jev").lower().strip()
    norm_lang = normalize_lang_code(lang)
    norm_speed = round(float(speed), 2)

    # 1. Backward-compatible check for legacy Fish Audio cache files
    if norm_voice in ("jev", "default") and norm_lang == "en-us" and norm_speed == 1.0:
        legacy_hash = hashlib.sha1(f"{FISH_VOICE_ID}|{norm_text}".encode("utf-8")).hexdigest()
        legacy_path = os.path.join(TTS_CACHE_DIR, f"{legacy_hash}.wav")
        if os.path.exists(legacy_path) and os.path.getsize(legacy_path) > 100:
            return legacy_path

    # 2. Canonical semantic hash key
    semantic_key = f"{norm_voice}|{norm_lang}|{norm_speed:.2f}|{norm_text}"
    canon_hash = hashlib.sha1(semantic_key.encode("utf-8")).hexdigest()
    return os.path.join(TTS_CACHE_DIR, f"{canon_hash}.wav")


# ---------------------------------------------------------------------------
# Kokoro-ONNX Local Synthesizer Singleton
# ---------------------------------------------------------------------------
_KOKORO_LOCK = threading.Lock()
_KOKORO_INSTANCE = None


def resolve_kokoro_files() -> Tuple[Optional[str], Optional[str]]:
    """Resolve local Kokoro model (.onnx) and voices (.bin) paths."""
    candidates_model = [
        os.path.join(APPDATA_DIR, "models", "kokoro-v1.0.onnx"),
        os.path.join(APPDATA_DIR, "models", "kokoro-v1.0.int8.onnx"),
        os.path.join(REPO_ROOT, "models", "kokoro-v1.0.onnx"),
        os.path.join(REPO_ROOT, "models", "kokoro-v1.0.int8.onnx"),
    ]
    candidates_voices = [
        os.path.join(APPDATA_DIR, "models", "voices-v1.0.bin"),
        os.path.join(REPO_ROOT, "models", "voices-v1.0.bin"),
    ]

    model_path = next((p for p in candidates_model if os.path.exists(p) and os.path.getsize(p) > 1000), None)
    voices_path = next((p for p in candidates_voices if os.path.exists(p) and os.path.getsize(p) > 1000), None)
    return model_path, voices_path


def get_kokoro_engine():
    """Lazy-load the Kokoro-ONNX synthesizer singleton with CPU-optimized session."""
    global _KOKORO_INSTANCE
    with _KOKORO_LOCK:
        if _KOKORO_INSTANCE is not None:
            return _KOKORO_INSTANCE

        model_path, voices_path = resolve_kokoro_files()
        if not model_path or not voices_path:
            raise FileNotFoundError(
                f"Kokoro model or voices file missing. Checked %APPDATA%/HeyJev/models and repo root."
            )

        try:
            import onnxruntime as ort
            from kokoro_onnx import Kokoro

            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 2
            opts.inter_op_num_threads = 1
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            sess = ort.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])
            _KOKORO_INSTANCE = Kokoro.from_session(sess, voices_path)
            print(f"  [tts] Loaded Kokoro-ONNX local model from {os.path.basename(model_path)}")
            return _KOKORO_INSTANCE
        except Exception as e:
            print(f"  [tts] Failed to initialize Kokoro-ONNX: {e}")
            raise


def synthesize_kokoro(
    text: str,
    voice: str = "jev",
    lang: str = "en-us",
    speed: float = 1.0,
    output_path: Optional[str] = None
) -> Tuple[str, int]:
    """Synthesize speech locally using Kokoro-ONNX (100% offline). Returns (path, latency_ms)."""
    import soundfile as sf

    engine = get_kokoro_engine()
    norm_lang = normalize_lang_code(lang)

    # Resolve persona voice
    if voice in ("jev", "default", None):
        target_voice = DEFAULT_KOKORO_VOICE_HI if norm_lang == "hi" else DEFAULT_KOKORO_VOICE_EN
    else:
        target_voice = voice

    # Auto-detect Hindi script if language was left as default English
    if norm_lang == "en-us" and is_hindi_text(text):
        norm_lang = "hi"
        if voice in ("jev", "default", None):
            target_voice = DEFAULT_KOKORO_VOICE_HI

    t0 = time.time()
    samples, sr = engine.create(
        text=text,
        voice=target_voice,
        speed=speed,
        lang=norm_lang,
        trim=True
    )
    latency_ms = int((time.time() - t0) * 1000)

    target_path = output_path or compute_cache_path(text, voice=target_voice, lang=norm_lang, speed=speed)
    sf.write(target_path, samples, sr, format="WAV")
    return target_path, latency_ms


# ---------------------------------------------------------------------------
# Fish Audio Cloud Synthesizer
# ---------------------------------------------------------------------------
def synthesize_fish_audio(text: str, output_path: str) -> int:
    """Synthesize speech using Fish Audio cloud API. Returns latency_ms or raises."""
    fish_key = get_secret("FISH_AUDIO_API_KEY")
    if not fish_key:
        raise RuntimeError("Missing FISH_AUDIO_API_KEY")

    t0 = time.time()
    resp = requests.post(
        "https://api.fish.audio/v1/tts",
        headers={"Authorization": f"Bearer {fish_key}", "model": "s2.1-pro-free"},
        json={"text": text, "reference_id": FISH_VOICE_ID, "format": "wav"},
        timeout=15,
    )
    resp.raise_for_status()
    with open(output_path, "wb") as f:
        f.write(resp.content)
    return int((time.time() - t0) * 1000)


# ---------------------------------------------------------------------------
# Provider Abstraction & Fallback Dispatcher
# ---------------------------------------------------------------------------
def fetch_tts(
    text: str,
    lang: str = "en-us",
    voice: str = "jev",
    speed: float = 1.0,
    allow_fallback: bool = True
) -> Tuple[str, int, bool]:
    """Fetch or synthesize audio line. Returns (wav_path, latency_ms, is_cached)."""
    norm_lang = normalize_lang_code(lang)
    if is_hindi_text(text):
        norm_lang = "hi"

    cache_path = compute_cache_path(text, voice=voice, lang=norm_lang, speed=speed)

    # 1. Cache hit check
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 100:
        return cache_path, 0, True

    # Corrupted cache file cleanup
    if os.path.exists(cache_path):
        try:
            os.remove(cache_path)
        except OSError:
            pass

    # 2. Routing decision:
    # Hindi text routes directly to Kokoro (Fish free English voice cannot handle Devanagari)
    if norm_lang == "hi":
        target_path, ms = synthesize_kokoro(text, voice=voice, lang="hi", speed=speed, output_path=cache_path)
        return target_path, ms, False

    # Check preferred provider
    provider = os.environ.get("HEYJEV_TTS_PROVIDER", DEFAULT_TTS_PROVIDER).lower()

    if provider == "kokoro":
        target_path, ms = synthesize_kokoro(text, voice=voice, lang=norm_lang, speed=speed, output_path=cache_path)
        return target_path, ms, False

    # Primary: Fish Audio
    try:
        latency_ms = synthesize_fish_audio(text, cache_path)
        return cache_path, latency_ms, False
    except Exception as exc:
        if not allow_fallback:
            raise

        reason = str(exc)
        print(f"  [tts] Fish Audio unavailable ({reason}); falling back to local Kokoro.")

        # Fallback: Kokoro-ONNX
        try:
            target_path, ms = synthesize_kokoro(text, voice=voice, lang=norm_lang, speed=speed, output_path=cache_path)
            return target_path, ms, False
        except Exception as k_exc:
            print(f"  [tts] Local Kokoro fallback also failed: {k_exc}")
            raise RuntimeError(f"All TTS providers failed: Fish Audio ({reason}), Kokoro ({k_exc})")


def is_muted() -> bool:
    """Check if speech audio output is muted via environment variable or CLI flag."""
    return os.environ.get("HEYJEV_MUTE", "0") in ("1", "true", "yes", "True")


def set_muted(muted: bool) -> None:
    """Set global mute state."""
    os.environ["HEYJEV_MUTE"] = "1" if muted else "0"


def speak(
    text: str,
    lang: str = "en-us",
    voice: str = "jev",
    speed: float = 1.0
) -> int:
    """Synthesize (or fetch from cache) and play audio line cleanly. Returns latency ms."""
    if is_muted():
        return 0
    path, ms, cached = fetch_tts(text, lang=lang, voice=voice, speed=speed)
    play_audio(path, blocking=True)
    return ms


# ---------------------------------------------------------------------------
# Dialogue Pre-Warming
# ---------------------------------------------------------------------------
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
    if os.environ.get("HEYJEV_DISABLE_WARM_CACHE", "0") == "1":
        return
    fish_key = get_secret("FISH_AUDIO_API_KEY")
    kokoro_model, _ = resolve_kokoro_files()
    if not fish_key and not kokoro_model:
        return
    count = 0
    for line in all_scripted_lines(apps_say):
        try:
            _, _, cached = fetch_tts(line)
            if not cached:
                count += 1
        except Exception:
            pass
    if count:
        print(f"  [tts] Cached {count} new reply lines")


def get_tts_status() -> Dict[str, Any]:
    """Retrieve diagnostic overview of the TTS subsystem."""
    fish_key = bool(get_secret("FISH_AUDIO_API_KEY"))
    model_path, voices_path = resolve_kokoro_files()
    cached_count = len([f for f in os.listdir(TTS_CACHE_DIR) if f.endswith(".wav")]) if os.path.exists(TTS_CACHE_DIR) else 0

    return {
        "provider_configured": DEFAULT_TTS_PROVIDER,
        "fish_audio_available": fish_key,
        "kokoro_available": bool(model_path and voices_path),
        "kokoro_model": os.path.basename(model_path) if model_path else None,
        "cached_files_count": cached_count,
        "muted": is_muted(),
    }
