"""Bounded Local Voice Activity Detection (VAD) and Turn Endpointing for Hey Jev.

Architecture:
1. Continuous frame-by-frame audio input (WASAPI capture).
2. Preroll buffering (300ms) to protect word onsets.
3. Neural Silero VAD (ONNX CPU, 512-sample / 32ms chunks) for speech/non-speech classification.
4. Deterministic fallback to dynamic RMS energy threshold if ONNX/model is unavailable.
5. Bounded silence tail:
   - Neural VAD silence tail: VAD_SILENCE_MS (500ms).
   - Hard finish ceiling: FINISH_MS (800ms).
   - Energy fallback silence tail: FALLBACK_SILENCE_MS (800ms).
6. Smart Turn pluggable hook: can shorten turn finalization, but can NEVER extend past FINISH_MS.
7. Fast-path interrupt recognition for 'stop', 'wait', 'cancel', 'stop that', 'wait a second', 'hold on'.
"""
from __future__ import annotations

import os
import sys
import time
import collections
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

# ----------------- Timing and Safety Constants -----------------
SAMPLE_RATE = 16000
VAD_FRAME_SIZE = 512           # 32ms at 16kHz
FRAME_DURATION_MS = 32.0       # 512 / 16000 * 1000 = 32ms
FINISH_MS = 800                # Hard maximum finish ceiling (absolute max wait after speech ends)
VAD_SILENCE_MS = 500           # Target silence tail when neural VAD is active
FALLBACK_SILENCE_MS = 800      # Fallback silence tail when using dynamic energy RMS
PREROLL_MS = 300               # Audio prepended before speech onset to prevent clipping (~10 frames)
MIN_SPEECH_MS = 250            # Minimum speech duration required to accept a segment (~8 frames)
MAX_UTTERANCE_MS = 15000       # 15s maximum phrase length (30s in dictation)

INTERRUPT_PHRASES = (
    "stop",
    "wait",
    "cancel",
    "halt",
    "abort",
    "stop that",
    "wait a second",
    "hold on",
)

def is_interrupt_phrase(text: str) -> bool:
    """Return True if text matches an immediate emergency interrupt phrase."""
    cleaned = text.lower().strip(" .,!?\"'")
    return cleaned in INTERRUPT_PHRASES


# ----------------- Model Resolution -----------------
def get_silero_model_path() -> Optional[str]:
    """Resolve local path to silero_vad.onnx model weight file."""
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    candidates = [
        os.path.join(appdata, "HeyJev", "models", "silero_vad.onnx"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "recon_models", "silero_vad.onnx"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "silero_vad.onnx"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "models", "silero_vad.onnx"),
    ]
    for p in candidates:
        if os.path.exists(p) and os.path.getsize(p) > 10000:
            return p
    return None


# ----------------- Silero VAD ONNX Engine -----------------
class SileroVad:
    """Zero-PyTorch Silero VAD running locally via ONNX Runtime CPU Provider."""

    def __init__(self, model_path: Optional[str] = None, threshold: float = 0.5):
        self.model_path = model_path or get_silero_model_path()
        self.threshold = threshold
        self.session = None
        self._h = None
        self._c = None
        self.available = False
        self._init_session()

    def _init_session(self):
        if not self.model_path or not os.path.exists(self.model_path):
            self.available = False
            return
        try:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self.session = ort.InferenceSession(
                self.model_path,
                providers=["CPUExecutionProvider"],
                sess_options=opts
            )
            self.reset()
            self.available = True
        except Exception as exc:
            print(f"  [silero_vad] Failed to initialize ONNX session: {exc}")
            self.session = None
            self.available = False

    def reset(self):
        """Reset internal recurrent states."""
        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)

    def is_speech(self, chunk_f32: np.ndarray) -> Tuple[bool, float]:
        """Compute speech probability for 512 samples of 16kHz float32 audio.
        
        Returns (is_speech_bool, probability_float).
        """
        if not self.available or self.session is None:
            return False, 0.0

        if len(chunk_f32) != VAD_FRAME_SIZE:
            if len(chunk_f32) < VAD_FRAME_SIZE:
                chunk_f32 = np.pad(chunk_f32, (0, VAD_FRAME_SIZE - len(chunk_f32)))
            else:
                chunk_f32 = chunk_f32[:VAD_FRAME_SIZE]

        x = np.expand_dims(chunk_f32.astype(np.float32), axis=0)
        try:
            outputs = self.session.run(None, {"x": x, "h": self._h, "c": self._c})
            prob = float(outputs[0][0][0])
            self._h = outputs[1]
            self._c = outputs[2]
            return prob >= self.threshold, prob
        except Exception as exc:
            print(f"  [silero_vad] Inference error: {exc}")
            return False, 0.0


