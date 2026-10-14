"""B2 — a trailing browser qualifier must not destroy the target.

Measured on pre-fix code (real modules, real resolution):

    extract_target("open youtube in chrome")  -> ('open', 'youtube in chrome')
    resolve_system_target("open youtube in chrome") -> None
    resolve_tier2("open youtube in chrome")   -> status='ambiguous'
                                                ("Did you mean Google Chrome or ...?")

So the qualifier is glued onto the site name, KNOWN_SITES lookup misses, and
the user gets a clarification prompt instead of YouTube in Chrome.

These tests fail on pre-fix code.
"""
from __future__ import annotations

import pytest


def _split(target: str):
    from target_extractor import split_browser_qualifier
    return split_browser_qualifier(target)


def test_open_youtube_in_chrome_splits_site_and_browser():
    """The qualifier splits cleanly: site=youtube, browser=chrome."""
    assert _split("youtube in chrome") == ("youtube", "chrome")


def test_open_youtube_on_edge_splits_site_and_browser():
    assert _split("youtube on edge") == ("youtube", "edge")


def test_bare_site_has_no_browser():
    assert _split("youtube") == ("youtube", None)


def test_qualifier_does_not_mangle_non_browser_targets():
    """A closed browser set means ordinary phrases are left alone.

    'in documents' / 'on my desktop' are not browsers, so the target must be
    preserved verbatim rather than being split on a generic preposition.
    """
    assert _split("documents in documents") == ("documents in documents", None)
    assert _split("notes") == ("notes", None)


def test_resolve_open_youtube_in_chrome_yields_site_and_browser():
    """End-to-end resolution: 'open youtube in chrome' must resolve to the site.

    Pre-fix this returned None, which is what pushed the utterance into an
    ambiguous app-index match.
    """
    from system_targets import resolve_system_target

    res = resolve_system_target("open youtube in chrome", execute=False)
    assert res is not None, "qualifier prevented site resolution entirely"
    assert res["type"] == "website"
    assert "youtube" in res["url"].lower()
    assert res.get("browser") == "chrome"


def test_resolve_open_twitter_unchanged_by_qualifier_support():
    """Regression guard: the plain form must keep working exactly as before."""
    from system_targets import resolve_system_target

    res = resolve_system_target("open twitter", execute=False)
    assert res is not None
    assert res["type"] == "website"
    assert res.get("browser") is None