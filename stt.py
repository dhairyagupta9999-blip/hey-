"""Speech-to-Text abstraction and backends (faster-whisper and Whistle) for Hey Jev."""
from __future__ import annotations

import os
import sys
import time
import json
from abc import ABC, abstractmethod
import psutil
import numpy as np

from config import (
    WHISPER_MODEL, DEFAULT_STT_ENGINE, NO_SPEECH_MAX, SAMPLE_RATE,
    SETTINGS_FILE, USER_VOCAB_FILE, DEFAULT_VOCAB_FILE
)

def get_cpu_threads() -> int:
    """Choose optimal CPU threads to avoid hyperthreading contention on CPU."""
    try:
        physical = psutil.cpu_count(logical=False)
        return max(1, min(physical or 2, 4))
    except Exception:
        return 2

def trim_silence(audio: np.ndarray, threshold: float = 0.01, frame_ms: int = 50, margin_ms: int = 150) -> np.ndarray:
    """Trim leading and trailing silence from audio buffer using energy (RMS)."""
    if len(audio) == 0:
        return audio
    frame_len = int(SAMPLE_RATE * frame_ms / 1000)
    margin = int(SAMPLE_RATE * margin_ms / 1000)
    if len(audio) < frame_len:
        return audio
    rms = [np.sqrt(np.mean(audio[i:i + frame_len] ** 2)) for i in range(0, len(audio) - frame_len + 1, frame_len)]
    active = [i for i, r in enumerate(rms) if r > threshold]
    if not active:
        return audio
    start = max(0, active[0] * frame_len - margin)
    end = min(len(audio), (active[-1] + 1) * frame_len + margin)
    return audio[start:end]

def load_vocabulary_words() -> list[str]:
    """Load user vocabulary words from vocabulary.json (Dictionary tab)."""
    words = []
    p = USER_VOCAB_FILE if os.path.exists(USER_VOCAB_FILE) else DEFAULT_VOCAB_FILE
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    words.append(k)
                    if isinstance(v, list):
                        for item in v:
                            if isinstance(item, str):
                                words.append(item)
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, list) and item:
                        words.append(item[-1])
                    elif isinstance(item, str):
                        words.append(item)
        except Exception:
            pass
    seen = set()
    deduped = []
    for w in words:
        if w and w not in seen:
            seen.add(w)
            deduped.append(w)
    return deduped


DEFAULT_INITIAL_PROMPT = "Hey Jev, a command for my Windows assistant."

def get_default_initial_prompt() -> str:
    """Return default initial prompt for faster-whisper (single natural sentence)."""
    return DEFAULT_INITIAL_PROMPT

def get_default_hotwords() -> str:
    """Build a deterministic, bounded hotwords string from base keywords and app index.
    
    Includes base command phrase 'Jev lock the screen' and dynamically appends
    sanitized high-priority application names (max 15) without leaking paths,
    usernames, or unrelated metadata.
    """
    base_phrase = "Jev lock the screen"
    try:
        import app_index
        import getpass
        import re
        
        idx = app_index.get_app_index()
        curr_user = getpass.getuser().lower()
        candidates = []
        for app in idx.apps:
            p = app.get("priority", 0)
            name = app.get("name", "").strip()
            if p >= 80 or name.lower() in ("notepad", "calculator", "task manager", "settings", "command prompt"):
                if re.match(r"^[A-Za-z0-9][A-Za-z0-9 ]{1,24}$", name):
                    n_low = name.lower()
                    if curr_user not in n_low and not any(ext in n_low for ext in (".exe", ".lnk", ".bat", "\\", "/", ":")):
                        candidates.append(name)
        unique_sorted = sorted(list(dict.fromkeys(candidates)))[:15]
        if unique_sorted:
            return f"{base_phrase} " + " ".join(unique_sorted)
        return base_phrase
    except Exception:
        return base_phrase


class STTBackend(ABC):
    """Abstract speech-to-text backend interface."""

    @abstractmethod
    def transcribe(
        self,
        audio: np.ndarray,
        prompt: str | None = None,
        drop_noise: bool = False,
        hotwords: str | None = None
    ) -> tuple[str, int]:
        """Transcribe 16 kHz mono float32 numpy audio. Returns (text, latency_ms)."""
        pass

    @abstractmethod
    def warmup(self) -> None:
        """Warm up engine and models on a silent buffer at startup."""
        pass


