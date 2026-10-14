"""Store API keys in the Windows Credential Manager, with .env as a dev fallback."""
import os
import keyring
from dotenv import load_dotenv

load_dotenv()  # so a .env works from the app bundle too, not just the terminal

SERVICE = "com.jevsiri.keys"
KEY_NAMES = ("TYPESAFE_API_KEY", "FISH_AUDIO_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "OPENCODE_ZEN_API_KEY")
OPTIONAL = ("OPENAI_API_KEY", "OPENCODE_ZEN_API_KEY")  # optional providers / dictation


def credential_manager_value(name):
    """Retrieve secret from Windows Credential Manager."""
    try:
        val = keyring.get_password(SERVICE, name)
        return val.strip() if val else None
    except Exception:
        return None


def get_secret(name):
    """Return key from .env if present, otherwise from Windows Credential Manager."""
    if name == "OPENCODE_ZEN_API_KEY":
        env_val = os.getenv("OPENCODE_ZEN_API_KEY") or os.getenv("OPENCODE_API_KEY")
        if env_val:
            return env_val.strip()
    return os.getenv(name) or credential_manager_value(name)  # .env wins, so editing it always takes effect


def save_secret(name, value):
    """Save key to Windows Credential Manager."""
    if name not in KEY_NAMES:
        raise ValueError(f"unknown secret: {name}")
    value = value.strip()
    if not value:
        return
    try:
        keyring.set_password(SERVICE, name, value)
    except Exception as exc:
        raise RuntimeError(f"Could not save to Windows Credential Manager: {exc}") from exc


def missing_secrets():
    """Return required keys that have not yet been provided."""
    return [name for name in KEY_NAMES if name not in OPTIONAL and not get_secret(name)]
