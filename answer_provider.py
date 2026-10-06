"""Answer Provider for Hey Jev: Non-command Q&A and Chit-Chat.

Supports:
- OpenRouter (Haiku) [Default]
- OpenCode Zen (Free OpenAI-compatible models)

Architecture & Privacy Guarantees:
- Zen is used ONLY for plain Q&A (information requests and chit-chat).
- Sends ONLY the spoken user transcript. Never sends file paths, names,
  window text, clipboard, or Phase 6 tool results.
- Never logs API keys.
- Fallback chain:
    1. Zen big-pickle (timeout 8s)
    2. Another free model from /zen/v1/models (timeout 8s)
    3. OpenRouter Haiku if key exists (timeout 8s)
    4. Spoken: "I can't answer that right now."
- Traces provider, model, latency, and cost ($0.00000 for free models) per turn.
"""
from __future__ import annotations

import os
import time
import requests
from typing import Any, Dict, List, Optional, Tuple

import secrets_store
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-haiku-4.5"

# Endpoints from official OpenCode Zen documentation (https://opencode.ai/docs/zen/)
ZEN_CHAT_URL = "https://opencode.ai/zen/v1/chat/completions"
ZEN_MODELS_URL = "https://opencode.ai/zen/v1/models"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

TIMEOUT_SECONDS = 8.0

# Curated free models on OpenCode Zen
PRIMARY_ZEN_MODEL = "big-pickle"
KNOWN_FREE_ZEN_MODELS = [
    "space-bunny-free",
    "longcat-2.5-preview-free",
    "fledge-alpha-free",
    "mimo-v2.6-flash-free",
    "mimo-v2.5-free",
    "ling-3.1-flash-free",
    "ling-3.0-flash-fin-free",
    "nemotron-3.5-lightning-free",
    "nemotron-3-ultra-free",
]

SYSTEM_PROMPT = (
    "You are a voice assistant. Answer in one short spoken sentence, no markdown. "
    "You may start with exactly one tag from: [chuckling] [laughing] [sighing] [cheerful], or none."
)


def get_configured_provider() -> str:
    """Return 'openrouter' or 'opencode_zen' from env or settings."""
    env = os.getenv("HEYJEV_ANSWER_PROVIDER")
    if env in ("opencode_zen", "openrouter"):
        return env

    try:
        from assistant_ui import load_settings
        settings = load_settings()
        return settings.get("answer_provider", "openrouter")
    except Exception:
        return "openrouter"


def get_opencode_zen_key() -> Optional[str]:
    """Retrieve Zen API key with environment variable precedence. Never logs key."""
    return secrets_store.get_secret("OPENCODE_ZEN_API_KEY")


def get_openrouter_key() -> Optional[str]:
    """Retrieve OpenRouter API key. Never logs key."""
    return secrets_store.get_secret("OPENROUTER_API_KEY")


