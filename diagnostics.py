"""Diagnostics Engine for Hey Jev (Phase 8d).

Collects and formats system health indicators containing:
BOOLEANS AND COUNTERS ONLY:
- listening: bool
- stt_ready: bool
- tts_ready: bool
- decision_backend_ready: bool
- provider_reachable: bool
- mcp_up: bool
- confirmation_pending: bool
- cancel_state: bool
- mic_paused: bool
- loop_breaker_tripped: bool
- today_spent_usd: float
- daily_cap_usd: float
- spend_cap_exceeded: bool
- last_turn_latency_ms: int
- turns_completed: int
- mcp_actions_executed: int

STRICT PRIVACY ENFORCEMENT:
Never records, includes, or formats app names, URLs, transcripts, window titles, or secrets.
All values are strictly cast to bool, int, or float.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict

# Session counters & state
_LOCK = threading.Lock()
_TURNS_COMPLETED = 0
_MCP_ACTIONS_EXECUTED = 0
_LAST_TURN_LATENCY_MS = 0


def record_turn_completed(latency_ms: int = 0) -> None:
    global _TURNS_COMPLETED, _LAST_TURN_LATENCY_MS
    with _LOCK:
        _TURNS_COMPLETED += 1
        _LAST_TURN_LATENCY_MS = max(0, int(latency_ms))


def record_mcp_action_executed() -> None:
    global _MCP_ACTIONS_EXECUTED
    with _LOCK:
        _MCP_ACTIONS_EXECUTED += 1


def get_diagnostics_dict() -> Dict[str, Any]:
    """Retrieve system diagnostics strictly as booleans and numeric counters."""
    # 1. Listening & Loop state
    listening = False
    mic_paused = False
    loop_breaker_tripped = False
    try:
        import siri
        listening = bool(getattr(siri, "_WAKE_LISTENER_ACTIVE", False) or getattr(siri, "_LISTENING", False))
        mic_paused = bool(getattr(siri, "_MIC_PAUSED", False))
        loop_breaker_tripped = bool(getattr(siri, "_LOOP_BREAKER_TRIPPED", False))
    except Exception:
        pass

    # 2. STT Ready
    stt_ready = False
    try:
        import stt
        stt_ready = bool(getattr(stt, "_WHISPER_LOADED", False) or getattr(stt, "_WHISTLE_AVAILABLE", False))
    except Exception:
        pass

    # 3. TTS Ready
    tts_ready = False
    try:
        import tts
        import secrets_store
        tts_ready = bool(secrets_store.get_secret("FISH_AUDIO_API_KEY") or os.path.exists(tts.TTS_CACHE_DIR))
    except Exception:
        pass

    # 4. Decision Backend Ready
    decision_backend_ready = False
    try:
        import siri
        backend = siri.get_backend()
        decision_backend_ready = bool(backend is not None and getattr(backend, "api_key", None))
    except Exception:
        pass

    # 5. Provider Reachable
    provider_reachable = False
    try:
        import answer_provider
        prov = answer_provider.get_configured_provider()
        if prov == "opencode_zen":
            provider_reachable = bool(answer_provider.get_opencode_zen_key())
        else:
            provider_reachable = bool(answer_provider.get_openrouter_key())
    except Exception:
        pass

    # 6. MCP Up
    mcp_up = False
    try:
        import mcp_client
        client = mcp_client.get_mcp_client()
        mcp_up = bool(client.is_available())
    except Exception:
        pass

    # 7. Confirmation Pending & Cancel State
    confirmation_pending = False
    cancel_state = False
    try:
        import safety_engine
        cm = safety_engine.get_confirmation_manager()
        confirmation_pending = bool(cm.pending_confirmation is not None)
        cancel_state = bool(safety_engine.is_cancel_requested())
    except Exception:
        pass

    # 8. Spend & Cap
    today_spent = 0.0
    daily_cap = 0.10
    cap_exceeded = False
    try:
        import tier3_agent
        today_spent = float(tier3_agent.get_daily_spend())
        daily_cap = float(tier3_agent.get_daily_spend_cap())
        cap_exceeded = bool(today_spent >= daily_cap)
    except Exception:
        pass

    with _LOCK:
        turns = _TURNS_COMPLETED
        mcp_actions = _MCP_ACTIONS_EXECUTED
        last_latency = _LAST_TURN_LATENCY_MS

    # Return STRICT booleans and numbers only
    return {
        "listening": bool(listening),
        "stt_ready": bool(stt_ready),
        "tts_ready": bool(tts_ready),
        "decision_backend_ready": bool(decision_backend_ready),
        "provider_reachable": bool(provider_reachable),
        "mcp_up": bool(mcp_up),
        "confirmation_pending": bool(confirmation_pending),
        "cancel_state": bool(cancel_state),
        "mic_paused": bool(mic_paused),
        "loop_breaker_tripped": bool(loop_breaker_tripped),
        "today_spent_usd": round(float(today_spent), 5),
        "daily_cap_usd": round(float(daily_cap), 2),
        "spend_cap_exceeded": bool(cap_exceeded),
        "last_turn_latency_ms": int(last_latency),
        "turns_completed": int(turns),
        "mcp_actions_executed": int(mcp_actions)
    }


def format_diagnostics_text() -> str:
    """Format diagnostics into a clean text block containing only booleans and numbers."""
    d = get_diagnostics_dict()
    lines = [
        "Hey Jev Diagnostics (Booleans & Counters Only):",
        f"  listening:              {d['listening']}",
        f"  stt_ready:              {d['stt_ready']}",
        f"  tts_ready:              {d['tts_ready']}",
        f"  decision_backend_ready: {d['decision_backend_ready']}",
        f"  provider_reachable:     {d['provider_reachable']}",
        f"  mcp_up:                 {d['mcp_up']}",
        f"  confirmation_pending:   {d['confirmation_pending']}",
        f"  cancel_state:           {d['cancel_state']}",
        f"  mic_paused:             {d['mic_paused']}",
        f"  loop_breaker_tripped:   {d['loop_breaker_tripped']}",
        f"  today_spent_usd:        ${d['today_spent_usd']:.5f}",
        f"  daily_cap_usd:          ${d['daily_cap_usd']:.2f}",
        f"  spend_cap_exceeded:     {d['spend_cap_exceeded']}",
        f"  last_turn_latency_ms:   {d['last_turn_latency_ms']} ms",
        f"  turns_completed:        {d['turns_completed']}",
        f"  mcp_actions_executed:   {d['mcp_actions_executed']}"
    ]
    return "\n".join(lines)
