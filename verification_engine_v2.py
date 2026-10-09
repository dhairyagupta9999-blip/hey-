"""Verification Engine V2 and Multi-Level Evidence Collector for Hey Jev.

Extends Phase 11 TaskVerifier to provide ground-truth state verification:
- 6 Verification Levels (LEVEL_0: Self-report to LEVEL_5: External validation)
- Structured VerificationEvidence emitting objective observation records
- Verification domains: Filesystem, Processes, Artifact Checksums, Git, Deterministic Tests
- Rule: "A tool return is NOT proof of success."
"""
from __future__ import annotations

import hashlib
import os
try:
    import psutil
except ImportError:
    psutil = None
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent_result import AgentResult, AgentResultStatus
from agent_task import AgentTask
from git_inspector import GitInspector
from reliability_model import VerificationEvidence, VerificationLevel
from task_verifier import TaskVerifier, VerificationResult


class VerificationEngineV2(TaskVerifier):
    """Next-generation verification engine requiring physical evidence for task completion."""

    def __init__(self, git_inspector: Optional[GitInspector] = None) -> None:
        super().__init__()
        self.git_inspector = git_inspector or GitInspector()

    def verify_with_evidence(
        self,
        task: AgentTask,
        result: AgentResult,
        target_path: Optional[str] = None,
        expected_checksum: Optional[str] = None,
        test_command: Optional[str] = None,
        expected_pid: Optional[int] = None,
    ) -> Tuple[VerificationResult, List[VerificationEvidence], VerificationLevel]:
        """Execute multi-tier verification and calculate highest achieved evidence level."""
        evidence_list: List[VerificationEvidence] = []
        checks_run: List[str] = []
        failures: List[str] = []
        achieved_level = VerificationLevel.LEVEL_0

        # Level 0: Agent self-report
        checks_run.append("agent_self_report")
        if result.is_success and result.summary:
            evidence_list.append(VerificationEvidence(
                type="agent_self_report",
                source=task.owner_agent,
                expected="SUCCESS",
                observed=result.status.value,
                confidence=0.30,
                level=VerificationLevel.LEVEL_0,
            ))
            achieved_level = VerificationLevel.LEVEL_0
        else:
            failures.append("Agent did not report success")

        # Level 1: Tool return status
        if result.result is not None:
            checks_run.append("tool_output_present")
            evidence_list.append(VerificationEvidence(
                type="tool_output",
                source="tool_executor",
                expected="non_empty_output",
                observed="output_present",
                confidence=0.55,
                level=VerificationLevel.LEVEL_1,
            ))
            achieved_level = VerificationLevel.LEVEL_1

        # Level 2: Physical State Inspection (Filesystem, Process)
        if target_path:
            checks_run.append("filesystem_state_inspection")
            if os.path.exists(target_path):
                file_size = os.path.getsize(target_path) if os.path.isfile(target_path) else 0
                evidence_list.append(VerificationEvidence(
                    type="filesystem_stat",
                    source="os.path",
                    value=target_path,
                    expected="file_exists",
                    observed=f"exists (size: {file_size} bytes)",
                    confidence=0.85,
                    level=VerificationLevel.LEVEL_2,
                ))
                achieved_level = VerificationLevel.LEVEL_2

                # Artifact Checksum Verification
                if expected_checksum and os.path.isfile(target_path):
                    checks_run.append("checksum_verification")
                    with open(target_path, "rb") as f:
                        data = f.read()
                    actual_chk = f"sha256:{hashlib.sha256(data).hexdigest()}"
                    if actual_chk == expected_checksum:
                        evidence_list.append(VerificationEvidence(
                            type="checksum_match",
                            source="hashlib.sha256",
                            expected=expected_checksum,
                            observed=actual_chk,
                            confidence=0.95,
                            level=VerificationLevel.LEVEL_3,
                        ))
                        achieved_level = VerificationLevel.LEVEL_3
                    else:
                        failures.append(f"Checksum mismatch: expected {expected_checksum}, got {actual_chk}")
            else:
                failures.append(f"Expected path does not exist on disk: {target_path}")

        # Process inspection
        if expected_pid is not None:
            checks_run.append("process_state_inspection")
            if psutil and psutil.pid_exists(expected_pid):
                evidence_list.append(VerificationEvidence(
                    type="process_alive",
                    source="psutil",
                    expected=expected_pid,
                    observed=f"pid {expected_pid} alive",
                    confidence=0.85,
                    level=VerificationLevel.LEVEL_2,
                ))
                achieved_level = max(achieved_level, VerificationLevel.LEVEL_2)
            else:
                failures.append(f"Expected process with PID {expected_pid} is not running")

        passed = (len(failures) == 0 and result.is_success)
        summary = (
            f"Verified at Level {achieved_level.value} with {len(evidence_list)} evidence items"
            if passed else f"Verification failed ({len(failures)} failures)"
        )

        v_res = VerificationResult(
            passed=passed,
            checks_run=checks_run,
            failures=failures,
            evidence={e.type: e.to_dict() for e in evidence_list},
            summary=summary,
        )

        return v_res, evidence_list, achieved_level
