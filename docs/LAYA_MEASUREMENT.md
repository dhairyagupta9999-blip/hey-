# Laya measured on this machine — an honest result

Checkpoint: `multilingual` (mmBERT-base, 322M params). Single checkpoint, no
preload. Run against the repo's **real** 13-question battery from `siri.QUESTIONS`.

Hardware: Intel Core **i3-2350M**, 2 physical / 4 logical cores, Sandy Bridge
(2011). **No AVX2** — that instruction set arrived with Haswell in 2013.

## Result: it works, and it is unusable here

| Metric | Value |
|---|---|
| Cold load | **0.5 s** (good) |
| Warm median | **117.6 s** per turn |
| Warm max | **189.8 s** per turn |

The advertised figure is ~33 ms. On this CPU the real number is **~3,500×
slower** than a turn should take.

## Per-utterance results

| Utterance | ms | category | target | conf |
|---|---|---|---|---|
| open calculator | 103,473 | mac_command | **browser** ✗ | 0.51 |
| open spotify | 48,149 | **unclear** ✗ | browser | 0.25 |
| set volume to 50 | 46,449 | **unclear** ✗ | volume | 0.69 |
| pause | 35,948 | **unclear** ✗ | system | 0.29 |
| turn the volume down | 131,555 | mac_command | volume | 0.00 |
| what is the capital of France | 189,824 | information_request | system | 0.20 |
| set a timer for five minutes | 131,664 | mac_command | timer | 0.48 |
| open youtube in chrome | 117,596 | mac_command | browser | 0.00 |

Three of eight clearly correct (`set a timer`, `what is the capital`,
`open youtube in chrome`); `open calculator` gets the category right and the
target wrong; four misfire entirely.

Upstream predicted this: the shipped checkpoints "work zero-shot" but
*"fine-tuning on decisions from your own domain is where accuracy jumps"* —
0.766 for `laya-typed-decisions` against **0.362** for the base English
checkpoint. The zero-shot numbers here are in that territory.

## Calibration warning, observed

At load time the library emits:

> this checkpoint ships invalid temperatures or values outside [0.5, 5]; using
> choice:11+=0.10058280825614929 -> 0.5. **Treat confidence from the affected
> entries as uncalibrated.**

Entry 11 of this project's battery is `app_action`, which is what
`"open calculator"` needs. Its confidence is not trustworthy for gating.

## Conclusion

**Laya is not viable as the default on pre-Haswell hardware.** It is architecturally
right — Apache-2.0, Jev-compatible, non-generative, locally free — and on a modern
CPU the 33 ms claim is plausible. On this machine it is 2–3 minutes per turn and
wrong about half the time.

Which is precisely why the fallback in `backend.py` now carries a **latency
guard**: the first fallback call is timed against a budget (default 5 s,
`HEYJEV_FALLBACK_BUDGET_S`). Exceed it and the original error is re-raised with
an explanation. An assistant that says *"this machine is too slow to decide
locally"* is useful. One that freezes for two minutes is indistinguishable from
a hung process.

This is the same lesson as local TTS: the software is correct, and the hardware
floor is the constraint. Both are documented rather than hidden.