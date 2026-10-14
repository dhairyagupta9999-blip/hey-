"""B4 — a user who asks to *open* or *click* something must SEE it happen.

`browser_adapter.py:139` read:

    self.manager.start(headless=task.context.get("headless", True))

Headless was the DEFAULT. Every browser action the assistant took ran invisibly
and still reported success, which directly contradicts PROJECT_VISION.md
("Never report that an action succeeded unless verified") and makes the
assistant feel broken: you ask it to click a video and nothing happens on screen.

These tests fail on pre-fix code.
"""
from __future__ import annotations

import pytest


def _make_task(goal: str, context: dict | None = None):
    """Build a real AgentTask without invoking any network or browser."""
    from agent_task import AgentTask
    return AgentTask(goal=goal, context=dict(context or {}))


class _RecordingManager:
    """Captures the headless argument the adapter passes to the browser."""

    def __init__(self):
        self.calls: list[bool] = []

    @property
    def backend(self):
        return _StubBackend()

    def start(self, headless: bool = True, **kwargs):
        self.calls.append(headless)

    def execute_observe_act_verify(self, *a, **k):
        return {"status": "unknown"}


class _StubBackend:
    """Minimal stand-in so the adapter can reach the line under test."""

    def __init__(self):
        self.calls: list[str] = []

    def navigate(self, url):
        self.calls.append(f"navigate({url})")
        return {"url": url}

    def list_tabs(self):
        return []


def test_browser_is_visible_by_default():
    """The defect itself: a browser task with no explicit context must be visible.

    Pre-fix, `task.context.get("headless", True)` yields True here, so the
    adapter starts an invisible browser and then reports success.
    """
    from browser_adapter import BrowserAgent

    manager = _RecordingManager()
    adapter = BrowserAgent(manager)

    task = _make_task("open url https://example.com")
    with pytest.raises(Exception):
        # The execute path will fail against the dummy backend; we only care
        # about what it asked the browser manager to do first.
        adapter._execute_browser_goal(task)

    assert manager.calls, "browser was never started"
    assert manager.calls[0] is False, (
        f"browser started headless by default: headless={manager.calls[0]}. "
        "A user asking to open something gets no window."
    )


def test_explicit_headless_opt_in_is_still_honoured():
    """Opting in to headless must still work -- the fix adds a default, not a ban."""
    from browser_adapter import BrowserAgent

    manager = _RecordingManager()
    adapter = BrowserAgent(manager)

    task = _make_task("open url https://example.com", context={"headless": True})
    with pytest.raises(Exception):
        adapter._execute_browser_goal(task)

    assert manager.calls[0] is True, "explicit headless=True was ignored"


def test_browser_task_defaults_declare_visible():
    """Contract check: a browser task carries no implicit headless instruction."""
    task = _make_task("open url https://example.com")
    assert "headless" not in task.context, (
        "task context should not pre-seed headless; the adapter owns that default"
    )