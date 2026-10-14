"""Model-asset gating for tests that need downloaded weights.

Several tests exercise code that only works once the speech models are present:
Kokoro synthesis, Silero VAD, faster-whisper STT. On a fresh CI runner or a
newly cloned machine those files do not exist, and the tests used to FAIL --
reporting "this machine has no model" in the same red as "this code is broken".

Same rule as tests/credentials.py: xfail, never skip, and name the exact
command that resolves it. These tests still run in full wherever the assets
are present, so nothing is silently switched off.

CI runs `python setup_assets.py --download` so the common path exercises the
real models; the gates exist so a partial or cached runner degrades honestly
rather than producing a misleading failure.
"""
from __future__ import annotations

from pathlib import Path


def _models_dir() -> Path:
    import config
    return Path(config.APPDATA_DIR) / "models"


def has_silero_vad() -> bool:
    return (_models_dir() / "silero_vad.onnx").exists()


def has_kokoro() -> bool:
    models = _models_dir()
    return any(models.glob("kokoro*.onnx")) and (models / "voices-v1.0.bin").exists()


def has_whisper_model() -> bool:
    """faster-whisper caches under %USERPROFILE%/.cache/huggingface."""
    import os
    root = os.path.expanduser("~/.cache/huggingface/hub")
    return Path(root).exists() and any(Path(root).glob("models--Systran--faster-whisper-*"))


def requires_silero_vad():
    def decorator(func):
        if has_silero_vad():
            return func
        return _xfail(func, "Silero VAD",
                      "python setup_assets.py --download")
    return decorator


def requires_kokoro():
    def decorator(func):
        if has_kokoro():
            return func
        return _xfail(func, "Kokoro TTS models",
                      "python setup_assets.py --download")
    return decorator


def requires_whisper_model():
    def decorator(func):
        if has_whisper_model():
            return func
        return _xfail(func, "faster-whisper STT model",
                      "python setup_assets.py --download  (or transcribe once)")
    return decorator


def _xfail(func, what, remedy):
    import pytest
    return pytest.mark.xfail(
        reason=f"{what} not downloaded on this machine; run: {remedy}",
        strict=False,
    )(func)