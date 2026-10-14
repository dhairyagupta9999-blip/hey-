"""Generate crisp, pleasant chime wav assets for Hey Jev."""
import os
import numpy as np
import soundfile as sf
from config import WAKE_CHIME, DICTATE_CHIME, TIMER_CHIME, SOUNDS_DIR

def make_chimes():
    os.makedirs(SOUNDS_DIR, exist_ok=True)
    sr = 44100

    # 1. Wake chime: D5 (587.33 Hz) -> A5 (880.00 Hz)
    t1 = np.linspace(0, 0.08, int(sr * 0.08), False)
    t2 = np.linspace(0, 0.16, int(sr * 0.16), False)
    env1 = np.sin(np.pi * np.linspace(0, 1, len(t1))) ** 2
    env2 = np.exp(-12 * t2)
    s1 = 0.25 * np.sin(2 * np.pi * 587.33 * t1) * env1
    s2 = 0.35 * np.sin(2 * np.pi * 880.00 * t2) * env2
    wake = np.concatenate([s1, s2]).astype(np.float32)
    sf.write(WAKE_CHIME, wake, sr)

    # 2. Pop chime: short blip for dictation bubble
    t = np.linspace(0, 0.04, int(sr * 0.04), False)
    freq = np.geomspace(1200, 400, len(t))
    env = np.sin(np.pi * np.linspace(0, 1, len(t))) ** 2
    phase = 2 * np.pi * np.cumsum(freq) / sr
    pop = (0.25 * np.sin(phase) * env).astype(np.float32)
    sf.write(DICTATE_CHIME, pop, sr)

    # 3. Timer chime: C5 -> E5 -> G5 major chord resolution
    tones = []
    for freq in [523.25, 659.25, 783.99]:
        dur = 0.15
        tt = np.linspace(0, dur, int(sr * dur), False)
        env = np.exp(-6 * tt)
        tone = 0.3 * np.sin(2 * np.pi * freq * tt) * env
        tones.append(tone)
    timer = np.concatenate(tones).astype(np.float32)
    sf.write(TIMER_CHIME, timer, sr)
    print("Generated sound assets successfully in", SOUNDS_DIR)

if __name__ == "__main__":
    make_chimes()
