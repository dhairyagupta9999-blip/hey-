"""Audio input (WASAPI capture + VAD) and output (MP3/WAV playback) for Hey Jev."""
import io
import os
import sys
import time
import queue
import threading
import collections
import numpy as np
import sounddevice as sd
import soundfile as sf
from config import SAMPLE_RATE, WAKE_CHIME, DICTATE_CHIME, TIMER_CHIME
from endpointing import TurnEndpointDetector

MIC_LEVELS = collections.deque(maxlen=40)  # RMS loudness for dictation bubble

# --------------------------------------------------------------------------- Output: MP3 / WAV Player
def load_audio_file(path):
    """Load any audio file (WAV, MP3, FLAC, OGG) into (float32 numpy array, sample_rate)."""
    try:
        data, sr = sf.read(path, dtype="float32")
        return data, sr
    except Exception:
        # Fallback to PyAV if soundfile does not have mp3 support on this build
        import av
        container = av.open(path)
        stream = container.streams.audio[0]
        sr = stream.rate
        frames = []
        for frame in container.decode(stream):
            # Convert to float32
            arr = frame.to_ndarray()
            if arr.dtype == np.int16:
                arr = arr.astype(np.float32) / 32768.0
            elif arr.dtype == np.int32:
                arr = arr.astype(np.float32) / 2147483648.0
            frames.append(arr)
        if not frames:
            raise RuntimeError(f"Could not decode audio from {path}")
        data = np.concatenate(frames, axis=-1)
        if data.ndim == 2:
            data = data.T  # shape (samples, channels)
            if data.shape[1] == 1:
                data = data[:, 0]
        return data, sr


_PLAYBACK_LOCK = threading.Lock()
_AUDIO_PLAYING_COUNT = 0
_LAST_PLAYBACK_END_TIME = 0.0


def set_playback_active(active: bool):
    global _AUDIO_PLAYING_COUNT, _LAST_PLAYBACK_END_TIME
    with _PLAYBACK_LOCK:
        if active:
            _AUDIO_PLAYING_COUNT += 1
        else:
            _AUDIO_PLAYING_COUNT = max(0, _AUDIO_PLAYING_COUNT - 1)
            _LAST_PLAYBACK_END_TIME = time.time()


def is_audio_playing_or_settling(settle_s: float = 0.700) -> bool:
    """Return True if TTS or chime is currently playing or within 700ms after."""
    with _PLAYBACK_LOCK:
        if _AUDIO_PLAYING_COUNT > 0:
            return True
        return (time.time() - _LAST_PLAYBACK_END_TIME) < settle_s


def reset_playback_state():
    """Reset playback tracking state (primarily for unit testing)."""
    global _AUDIO_PLAYING_COUNT, _LAST_PLAYBACK_END_TIME
    with _PLAYBACK_LOCK:
        _AUDIO_PLAYING_COUNT = 0
        _LAST_PLAYBACK_END_TIME = 0.0


def play_audio(path, blocking=True):
    """Play a WAV or MP3 audio file cleanly via sounddevice without winsound."""
    if not os.path.exists(path):
        print(f"  [audio_player] File not found: {path}")
        return
    if not blocking:
        threading.Thread(target=play_audio, args=(path, True), daemon=True).start()
        return

    set_playback_active(True)
    try:
        data, sr = load_audio_file(path)
        sd.play(data, sr)
        sd.wait()
    finally:
        set_playback_active(False)


def play_chime(kind):
    """Play wake, pop, or timer chimes."""
    chimes = {
        "wake": WAKE_CHIME,
        "pop": DICTATE_CHIME,
        "timer": TIMER_CHIME,
    }
    path = chimes.get(kind)
    if path and os.path.exists(path):
        play_audio(path, blocking=False)


# --------------------------------------------------------------------------- Input: WASAPI Capture + VAD
class Recorder:
    BLOCK = 1600  # 100ms at 16kHz

    def __init__(self, mic="", use_vad=True):
        self.frames = []
        self.on = False
        self.wake = False
        self.paused = False
        self.dictating = False
        self.segments = queue.Queue()
        self.noise = 0.005
        self.detector = TurnEndpointDetector(use_neural_vad=use_vad)
        self._reset_segment()
        self.mic_lock = threading.Lock()
        self.stream = self._open(mic)

    def _open(self, mic):
        kwargs = {
            "samplerate": SAMPLE_RATE,
            "channels": 1,
            "dtype": "float32",
            "blocksize": self.BLOCK,
            "callback": self._cb,
        }
        if mic:
            try:
                return sd.InputStream(device=mic, **kwargs)
            except Exception as exc:
                print(f"\n[mic {mic!r} not available, using default: {exc}]")
        return sd.InputStream(**kwargs)

    def set_device(self, mic):
        """Swap to another microphone, carrying on listening if active."""
        with self.mic_lock:
            was_active = self.stream.active
            self.stream.close()
            self.stream = self._open(mic)
            if was_active:
                self._reset_segment()
                self.stream.start()
        print(f"\n[mic: {mic or 'system default'}]")

    def sync_mic(self):
        """Only keep WASAPI stream active when listening/dictating/recording."""
        with self.mic_lock:
            needed = self.wake or self.dictating or self.on
            if needed and not self.stream.active:
                self._reset_segment()
                self.stream.start()
            elif not needed and self.stream.active:
                self.stream.stop()

    def _reset_segment(self):
        self.speech = []
        self.silent = 0
        self.preroll = collections.deque(maxlen=3)
        if hasattr(self, "detector") and self.detector:
            self.detector.reset()

    def _cb(self, indata, frames, time_info, status):
        # Guard a: Pause microphone processing while TTS or chimes play, and for 700 ms after.
        if is_audio_playing_or_settling():
            if self.speech or self.detector.is_speech_active():
                self._reset_segment()
            return
        if self.on:
            self.frames.append(indata.copy())
        if not (self.wake or self.dictating) or self.paused:
            if self.speech or self.detector.is_speech_active():
                self._reset_segment()
            return

        block = indata[:, 0].copy()
        rms = float(np.sqrt(np.mean(block ** 2)))
        if self.dictating:
            MIC_LEVELS.append(rms)

        self.detector.set_dictating(self.dictating)
        segment = self.detector.process_audio(block)
        self.speech = self.detector.speech_chunks if self.detector.is_speech_active() else []
        if segment is not None and len(segment) > 0:
            self.segments.put(segment)
            self._reset_segment()

    def start(self):
        self.frames = []
        self.on = True
        self.sync_mic()

    def stop(self):
        self.on = False
        audio = np.concatenate(self.frames)[:, 0] if self.frames else np.zeros(0, dtype="float32")
        self.sync_mic()
        return audio
