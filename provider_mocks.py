"""Provider Transports, Failure Classification, and Deterministic Mock Agents.

Provides:
- FailureType enum for granular agent error categorization
- Transport interface (API, A2A, MCP, Browser fallback, Manual)
- High-fidelity mock agents for deterministic testing:
  MockCodingAgent, MockResearchAgent, MockReviewerAgent, MockFailingAgent, MockSlowAgent
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional

from agent_adapter import AgentAdapter
from agent_message import AgentMessage, AgentMessageType
from agent_result import AgentResult, AgentResultStatus
from agent_task import AgentTask, TaskPermission, TaskStatus


class FailureType(str, Enum):
    """Categorized root cause of an agent failure for intelligent recovery."""
    TRANSIENT = "TRANSIENT"
    AUTHENTICATION = "AUTHENTICATION"
    RATE_LIMIT = "RATE_LIMIT"
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"
    TOOL_FAILURE = "TOOL_FAILURE"
    MODEL_FAILURE = "MODEL_FAILURE"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    TIMEOUT = "TIMEOUT"
    USER_REQUIRED = "USER_REQUIRED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    UNKNOWN = "UNKNOWN"


class Transport(ABC):
    """Abstract communication transport for external agents."""

    @abstractmethod
    def send(self, message: AgentMessage) -> Optional[AgentMessage]:
        pass


class APITransport(Transport):
    """Direct REST / JSON-RPC / SDK transport."""

    def __init__(self, endpoint_url: str = "https://api.example.com/v1") -> None:
        self.endpoint_url = endpoint_url

    def send(self, message: AgentMessage) -> Optional[AgentMessage]:
        # Simulated API transmission
        return message.create_reply(
            sender_agent="api_remote",
            message_type=AgentMessageType.TASK_RESULT,
            result=AgentResult(
                result=f"API processed: {message.goal}",
                summary=f"Processed via {self.endpoint_url}",
                status=AgentResultStatus.SUCCESS,
            ),
        )


class A2ATransport(Transport):
    """Agent-to-Agent protocol transport."""

    def send(self, message: AgentMessage) -> Optional[AgentMessage]:
        return message.create_reply(
            sender_agent="a2a_remote",
            message_type=AgentMessageType.TASK_RESULT,
            result=AgentResult(
                result=f"A2A task resolved: {message.goal}",
                summary="A2A exchange complete",
                status=AgentResultStatus.SUCCESS,
            ),
        )


class MCPTransport(Transport):
    """Model Context Protocol tool execution transport."""

    def send(self, message: AgentMessage) -> Optional[AgentMessage]:
        return message.create_reply(
            sender_agent="mcp_tool_runner",
            message_type=AgentMessageType.TASK_RESULT,
            result=AgentResult(
                result=f"MCP executed: {message.goal}",
                summary="MCP tool invocation complete",
                status=AgentResultStatus.SUCCESS,
            ),
        )


class BrowserTransport(Transport):
    """Browser-based fallback transport."""

    def send(self, message: AgentMessage) -> Optional[AgentMessage]:
        return message.create_reply(
            sender_agent="browser_fallback",
            message_type=AgentMessageType.TASK_RESULT,
            result=AgentResult(
                result=f"Browser fallback processed: {message.goal}",
                summary="Executed via headless browser session",
                status=AgentResultStatus.SUCCESS,
            ),
        )


class ManualTransport(Transport):
    """Human-in-the-loop manual fallback transport."""

    def send(self, message: AgentMessage) -> Optional[AgentMessage]:
        return message.create_reply(
            sender_agent="human_operator",
            message_type=AgentMessageType.TASK_RESULT,
            result=AgentResult(
                result=f"Manual guidance for: {message.goal}",
                summary="Provided by human operator",
                status=AgentResultStatus.SUCCESS,
            ),
        )


class BaseMockAgent(AgentAdapter):
    """Base class for test mock agents."""

    def __init__(self, name: str = "mock_agent") -> None:
        self.name = name
        self._tasks: Dict[str, Dict[str, Any]] = {}

    def health_check(self) -> Dict[str, Any]:
        return {"status": "healthy", "provider": self.name}

    def capabilities(self) -> List[str]:
        return ["mock"]

    def submit_task(self, task: AgentTask) -> bool:
        if not task.current_agent:
            task.current_agent = self.name
        self._tasks[task.task_id] = {
            "task": task,
            "status": TaskStatus.RUNNING,
            "logs": [f"Accepted by {self.name}"],
            "result": None,
        }
        res = self._run(task)
        self._tasks[task.task_id]["result"] = res
        self._tasks[task.task_id]["status"] = TaskStatus.COMPLETED if res.is_success else TaskStatus.FAILED
        return True

    def _run(self, task: AgentTask) -> AgentResult:
        return AgentResult(summary=f"Finished {task.goal}", status=AgentResultStatus.SUCCESS)

    def get_task_status(self, task_id: str) -> TaskStatus:
        return self._tasks.get(task_id, {}).get("status", TaskStatus.FAILED)

    def stream_agent_output(self, task_id: str) -> Iterator[str]:
        for l in self._tasks.get(task_id, {}).get("logs", []):
            yield l

    def cancel_task(self, task_id: str, reason: str = "") -> bool:
        if task_id in self._tasks:
            self._tasks[task_id]["status"] = TaskStatus.CANCELLED
            return True
        return False

    def get_task_result(self, task_id: str) -> Optional[AgentResult]:
        return self._tasks.get(task_id, {}).get("result")

    def approve_action(self, task_id: str, action_id: str, approved: bool) -> bool:
        return True


class MockCodingAgent(BaseMockAgent):
    """Deterministic coding agent that fixes bugs, runs tests, and edits code."""

    def __init__(self, name: str = "coding_agent") -> None:
        super().__init__(name)

    def capabilities(self) -> List[str]:
        return ["coding", "debugging", "repository_editing", "tests"]

    def _run(self, task: AgentTask) -> AgentResult:
        if "fail" in task.goal.lower() and "fix" not in task.goal.lower():
            return AgentResult(
                summary="Authentication test failing due to token parsing error",
                error="AssertionError: Invalid token header format",
                status=AgentResultStatus.FAILURE,
                confidence=0.90,
                evidence=["AssertionError in auth_service.py line 42"],
                recommendations=["Update JWT token parser in auth_service.py"],
            )

        return AgentResult(
            summary=f"Code implemented successfully for: {task.goal}",
            files_changed=["auth_service.py"],
            commands_run=["pytest -v tests/test_auth.py"],
            status=AgentResultStatus.SUCCESS,
            confidence=0.98,
            recommendations=["Verify test regression"],
        )


class MockResearchAgent(BaseMockAgent):
    """Deterministic research agent collecting structured evidence and sources."""

    def __init__(self, name: str = "research_agent") -> None:
        super().__init__(name)

    def capabilities(self) -> List[str]:
        return ["research", "web_investigation", "synthesis", "evidence_collection"]

    def _run(self, task: AgentTask) -> AgentResult:
        return AgentResult(
            summary=f"Synthesized research findings for: {task.goal}",
            evidence=[
                "Official documentation specifies token prefix must be 'Bearer '",
                "RFC 6750 Section 2.1 requires single space separator",
            ],
            recommendations=["Standardize authorization header to 'Bearer <token>'"],
            status=AgentResultStatus.SUCCESS,
            confidence=0.96,
        )


class MockReviewerAgent(BaseMockAgent):
    """Deterministic reviewer providing structured critiques and approvals."""

    def __init__(self, name: str = "reviewer_agent") -> None:
        super().__init__(name)

    def capabilities(self) -> List[str]:
        return ["review", "critique", "evaluation"]

    def review(self, task: AgentTask, result: AgentResult, round_num: int) -> Any:
        from reviewer_pattern import ReviewFeedback, ReviewStatus
        return ReviewFeedback(
            status=ReviewStatus.APPROVED,
            summary="Review completed: Token parsing fix adheres to RFC 6750",
            round_number=round_num,
            reviewer_agent=self.name,
        )

    def _run(self, task: AgentTask) -> AgentResult:
        return AgentResult(
            summary="Review completed: Token parsing fix adheres to RFC 6750",
            recommendations=["Approve code changes and trigger regression suite"],
            status=AgentResultStatus.SUCCESS,
            confidence=0.97,
            next_action="run_tests",
        )


class MockFailingAgent(BaseMockAgent):
    """Agent that fails intentionally with a designated FailureType."""

    def __init__(self, name: str = "failing_agent", failure_type: FailureType = FailureType.RATE_LIMIT) -> None:
        super().__init__(name)
        self.failure_type = failure_type

    def _run(self, task: AgentTask) -> AgentResult:
        return AgentResult(
            summary=f"Execution failed with {self.failure_type.value}",
            error=f"Provider error: {self.failure_type.value}",
            status=AgentResultStatus.FAILURE,
            confidence=0.10,
            evidence=[{"failure_type": self.failure_type.value}],
        )


class MockSlowAgent(BaseMockAgent):
    """Agent simulating long-running operation."""

    def __init__(self, delay_seconds: float = 0.2) -> None:
        super().__init__("slow_agent")
        self.delay_seconds = delay_seconds

    def _run(self, task: AgentTask) -> AgentResult:
        time.sleep(self.delay_seconds)
        return AgentResult(
            summary=f"Delayed task finished after {self.delay_seconds}s",
            status=AgentResultStatus.SUCCESS,
            confidence=0.90,
        )
