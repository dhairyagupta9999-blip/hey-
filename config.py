"""Central configuration and paths for Hey Jev on Windows."""
import os
import sys
import json
import shutil

# Root application directory in %APPDATA%/HeyJev
APPDATA_BASE = os.environ.get("APPDATA", os.path.expanduser("~"))
APPDATA_DIR = os.path.join(APPDATA_BASE, "HeyJev")
os.makedirs(APPDATA_DIR, exist_ok=True)

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

os.makedirs(TTS_CACHE_DIR, exist_ok=True)
os.makedirs(DICTATION_FAILED_DIR, exist_ok=True)
os.makedirs(CLIPS_DIR, exist_ok=True)

# Seed default apps.json and vocabulary.json into APPDATA if missing
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_APPS_FILE = os.path.join(REPO_ROOT, "apps.json")
DEFAULT_VOCAB_FILE = os.path.join(REPO_ROOT, "vocabulary.example.json")

if not os.path.exists(USER_APPS_FILE) and os.path.exists(DEFAULT_APPS_FILE):
    shutil.copy2(DEFAULT_APPS_FILE, USER_APPS_FILE)

if not os.path.exists(USER_VOCAB_FILE) and os.path.exists(DEFAULT_VOCAB_FILE):
    shutil.copy2(DEFAULT_VOCAB_FILE, USER_VOCAB_FILE)

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
os.makedirs(SOUNDS_DIR, exist_ok=True)

WAKE_CHIME = os.path.join(SOUNDS_DIR, "wake.wav")
DICTATE_CHIME = os.path.join(SOUNDS_DIR, "pop.wav")
TIMER_CHIME = os.path.join(SOUNDS_DIR, "timer.wav")
