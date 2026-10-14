"""Safe cleanup utilities for test harnesses and ephemeral database artifacts.

Provides narrow, explicit deletion of specified test artifacts, strictly
forbidding recursive directory deletions on repository or application data paths.
"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Optional, Sequence


PROTECTED_DIRECTORIES = [
    os.path.normpath(r"C:\Windows"),
    os.path.normpath(r"C:\Program Files"),
    os.path.normpath(r"C:\Program Files (x86)"),
]


def is_path_in_directory(path: str, directory: str) -> bool:
    """Check if a path resides within a specific directory."""
    try:
        norm_path = os.path.normcase(os.path.abspath(path))
        norm_dir = os.path.normcase(os.path.abspath(directory))
        return norm_path.startswith(norm_dir + os.sep) or norm_path == norm_dir
    except Exception:
        return False


def narrow_cleanup_artifacts(
    target_files: Sequence[str],
    allow_companion_sqlite: bool = True,
    allowed_root_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Narrowly deletes ONLY explicitly listed temporary artifact files.
    
    Rules:
      1. Refuses to delete directories (os.path.isdir returns an error).
      2. If allow_companion_sqlite is True, also deletes exact SQLite companion
         files (<file>-wal, <file>-shm) for explicitly listed files only.
      3. Never deletes unlisted files.
      4. Validates that targets are not inside protected system directories.
    
    Returns:
      Dict with 'removed_files', 'skipped_files', and 'errors'.
    """
    removed: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []

    for file_path in target_files:
        if not file_path:
            continue

        abs_path = os.path.abspath(file_path)

        # Check for directory — directory deletion is strictly forbidden
        if os.path.isdir(abs_path):
            errors.append(f"Refusing to delete directory '{abs_path}'. Only individual files may be cleaned.")
            continue

        # Check allowed root if specified
        if allowed_root_dir and not is_path_in_directory(abs_path, allowed_root_dir):
            errors.append(f"File '{abs_path}' is outside allowed root '{allowed_root_dir}'. Deletion blocked.")
            continue

        # Check system protected directories
        is_protected = any(is_path_in_directory(abs_path, p) for p in PROTECTED_DIRECTORIES)
        if is_protected:
            errors.append(f"File '{abs_path}' is in a protected system directory. Deletion blocked.")
            continue

        # Gather file and any SQLite companion files
        candidates = [abs_path]
        if allow_companion_sqlite:
            for suffix in ("-wal", "-shm", "-journal"):
                companion = abs_path + suffix
                if os.path.exists(companion) and not os.path.isdir(companion):
                    candidates.append(companion)

        for candidate in candidates:
            if os.path.exists(candidate):
                try:
                    os.remove(candidate)
                    removed.append(candidate)
                except Exception as exc:
                    errors.append(f"Failed to remove '{candidate}': {exc}")
            else:
                skipped.append(candidate)

    return {
        "removed_files": removed,
        "skipped_files": skipped,
        "errors": errors,
        "success": len(errors) == 0,
    }


def safe_temp_dir_cleanup(
    temp_dir: str,
    explicit_artifacts: Sequence[str],
) -> Dict[str, Any]:
    """Safely cleans up an ephemeral temporary directory created by tempfile.mkdtemp.
    
    Guarantees:
      1. temp_dir must be inside the OS temporary directory.
      2. Removes ONLY the explicit artifacts first.
      3. Attempts to remove temp_dir using non-recursive os.rmdir.
      4. If unexpected files (sentinels, user data) exist, os.rmdir fails safely,
         preserving the remaining files intact.
    """
    abs_temp = os.path.abspath(temp_dir)
    os_temp = os.path.abspath(tempfile.gettempdir())

    if not is_path_in_directory(abs_temp, os_temp) or abs_temp == os_temp:
        raise ValueError(
            f"Security Violation: '{abs_temp}' is not a subdirectory of the OS temp directory '{os_temp}'."
        )

    # Clean only explicit artifacts
    result = narrow_cleanup_artifacts(
        explicit_artifacts,
        allow_companion_sqlite=True,
        allowed_root_dir=abs_temp,
    )

    # Attempt non-recursive rmdir: will only succeed if completely empty
    dir_removed = False
    if os.path.exists(abs_temp):
        try:
            os.rmdir(abs_temp)
            dir_removed = True
        except OSError:
            # Directory is not empty — unexpected files/sentinels preserved safely
            dir_removed = False

    result["temp_dir_removed"] = dir_removed
    return result
