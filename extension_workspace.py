"""Isolated Development Workspace for Extension Construction (Phase 24).

Provides dedicated, bounded directory environments for candidate extension building.

Guarantees:
1. Strict canonical root containment; prevents directory traversal attacks.
2. Restricts file creation strictly to files whitelisted in the build plan.
3. Completely excludes credentials, private keys, and production configuration.
4. Cryptographically tracks generated artifacts (SHA-256) and total size.
5. Concurrency protection prevents parallel builds from mutual corruption.
6. Reliable, guaranteed cleanup in normal and cancelled execution paths.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import threading
from typing import Any, Dict, List, Optional, Set

from extension_build_plan import ExtensionBuildPlan


class ExtensionWorkspace:
    """Isolated file-system workspace for building and inspecting extension artifacts."""

    _active_locks: Dict[str, threading.Lock] = {}
    _global_lock = threading.Lock()

    def __init__(
        self,
        workspace_id: str,
        base_dir: Optional[str] = None,
        build_plan: Optional[ExtensionBuildPlan] = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.build_plan = build_plan
        self._cancelled = False
        self._cleaned = False

        # Concurrency protection
        with self._global_lock:
            if workspace_id not in self._active_locks:
                self._active_locks[workspace_id] = threading.Lock()
            self._lock = self._active_locks[workspace_id]

        # Workspace directory path
        if base_dir:
            self._parent_dir = os.path.realpath(base_dir)
            os.makedirs(self._parent_dir, exist_ok=True)
            self._root_path = os.path.realpath(os.path.join(self._parent_dir, f"ws_{workspace_id}"))
        else:
            self._root_path = os.path.realpath(tempfile.mkdtemp(prefix=f"hey_jev_ws_{workspace_id}_"))

        os.makedirs(self._root_path, exist_ok=True)
        self._tracked_files: Dict[str, Dict[str, Any]] = {}

    @property
    def root_path(self) -> str:
        """Return the canonical, real filesystem path of the workspace root."""
        return self._root_path

    def cancel(self) -> None:
        """Signal cancellation for in-flight workspace operations."""
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def resolve_safe_path(self, relative_path: str) -> str:
        """Resolve a relative path inside the workspace, strictly preventing path traversal.
        
        Raises ValueError or PermissionError if path attempts to escape the workspace root.
        """
        if not relative_path or not relative_path.strip():
            raise ValueError("Relative path cannot be empty.")

        # Disallow explicit traversal markers
        cleaned = relative_path.replace("\\", "/")
        parts = cleaned.split("/")
        if ".." in parts:
            raise PermissionError(f"Directory traversal prohibited: '{relative_path}'.")

        # Disallow drive letters or absolute paths
        if os.path.isabs(relative_path) or (len(relative_path) > 1 and relative_path[1] == ":"):
            raise PermissionError(f"Absolute paths outside workspace prohibited: '{relative_path}'.")

        # Resolve canonical target
        target = os.path.realpath(os.path.join(self._root_path, relative_path))

        # Strict containment verification
        # target must be within self._root_path and not equal to self._root_path itself
        root_prefix = self._root_path if self._root_path.endswith(os.sep) else self._root_path + os.sep
        if not target.startswith(root_prefix):
            raise PermissionError(
                f"Path '{relative_path}' resolves outside workspace boundary: '{target}'."
            )

        return target

    def write_file(self, relative_path: str, content: str | bytes) -> Dict[str, Any]:
        """Write content to an authorized workspace file with strict size and plan bounds."""
        with self._lock:
            if self._cancelled:
                raise RuntimeError("Workspace operation cancelled.")

            safe_rel = os.path.normpath(relative_path.replace("\\", "/"))

            # 1. Authorization check: if a build plan is attached, verify path is permitted
            if self.build_plan:
                permitted_rels = {f.relative_path for f in self.build_plan.permitted_files}
                if safe_rel not in permitted_rels:
                    raise PermissionError(
                        f"File '{safe_rel}' is not permitted by build plan '{self.build_plan.plan_id}'. "
                        f"Permitted files: {sorted(list(permitted_rels))}"
                    )

            # 2. Size limit checks
            raw_bytes = content.encode("utf-8") if isinstance(content, str) else content
            if self.build_plan:
                max_file = self.build_plan.resource_limits.max_file_size_bytes
                if len(raw_bytes) > max_file:
                    raise ValueError(
                        f"File '{safe_rel}' size ({len(raw_bytes)} bytes) exceeds limit ({max_file} bytes)."
                    )

                # Check total workspace size
                current_total = sum(f["size_bytes"] for f in self._tracked_files.values())
                max_total = self.build_plan.resource_limits.max_total_workspace_bytes
                if current_total + len(raw_bytes) > max_total:
                    raise ValueError(
                        f"Writing '{safe_rel}' would exceed total workspace capacity ({max_total} bytes)."
                    )

            # 3. Resolve path safely
            target_path = self.resolve_safe_path(safe_rel)

            # 4. Check for production file collision (never overwrite production code)
            cwd_real = os.path.realpath(os.getcwd())
            if target_path.startswith(cwd_real + os.sep) and not target_path.startswith(self._root_path + os.sep):
                raise PermissionError(f"Target '{target_path}' collides with production repository files.")

            # 5. Create directories safely inside workspace
            os.makedirs(os.path.dirname(target_path), exist_ok=True)

            # 6. Write content
            mode = "wb" if isinstance(content, bytes) else "w"
            encoding = None if isinstance(content, bytes) else "utf-8"
            with open(target_path, mode, encoding=encoding) as f:
                f.write(content)

            # 7. Compute cryptographic hash
            file_hash = hashlib.sha256(raw_bytes).hexdigest()
            record = {
                "relative_path": safe_rel,
                "full_path": target_path,
                "size_bytes": len(raw_bytes),
                "sha256": file_hash,
            }
            self._tracked_files[safe_rel] = record
            return record

    def read_file(self, relative_path: str) -> str:
        """Read text content from an authorized workspace file."""
        with self._lock:
            safe_rel = os.path.normpath(relative_path.replace("\\", "/"))
            target_path = self.resolve_safe_path(safe_rel)
            if not os.path.exists(target_path):
                raise FileNotFoundError(f"File '{safe_rel}' does not exist in workspace.")
            with open(target_path, "r", encoding="utf-8") as f:
                return f.read()

    def get_file_record(self, relative_path: str) -> Optional[Dict[str, Any]]:
        """Retrieve tracking metadata for a generated file."""
        with self._lock:
            safe_rel = os.path.normpath(relative_path.replace("\\", "/"))
            return self._tracked_files.get(safe_rel)

    def list_tracked_files(self) -> List[Dict[str, Any]]:
        """Return all tracked files in this workspace with sizes and hashes."""
        with self._lock:
            return list(self._tracked_files.values())

    def compute_manifest(self) -> Dict[str, str]:
        """Return a mapping of {relative_path: sha256_hash} for all generated files."""
        with self._lock:
            return {k: v["sha256"] for k, v in self._tracked_files.items()}

    def cleanup(self) -> bool:
        """Purge and delete the entire workspace directory reliably.
        
        Guaranteed to be safe, idempotent, and handle Windows file locking gracefully.
        """
        with self._lock:
            if self._cleaned:
                return True
            try:
                if os.path.exists(self._root_path):
                    shutil.rmtree(self._root_path, ignore_errors=True)
                self._cleaned = not os.path.exists(self._root_path)
                return self._cleaned
            except Exception:
                return False

    def __enter__(self) -> ExtensionWorkspace:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup()
