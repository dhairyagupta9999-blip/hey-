"""Antigravity Agent Provider Adapter for Hey Jev.

Connects Hey Jev's orchestrator to the local Antigravity environment
using real CLI tools (agentapi.bat), local brain filesystem artifacts,
and transcript logging.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Dict, Iterator, List, Optional

from agent_adapter import AgentAdapter
from agent_result import AgentResult, AgentResultStatus
from agent_task import AgentTask, TaskPermission, TaskStatus


class AntigravityAdapter(AgentAdapter):
    """Adapter for Google DeepMind's Antigravity agent system."""

    DEFAULT_BASE_DIR = os.path.expanduser(r"~\.gemini\antigravity")

    def __init__(
        self,
        base_dir: Optional[str] = None,
        cli_path: Optional[str] = None,
        active_conversation_id: Optional[str] = None,
    ) -> None:
        self.base_dir = base_dir or self.DEFAULT_BASE_DIR
        self.bin_dir = os.path.join(self.base_dir, "bin")
        self.brain_dir = os.path.join(self.base_dir, "brain")

        # Detect CLI
        candidate_cli = cli_path or os.path.join(self.bin_dir, "agentapi.bat")
        if os.path.exists(candidate_cli):
            self.cli_path = candidate_cli
        else:
            self.cli_path = None

        self.active_conversation_id = active_conversation_id
        self._active_tasks: Dict[str, Dict[str, Any]] = {}

    def health_check(self) -> Dict[str, Any]:
        """Verify presence of local Antigravity environment and CLI."""
        installed = os.path.exists(self.base_dir)
        cli_present = bool(self.cli_path and os.path.exists(self.cli_path))
        brain_present = os.path.exists(self.brain_dir)

        status = "healthy" if (installed and (cli_present or brain_present)) else "degraded"
        return {
            "status": status,
            "provider": "antigravity",
            "base_dir": self.base_dir,
            "cli_path": self.cli_path,
            "cli_available": cli_present,
            "brain_available": brain_present,
            "active_conversation": self.active_conversation_id,
        }

    def capabilities(self) -> List[str]:
        """Return supported capabilities."""
        return [
            "coding",
            "debugging",
            "repository_editing",
            "command_execution",
            "file_inspection",
            "artifact_generation",
            "transcript_audit",
        ]

    def discover(self) -> Dict[str, Any]:
        """Discover existing conversations and artifacts."""
        conversations: List[str] = []
        if os.path.exists(self.brain_dir):
            try:
                for entry in os.listdir(self.brain_dir):
                    full = os.path.join(self.brain_dir, entry)
                    if os.path.isdir(full) and not entry.startswith("."):
                        conversations.append(entry)
            except Exception:
                pass

        return {
            "name": "antigravity",
            "capabilities": self.capabilities(),
            "discovered_conversations": conversations,
            "health": self.health_check(),
        }

    def submit_task(self, task: AgentTask) -> bool:
        """Submit task to Antigravity provider."""
        # Require file/command permissions if needed
        task.current_agent = "antigravity"
        if "antigravity" not in task.assigned_agents:
            task.assigned_agents.append("antigravity")

        record = {
            "task": task,
            "status": TaskStatus.RUNNING,
            "logs": [f"Task {task.task_id} accepted by Antigravity adapter"],
            "created_at": time.time(),
            "result": None,
            "cancelled": False,
        }
        self._active_tasks[task.task_id] = record

        try:
            task.transition_to(TaskStatus.RUNNING, reason="Accepted by Antigravity adapter")
        except Exception:
            pass

        # If a conversation is active, write task dispatch record to its scratch folder
        conv_id = self.active_conversation_id
        if conv_id:
            scratch_dir = os.path.join(self.brain_dir, conv_id, "scratch")
            if os.path.exists(scratch_dir):
                dispatch_file = os.path.join(scratch_dir, f"{task.task_id}.json")
                try:
                    with open(dispatch_file, "w", encoding="utf-8") as f:
                        json.dump(task.to_dict(), f, indent=2)
                    record["logs"].append(f"Dispatched task payload to {dispatch_file}")
                except Exception as e:
                    record["logs"].append(f"Notice: scratch write failed ({e})")

        return True

    def get_task_status(self, task_id: str) -> TaskStatus:
        """Get status of task."""
        if task_id not in self._active_tasks:
            return TaskStatus.FAILED
        record = self._active_tasks[task_id]
        if record.get("cancelled"):
            return TaskStatus.CANCELLED
        return record.get("status", TaskStatus.RUNNING)

    def stream_agent_output(self, task_id: str) -> Iterator[str]:
        """Stream log lines from task execution."""
        if task_id not in self._active_tasks:
            yield f"Task {task_id} not found."
            return

        record = self._active_tasks[task_id]
        for log in record["logs"]:
            yield log

    def cancel_task(self, task_id: str, reason: str = "") -> bool:
        """Cancel task running under Antigravity."""
        if task_id not in self._active_tasks:
            return False

        record = self._active_tasks[task_id]
        record["cancelled"] = True
        record["status"] = TaskStatus.CANCELLED
        record["logs"].append(f"Task cancelled: {reason}")
        task: AgentTask = record["task"]
        task.request_cancellation(reason=reason or "Antigravity adapter cancellation")
        return True

    def complete_task(
        self,
        task_id: str,
        result: AgentResult,
    ) -> None:
        """Helper to mark task completed with an AgentResult."""
        if task_id not in self._active_tasks:
            return
        record = self._active_tasks[task_id]
        record["result"] = result
        record["status"] = TaskStatus.COMPLETED if result.is_success else TaskStatus.FAILED
        record["logs"].append(f"Task completed with status {result.status.value}")
        task: AgentTask = record["task"]
        task.outputs = result.to_dict()
        task.artifacts.extend(result.artifacts)
        if result.error:
            task.add_error(result.error)
        task.recommendations.extend(result.recommendations)
        task.next_action = result.next_action
        task.confidence = result.confidence

    def get_task_result(self, task_id: str) -> Optional[AgentResult]:
        """Return the structured AgentResult if finished."""
        if task_id not in self._active_tasks:
            return None
        return self._active_tasks[task_id].get("result")

    def approve_action(self, task_id: str, action_id: str, approved: bool) -> bool:
        """Handle human approval response."""
        if task_id not in self._active_tasks:
            return False
        record = self._active_tasks[task_id]
        task: AgentTask = record["task"]
        task.approval_state = "APPROVED" if approved else "REJECTED"
        record["logs"].append(f"Action {action_id} {'approved' if approved else 'rejected'}")
        return True

    def query_conversation_metadata(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Call CLI agentapi.bat get-conversation-metadata if available."""
        if not self.cli_path or not os.path.exists(self.cli_path):
            return None

        cmd = [self.cli_path, "get-conversation-metadata", conversation_id]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0 and res.stdout.strip():
                return json.loads(res.stdout)
        except Exception:
            pass
        return None