class FasterWhisperBackend(STTBackend):
    """Faster-whisper on-device speech-to-text engine (tiny.en, base.en, small.en)."""

    def __init__(
        self,
        model_size: str = "small.en",
        initial_prompt: str | None = DEFAULT_INITIAL_PROMPT,
        hotwords: str | None = None
    ):
        self.model_size = model_size
        self._model = None
        self.initial_prompt = initial_prompt
        self.hotwords = hotwords

    def get_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            threads = get_cpu_threads()
            print(f"  [stt] Loading Whisper model ({self.model_size}, cpu, int8, cpu_threads={threads})...")
            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8", cpu_threads=threads)
            print(f"  [stt] Whisper {self.model_size} loaded.")
        return self._model

    def warmup(self) -> None:
        t0 = time.time()
        model = self.get_model()
        try:
            warm_buf = np.zeros(SAMPLE_RATE, dtype=np.float32)
            list(model.transcribe(warm_buf, language="en", beam_size=1, vad_filter=True)[0])
            dur = int((time.time() - t0) * 1000)
            print(f"  [stt] Whisper ({self.model_size}) warmed up ({dur}ms).")
        except Exception as exc:
            print(f"  [stt] Whisper warmup warning: {exc}")

    def transcribe(
        self,
        audio: np.ndarray,
        prompt: str | None = None,
        drop_noise: bool = False,
        hotwords: str | None = None
    ) -> tuple[str, int]:
        t0 = time.time()
        audio_secs = len(audio) / float(SAMPLE_RATE) if len(audio) else 0.0
        trimmed_audio = trim_silence(audio)
        if trimmed_audio.dtype != np.float32:
            trimmed_audio = trimmed_audio.astype(np.float32)
        trimmed_secs = len(trimmed_audio) / float(SAMPLE_RATE) if len(trimmed_audio) else 0.0

        # Effective prompt and hotwords
        effective_prompt = prompt if prompt is not None else self.initial_prompt
        effective_hotwords = hotwords if hotwords is not None else self.hotwords

        model = self.get_model()
        transcribe_kwargs = {
            "language": "en",
            "beam_size": 1,
            "best_of": 1,
            "vad_filter": True,
            "without_timestamps": True,
            "condition_on_previous_text": False
        }
        if effective_prompt:
            transcribe_kwargs["initial_prompt"] = effective_prompt
        if effective_hotwords:
            transcribe_kwargs["hotwords"] = effective_hotwords

        segs, _ = model.transcribe(trimmed_audio, **transcribe_kwargs)
        segs = [s for s in segs if not drop_noise or s.no_speech_prob <= NO_SPEECH_MAX]
        text = " ".join(s.text.strip() for s in segs).strip()
        latency_ms = int((time.time() - t0) * 1000)
        print(f"  [stt-whisper] {latency_ms}ms ({audio_secs:.2f}s audio, trimmed to {trimmed_secs:.2f}s)")
        return text, latency_ms


class WhistleBackend(STTBackend):
    """Whistle (Cactus Compute) on-device speech-to-text engine with keyword biasing."""


    def __init__(self, fallback_model_size: str = "small.en"):
        self.fallback = FasterWhisperBackend(model_size=fallback_model_size)
        self._needle = None
        self._init_engine()

    def _init_engine(self):
        try:
            import needle
            self._needle = needle
        except Exception as exc:
            print(f"  [stt] Failed to initialize Whistle backend: {exc}")
            self._needle = None

    def get_keywords(self) -> list[str]:
        base_kws = ["Jev", "Hey Jev"]
        vocab = load_vocabulary_words()
        return base_kws + vocab

    def warmup(self) -> None:
        t0 = time.time()
        if self._needle is not None:
            try:
                warm_buf = np.zeros(SAMPLE_RATE, dtype=np.float32)
                self._needle.transcribe(warm_buf, language="en", keywords=self.get_keywords())
                dur = int((time.time() - t0) * 1000)
                print(f"  [stt] Whistle model warmed up ({dur}ms).")
                return
            except Exception as exc:
                print(f"  [stt] Whistle warmup failed: {exc}, warming fallback Whisper...")
        self.fallback.warmup()

    def transcribe(
        self,
        audio: np.ndarray,
        prompt: str | None = None,
        drop_noise: bool = False,
        hotwords: str | None = None
    ) -> tuple[str, int]:
        if self._needle is None:
            return self.fallback.transcribe(audio, prompt=prompt, drop_noise=drop_noise, hotwords=hotwords)

        t0 = time.time()
        audio_secs = len(audio) / float(SAMPLE_RATE) if len(audio) else 0.0
        try:
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32)
            res = self._needle.transcribe(audio, language="en", keywords=self.get_keywords())
            text = res.get("text", "").strip()
            latency_ms = int((time.time() - t0) * 1000)
            print(f"  [stt-whistle] {latency_ms}ms ({audio_secs:.2f}s audio)")
            return text, latency_ms
        except Exception as exc:
            print(f"  [stt] Whistle runtime failure: {exc}. Falling back to faster-whisper.")
            return self.fallback.transcribe(audio, prompt=prompt, drop_noise=drop_noise, hotwords=hotwords)


