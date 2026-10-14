"""Root conftest: make the whole legacy suite importable and isolated.

Historically the tests were run from the repo root with no sys.path setup,
relying on the CWD. Making it explicit means `pytest` behaves identically no
matter where it is invoked from -- which matters because CI, the behavioral
tier, and a developer all launch it differently.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))