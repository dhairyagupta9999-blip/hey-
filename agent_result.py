"""Universal Agent Result Schema for Hey Jev Agent Harness.

Ensures structured, normalized inter-agent communication, handoffs,
and verification evidence instead of unparsed raw prose.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class AgentResultStatus(str, Enum):
    """Standardized result statuses returned by agent adapters."""
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILURE = "FAILURE"
    FAILED = "FAILURE"  # Alias for backward-compat / developer ergonomics
    BLOCKED = "BLOCKED"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"


@dataclass
class AgentResult:
    """Universal structured result object emitted by agents."""
    result: Any = None
    summary: str = ""
    error: Optional[str] = None
    files_changed: List[str] = field(default_factory=list)
    commands_run: List[str] = field(default_factory=list)
    questions: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    confidence: float = 1.0
    next_action: Optional[str] = None
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    evidence: List[Union[Dict[str, Any], str]] = field(default_factory=list)
    status: AgentResultStatus = AgentResultStatus.SUCCESS

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            try:
                self.status = AgentResultStatus(self.status.upper())
            except ValueError:
                self.status = AgentResultStatus.FAILURE

        # Bound confidence between 0.0 and 1.0
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    @property
    def is_success(self) -> bool:
        """True if the status is SUCCESS and no fatal error is set."""
        return self.status == AgentResultStatus.SUCCESS and not self.error

    @property
    def has_error(self) -> bool:
        """True if an error exists or status is FAILURE."""
        return bool(self.error) or self.status == AgentResultStatus.FAILURE

    def add_artifact(self, artifact_type: str, uri_or_path: str, description: str = "") -> None:
        """Record an artifact produced during execution."""
        self.artifacts.append({
            "type": artifact_type,
            "uri": uri_or_path,
            "description": description,
        })

    def add_evidence(self, description: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Record verification evidence supporting this result."""
        entry: Dict[str, Any] = {"description": description}
        if data is not None:
            entry["data"] = data
        self.evidence.append(entry)

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to a plain serializable dictionary."""
        return {
            "result": self.result,
            "summary": self.summary,
            "error": self.error,
            "files_changed": list(self.files_changed),
            "commands_run": list(self.commands_run),
            "questions": list(self.questions),
            "recommendations": list(self.recommendations),
            "confidence": self.confidence,
            "next_action": self.next_action,
            "artifacts": self.artifacts,
            "evidence": self.evidence,
            "status": self.status.value,
        }

    def to_json(self, indent: Optional[int] = None) -> str:
        """Serialize result to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentResult:
        """Reconstruct an AgentResult from dictionary."""
        d = dict(data)
        if "status" in d:
            status_val = d["status"]
            if isinstance(status_val, str):
                d["status"] = AgentResultStatus(status_val.upper())
        return cls(**d)

    @classmethod
    def from_json(cls, json_str: str) -> AgentResult:
        """Reconstruct an AgentResult from JSON string."""
        return cls.from_dict(json.loads(json_str))
