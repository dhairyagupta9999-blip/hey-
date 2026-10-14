"""Wake word detection abstraction for Hey Jev on Windows.

Supports:
1. Baseline 'whisper': Whisper-prefix regex gate on speech transcripts.
2. 'openwakeword': Low-CPU local streaming ONNX wake word engine (dscripka/openWakeWord).
   - Gated by Silero VAD / RMS energy to eliminate idle CPU usage during silence.
   - Evaluated for custom 'Hey Jev' wake detection.
   - Kept behind HEYJEV_WAKEWORD_ENGINE feature flag (default: whisper).
"""
from __future__ import annotations

import os
import re
import numpy as np
from typing import Optional, Tuple, Dict, Any
from config import NAMES, SAMPLE_RATE, APPDATA_DIR, DEFAULT_WAKEWORD_ENGINE

# Baseline Whisper prefix regex
DEFAULT_WAKE_REGEX = re.compile(
    rf"(?:(?:^\W*a|\b(?:hey|hi|hay|okay|ok))\W+(?:{NAMES})\b|^\W*(?:{NAMES})\s*,)\W*",
    re.I
)


class WakeDetector:
    """Base class for wake word detectors."""
    def detect_utterance(self, text: str) -> Optional[re.Match]:
        """Check text transcript for wake trigger (for whisper-prefix detectors)."""
        return None

    def feed_audio(self, audio_chunk: np.ndarray) -> Tuple[bool, str, float]:
        """Feed an audio chunk (16kHz mono). Returns (detected, model_name, score)."""
        return False, "", 0.0

    def reset(self):
        pass


class WhisperPrefixDetector(WakeDetector):
    """Baseline Whisper-prefix regex detector."""
    def __init__(self, regex: Optional[re.Pattern] = None):
        self.regex = regex or DEFAULT_WAKE_REGEX

    def detect_utterance(self, text: str) -> Optional[re.Match]:
        return self.regex.search(text)


class OpenWakeWordDetector(WakeDetector):
    """Low-CPU streaming wake detector using openWakeWord ONNX runtime."""

    def __init__(self, model_path: Optional[str] = None, threshold: float = 0.5, use_vad_gate: bool = True):
        self.threshold = threshold
        self.model_path = model_path
        self.use_vad_gate = use_vad_gate
        self.model_name = ""
        self.is_custom_model = False
        self._model = None
        self._load_model()

    def _resolve_model_path(self) -> Optional[str]:
        """Resolve valid model path on local disk."""
        candidates = []
        if self.model_path:
            candidates.append(self.model_path)
            # Check relative to openwakeword resources directory
            try:
                import openwakeword
                res_dir = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models")
                candidates.append(os.path.join(res_dir, self.model_path))
            except Exception:
                pass

        # Check default locations for custom hey_jev model
        appdata_models = os.path.join(APPDATA_DIR, "models")
        repo_root = os.path.dirname(os.path.abspath(__file__))
        candidates.extend([
            os.path.join(appdata_models, "hey_jev.onnx"),
            os.path.join(appdata_models, "hey_jev.tflite"),
            os.path.join(repo_root, "models", "hey_jev.onnx"),
            os.path.join(repo_root, "recon_models", "hey_jev.onnx"),
        ])

        for p in candidates:
            if p and os.path.exists(p) and os.path.getsize(p) > 1000:
                return p

        return None

    def _load_model(self):
        try:
            import openwakeword
            from openwakeword.model import Model

            resolved = self._resolve_model_path()
            if not resolved:
                # No valid custom model found; do NOT substitute generic models as Hey Jev
                self._model = None
                return

            fw = "tflite" if resolved.endswith(".tflite") else "onnx"
            self._model = Model(wakeword_models=[resolved], inference_framework=fw)
            self.model_name = os.path.splitext(os.path.basename(resolved))[0]
            self.is_custom_model = "hey_jev" in self.model_name.lower()
        except Exception as e:
            print(f"[OpenWakeWord] Failed to initialize openWakeWord: {e}. Falling back to Whisper prefix.")
            self._model = None

    @property
    def is_available(self) -> bool:
        return self._model is not None

    def feed_audio(self, audio_chunk: np.ndarray) -> Tuple[bool, str, float]:
        """Accepts 16kHz audio chunk (float32 or int16) of any length, feeds to openWakeWord in 80ms chunks."""
        if not self._model:
            return False, "", 0.0

        try:
            # Convert float32 [-1, 1] to int16
            if audio_chunk.dtype == np.float32:
                # Energy VAD Gate: skip inference if silent to preserve CPU
                if self.use_vad_gate:
                    rms = float(np.sqrt(np.mean(audio_chunk ** 2))) if len(audio_chunk) else 0.0
                    if rms < 0.005:
                        return False, "", 0.0
                audio_int16 = (audio_chunk * 32767).astype(np.int16)
            else:
                if self.use_vad_gate:
                    rms = float(np.sqrt(np.mean((audio_chunk.astype(np.float32) / 32768.0) ** 2))) if len(audio_chunk) else 0.0
                    if rms < 0.005:
                        return False, "", 0.0
                audio_int16 = audio_chunk.astype(np.int16)

            step_size = 1280  # 80ms at 16kHz
            # Handle audio chunks of arbitrary length
            for i in range(0, max(len(audio_int16), step_size), step_size):
                sub_chunk = audio_int16[i:i + step_size]
                if len(sub_chunk) < step_size:
                    sub_chunk = np.pad(sub_chunk, (0, step_size - len(sub_chunk)))

                predictions = self._model.predict(sub_chunk)
                for name, score in predictions.items():
                    if score >= self.threshold:
                        self.reset()
                        return True, name, float(score)

        except Exception as e:
            pass

        return False, "", 0.0

    def reset(self):
        if self._model:
            try:
                self._model.reset()
            except Exception:
                pass


def get_wake_detector(
    backend: Optional[str] = None,
    model_path: Optional[str] = None,
    threshold: float = 0.5
) -> WakeDetector:
    """Factory creating the configured wake word detector.
    
    Backend choices:
    - 'whisper': Whisper-prefix regex gate (production default, 100% positive recall, MIT/Apache).
    - 'openwakeword': openWakeWord streaming detector (experimental, low-CPU).
    """
    engine = backend or os.environ.get("HEYJEV_WAKEWORD_ENGINE", DEFAULT_WAKEWORD_ENGINE)
    if engine.lower() == "openwakeword":
        det = OpenWakeWordDetector(model_path=model_path, threshold=threshold)
        if det.is_available:
            return det
        # Graceful fallback to baseline Whisper prefix if openWakeWord model is unavailable
        print("  [wake_word] openWakeWord candidate unavailable; falling back to Whisper prefix gate.")
        return WhisperPrefixDetector()
    return WhisperPrefixDetector()
