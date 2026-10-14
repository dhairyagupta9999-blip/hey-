"""B8 — importing a module must have no filesystem side effects.

`config.py` called `os.makedirs(...)` five times at module scope (lines 10, 24,
25, 26 and 56 before the fix). Importing config therefore creates directories
under %APPDATA% as a side effect of merely reading it -- which means a test
collection, a lint pass, or any `import config` from a REPL mutates the user's
machine, and a read-only or sandboxed environment raises at import time instead
of failing with a clear message.

The fix routes directory creation through an explicit `ensure_app_dirs()` that
only the entrypoint calls.

This test runs in a subprocess so it observes a genuinely fresh interpreter,
with %APPDATA% redirected to an empty temporary directory.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _import_config_with_clean_appdata() -> tuple[int, str, list[str]]:
    """Spawn a fresh interpreter with an empty APPDATA and import config."""
    with tempfile.TemporaryDirectory() as sandbox:
        appdata = Path(sandbox) / "AppData"
        appdata.mkdir()

        script = (
            "import json, os, sys\n"
            f"sys.path.insert(0, {str(REPO)!r})\n"
            "import config\n"
            f"root = {str(appdata)!r}\n"
            "created = []\n"
            "for dirpath, dirnames, filenames in os.walk(root):\n"
            "    for d in dirnames:\n"
            "        created.append('dir:' + os.path.relpath(os.path.join(dirpath, d), root))\n"
            "    for f in filenames:\n"
            "        created.append('file:' + os.path.relpath(os.path.join(dirpath, f), root))\n"
            "print(json.dumps(sorted(created)))\n"
        )
        env = dict(os.environ)
        env["APPDATA"] = str(appdata)
        env["LOCALAPPDATA"] = str(appdata)
        env["PYTHONPATH"] = str(REPO)

        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, env=env, timeout=120,
        )
        line = (proc.stdout or "").strip().splitlines()
        created = json.loads(line[-1]) if line else []
        return proc.returncode, (proc.stderr or ""), created


def test_import_config_creates_no_directories():
    """The defect: five import-time os.makedirs() calls."""
    code, stderr, created = _import_config_with_clean_appdata()
    assert code == 0, f"importing config failed:\n{stderr}"
    assert created == [], (
        "importing config created directories as a side effect: "
        f"{created}. Directory creation must go through ensure_app_dirs(), "
        "called only by the entrypoint."
    )


def test_config_exposes_explicit_dir_bootstrap():
    """The replacement must exist and be callable."""
    import config

    assert hasattr(config, "ensure_app_dirs"), (
        "config.ensure_app_dirs() is missing; import-time makedirs were "
        "replaced by nothing"
    )
    assert callable(config.ensure_app_dirs)


def test_ensure_app_dirs_is_idempotent():
    """Calling it twice must be safe -- the entrypoint may run it repeatedly."""
    import config

    config.ensure_app_dirs()
    config.ensure_app_dirs()  # must not raise


def test_ensure_app_dirs_actually_creates_the_directories():
    """Otherwise B8 would be 'fixed' by simply never creating them.

    Runs against a redirected APPDATA in a subprocess so the assertion cannot
    pass because an earlier test already created the real directories -- which
    is exactly how the previous version of this test passed vacuously.
    """
    import os
    import subprocess
    import sys
    import tempfile

    with tempfile.TemporaryDirectory() as sandbox:
        appdata = Path(sandbox) / "AppData"
        appdata.mkdir()

        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(REPO)!r})\n"
            "import config\n"
            "from pathlib import Path\n"
            "assert not Path(config.APPDATA_DIR).exists(), 'APPDATA_DIR pre-existed'\n"
            "config.ensure_app_dirs()\n"
            "for name in ('APPDATA_DIR', 'TTS_CACHE_DIR', 'DICTATION_FAILED_DIR',\n"
            "             'CLIPS_DIR', 'SOUNDS_DIR'):\n"
            "    p = getattr(config, name)\n"
            "    assert Path(p).is_dir(), f'ensure_app_dirs did not create {name}: {p}'\n"
            "print('OK')\n"
        )
        env = dict(os.environ)
        env["APPDATA"] = str(appdata)
        env["LOCALAPPDATA"] = str(appdata)

        proc = subprocess.run([sys.executable, "-c", script],
                              capture_output=True, text=True, env=env, timeout=120)

    assert proc.returncode == 0, (
        f"ensure_app_dirs() did not create every directory from a clean state:\n"
        f"{proc.stderr}"
    )