def fetch_free_zen_models(api_key: str, timeout: float = TIMEOUT_SECONDS) -> List[str]:
    """Fetch available models from /zen/v1/models and return candidate free model IDs."""
    candidates = []
    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = requests.get(ZEN_MODELS_URL, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            models_list = data.get("data", []) if isinstance(data, dict) else data
            for m in models_list:
                m_id = m.get("id") if isinstance(m, dict) else str(m)
                if m_id and m_id != PRIMARY_ZEN_MODEL and ("free" in m_id.lower() or m.get("pricing", {}).get("input") == 0):
                    candidates.append(m_id)
    except Exception:
        pass

    # Fall back to curated known free models if dynamic fetch fails or is empty
    for km in KNOWN_FREE_ZEN_MODELS:
        if km not in candidates:
            candidates.append(km)
    return candidates


def query_zen_chat(
    transcript: str,
    model: str = PRIMARY_ZEN_MODEL,
    api_key: Optional[str] = None,
    timeout: float = TIMEOUT_SECONDS
) -> Tuple[str, int]:
    """Query OpenCode Zen chat completions with the spoken transcript.

    Strict privacy enforcement:
    - ONLY the transcript is transmitted.
    - NEVER includes file names, paths, window text, clipboard, or tool results.
    - Never logs the API key.
    """
    key = api_key or get_opencode_zen_key()
    if not key:
        raise ValueError("Missing OpenCode Zen API key")

    # Strict transcript validation: only plain string transcript
    clean_text = str(transcript).strip()

    payload = {
        "model": model,
        "max_tokens": 80,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": clean_text}
        ]
    }

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }

    t0 = time.time()
    resp = requests.post(ZEN_CHAT_URL, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    latency_ms = int((time.time() - t0) * 1000)

    data = resp.json()
    reply = data["choices"][0]["message"]["content"].strip()
    return reply, latency_ms


def query_openrouter_chat(
    transcript: str,
    api_key: Optional[str] = None,
    timeout: float = TIMEOUT_SECONDS
) -> Tuple[str, int, float]:
    """Query OpenRouter Claude Haiku. Never logs key."""
    key = api_key or get_openrouter_key()
    if not key:
        raise ValueError("Missing OpenRouter API key")

    clean_text = str(transcript).strip()
    payload = {
        "model": DEFAULT_OPENROUTER_MODEL,
        "max_tokens": 80,
        "usage": {"include": True},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": clean_text}
        ]
    }

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }

    t0 = time.time()
    resp = requests.post(OPENROUTER_CHAT_URL, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    latency_ms = int((time.time() - t0) * 1000)

    data = resp.json()
    reply = data["choices"][0]["message"]["content"].strip()
    cost = float(data.get("usage", {}).get("cost", 0.0) or 0.0)
    return reply, latency_ms, cost


def _format_error_reason(exc: Optional[Exception] = None, key_missing: bool = False, provider: str = "") -> str:
    if key_missing:
        return f"{provider or 'service'} API key is missing"
    if not exc:
        return "the service was unavailable"
    if isinstance(exc, requests.Timeout):
        return "the service timed out"
    if isinstance(exc, requests.HTTPError):
        code = getattr(getattr(exc, "response", None), "status_code", None)
        if code == 401:
            return "authentication failed"
        if code == 429:
            return "rate limit was reached"
        return f"server returned error {code}" if code else "server returned an error"
    if isinstance(exc, requests.ConnectionError):
        return "network connection failed"
    if isinstance(exc, ValueError) and "key" in str(exc).lower():
        return "API key is missing"
    return "the service reported an error"


def ask_answer(
    transcript: str,
    provider: Optional[str] = None
) -> Tuple[str, int, float, str, str]:
    """Generate spoken answer for non-command turns (information_request, chit_chat).

    Returns:
        (reply_text, latency_ms, cost_dollars, provider_used, model_used)

    Fallback chain:
        Zen big-pickle -> another free model from /zen/v1/models -> OpenRouter Haiku -> spoken reason
    """
    prov = provider or get_configured_provider()
    t_start = time.time()
    last_failure_reason = ""

    if prov == "opencode_zen":
        zen_key = get_opencode_zen_key()
        if zen_key:
            # 1. Primary: Zen big-pickle
            try:
                reply, lat = query_zen_chat(transcript, model=PRIMARY_ZEN_MODEL, api_key=zen_key, timeout=TIMEOUT_SECONDS)
                return reply, lat, 0.0, "opencode_zen", PRIMARY_ZEN_MODEL
            except Exception as e:
                last_failure_reason = _format_error_reason(e)
                print(f"  [zen primary {PRIMARY_ZEN_MODEL} failed]: {type(e).__name__}")

            # 2. Fallback: Another free model from /zen/v1/models
            free_models = fetch_free_zen_models(zen_key, timeout=TIMEOUT_SECONDS)
            for alt_model in free_models:
                if alt_model == PRIMARY_ZEN_MODEL:
                    continue
                try:
                    reply, lat = query_zen_chat(transcript, model=alt_model, api_key=zen_key, timeout=TIMEOUT_SECONDS)
                    return reply, lat, 0.0, "opencode_zen", alt_model
                except Exception as e:
                    last_failure_reason = _format_error_reason(e)
                    print(f"  [zen fallback {alt_model} failed]: {type(e).__name__}")
                    break  # Try next tier in fallback chain
        else:
            last_failure_reason = _format_error_reason(key_missing=True, provider="OpenCode Zen")

        # 3. Fallback: OpenRouter Haiku (if its key exists)
        or_key = get_openrouter_key()
        if or_key:
            try:
                reply, lat, cost = query_openrouter_chat(transcript, api_key=or_key, timeout=TIMEOUT_SECONDS)
                return reply, lat, cost, "openrouter", DEFAULT_OPENROUTER_MODEL
            except Exception as e:
                last_failure_reason = _format_error_reason(e)
                print(f"  [openrouter fallback failed]: {type(e).__name__}")
        elif not last_failure_reason:
            last_failure_reason = _format_error_reason(key_missing=True, provider="OpenRouter")

    else:
        # Default: OpenRouter
        or_key = get_openrouter_key()
        if or_key:
            try:
                reply, lat, cost = query_openrouter_chat(transcript, api_key=or_key, timeout=TIMEOUT_SECONDS)
                return reply, lat, cost, "openrouter", DEFAULT_OPENROUTER_MODEL
            except Exception as e:
                last_failure_reason = _format_error_reason(e)
                print(f"  [openrouter primary failed]: {type(e).__name__}")
        else:
            last_failure_reason = _format_error_reason(key_missing=True, provider="OpenRouter")

        # If OpenRouter failed, check if Zen is available as fallback
        zen_key = get_opencode_zen_key()
        if zen_key:
            try:
                reply, lat = query_zen_chat(transcript, model=PRIMARY_ZEN_MODEL, api_key=zen_key, timeout=TIMEOUT_SECONDS)
                return reply, lat, 0.0, "opencode_zen", PRIMARY_ZEN_MODEL
            except Exception as e:
                last_failure_reason = _format_error_reason(e)

    # 4. Spoken failure with specific reason
    reason = last_failure_reason or "the service was unavailable"
    spoken_fallback = f"I can't answer that right now: {reason}."
    total_ms = int((time.time() - t_start) * 1000)
    return spoken_fallback, total_ms, 0.0, "fallback", "none"
