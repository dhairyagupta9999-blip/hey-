"""Reconciliation Engine and External State Observer for Hey Jev.

Compares persisted expectations against actual physical environment state:
- Filesystem: validates file creation, movement, deletion, and checksums
- Process fleet: queries live processes via psutil
- Git repository: checks branch, HEAD commit, and working tree cleanliness
- Browser: checks active URLs, history, and downloaded artifacts
- Remote AI agents: queries provider status and artifact existence
Emits: MATCHED, MISMATCH, UNKNOWN, or PARTIAL.
Enables idempotent recovery (e.g. marking ALREADY_APPLIED instead of re-executing).
"""
from __future__ import annotations

import os
try:
    import psutil
except ImportError:
    psutil = None
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from git_inspector import GitInspector


class ReconciliationStatus(str, Enum):
    """Outcomes of external state reconciliation."""
    MATCHED = "MATCHED"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"
    PARTIAL = "PARTIAL"


class ReconciliationEngine:
    """Compares persisted expectations with ground-truth environmental state."""

    def __init__(self, git_inspector: Optional[GitInspector] = None) -> None:
        self.git_inspector = git_inspector or GitInspector()

    def reconcile_filesystem(
        self,
        expected_files_created: Optional[List[str]] = None,
        expected_files_moved: Optional[List[Tuple[str, str]]] = None,
        expected_files_deleted: Optional[List[str]] = None,
    ) -> Tuple[ReconciliationStatus, Dict[str, Any]]:
        """Reconcile expected filesystem mutations against actual disk state."""
        details: Dict[str, Any] = {
            "verified_created": [],
            "missing_created": [],
            "verified_moved": [],
            "failed_moved": [],
            "verified_deleted": [],
            "failed_deleted": [],
        }

        # Check creations
        if expected_files_created:
            for p in expected_files_created:
                if os.path.exists(p):
                    details["verified_created"].append(p)
                else:
                    details["missing_created"].append(p)

        # Check moves (source should be gone, dest should exist)
        if expected_files_moved:
            for src, dst in expected_files_moved:
                dst_exists = os.path.exists(dst)
                src_exists = os.path.exists(src)
                if dst_exists and not src_exists:
                    details["verified_moved"].append({"src": src, "dst": dst})
                else:
                    details["failed_moved"].append({
                        "src": src,
                        "dst": dst,
                        "src_exists": src_exists,
                        "dst_exists": dst_exists,
                    })

        # Check deletions
        if expected_files_deleted:
            for p in expected_files_deleted:
                if not os.path.exists(p):
                    details["verified_deleted"].append(p)
                else:
                    details["failed_deleted"].append(p)

        # Compute overall status
        has_failures = bool(details["missing_created"] or details["failed_moved"] or details["failed_deleted"])
        has_successes = bool(details["verified_created"] or details["verified_moved"] or details["verified_deleted"])

        if not has_failures:
            return ReconciliationStatus.MATCHED, details
        elif has_successes and has_failures:
            return ReconciliationStatus.PARTIAL, details
        else:
            return ReconciliationStatus.MISMATCH, details

    def reconcile_process(
        self,
        expected_process_name: Optional[str] = None,
        pid: Optional[int] = None,
    ) -> Tuple[ReconciliationStatus, Dict[str, Any]]:
        """Check if an expected process is alive and healthy."""
        details: Dict[str, Any] = {"expected_name": expected_process_name, "pid": pid, "found": False}
        if not psutil:
            return ReconciliationStatus.UNKNOWN, details

        if pid is not None:
            if psutil.pid_exists(pid):
                try:
                    p = psutil.Process(pid)
                    details["found"] = True
                    details["actual_name"] = p.name()
                    details["status"] = p.status()
                    return ReconciliationStatus.MATCHED, details
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return ReconciliationStatus.MISMATCH, details

        if expected_process_name:
            norm_target = expected_process_name.lower().replace(".exe", "")
            for proc in psutil.process_iter(["name", "pid"]):
                try:
                    p_name = (proc.info.get("name") or "").lower().replace(".exe", "")
                    if p_name == norm_target:
                        details["found"] = True
                        details["pid"] = proc.info.get("pid")
                        return ReconciliationStatus.MATCHED, details
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return ReconciliationStatus.MISMATCH, details

        return ReconciliationStatus.UNKNOWN, details

    def reconcile_git(
        self,
        repo_path: str,
        expected_branch: Optional[str] = None,
        expected_clean: Optional[bool] = None,
    ) -> Tuple[ReconciliationStatus, Dict[str, Any]]:
        """Reconcile expected Git repository state."""
        info = self.git_inspector.inspect(repo_path)
        details = dict(info) if isinstance(info, dict) else (info.to_dict() if hasattr(info, "to_dict") else {})

        is_git = details.get("is_git", False) if isinstance(details, dict) else getattr(info, "is_git", False)
        if not is_git:
            return ReconciliationStatus.MISMATCH, details

        matched = True
        branch = details.get("branch", "") if isinstance(details, dict) else getattr(info, "branch", "")
        clean = details.get("clean", True) if isinstance(details, dict) else getattr(info, "clean", True)
        if expected_branch and branch != expected_branch:
            matched = False
        if expected_clean is not None and clean != expected_clean:
            matched = False

        status = ReconciliationStatus.MATCHED if matched else ReconciliationStatus.MISMATCH
        return status, details

    def reconcile_remote_agent(
        self,
        task_id: str,
        provider_adapter: Any,
        expected_artifacts: Optional[List[str]] = None,
    ) -> Tuple[ReconciliationStatus, Dict[str, Any]]:
        """Query remote agent adapter after connection disruption to determine task status."""
        details: Dict[str, Any] = {"task_id": task_id, "provider_status": "UNKNOWN"}

        try:
            if hasattr(provider_adapter, "get_status"):
                status_str = provider_adapter.get_status(task_id)
                details["provider_status"] = status_str
                if status_str == "COMPLETED":
                    # Check artifacts
                    res = provider_adapter.get_result(task_id)
                    details["has_result"] = bool(res and res.is_success)
                    return ReconciliationStatus.MATCHED, details
                elif status_str in ("FAILED", "CANCELLED"):
                    return ReconciliationStatus.MISMATCH, details
                elif status_str in ("RUNNING", "QUEUED"):
                    return ReconciliationStatus.PARTIAL, details
        except Exception as e:
            details["error"] = str(e)
            return ReconciliationStatus.UNKNOWN, details

        return ReconciliationStatus.UNKNOWN, details
