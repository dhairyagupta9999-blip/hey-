"""Deterministic Task Verifier for Hey Jev Agent Harness.

Independently validates agent task completion claims rather than trusting
self-reporting. Checks status, file modifications, confidence thresholds,
and required artifacts.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from agent_result import AgentResult, AgentResultStatus
from agent_task import AgentTask


@dataclass
class VerificationResult:
    """Outcome of independent task verification."""
    passed: bool
    checks_run: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""


class TaskVerifier:
    """Deterministic verification engine for task completion."""

    def __init__(self) -> None:
        self._custom_validators: Dict[str, Callable[[AgentTask, AgentResult], VerificationResult]] = {}

    def register_validator(
        self,
        task_type: str,
        validator_fn: Callable[[AgentTask, AgentResult], VerificationResult],
    ) -> None:
        """Register a custom validator function for a specific task category."""
        self._custom_validators[task_type] = validator_fn

    def verify(self, task: AgentTask, result: AgentResult) -> VerificationResult:
        """Execute independent verification checks against task result."""
        checks_run: List[str] = []
        failures: List[str] = []
        evidence: Dict[str, Any] = {}

        # 1. Custom validator dispatch if registered
        task_type = str(task.context.get("task_type", ""))
        if task_type in self._custom_validators:
            return self._custom_validators[task_type](task, result)

        # 2. Result Status Check
        checks_run.append("result_status_success")
        if result.status != AgentResultStatus.SUCCESS:
            failures.append(f"Result status is {result.status.value}, expected SUCCESS")
        if result.error:
            failures.append(f"Result contains error: {result.error}")

        # 3. Substantive Output Check
        checks_run.append("substantive_output")
        has_content = bool(
            result.result is not None
            or result.summary.strip()
            or result.files_changed
            or result.artifacts
        )
        if not has_content:
            failures.append("Result is completely empty (no result, summary, files, or artifacts)")

        # 4. Confidence Threshold Check
        checks_run.append("confidence_threshold")
        min_conf = float(task.context.get("min_confidence", 0.50))
        if result.confidence < min_conf:
            failures.append(f"Confidence {result.confidence:.2f} is below required threshold {min_conf:.2f}")
        evidence["confidence"] = result.confidence
        evidence["min_confidence"] = min_conf

        # 5. File Verification Check (if files_changed specified)
        if result.files_changed and task.context.get("verify_files_exist", True):
            checks_run.append("files_changed_exist")
            missing_files: List[str] = []
            verified_files: List[str] = []
            for file_path in result.files_changed:
                if os.path.isabs(file_path):
                    if os.path.exists(file_path):
                        verified_files.append(file_path)
                    else:
                        missing_files.append(file_path)
            if missing_files:
                failures.append(f"Claimed modified files do not exist on disk: {missing_files}")
            evidence["verified_files"] = verified_files
            evidence["missing_files"] = missing_files

        # 6. Required Artifacts Check
        required_artifacts = task.context.get("required_artifacts", [])
        if required_artifacts:
            checks_run.append("required_artifacts")
            produced_types = {a.get("type") for a in result.artifacts}
            missing_artifacts = [req for req in required_artifacts if req not in produced_types]
            if missing_artifacts:
                failures.append(f"Missing required artifact types: {missing_artifacts}")
            evidence["required_artifacts"] = required_artifacts
            evidence["produced_artifacts"] = list(produced_types)

        passed = len(failures) == 0
        summary = (
            f"Verification PASSED ({len(checks_run)} checks ok)"
            if passed
            else f"Verification FAILED ({len(failures)} failures out of {len(checks_run)} checks)"
        )

        return VerificationResult(
            passed=passed,
            checks_run=checks_run,
            failures=failures,
            evidence=evidence,
            summary=summary,
        )
