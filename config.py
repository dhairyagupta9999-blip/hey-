"""Central configuration and paths for Hey Jev on Windows."""
import os
import sys
import json
import shutil

# Root application directory in %APPDATA%/HeyJev
APPDATA_BASE = os.environ.get("APPDATA", os.path.expanduser("~"))
APPDATA_DIR = os.path.join(APPDATA_BASE, "HeyJev")

# Standard file locations
LOG_FILE = os.path.join(APPDATA_DIR, "Hey Jev.log")
DICTATION_HISTORY_FILE = os.path.join(APPDATA_DIR, "Hey Jev dictation.jsonl")
DICTATION_LOG = DICTATION_HISTORY_FILE
DICTATION_FAILED_DIR = os.path.join(APPDATA_DIR, "dictation_failed")
TTS_CACHE_DIR = os.path.join(APPDATA_DIR, "cache", "tts")
CLIPS_DIR = os.path.join(APPDATA_DIR, "clips")
TIMERS_FILE = os.path.join(APPDATA_DIR, "timers.json")
SETTINGS_FILE = os.path.join(APPDATA_DIR, "settings.json")
USER_VOCAB_FILE = os.path.join(APPDATA_DIR, "vocabulary.json")
USER_APPS_FILE = os.path.join(APPDATA_DIR, "apps.json")

# Seed default apps.json and vocabulary.json into APPDATA if missing
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_APPS_FILE = os.path.join(REPO_ROOT, "apps.json")
DEFAULT_VOCAB_FILE = os.path.join(REPO_ROOT, "vocabulary.example.json")

# B8: importing this module must not touch the filesystem. It previously called
# os.makedirs() five times and seeded two user files at import time, so merely
# reading config -- a lint pass, a test collection, an import from a REPL --
# mutated the user's machine, and a read-only or sandboxed environment raised
# at import instead of failing with a clear message. Everything that writes now
# lives in ensure_app_dirs(), which only the entrypoint calls.

# Audio and recognition defaults
SAMPLE_RATE = 16000
WHISPER_MODEL = "small.en"
DEFAULT_STT_ENGINE = "whisper"
DEFAULT_WAKEWORD_ENGINE = os.environ.get("HEYJEV_WAKEWORD_ENGINE", "whisper")
DEFAULT_GATE = 0.65
COMMAND_PROMPT = "Open Spotify. Set a timer for five minutes. Play. Pause. Next track. Turn Spotify down. Turn the volume down. Mute. Dark mode on. Lock the screen."
WAKE_PROMPT = None
NO_SPEECH_MAX = 0.6

# Names and wake regex
NAMES = "jev|jevs|jeff|jeffs|jef|jeb|jab|chev|jeve|jav"
WAKE_WINDOW = 10.0

# Sound assets directory
ASSETS_DIR = os.path.join(REPO_ROOT, "assets")
SOUNDS_DIR = os.path.join(ASSETS_DIR, "sounds")

WAKE_CHIME = os.path.join(SOUNDS_DIR, "wake.wav")
DICTATE_CHIME = os.path.join(SOUNDS_DIR, "pop.wav")
TIMER_CHIME = os.path.join(SOUNDS_DIR, "timer.wav")

# OpenDots Interoperability Gateway defaults
OPENDOTS_GATEWAY_HOST_DEFAULT = "127.0.0.1"
OPENDOTS_GATEWAY_PORT_DEFAULT = 8765
OPENDOTS_GATEWAY_ENABLED_DEFAULT = False

# Single source of truth for the shipped placeholder token (B9). `.env.example`
# must carry this exact value, and the gateway refuses to start on it -- a user
# who copies the example file must get a loud failure, not a service protected
# by a value published in the repository. Plain ASCII sentinel by construction.
OPENDOTS_TOKEN_PLACEHOLDER = "change_me_to_a_secure_random_token"


def ensure_app_dirs() -> None:
    """Create the application's directories and seed its user files.

    The single place where importing Hey Jev is allowed to touch the disk (B8).
    Call this from the entrypoint before anything reads or writes a path.
    Idempotent: safe to call on every start.
    """
    for directory in (APPDATA_DIR, TTS_CACHE_DIR, DICTATION_FAILED_DIR,
                      CLIPS_DIR, SOUNDS_DIR):
        os.makedirs(directory, exist_ok=True)

    if not os.path.exists(USER_APPS_FILE) and os.path.exists(DEFAULT_APPS_FILE):
        shutil.copy2(DEFAULT_APPS_FILE, USER_APPS_FILE)

    if not os.path.exists(USER_VOCAB_FILE) and os.path.exists(DEFAULT_VOCAB_FILE):
        shutil.copy2(DEFAULT_VOCAB_FILE, USER_VOCAB_FILE)