# ----------------- Pluggable Turn Endpoint Detector -----------------
class TurnEndpointDetector:
    """State machine governing utterance start, speech buffering, and bounded finalization."""

    STATE_IDLE = "IDLE"
    STATE_SPEECH = "SPEECH"
    STATE_TRAILING_SILENCE = "TRAILING_SILENCE"

    def __init__(
        self,
        use_neural_vad: bool = True,
        vad_model_path: Optional[str] = None,
        finish_ms: int = FINISH_MS,
        vad_silence_ms: int = VAD_SILENCE_MS,
        smart_turn_analyzer: Optional[Any] = None,
    ):
        self.finish_ms = finish_ms
        self.vad_silence_ms = vad_silence_ms
        self.smart_turn_analyzer = smart_turn_analyzer
        self.max_utterance_ms = MAX_UTTERANCE_MS

        # Silero VAD engine
        self.vad = SileroVad(vad_model_path) if use_neural_vad else None
        self.has_neural_vad = bool(self.vad and self.vad.available)

        # Fallback dynamic energy state
        self.noise_floor = 0.005

        # Preroll buffer (300ms = 10 frames of 32ms)
        preroll_len = max(1, int(round(PREROLL_MS / FRAME_DURATION_MS)))
        self.preroll_chunks: collections.deque = collections.deque(maxlen=preroll_len)

        # Incoming sample buffer for arbitrary block sizes
        self._input_buffer: np.ndarray = np.zeros(0, dtype=np.float32)

        # Utterance accumulator
        self.speech_chunks: List[np.ndarray] = []
        self.state = self.STATE_IDLE
        self.speech_frames = 0
        self.silent_frames = 0
        self.consecutive_silent_frames = 0
        self.smart_turn_evaluated_for_pause = False

    def set_dictating(self, is_dictating: bool):
        """Set dictation mode which allows longer utterance duration (30s vs 15s)."""
        self.max_utterance_ms = 30000 if is_dictating else 15000

    def reset(self):
        """Reset state machine and buffers."""
        self.state = self.STATE_IDLE
        self.speech_chunks.clear()
        self.preroll_chunks.clear()
        self._input_buffer = np.zeros(0, dtype=np.float32)
        self.speech_frames = 0
        self.silent_frames = 0
        self.consecutive_silent_frames = 0
        self.smart_turn_evaluated_for_pause = False
        if self.has_neural_vad and self.vad:
            self.vad.reset()

    def process_audio(self, audio: np.ndarray) -> Optional[np.ndarray]:
        """Process an arbitrary-length audio chunk by slicing into 512-sample frames."""
        if audio is None or len(audio) == 0:
            return None
        self._input_buffer = np.concatenate([self._input_buffer, audio.astype(np.float32)])
        finalized_segment = None
        while len(self._input_buffer) >= VAD_FRAME_SIZE:
            frame = self._input_buffer[:VAD_FRAME_SIZE]
            self._input_buffer = self._input_buffer[VAD_FRAME_SIZE:]
            result = self.process_frame(frame)
            if result is not None:
                finalized_segment = result
                break
        return finalized_segment

    def process_frame(self, chunk_512: np.ndarray) -> Optional[np.ndarray]:
        """Process one 512-sample (32ms at 16kHz) audio frame.
        
        Returns:
            np.ndarray: Finalized audio buffer if turn endpoint reached, or None.
        """
        chunk = chunk_512.astype(np.float32)

        # Classify speech
        is_speech = False
        prob = 0.0
        if self.has_neural_vad and self.vad:
            try:
                is_speech, prob = self.vad.is_speech(chunk)
            except Exception as exc:
                print(f"  [TurnEndpointDetector] VAD inference error: {exc}, disabling neural VAD")
                self.has_neural_vad = False
                is_speech = False

        if not self.has_neural_vad or not self.vad:
            # Deterministic dynamic RMS energy fallback
            rms = float(np.sqrt(np.mean(chunk ** 2)))
            is_speech = rms > max(self.noise_floor * 3, 0.01)
            if not is_speech and self.state == self.STATE_IDLE:
                self.noise_floor = 0.95 * self.noise_floor + 0.05 * rms

        # State 1: IDLE (waiting for speech onset)
        if self.state == self.STATE_IDLE:
            if is_speech:
                self.state = self.STATE_SPEECH
                self.speech_frames = 1
                self.silent_frames = 0
                self.consecutive_silent_frames = 0
                self.smart_turn_evaluated_for_pause = False
                # Prepend preroll
                self.speech_chunks = list(self.preroll_chunks) + [chunk]
            else:
                self.preroll_chunks.append(chunk)
            return None

        # State 2: SPEECH or TRAILING_SILENCE (actively buffering utterance)
        self.speech_chunks.append(chunk)

        if is_speech:
            self.state = self.STATE_SPEECH
            self.speech_frames += 1
            self.consecutive_silent_frames = 0
            self.smart_turn_evaluated_for_pause = False
        else:
            if self.state == self.STATE_SPEECH:
                self.state = self.STATE_TRAILING_SILENCE
                self.smart_turn_evaluated_for_pause = False
            self.silent_frames += 1
            self.consecutive_silent_frames += 1

        silence_duration_ms = self.consecutive_silent_frames * FRAME_DURATION_MS
        speech_duration_ms = self.speech_frames * FRAME_DURATION_MS
        utterance_duration_ms = (self.speech_frames + self.silent_frames) * FRAME_DURATION_MS

        # Optional Smart Turn completion trigger (evaluates once at ~200ms silence)
        if (
            self.state == self.STATE_TRAILING_SILENCE
            and self.smart_turn_analyzer is not None
            and not self.smart_turn_evaluated_for_pause
            and silence_duration_ms >= 190.0
        ):
            self.smart_turn_evaluated_for_pause = True
            try:
                accumulated = np.concatenate(self.speech_chunks)
                decision = self.smart_turn_analyzer.predict_turn_complete(accumulated)
                if decision:  # COMPLETE: shorten silence wait!
                    return self._finalize()
            except Exception:
                # Fallback to deterministic timeout on any Smart Turn failure
                pass

        # Finalization trigger conditions:
        # Condition A: Neural VAD silence tail reached
        vad_tail_met = self.has_neural_vad and (silence_duration_ms >= self.vad_silence_ms)
        
        # Condition B: Energy fallback silence tail reached
        fallback_tail_met = (not self.has_neural_vad) and (silence_duration_ms >= FALLBACK_SILENCE_MS)
        
        # Condition C: Hard finish ceiling FINISH_MS reached (strictly bounds any wait)
        finish_ceiling_met = silence_duration_ms >= self.finish_ms
        
        # Condition D: Max utterance duration reached
        max_duration_met = utterance_duration_ms >= self.max_utterance_ms

        if (vad_tail_met or fallback_tail_met or finish_ceiling_met or max_duration_met):
            # Check minimum speech duration (discard clicks/short bursts)
            if speech_duration_ms >= MIN_SPEECH_MS:
                return self._finalize()
            else:
                self.reset()
                return None

        return None

    def _finalize(self) -> np.ndarray:
        """Produce finalized audio segment and reset state."""
        if not self.speech_chunks:
            self.reset()
            return np.zeros(0, dtype=np.float32)
        segment = np.concatenate(self.speech_chunks)
        self.reset()
        return segment

    def is_speech_active(self) -> bool:
        """Return True if currently buffering an in-flight utterance."""
        return self.state in (self.STATE_SPEECH, self.STATE_TRAILING_SILENCE)
