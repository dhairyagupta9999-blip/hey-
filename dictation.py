"""Dictation: "Hey Jev, transcribe" records until "stop transcribing", then pastes text at cursor."""
import io
import os
import re
import json
import time
import base64
import ctypes
import subprocess
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import requests
import soundfile as sf
from config import DICTATION_HISTORY_FILE as HISTORY, DICTATION_FAILED_DIR as FAILED_DIR, USER_VOCAB_FILE as USER_VOCAB, DEFAULT_VOCAB_FILE

MODEL = "openai/gpt-4o-mini-transcribe"
PROMPT = ("The following is a transcript of a person talking, you can remove any duplicated words and any filler words. "
          "If it's a longer transcript put into paragraphs for better readability.")
CHUNK_SECS = 30
MAX_SECS = 15 * 60
START = re.compile(r"^\W*(?:(?:please|can you|could you)\s+)?(?:start\s+)?(?:transcrib\w*|dictat\w*|take notes)"
                   r"(?:\s+(?:this|this meeting|this call|notes|mode|now|please|for me))?\W*$", re.I)

VOCAB = []

def read_vocab():
    path = USER_VOCAB if os.path.exists(USER_VOCAB) else DEFAULT_VOCAB_FILE
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def load_vocab():
    global VOCAB
    VOCAB = [(re.compile(r"\b" + re.escape(alt) + r"\b", re.I), word) for word, alts in read_vocab().items() for alt in alts]

def save_vocab(words):
    with open(USER_VOCAB, "w", encoding="utf-8") as f:
        json.dump(words, f, indent=2, ensure_ascii=False)
        f.write("\n")
    load_vocab()

load_vocab()

def fix_vocab(text):
    for rx, word in VOCAB:
        text = rx.sub(word, text)
    return text

def strip_prompt(text):
    cleaned = text
    for part in (PROMPT, *PROMPT.split(", "), *PROMPT.split(". ")):
        cleaned = re.sub(re.escape(part.strip(" .")), "", cleaned, flags=re.I)
    cleaned = re.sub(r"[ \t]+", " ", cleaned).strip(" ,.")
    return cleaned if cleaned else text

def paste(text):
    """Set text to Windows clipboard and synthesize Ctrl+V to paste at the current cursor position."""
    # 1. Place on clipboard using Win32 API
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    if user32.OpenClipboard(None):
        try:
            user32.EmptyClipboard()
            encoded = text.encode("utf-16-le") + b"\x00\x00"
            h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
            ptr = kernel32.GlobalLock(h_mem)
            ctypes.memmove(ptr, encoded, len(encoded))
            kernel32.GlobalUnlock(h_mem)
            user32.SetClipboardData(CF_UNICODETEXT, h_mem)
        finally:
            user32.CloseClipboard()

    time.sleep(0.15)

    # 2. Synthesize Ctrl+V via keybd_event
    VK_CONTROL = 0x11
    VK_V = 0x56
    KEYEVENTF_KEYUP = 0x0002

    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


class Dictation:
    def __init__(self, names, get_key, sample_rate):
        self.stop_rx = re.compile(rf"(?:\b(?:hey|hi|hay|okay|ok)\W+)?(?:\b(?:{names})\W+)?\b(?:stop|end|finish)\W+(?:the\W+)?"
                                  r"(?:transcri|dictat)\w*\W*$", re.I)
        self.get_key = get_key
        self.rate = sample_rate
        self.pool = ThreadPoolExecutor(3)
        self.active = False
        self.buffer = []
        self.chunks = []
        self.started = 0
        self.stamp = ""

    def start(self):
        self.active = True
        self.started = time.time()
        self.stamp = time.strftime("%Y-%m-%d %H-%M-%S")
        self.buffer = []
        self.chunks = []

    def timed_out(self):
        return self.active and time.time() - self.started > MAX_SECS

    def add(self, audio, heard):
        self.buffer.append(audio)
        stop = bool(self.stop_rx.search(heard)) or self.timed_out()
        if stop or sum(map(len, self.buffer)) >= CHUNK_SECS * self.rate:
            self._send()
        return stop

    def _send(self):
        if self.buffer:
            self.chunks.append(self.pool.submit(self._transcribe, np.concatenate(self.buffer), len(self.chunks) + 1))
            self.buffer = []

    def finish(self):
        self._send()
        self.active = False
        parts, failed = [], 0
        for i, chunk in enumerate(self.chunks):
            try:
                parts.append(chunk.result())
            except Exception as exc:
                failed += 1
                parts.append("[missing part]")
                print(f"  dictation chunk {i + 1} of {len(self.chunks)} failed: {exc}")
        text = " ".join(p for p in parts if p)
        text = self.stop_rx.sub("", text).strip(" ,")
        text = fix_vocab(strip_prompt(text)) if text else ""
        if text:
            with open(HISTORY, "a", encoding="utf-8") as f:
                entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "text": text, **({"failed_parts": failed} if failed else {})}
                f.write(json.dumps(entry) + "\n")
        return text, failed, len(self.chunks)

    def _transcribe(self, audio, part):
        wav = io.BytesIO()
        sf.write(wav, audio, self.rate, format="WAV")
        for attempt in range(3):
            try:
                t = time.time()
                openai_key, openrouter_key = self.get_key()
                if openai_key:
                    r = requests.post("https://api.openai.com/v1/audio/transcriptions",
                                      headers={"Authorization": f"Bearer {openai_key}"},
                                      data={"model": MODEL.split("/")[1], "language": "en", "prompt": PROMPT},
                                      files={"file": ("dictation.wav", wav.getvalue(), "audio/wav")}, timeout=90)
                else:
                    r = requests.post("https://openrouter.ai/api/v1/audio/transcriptions",
                                      headers={"Authorization": f"Bearer {openrouter_key}"},
                                      json={"model": MODEL, "language": "en", "provider": {"options": {"openai": {"prompt": PROMPT}}},
                                            "input_audio": {"data": base64.b64encode(wav.getvalue()).decode(), "format": "wav"}},
                                      timeout=90)
                if not r.ok:
                    print(f"  {'openai' if openai_key else 'openrouter'} said: {r.status_code} {r.text[:200]}")
                r.raise_for_status()
                text = r.json().get("text", "").strip()
                print(f"  dictation chunk {len(audio) / self.rate:.0f}s -> {len(text.split())} words  {int((time.time() - t) * 1000)}ms")
                return text
            except Exception as exc:
                if attempt == 2 or (isinstance(exc, requests.HTTPError) and exc.response.status_code < 500
                                    and exc.response.status_code != 429):
                    os.makedirs(FAILED_DIR, exist_ok=True)
                    sf.write(os.path.join(FAILED_DIR, f"{self.stamp} part {part}.wav"), audio, self.rate)
                    raise
                time.sleep(2 ** attempt)
