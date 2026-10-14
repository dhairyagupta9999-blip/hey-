"""B3 — a retry must not be silently swallowed; a suppression must be announced.

`_TRANSCRIPT_DEDUP_WINDOW_S = 5.0` conflated two unrelated events:

  * ECHO       — the mic re-heard the assistant's own TTS. Guard d already
                 targets this with `_RECENT_REPLIES_WINDOW_S = 15.0`.
  * CORRECTION — the user retrying a command that visibly failed. This must
                 always be honoured.

Measured on pre-fix code: a user who said "open calculator", watched nothing
happen, and said it again one second later got complete silence. Five seconds
of dead air reads as a broken assistant, not as protection.

The fix narrows the window to 300 ms -- enough to absorb a key-release
retrigger or a doubled VAD segment, short enough for a human retry -- and
announces every suppression, because the silence is what reads as broken.

These tests fail on pre-fix code.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_dedup_state():
    import siri
    siri.reset_transcript_dedup()
    yield
    siri.reset_transcript_dedup()


def test_user_retry_after_failure_is_not_suppressed():
    """The defect: retrying a failed command within 5s was silently dropped."""
    import siri

    t0 = 1_000_000.0
    assert siri.check_and_update_transcript_dedup("open calculator", t0) is False
    # One second later the user tries again. This must be honoured.
    assert siri.check_and_update_transcript_dedup("open calculator", t0 + 1.0) is False, (
        "a human retry one second later was suppressed; the 5s dedup window "
        "swallows corrections"
    )


def test_retry_after_two_seconds_is_not_suppressed():
    """Two seconds is still an obvious human retry, not an echo."""
    import siri

    t0 = 2_000_000.0
    assert siri.check_and_update_transcript_dedup("open spotify", t0) is False
    assert siri.check_and_update_transcript_dedup("open spotify", t0 + 2.0) is False


def test_repeat_within_300ms_is_suppressed():
    """Echo guard still works: a doubled VAD segment must not run twice."""
    import siri

    t0 = 3_000_000.0
    assert siri.check_and_update_transcript_dedup("open calculator", t0) is False
    assert siri.check_and_update_transcript_dedup("open calculator", t0 + 0.1) is True, (
        "a repeat within the retrigger window should still be suppressed"
    )


def test_dedup_window_is_300ms():
    """The window itself is the contract: 300 ms, not 5000."""
    import siri

    assert siri._TRANSCRIPT_DEDUP_WINDOW_S == 0.3, (
        f"dedup window is {siri._TRANSCRIPT_DEDUP_WINDOW_S}s; "
        "must be 0.3s so a human retry is honoured"
    )


def test_repeat_within_300ms_is_suppressed_and_announced():
    """Suppression must be announced, not silent.

    Asserted through the real `notify` channel that handle() already uses to
    report state to the user. Capturing what the assistant tells the user is
    observation, not mocking the code under test.

    Pre-fix this fails: the transcript was dropped with a print() and a trace
    line, and the user heard nothing at all.
    """
    import time

    import siri

    seen: list[tuple[str, str]] = []

    def notify(state, detail=""):
        seen.append((state, detail))

    # Prime the guard directly instead of issuing a real first turn. An earlier
    # version called handle() twice and relied on them landing within 300 ms --
    # which held only while TTS was absent. With local Kokoro installed the
    # first call takes ~25 s of synthesis, so the "repeat" was no longer a
    # repeat and this test failed for the wrong reason. Priming the state makes
    # the assertion deterministic on any machine, at any synthesis speed.
    siri.reset_transcript_dedup()
    siri._LAST_TRANSCRIPT = "open calculator"
    siri._LAST_TRANSCRIPT_TIME = time.time()

    siri.handle("open calculator", notify=notify)

    assert seen, (
        "a suppressed transcript produced no user-visible notification; the "
        "silence is what reads as a broken assistant"
    )
    joined = " ".join(f"{state} {detail}".lower() for state, detail in seen)
    assert any(word in joined for word in ("ignor", "repeat", "already", "last command")), (
        f"suppression was announced with an unhelpful message: {seen!r}"
    )