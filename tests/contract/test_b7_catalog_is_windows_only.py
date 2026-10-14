"""B7 — the shipped app catalog must not contain apps that cannot exist on Windows.

This is a Windows port. `apps.json` shipped `safari`, `finder`, `messages` and
`notes` -- all macOS-only. They resolved through the app index at priority 100
(alias match, score 100.0) purely because they were in the file, so "open
safari" produced a confident match for an application that does not and cannot
exist on the target platform.

Note that a test of the form "every entry resolves against the index" would
NOT catch this: find_app() matches apps.json aliases by construction. The
invariant worth protecting is that the default catalog contains nothing the
platform cannot run.

These tests fail on pre-fix code.
"""
from __future__ import annotations

import json
from pathlib import Path

# Applications that do not exist on Windows 10/11. Kept explicit rather than
# inferred, so the guard fails loudly if the catalog ever grows again.
# NOTE: deliberately does NOT include mail / calendar / terminal -- Windows
# ships all three (Windows Mail, Windows Calendar, Windows Terminal), so
# banning them would be wrong. Only macOS-exclusive names belong here.
MACOS_ONLY_APPS = {
    "safari", "finder", "messages", "xcode", "keynote", "numbers", "pages",
    "preview", "activity monitor", "system preferences", "textedit",
    "reminders", "contacts", "facetime", "garageband", "imovie",
    "final cut pro", "itunes", "mission control", "launchpad",
}


def _default_apps() -> dict:
    import config
    path = Path(config.REPO_ROOT) / "apps.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_default_catalog_has_no_macos_only_apps():
    """The defect: four macOS apps shipped in a Windows catalog."""
    offenders = {
        key: value
        for key, value in _default_apps().items()
        if str(key).strip().lower() in MACOS_ONLY_APPS
    }
    assert not offenders, (
        f"apps.json contains macOS-only applications in a Windows port: {offenders}"
    )


def test_default_catalog_is_valid_json_with_string_keys():
    """Regression guard: the edit must not corrupt the file."""
    apps = _default_apps()
    assert isinstance(apps, dict) and apps, "apps.json must be a non-empty object"
    for key, value in apps.items():
        assert isinstance(key, str) and key.strip(), f"bad key: {key!r}"
        assert isinstance(value, (str, dict)), f"bad value for {key!r}: {value!r}"


def test_builtin_windows_tools_are_present():
    """The catalog must still cover real Windows apps.

    Guards against 'fixing' B7 by gutting the file.
    """
    import app_index

    index = app_index.get_app_index()
    names = {entry["name"].lower() for entry in index.apps}
    for expected in ("notepad", "calculator"):
        assert expected in names, (
            f"builtin Windows tool {expected!r} missing from the index"
        )


def test_removed_macos_apps_no_longer_resolve():
    """A freshly-seeded user catalog must not resolve macOS apps.

    Scoped deliberately. config.py seeds %APPDATA%/HeyJev/apps.json from the
    shipped catalog only when the user file is ABSENT, and app_index prefers
    that user copy. So an install that first ran before this fix keeps the old
    catalog -- editing the repo file alone cannot repair it. This test builds
    an index from a freshly-seeded copy, which is what a clean install gets.

    (Migrating an existing stale user catalog is tracked separately; it is the
    structural half of B7 -- see plan §4.1, regenerate rather than hand-maintain.)

    NOTE -- a separate defect surfaced while writing this test. It is NOT part
    of B7 and is deliberately left unfixed under the feature freeze:
    `find_app("finder")` fuzzy-matches "Windows Defender Firewall with Advanced
    Security" at score 75.0, which clears the >= 70.0 acceptance gate -- so
    "open finder" would launch Windows Defender even with the macOS entry gone.
    That is a fuzzy-threshold problem inside app_index, not a catalog-contents
    problem, so this test asserts catalog membership and nothing more.
    """
    import shutil
    import tempfile

    import app_index
    import config

    with tempfile.TemporaryDirectory() as tmp:
        seeded = Path(tmp) / "apps.json"
        shutil.copy2(Path(config.REPO_ROOT) / "apps.json", seeded)

        saved_user = app_index.USER_APPS_FILE
        saved_cache = app_index.CACHE_FILE
        try:
            app_index.USER_APPS_FILE = str(seeded)
            app_index.CACHE_FILE = str(Path(tmp) / "cache.json")
            index = app_index.AppIndex(cache_file=str(Path(tmp) / "cache.json"))
            from_catalog = [
                entry["name"].lower()
                for entry in index.apps
                if entry.get("source") in ("apps_json", "builtin")
            ]
            offenders = sorted(
                name for name in from_catalog
                if name.strip().lower() in MACOS_ONLY_APPS
            )
            assert not offenders, (
                f"a freshly-seeded catalog still contains macOS-only apps: {offenders}"
            )
        finally:
            app_index.USER_APPS_FILE = saved_user
            app_index.CACHE_FILE = saved_cache