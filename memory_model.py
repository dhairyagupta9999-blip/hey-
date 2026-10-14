"""Memory Model, Explicit Categories, Epistemic States, and Sanitization for Hey Jev.

Defines:
- MemoryType (USER_PREFERENCE, USER_FACT, PROJECT, PROJECT_DECISION, etc.)
- EpistemicStatus (FACT, INFERENCE, UNCERTAINTY)
- MemorySource (USER, AGENT, TASK, MISSION, PROJECT_SCAN, etc.)
- MemorySensitivity (PUBLIC, NORMAL, PRIVATE, SENSITIVE)
- MemoryStatus (ACTIVE, SUPERSEDED, EXPIRED, ARCHIVED, DELETED)
- MemoryRecord: Normalized schema with temporal bounds and checksum integrity
- Credential sanitization to guarantee zero secrets stored in persistent memory
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class MemoryType(str, Enum):
    """Explicit functional categories for memories."""
    USER_PREFERENCE = "USER_PREFERENCE"
    USER_FACT = "USER_FACT"
    PROJECT = "PROJECT"
    PROJECT_DECISION = "PROJECT_DECISION"
    PROJECT_STATE = "PROJECT_STATE"
    WORKFLOW = "WORKFLOW"
    SKILL_LESSON = "SKILL_LESSON"
    TOOL_LESSON = "TOOL_LESSON"
    AGENT_LESSON = "AGENT_LESSON"
    TASK_SUMMARY = "TASK_SUMMARY"
    MISSION_SUMMARY = "MISSION_SUMMARY"
    RESEARCH_FACT = "RESEARCH_FACT"
    RESEARCH_SOURCE = "RESEARCH_SOURCE"
    ENVIRONMENT_FACT = "ENVIRONMENT_FACT"
    TEMPORAL_EVENT = "TEMPORAL_EVENT"


class EpistemicStatus(str, Enum):
    """Epistemic classification distinguishing direct truth from hypothesis."""
    FACT = "FACT"                  # Direct user statement or verified OS state
    INFERENCE = "INFERENCE"        # Derived conclusion or pattern observed
    UNCERTAINTY = "UNCERTAINTY"    # Unverified hypothesis or conflicting signal


class MemorySource(str, Enum):
    """Origin of a memory observation."""
    USER = "USER"
    AGENT = "AGENT"
    TASK = "TASK"
    MISSION = "MISSION"
    PROJECT_SCAN = "PROJECT_SCAN"
    GIT = "GIT"
    BROWSER = "BROWSER"
    RESEARCH = "RESEARCH"
    WINDOWS = "WINDOWS"
    SYSTEM = "SYSTEM"


class MemorySensitivity(str, Enum):
    """Privacy tier governing retrieval permissions."""
    PUBLIC = "PUBLIC"              # Public knowledge, non-confidential
    NORMAL = "NORMAL"              # Standard task and project facts
    PRIVATE = "PRIVATE"            # User specific preferences and local history
    SENSITIVE = "SENSITIVE"        # High-privacy information requiring explicit authorization


class MemoryStatus(str, Enum):
    """Lifecycle state of a memory record."""
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"
    ARCHIVED = "ARCHIVED"
    DELETED = "DELETED"


# Sensitive token regex patterns for memory sanitization
_SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9_\-]{20,}", re.IGNORECASE),
    re.compile(r"ghp_[a-zA-Z0-9]{30,}", re.IGNORECASE),
    re.compile(r"AIza[0-9A-Za-z\-_]{35}", re.IGNORECASE),
    re.compile(r"\b(password|passwd|api_key|secret|token)\s*[:=]\s*['\"]?([^\s'\"]+)['\"]?", re.IGNORECASE),
    re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{20,}", re.IGNORECASE),
]


def sanitize_content(text: str) -> str:
    """Sanitize text to guarantee credentials and private tokens are never stored."""
    if not text:
        return ""
    sanitized = text
    for pat in _SECRET_PATTERNS:
        sanitized = pat.sub("[REDACTED_CREDENTIAL]", sanitized)
    return sanitized


@dataclass
class MemoryRecord:
    """Normalized persistent record representing an item of memory."""
    memory_id: str = field(default_factory=lambda: f"mem_{uuid.uuid4().hex[:12]}")
    type: MemoryType = MemoryType.USER_FACT
    content: str = ""
    summary: str = ""
    source: MemorySource = MemorySource.SYSTEM
    epistemic_status: EpistemicStatus = EpistemicStatus.FACT
    scope: str = "global"  # "global", project_id, or task_id
    entity_ids: List[str] = field(default_factory=list)
    importance: float = 0.5  # 0.0 to 1.0
    confidence: float = 1.0  # 0.0 to 1.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    valid_from: Optional[float] = None
    valid_until: Optional[float] = None
    superseded_at: Optional[float] = None
    superseded_by: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    task_id: Optional[str] = None
    mission_id: Optional[str] = None
    project_id: Optional[str] = None
    agent_id: Optional[str] = None
    sensitivity: MemorySensitivity = MemorySensitivity.NORMAL
    status: MemoryStatus = MemoryStatus.ACTIVE
    checksum: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Normalize enums from strings if needed
        if isinstance(self.type, str):
            try:
                self.type = MemoryType(self.type.upper())
            except ValueError:
                self.type = MemoryType.USER_FACT
        if isinstance(self.epistemic_status, str):
            try:
                self.epistemic_status = EpistemicStatus(self.epistemic_status.upper())
            except ValueError:
                self.epistemic_status = EpistemicStatus.FACT
        if isinstance(self.source, str):
            try:
                self.source = MemorySource(self.source.upper())
            except ValueError:
                self.source = MemorySource.SYSTEM
        if isinstance(self.sensitivity, str):
            try:
                self.sensitivity = MemorySensitivity(self.sensitivity.upper())
            except ValueError:
                self.sensitivity = MemorySensitivity.NORMAL
        if isinstance(self.status, str):
            try:
                self.status = MemoryStatus(self.status.upper())
            except ValueError:
                self.status = MemoryStatus.ACTIVE

        # Sanitize text
        self.content = sanitize_content(self.content)
        self.summary = sanitize_content(self.summary)

        # Clamp metrics
        self.importance = max(0.0, min(1.0, float(self.importance)))
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

        if self.valid_from is None:
            self.valid_from = self.created_at

        # Compute integrity checksum
        if not self.checksum:
            self.checksum = self._compute_checksum()

    def _compute_checksum(self) -> str:
        body = f"{self.type.value}:{self.content}:{self.source.value}:{self.created_at}"
        return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]

    def is_current_at(self, timestamp: Optional[float] = None) -> bool:
        """Evaluate if memory was actively valid at a given epoch timestamp."""
        ts = timestamp if timestamp is not None else time.time()
        if self.status not in (MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED):
            return False
        if self.valid_from and ts < self.valid_from:
            return False
        if self.valid_until and ts > self.valid_until:
            return False
        if self.superseded_at and ts >= self.superseded_at:
            return False
        if self.expires_at and ts >= self.expires_at:
            return False
        return True

    def is_expired(self, timestamp: Optional[float] = None) -> bool:
        """Evaluate if memory has expired."""
        ts = timestamp if timestamp is not None else time.time()
        return bool(self.expires_at is not None and ts >= self.expires_at)

    def __hash__(self) -> int:
        return hash(self.memory_id)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, MemoryRecord):
            return False
        return self.memory_id == other.memory_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "type": self.type.value,
            "content": self.content,
            "summary": self.summary,
            "source": self.source.value,
            "epistemic_status": self.epistemic_status.value,
            "scope": self.scope,
            "entity_ids": list(self.entity_ids),
            "importance": self.importance,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_accessed_at": self.last_accessed_at,
            "expires_at": self.expires_at,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "superseded_at": self.superseded_at,
            "superseded_by": self.superseded_by,
            "tags": list(self.tags),
            "task_id": self.task_id,
            "mission_id": self.mission_id,
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "sensitivity": self.sensitivity.value,
            "status": self.status.value,
            "checksum": self.checksum,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MemoryRecord:
        d = dict(data)
        return cls(**d)
