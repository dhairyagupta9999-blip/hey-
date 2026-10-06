"""Exact-Catalog Rule Enforcement for Hey Jev (Phase 8f).

Any switch of model, STT engine, or TTS voice resolves ONLY against an explicit catalog.
Near-misses (e.g., "opus 5" when only "opus 4" or Claude Haiku exists) are REFUSED
with an explicit error and a list of valid choices, never a silent nearest match.
Runtime in-memory swaps reset to persistent config on application restart.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

MODELS_CATALOG: Dict[str, Dict[str, Any]] = {
    "anthropic/claude-haiku-4.5": {
        "provider": "openrouter",
        "description": "Claude 3.5 Haiku via OpenRouter (default for Q&A and Tier 3)"
    },
    "big-pickle": {
        "provider": "opencode_zen",
        "description": "OpenCode Zen primary free model"
    },
    "space-bunny-free": {
        "provider": "opencode_zen",
        "description": "OpenCode Zen free model"
    },
    "longcat-2.5-preview-free": {
        "provider": "opencode_zen",
        "description": "OpenCode Zen free model"
    },
    "fledge-alpha-free": {
        "provider": "opencode_zen",
        "description": "OpenCode Zen free model"
    },
    "mimo-v2.6-flash-free": {
        "provider": "opencode_zen",
        "description": "OpenCode Zen free model"
    },
    "mimo-v2.5-free": {
        "provider": "opencode_zen",
        "description": "OpenCode Zen free model"
    },
    "ling-3.1-flash-free": {
        "provider": "opencode_zen",
        "description": "OpenCode Zen free model"
    },
    "ling-3.0-flash-fin-free": {
        "provider": "opencode_zen",
        "description": "OpenCode Zen free model"
    },
    "nemotron-3.5-lightning-free": {
        "provider": "opencode_zen",
        "description": "OpenCode Zen free model"
    },
    "nemotron-3-ultra-free": {
        "provider": "opencode_zen",
        "description": "OpenCode Zen free model"
    }
}

STT_ENGINES_CATALOG: Dict[str, Dict[str, Any]] = {
    "whisper-small.en": {
        "engine": "whisper",
        "model": "small.en",
        "description": "faster-whisper small.en (default on-device STT)"
    },
    "whisper-base.en": {
        "engine": "whisper",
        "model": "base.en",
        "description": "faster-whisper base.en"
    },
    "whisper-tiny.en": {
        "engine": "whisper",
        "model": "tiny.en",
        "description": "faster-whisper tiny.en"
    },
    "whistle": {
        "engine": "whistle",
        "model": "needle",
        "description": "Whistle (Cactus Compute fast streaming STT)"
    }
}

TTS_VOICES_CATALOG: Dict[str, Dict[str, Any]] = {
    "9a9cf47702da476aa4629e2506d4a857": {
        "provider": "fish_audio",
        "description": "Fish Audio Jev voice ID (default)"
    },
    "default": {
        "provider": "fish_audio",
        "description": "Default assistant voice"
    }
}

_RUNTIME_SWAPS: Dict[str, Optional[str]] = {
    "model": None,
    "stt_engine": None,
    "tts_voice": None
}


def resolve_model(requested_name: str) -> Tuple[bool, str, List[str]]:
    """Resolve model against explicit catalog. Returns (success, resolved_id, available_list)."""
    req = requested_name.strip()
    if req in MODELS_CATALOG:
        return True, req, []
    return False, f"Model '{requested_name}' not found in catalog.", sorted(list(MODELS_CATALOG.keys()))


def resolve_stt_engine(requested_name: str) -> Tuple[bool, str, List[str]]:
    """Resolve STT engine against explicit catalog."""
    req = requested_name.strip()
    if req in STT_ENGINES_CATALOG:
        return True, req, []
    return False, f"STT engine '{requested_name}' not found in catalog.", sorted(list(STT_ENGINES_CATALOG.keys()))


def resolve_tts_voice(requested_name: str) -> Tuple[bool, str, List[str]]:
    """Resolve TTS voice against explicit catalog."""
    req = requested_name.strip()
    if req in TTS_VOICES_CATALOG:
        return True, req, []
    return False, f"TTS voice '{requested_name}' not found in catalog.", sorted(list(TTS_VOICES_CATALOG.keys()))


def set_runtime_model(model_name: str) -> Tuple[bool, str]:
    """Switch active model for the current runtime session, rejecting near-misses."""
    ok, val, available = resolve_model(model_name)
    if not ok:
        avail_str = ", ".join(f"'{m}'" for m in available)
        return False, f"Cannot switch model to '{model_name}'. Allowed models: {avail_str}."
    _RUNTIME_SWAPS["model"] = val
    return True, f"Active model temporarily switched to '{val}'."


def set_runtime_stt_engine(engine_name: str) -> Tuple[bool, str]:
    """Switch active STT engine for current runtime session, rejecting near-misses."""
    ok, val, available = resolve_stt_engine(engine_name)
    if not ok:
        avail_str = ", ".join(f"'{e}'" for e in available)
        return False, f"Cannot switch STT engine to '{engine_name}'. Allowed engines: {avail_str}."
    _RUNTIME_SWAPS["stt_engine"] = val
    return True, f"Active STT engine temporarily switched to '{val}'."


def set_runtime_tts_voice(voice_name: str) -> Tuple[bool, str]:
    """Switch active TTS voice for current runtime session, rejecting near-misses."""
    ok, val, available = resolve_tts_voice(voice_name)
    if not ok:
        avail_str = ", ".join(f"'{v}'" for v in available)
        return False, f"Cannot switch TTS voice to '{voice_name}'. Allowed voices: {avail_str}."
    _RUNTIME_SWAPS["tts_voice"] = val
    return True, f"Active TTS voice temporarily switched to '{val}'."


def get_runtime_model() -> Optional[str]:
    return _RUNTIME_SWAPS.get("model")


def get_runtime_stt_engine() -> Optional[str]:
    return _RUNTIME_SWAPS.get("stt_engine")


def get_runtime_tts_voice() -> Optional[str]:
    return _RUNTIME_SWAPS.get("tts_voice")


def reset_runtime_swaps() -> None:
    """Reset runtime swaps to config defaults on restart."""
    _RUNTIME_SWAPS["model"] = None
    _RUNTIME_SWAPS["stt_engine"] = None
    _RUNTIME_SWAPS["tts_voice"] = None
