"""Tests for persistent data integrity and narrow test artifact cleanup.

Verifies that test cleanup removes ONLY explicitly specified temporary artifacts,
strictly preserving sentinel files representing unrelated user data and persistent
application state.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import pytest

from test_cleanup_utils import (
    narrow_cleanup_artifacts,
    safe_temp_dir_cleanup,
    is_path_in_directory,
)
from safety_engine import validate_powershell_command
import config
from mission_store import SqliteMissionStore


def test_01_narrow_cleanup_preserves_sentinels():
    """Verify that narrow cleanup deletes only its test database and companions, preserving sentinels."""
    temp_dir = tempfile.mkdtemp(prefix="test_integrity_")
    try:
        test_db = os.path.join(temp_dir, "temp_test_missions.db")
        test_db_wal = test_db + "-wal"
        test_db_shm = test_db + "-shm"
        sentinel_user = os.path.join(temp_dir, "sentinel_user_data.txt")
        sentinel_app = os.path.join(temp_dir, "sentinel_persistent_app.db")

        # Create test DB and companions
        with open(test_db, "wb") as f:
            f.write(b"SQLITE_TEMP_DB_DATA")
        with open(test_db_wal, "wb") as f:
            f.write(b"WAL_DATA")
        with open(test_db_shm, "wb") as f:
            f.write(b"SHM_DATA")

        # Create sentinel files
        user_content = "User personal notes: do not delete."
        with open(sentinel_user, "w", encoding="utf-8") as f:
            f.write(user_content)

        app_content = b"PERSISTENT_SQLITE_HEADER_MOCK"
        with open(sentinel_app, "wb") as f:
            f.write(app_content)

        # Execute narrow cleanup targeting ONLY the test DB
        res = narrow_cleanup_artifacts([test_db], allow_companion_sqlite=True)

        assert res["success"] is True
        # Verify test artifacts deleted
        assert not os.path.exists(test_db), "Temporary test database should be removed"
        assert not os.path.exists(test_db_wal), "Temporary WAL companion should be removed"
        assert not os.path.exists(test_db_shm), "Temporary SHM companion should be removed"

        # Verify BOTH sentinels preserved intact
        assert os.path.exists(sentinel_user), "User sentinel file MUST be preserved"
        with open(sentinel_user, "r", encoding="utf-8") as f:
            assert f.read() == user_content

        assert os.path.exists(sentinel_app), "Application sentinel database MUST be preserved"
        with open(sentinel_app, "wb") as f:
            f.write(app_content)

    finally:
        # Final clean of the isolated test directory
        for item in os.listdir(temp_dir):
            p = os.path.join(temp_dir, item)
            if os.path.isfile(p):
                os.remove(p)
        os.rmdir(temp_dir)


def test_02_safe_temp_dir_cleanup_preserves_dir_when_sentinels_present():
    """Verify safe_temp_dir_cleanup does not delete the parent dir if unexpected files remain."""
    temp_dir = tempfile.mkdtemp(prefix="test_integrity_")
    try:
        test_db = os.path.join(temp_dir, "temp_test_missions.db")
        sentinel_user = os.path.join(temp_dir, "sentinel_user_data.txt")
        sentinel_app = os.path.join(temp_dir, "sentinel_persistent_app.db")

        with open(test_db, "wb") as f:
            f.write(b"SQLITE_TEMP_DB_DATA")
        with open(sentinel_user, "w", encoding="utf-8") as f:
            f.write("Important user data")
        with open(sentinel_app, "wb") as f:
            f.write(b"Important app data")

        # Cleanup explicit test database
        res = safe_temp_dir_cleanup(temp_dir, explicit_artifacts=[test_db])

        # Test DB was removed
        assert not os.path.exists(test_db)
        # Directory was NOT removed because sentinels were preserved
        assert res["temp_dir_removed"] is False
        assert os.path.exists(temp_dir)
        assert os.path.exists(sentinel_user)
        assert os.path.exists(sentinel_app)

    finally:
        for item in os.listdir(temp_dir):
            p = os.path.join(temp_dir, item)
            if os.path.isfile(p):
                os.remove(p)
        os.rmdir(temp_dir)


def test_03_safe_temp_dir_cleanup_removes_dir_when_no_sentinels():
    """Verify safe_temp_dir_cleanup removes directory when only expected artifacts were present."""
    temp_dir = tempfile.mkdtemp(prefix="test_integrity_")
    test_db = os.path.join(temp_dir, "temp_test_missions.db")
    with open(test_db, "wb") as f:
        f.write(b"SQLITE_TEMP_DB_DATA")

    res = safe_temp_dir_cleanup(temp_dir, explicit_artifacts=[test_db])
    assert res["success"] is True
    assert res["temp_dir_removed"] is True
    assert not os.path.exists(temp_dir)


def test_04_narrow_cleanup_refuses_directory_deletion():
    """Verify that narrow_cleanup_artifacts strictly refuses to delete directories."""
    temp_dir = tempfile.mkdtemp(prefix="test_integrity_")
    try:
        sub_dir = os.path.join(temp_dir, "nested_data")
        os.makedirs(sub_dir, exist_ok=True)

        res = narrow_cleanup_artifacts([sub_dir])
        assert res["success"] is False
        assert len(res["errors"]) == 1
        assert "Refusing to delete directory" in res["errors"][0]
        assert os.path.exists(sub_dir)
    finally:
        os.rmdir(sub_dir)
        os.rmdir(temp_dir)


def test_05_safe_temp_dir_cleanup_rejects_non_temp_directory():
    """Verify that safe_temp_dir_cleanup rejects paths outside the OS temp directory."""
    project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "data"))
    with pytest.raises(ValueError, match="Security Violation"):
        safe_temp_dir_cleanup(project_dir, explicit_artifacts=[])


def test_06_powershell_safety_engine_blocks_recursive_data_deletion():
    """Verify that safety_engine blocks recursive deletion targeting data or application dirs."""
    dangerous_cmds = [
        'Remove-Item -Path "C:\\Users\\prabh\\.gemini\\antigravity\\scratch\\hey-jev\\data" -Recurse -Force',
        'Remove-Item -Path "data" -Recurse -Force',
        'Remove-Item -Recurse -Force data',
        'Remove-Item -Path "$env:APPDATA\\HeyJev" -Recurse -Force',
        'Remove-Item -Path "C:\\Users\\prabh\\AppData\\Roaming\\HeyJev" -Recurse',
    ]

    for cmd in dangerous_cmds:
        allowed, reason = validate_powershell_command(cmd)
        assert allowed is False, f"Expected command to be blocked: {cmd}"
        assert "recursive application or data directory deletion" in reason, f"Unexpected reason for: {cmd}"


def test_07_appdata_persistent_locations_integrity():
    """Verify that Hey Jev persistent configuration points to APPDATA and not relative data/."""
    # Central config APPDATA_DIR must be in %APPDATA%
    assert config.APPDATA_DIR.endswith(os.path.join("HeyJev"))
    assert not config.APPDATA_DIR.endswith(os.path.join("scratch", "hey-jev", "data"))

    # Mission store default path must be in %APPDATA%/hey-jev
    store = SqliteMissionStore()
    assert "AppData" in store.db_path or "hey-jev" in store.db_path
    assert not store.db_path.startswith("data" + os.sep)
    assert not store.db_path.endswith(os.path.join("hey-jev", "data", "hey_jev_missions.db"))
