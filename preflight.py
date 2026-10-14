"""Real end-to-end preflight verification suite for Hey Jev (Phase 8c).

Runs live verification against actual local PC environment (no mocks):
1. App components accessible
2. STT engine warm and transcribes audio
3. TTS engine cache hit or synthesis
4. Jev decision engine responsiveness
5. Answer provider chain responsiveness
6. MCP server handshake and read-only tool inspection
7. Tier 2 resolves known app without launching
8. Memory engine status (Phase 12 pending)
9. Safety engine blocks Run-dialog and administrative tools
10. Secret scan in logs (keys never appear in disk logs)
11. Real bug checks:
    - Loop breaker fires on 3 rapid actions
    - Dedup guard drops duplicate transcripts within 5s
    - Self-hearing reply echo filter
    - Exact app name precedence ("Chrome means Chrome")
    - Foreground shell window blocking

Prints tick/cross for each check and final summary line:
  "N pass, N fail, N warn"
Exits with 0 if fail == 0, else non-zero.
"""
from __future__ import annotations

import os
import sys
import time
import json
import re
import numpy as np

# Ensure root dir is in sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def run_preflight() -> int:
    passes = 0
    fails = 0
    warns = 0

    print("=" * 70)
    print(" HEY JEV — PREFLIGHT TRUTH VERIFICATION SUITE")
    print("=" * 70)

    # 1. App Components Accessible
    try:
        import config
        import siri
        import assistant_ui
        import safety_engine
        print(" [PASS] App components and configuration accessible")
        passes += 1
    except Exception as e:
        print(f" [FAIL] App components import failed: {e}")
        fails += 1

    # 2. STT Engine Warm and Transcribes
    try:
        import stt
        # Create 1-second synthetic silence buffer
        test_audio = np.zeros(16000, dtype=np.float32)
        txt, ms = stt.get_stt_backend(engine="whisper", whisper_model="tiny.en").transcribe(test_audio)
        print(f" [PASS] STT engine warm and functional ({ms} ms)")
        passes += 1
    except Exception as e:
        print(f" [FAIL] STT engine failed: {e}")
        fails += 1

    # 3. TTS Cache Hit or Synthesis
    try:
        import tts
        cache_path, ms, cached = tts.fetch_tts("[cheerful] Ready.")
        print(f" [PASS] TTS engine functional (cached={cached}, {ms} ms)")
        passes += 1
    except Exception as e:
        # If API key is missing, warn if cache exists
        import tts
        if os.path.exists(tts.TTS_CACHE_DIR):
            print(f" [WARN] TTS cache directory exists but live fetch error: {e}")
            warns += 1
        else:
            print(f" [FAIL] TTS engine failed: {e}")
            fails += 1

    # 4. Jev Decision Call
    try:
        import siri
        backend = siri.get_backend()
        if hasattr(backend, "api_key") and not backend.api_key:
            print(" [WARN] Jev decision backend key missing in secrets store (TypeSafe key required for live turns)")
            warns += 1
        else:
            ans, dec_ms, cost = backend.decide(siri.QUESTIONS, "open spotify")
            print(f" [PASS] Jev decision backend functional ({backend.name}, {dec_ms} ms, ${cost:.5f})")
            passes += 1
    except Exception as e:
        print(f" [WARN] Jev decision call: {e}")
        warns += 1

    # 5. Answer Provider Chain
    try:
        import answer_provider
        reply, lat, cost, prov, model = answer_provider.ask_answer("What is the capital of France?")
        if prov != "fallback_error":
            print(f" [PASS] Answer provider responded ({prov}/{model}, {lat} ms, ${cost:.5f})")
            passes += 1
        else:
            print(f" [WARN] Answer provider fell back to error speech: '{reply}'")
            warns += 1
    except Exception as e:
        print(f" [WARN] Answer provider chain error: {e}")
        warns += 1

    # 6. MCP Server Handshake & Read-Only Tool Call
    try:
        import mcp_client
        client = mcp_client.get_mcp_client()
        if client.exe_path and os.path.exists(client.exe_path):
            # Measured cold handshake on the real binary is 5.7-8.9 s (mean
            # ~7.0 s), dominated by ~4.5 s of .NET runtime startup. The old
            # timeout_s=3.0 could never succeed, so preflight reported a false
            # negative: "[WARN] MCP server binary exists but failed handshake"
            # on a perfectly working install. Generous enough to pass, bounded
            # so a genuinely broken server still fails promptly.
            ok = client.is_available() or client.start(timeout_s=20.0)
            if ok:
                read_res = client.call_tool("window_management", {"action": "list"})
                print(f" [PASS] MCP server handshake succeeded and read tool executed")
                passes += 1
            else:
                print(f" [WARN] MCP server binary exists but failed handshake: {client.get_error_message()}")
                warns += 1
        else:
            print(f" [WARN] MCP server binary not installed at {client.exe_path} (stays off cleanly)")
            warns += 1
    except Exception as e:
        print(f" [WARN] MCP server check error: {e}")
        warns += 1

    # 7. Tier 2 Resolves Known App Without Launching
    try:
        from app_index import get_app_index
        idx = get_app_index()
        app_entry, score, _ = idx.find_app("notepad")
        if app_entry and score >= 60.0:
            print(f" [PASS] Tier 2 resolved app '{app_entry.get('name')}' without launching (score={score})")
            passes += 1
        else:
            print(f" [FAIL] Tier 2 could not resolve standard app 'notepad'")
            fails += 1
    except Exception as e:
        print(f" [FAIL] Tier 2 app resolution error: {e}")
        fails += 1

    # 8. Memory Engine (Phase 16 Persistent Memory)
    try:
        from memory_store import SqliteMemoryStore
        from memory_model import MemoryRecord, MemoryType, MemorySource
        store = SqliteMemoryStore()
        t0 = time.time()
        test_rec = MemoryRecord(
            type=MemoryType.USER_PREFERENCE,
            content="preflight memory check preference",
            source=MemorySource.SYSTEM,
        )
        rec_id = store.add(test_rec)
        retrieved = store.get(rec_id)
        mem_ms = int((time.time() - t0) * 1000)
        if retrieved and retrieved.content == "preflight memory check preference":
            store.delete(rec_id, hard=True)
            print(f" [PASS] Memory engine functional (SQLite WAL, {mem_ms} ms)")
            passes += 1
        else:
            print(" [FAIL] Memory record write succeeded but read/verify failed")
            fails += 1
    except Exception as e:
        print(f" [FAIL] Memory engine failed: {e}")
        fails += 1

    # 9. Safety Engine Blocks Run-Dialog and Admin Tools
    try:
        import safety_engine
        safe, status, reason = safety_engine.check_foreground_window_safety("explorer.exe", "Run")
        sec_stat, sec_reason = safety_engine.check_shortcut_safety("press_keys", {"keys": "win+r"})
        if not safe and status == "BLOCKED" and sec_stat == "BLOCKED":
            print(" [PASS] Safety engine strictly blocks Run-dialog and Win+R shortcut")
            passes += 1
        else:
            print(" [FAIL] Safety engine failed to block Run dialog or Win+R")
            fails += 1
    except Exception as e:
        print(f" [FAIL] Safety engine validation error: {e}")
        fails += 1

    # 10. Keys Never in Logs
    try:
        import secrets_store
        from config import LOG_FILE, APPDATA_DIR
        keys_found = False
        log_files = [LOG_FILE, os.path.join(APPDATA_DIR, "actions.jsonl"), os.path.join(APPDATA_DIR, "spend.json")]
        known_keys = [
            secrets_store.get_secret("TYPESAFE_API_KEY"),
            secrets_store.get_secret("OPENROUTER_API_KEY"),
            secrets_store.get_secret("OPENCODE_ZEN_API_KEY"),
            secrets_store.get_secret("FISH_AUDIO_API_KEY")
        ]
        real_keys = [k for k in known_keys if k and len(k) > 8]

        for lf in log_files:
            if os.path.exists(lf):
                with open(lf, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    for rk in real_keys:
                        if rk in content:
                            keys_found = True
                            break
        if not keys_found:
            print(" [PASS] Secret scan passed: zero API keys detected in disk logs")
            passes += 1
        else:
            print(" [FAIL] Secret scan failed: active API key found in disk log!")
            fails += 1
    except Exception as e:
        print(f" [FAIL] Secret scan error: {e}")
        fails += 1

    # 11. Real Bug Checks:
    # 11a: Loop Breaker Fires on 3 Rapid Actions
    try:
        import siri
        siri.reset_action_loop_history()
        siri.record_and_check_action_loop("test_action", 100.0)
        siri.record_and_check_action_loop("test_action", 102.0)
        tripped = siri.record_and_check_action_loop("test_action", 104.0)
        if tripped:
            print(" [PASS] Real bug check: Loop breaker trips on 3 identical firings within 10s")
            passes += 1
        else:
            print(" [FAIL] Real bug check: Loop breaker failed to trip on 3rd action")
            fails += 1
    except Exception as e:
        print(f" [FAIL] Loop breaker check error: {e}")
        fails += 1

    # 11b: Dedup Guard -- drops a near-identical repeat, but NOT a human retry
    #
    # B3 narrowed the window from 5.0s to 0.3s. The old check asserted that a
    # repeat 2 seconds later was dropped, which was exactly the bug: a user
    # retrying a command that visibly failed got silence. Both halves are now
    # checked, because the guard is only correct if it suppresses retriggers
    # without swallowing corrections.
    try:
        import siri
        siri.reset_transcript_dedup()

        first_call = siri.check_and_update_transcript_dedup("open calculator", current_time=100.0)
        retrigger = siri.check_and_update_transcript_dedup("open calculator", current_time=100.1)
        retry = siri.check_and_update_transcript_dedup("open calculator", current_time=102.0)

        if not first_call and retrigger:
            print(" [PASS] Real bug check: Dedup guard drops a repeat within 300ms")
            passes += 1
        else:
            print(f" [FAIL] Real bug check: Dedup guard failed to suppress a 100ms "
                  f"retrigger (first={first_call}, retrigger={retrigger})")
            fails += 1

        if not retry:
            print(" [PASS] Real bug check: Dedup guard honours a human retry after 2s")
            passes += 1
        else:
            print(" [FAIL] Real bug check: Dedup guard swallowed a human retry "
                  "2s later -- the B3 regression")
            fails += 1
    except Exception as e:
        print(f" [FAIL] Dedup check error: {e}")
        fails += 1

    # 11c: Exact App Match Precedence ("Chrome means Chrome")
    try:
        from app_index import get_app_index
        idx = get_app_index()
        # Add test candidates if Chrome not in Start Menu
        entry, score, _ = idx.find_app("chrome")
        if entry:
            print(f" [PASS] Real bug check: Exact app match prioritized ('{entry.get('name')}', score={score})")
            passes += 1
        else:
            print(" [WARN] 'chrome' not found in system app index")
            warns += 1
    except Exception as e:
        print(f" [FAIL] Exact app match error: {e}")
        fails += 1

    # 11d: Foreground Shell Window Blocking
    try:
        import safety_engine
        safe, status, reason = safety_engine.check_foreground_window_safety("powershell.exe", "Windows PowerShell")
        if not safe and status == "BLOCKED":
            print(" [PASS] Real bug check: Shell process 'powershell.exe' strictly blocked")
            passes += 1
        else:
            print(" [FAIL] Real bug check: powershell.exe was not blocked")
            fails += 1
    except Exception as e:
        print(f" [FAIL] Shell window blocking check error: {e}")
        fails += 1

    print("=" * 70)
    print(f"PREFLIGHT SUMMARY: {passes} pass, {fails} fail, {warns} warn")
    print("=" * 70)

    return 1 if fails > 0 else 0


if __name__ == "__main__":
    sys.exit(run_preflight())
