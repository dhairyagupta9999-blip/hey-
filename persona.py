"""Persona Configuration Manager for Hey Jev (Phase 8e).

Manages greeting, status phrases, and confirmation wording loaded from persona.json.
GUARANTEE:
Persona settings provide conversational phrasing only and CAN NEVER alter,
weaken, or override Safety Engine policy decisions or risk levels.
"""
from __future__ import annotations

import os
import json
from typing import Any, Dict

DEFAULT_PERSONA: Dict[str, Any] = {
    "name": "Jev",
    "greeting": "Hey, I'm listening.",
    "status_phrases": {
        "ready": "Ready.",
        "thinking": "Thinking...",
        "done": "Done.",
        "cancelled": "Action cancelled."
    },
    "confirmation_wording": {
        "action_prompt": "I am about to execute '{action}'. Should I proceed?",
        "sensitive_page": "Window '{title}' is a sensitive page. Should I proceed?",
        "shortcut_warning": "Shortcut '{shortcut}' will perform a key operation. Should I proceed?",
        "data_disclosure": "I am reading the screen or window contents to show the AI model. Should I proceed?"
    }
}

PERSONA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona.json")


def load_persona() -> Dict[str, Any]:
    """Load persona configuration from persona.json, falling back to defaults."""
    if os.path.exists(PERSONA_FILE):
        try:
            with open(PERSONA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                merged = dict(DEFAULT_PERSONA)
                merged.update(data)
                return merged
        except Exception:
            pass
    return dict(DEFAULT_PERSONA)


def get_greeting() -> str:
    return load_persona().get("greeting", DEFAULT_PERSONA["greeting"])


def get_status_phrase(key: str) -> str:
    phrases = load_persona().get("status_phrases", {})
    return phrases.get(key, DEFAULT_PERSONA["status_phrases"].get(key, "Ready."))


def get_confirmation_wording(key: str, **kwargs) -> str:
    cw = load_persona().get("confirmation_wording", {})
    template = cw.get(key, DEFAULT_PERSONA["confirmation_wording"].get(key, "Should I proceed?"))
    try:
        return template.format(**kwargs)
    except Exception:
        return template
