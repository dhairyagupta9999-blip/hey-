"""Trace logging to %APPDATA%/HeyJev/Hey Jev.log matching the original Hey Jev trace format."""
import os
import threading
from config import LOG_FILE

_LOG_LOCK = threading.Lock()

def trace_line(line: str):
    """Append a single line to Hey Jev.log."""
    with _LOG_LOCK:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            print(f"[logger error]: {e}")

def trace_heard(text: str, stt_ms: int | None = None):
    line = f"> heard: {text!r}" + (f"  (stt {stt_ms}ms)" if stt_ms is not None else "")
    print(f"\n{line}")
    trace_line(f"\n{line}")

def trace_fixed(fixed: str):
    line = f"  fixed: {fixed!r}"
    print(line)
    trace_line(line)

def trace_answers(ans: dict, gate: float):
    for k, (v, c) in ans.items():
        flag = "" if c >= gate else "  <- below gate"
        line = f"  {k:15} {str(v):22} {c:.2f}{flag}"
        print(line)
        trace_line(line)

def trace_decision(backend: str, latency_ms: int, cost: float):
    line = f"  {backend} {latency_ms}ms  ${cost:.6f}  [backend: {backend}]"
    print(line)
    trace_line(line)

def trace_split_call(backend: str, latency_ms: int, cost: float):
    line = f"  -- split call: {backend} {latency_ms}ms  ${cost:.6f}  [backend: {backend}]"
    print(line)
    trace_line(line)

def trace_action(action: str, arg: str | None = None):
    line = f"  action: {action} {arg or ''}".rstrip()
    print(line)
    trace_line(line)

def trace_say(line_text: str):
    line = f"  say: {line_text}"
    print(line)
    trace_line(line)

def trace_fish(tts_ms: int):
    status = "cached" if tts_ms == 0 else f"{tts_ms}ms"
    line = f"  fish {status}"
    print(line)
    trace_line(line)