_ACTIVE_BACKEND: STTBackend | None = None
_ACTIVE_CONFIG: tuple[str, str, str | None, str | None] | None = None

def get_stt_backend(
    engine: str | None = None,
    whisper_model: str | None = None,
    initial_prompt: str | None = None,
    hotwords: str | None = None
) -> STTBackend:
    """Resolve and return active STT backend instance."""
    global _ACTIVE_BACKEND, _ACTIVE_CONFIG

    # Resolve engine
    if engine is None:
        engine = os.environ.get("HEYJEV_STT")
        if not engine and os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    engine = json.load(f).get("stt_engine")
            except Exception:
                pass
    if not engine:
        engine = DEFAULT_STT_ENGINE

    # Resolve whisper model
    if whisper_model is None:
        whisper_model = os.environ.get("HEYJEV_WHISPER_MODEL")
        if not whisper_model and os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    whisper_model = json.load(f).get("whisper_model")
            except Exception:
                pass
    if not whisper_model:
        whisper_model = WHISPER_MODEL

    # Resolve initial prompt
    if initial_prompt is None:
        prompt_env = os.environ.get("HEYJEV_STT_PROMPT")
        if prompt_env in ("0", "false", "none", "no"):
            active_prompt = None
        elif prompt_env:
            active_prompt = prompt_env
        else:
            active_prompt = DEFAULT_INITIAL_PROMPT
    else:
        active_prompt = initial_prompt

    # Resolve hotwords
    if hotwords is None:
        hw_env = os.environ.get("HEYJEV_STT_HOTWORDS")
        if hw_env == "1":
            active_hotwords = get_default_hotwords()
        elif hw_env and hw_env not in ("0", "false", "none", "no"):
            active_hotwords = hw_env
        else:
            active_hotwords = None
    else:
        active_hotwords = hotwords

    config_key = (engine.lower(), whisper_model.lower(), active_prompt, active_hotwords)
    if _ACTIVE_BACKEND is not None and _ACTIVE_CONFIG == config_key:
        return _ACTIVE_BACKEND

    if config_key[0] == "whistle":
        try:
            backend = WhistleBackend(fallback_model_size=whisper_model)
        except Exception as exc:
            print(f"  [stt] Whistle backend creation failed: {exc}, using faster-whisper")
            backend = FasterWhisperBackend(
                model_size=whisper_model,
                initial_prompt=active_prompt,
                hotwords=active_hotwords
            )
    else:
        backend = FasterWhisperBackend(
            model_size=whisper_model,
            initial_prompt=active_prompt,
            hotwords=active_hotwords
        )

    _ACTIVE_BACKEND = backend
    _ACTIVE_CONFIG = config_key
    return _ACTIVE_BACKEND

def transcribe_audio(
    audio: np.ndarray,
    prompt: str | None = None,
    drop_noise: bool = False,
    hotwords: str | None = None
) -> tuple[str, int]:
    """Transcribe audio chunk via active STT backend. Returns (text, latency_ms)."""
    return get_stt_backend().transcribe(
        audio,
        prompt=prompt,
        drop_noise=drop_noise,
        hotwords=hotwords
    )

def warmup_whisper() -> None:
    """Warm up the active STT backend at startup."""
    get_stt_backend().warmup()

def get_whisper_model():
    """Backward compatibility hook for test assertions."""
    backend = get_stt_backend()
    if isinstance(backend, FasterWhisperBackend):
        return backend.get_model()
    if isinstance(backend, WhistleBackend):
        return backend.fallback.get_model()
    return None



