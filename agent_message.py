"""Universal Agent Message Model and Envelope for Hey Jev Orchestrator.

Implements the A2A 1.0.0-aligned message envelope for structured,
verifiable inter-agent communication, handoffs, reviews, and delegations.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from agent_result import AgentResult
from agent_task import TaskPermission


class AgentMessageType(str, Enum):
    """Standardized message types for inter-agent communication."""
    TASK_REQUEST = "TASK_REQUEST"
    TASK_ACCEPTED = "TASK_ACCEPTED"
    TASK_PROGRESS = "TASK_PROGRESS"
    TASK_RESULT = "TASK_RESULT"
    TASK_ERROR = "TASK_ERROR"
    TASK_REVIEW_REQUEST = "TASK_REVIEW_REQUEST"
    TASK_REVIEW_RESULT = "TASK_REVIEW_RESULT"
    HANDOFF = "HANDOFF"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    CANCEL = "CANCEL"
    CANCELLED = "CANCELLED"


class ContextFilterMode(str, Enum):
    """Controls how much history and state is transferred during handoff."""
    FULL = "FULL"
    RELEVANT = "RELEVANT"
    SUMMARY = "SUMMARY"
    ARTIFACTS_ONLY = "ARTIFACTS_ONLY"


@dataclass
class ArtifactReference:
    """Structured reference to an artifact produced by an agent."""
    artifact_id: str = field(default_factory=lambda: f"art_{uuid.uuid4().hex[:8]}")
    type: str = "file"  # file, screenshot, report, diff, patch, log
    uri_or_path: str = ""
    producer_agent: str = ""
    task_id: str = ""
    created_at: float = field(default_factory=time.time)
    checksum: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ArtifactReference:
        return cls(**data)


@dataclass
class AgentMessage:
    """Universal envelope for agent-to-agent collaboration and handoffs."""
    message_id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:10]}")
    task_id: str = ""
    parent_task_id: Optional[str] = None
    mission_id: Optional[str] = None
    sender_agent: str = "jev_orchestrator"
    receiver_agent: str = ""
    message_type: AgentMessageType = AgentMessageType.TASK_REQUEST
    timestamp: float = field(default_factory=time.time)
    context: Dict[str, Any] = field(default_factory=dict)
    goal: str = ""
    instructions: str = ""
    inputs: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    result: Optional[Dict[str, Any]] = None
    evidence: List[Any] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    confidence: float = 1.0
    requested_action: Optional[str] = None
    reply_to: Optional[str] = None
    expires_at: Optional[float] = None

    def __post_init__(self) -> None:
        if isinstance(self.message_type, str):
            try:
                self.message_type = AgentMessageType(self.message_type)
            except ValueError:
                self.message_type = AgentMessageType.TASK_REQUEST

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "mission_id": self.mission_id,
            "sender_agent": self.sender_agent,
            "receiver_agent": self.receiver_agent,
            "message_type": self.message_type.value,
            "timestamp": self.timestamp,
            "context": self.context,
            "goal": self.goal,
            "instructions": self.instructions,
            "inputs": self.inputs,
            "artifacts": self.artifacts,
            "result": self.result,
            "evidence": self.evidence,
            "permissions": self.permissions,
            "confidence": self.confidence,
            "requested_action": self.requested_action,
            "reply_to": self.reply_to,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentMessage:
        d = dict(data)
        return cls(**d)

    @staticmethod
    def filter_context_dict(context: Dict[str, Any], mode: ContextFilterMode) -> Dict[str, Any]:
        """Apply structured filtering to a context dictionary."""
        if mode == ContextFilterMode.FULL:
            return dict(context)
        elif mode == ContextFilterMode.RELEVANT:
            relevant_keys = {"goal", "error", "file_path", "target_url", "command", "task_type", "criteria", "summary"}
            return {k: v for k, v in context.items() if k in relevant_keys}
        elif mode == ContextFilterMode.SUMMARY:
            return {
                "summary": context.get("summary", ""),
                "status": context.get("status", "ACTIVE"),
            }
        elif mode == ContextFilterMode.ARTIFACTS_ONLY:
            return {"artifacts": context.get("artifacts", [])}
        return {}

    def filter_context(self, mode: ContextFilterMode) -> Dict[str, Any]:
        """Apply structured filtering to avoid dumping full conversation histories."""
        if mode == ContextFilterMode.ARTIFACTS_ONLY:
            return {"artifacts": list(self.artifacts)}
        return self.filter_context_dict(self.context, mode)

    def create_reply(
        self,
        sender_agent: str,
        message_type: AgentMessageType,
        result: Optional[AgentResult] = None,
        instructions: str = "",
    ) -> AgentMessage:
        """Create a correlated reply message referencing this message's ID and task."""
        res_dict = result.to_dict() if result else None
        return AgentMessage(
            task_id=self.task_id,
            parent_task_id=self.parent_task_id,
            mission_id=self.mission_id,
            sender_agent=sender_agent,
            receiver_agent=self.sender_agent,
            message_type=message_type,
            reply_to=self.message_id,
            goal=self.goal,
            instructions=instructions,
            result=res_dict,
            confidence=result.confidence if result else 1.0,
            evidence=result.evidence if result else [],
            artifacts=result.artifacts if result else [],
        )
