"""Prioritized Context Assembly and Least-Privilege Agent Handoff for Hey Jev.

Assembles compact, non-bloated context packages for agents using strict priority ordering:
1. Current Task goal & constraints
2. Active Project metadata & Git status
3. Active Mission objectives
4. Recent relevant memories (within project/task scope)
5. Historical decisions & failure lessons
6. General user preferences (only if authorized)

Security:
- Enforces sensitivity limits: agents without elevated permissions never receive PRIVATE/SENSITIVE memories
- Limits context window footprint to prevent degradation on dual-core CPU
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent_task import AgentTask, TaskPermission
from context_snapshot import ContextSnapshot
from git_inspector import GitInspector
from memory_model import MemoryRecord, MemorySensitivity, MemoryType
from memory_store import MemoryStore
from mission import Mission
from project_model import Project


class ContextBuilder:
    """Builds prioritized, least-privilege context packages for agents."""

    def __init__(
        self,
        memory_store: Optional[MemoryStore] = None,
        git_inspector: Optional[GitInspector] = None,
    ) -> None:
        self.memory_store = memory_store
        self.git_inspector = git_inspector or GitInspector()

    def build_context(
        self,
        task: Optional[AgentTask] = None,
        project: Optional[Project] = None,
        mission: Optional[Mission] = None,
        env_snapshot: Optional[ContextSnapshot] = None,
        caller_permissions: Optional[List[TaskPermission]] = None,
        max_memories: int = 5,
    ) -> Dict[str, Any]:
        """Assemble a prioritized context package."""
        ctx: Dict[str, Any] = {
            "task": None,
            "project": None,
            "git": None,
            "mission": None,
            "environment": None,
            "memories": [],
        }

        # 1. Current Task
        if task:
            ctx["task"] = {
                "task_id": task.task_id,
                "goal": task.goal,
                "description": task.description,
                "priority": task.priority.value if hasattr(task.priority, "value") else str(task.priority),
            }

        # 2. Active Project & Git State
        if project:
            ctx["project"] = {
                "project_id": project.project_id,
                "name": project.name,
                "root_path": project.root_path,
                "languages": project.languages,
                "frameworks": project.frameworks,
            }
            if project.root_path:
                git_data = self.git_inspector.inspect(project.root_path)
                if git_data.get("is_git"):
                    ctx["git"] = {
                        "branch": git_data.get("branch"),
                        "commit": git_data.get("commit"),
                        "clean": git_data.get("clean"),
                        "modified_count": git_data.get("modified_count"),
                    }

        # 3. Active Mission
        if mission:
            ctx["mission"] = {
                "mission_id": mission.mission_id,
                "title": mission.title,
                "description": mission.description,
            }

        # 4. Environment
        if env_snapshot:
            ctx["environment"] = {
                "active_app": env_snapshot.active_app,
                "window_title": env_snapshot.window_title,
                "browser_url": env_snapshot.browser_url,
            }

        # 5. Prioritized Memories with Least-Privilege Filtering
        if self.memory_store and (task or project):
            query = task.goal if task else (project.name if project else "")
            proj_id = project.project_id if project else None

            # Permission check: PRIVATE / SENSITIVE requires explicit permission
            granted = set(caller_permissions or [])
            max_sens = (
                MemorySensitivity.PRIVATE
                if TaskPermission.READ_FILES in granted
                else MemorySensitivity.NORMAL
            )

            filter_opts: Dict[str, Any] = {"max_sensitivity": max_sens}
            if proj_id:
                filter_opts["project_id"] = proj_id

            retrieved = self.memory_store.search(
                query=query,
                filters=filter_opts,
                limit=max_memories,
            )

            # Sort by priority: Project Decisions > Lessons > General facts
            def priority_key(m: MemoryRecord) -> int:
                if m.type in (MemoryType.PROJECT_DECISION, MemoryType.PROJECT_STATE):
                    return 3
                if m.type in (MemoryType.SKILL_LESSON, MemoryType.TOOL_LESSON, MemoryType.AGENT_LESSON):
                    return 2
                if m.type == MemoryType.USER_PREFERENCE:
                    return 1
                return 0

            ranked = sorted(retrieved, key=priority_key, reverse=True)
            ctx["memories"] = [
                {
                    "memory_id": m.memory_id,
                    "type": m.type.value,
                    "content": m.content,
                    "importance": m.importance,
                    "confidence": m.confidence,
                }
                for m in ranked[:max_memories]
            ]

        return ctx

    def format_prompt_context(self, context_dict: Dict[str, Any]) -> str:
        """Format assembled context into a concise markdown injection string."""
        parts: List[str] = ["### Context"]

        # Task
        t = context_dict.get("task")
        if t:
            parts.append(f"- **Goal**: {t.get('goal')}")

        # Project
        p = context_dict.get("project")
        if p:
            parts.append(f"- **Project**: {p.get('name')} (`{p.get('root_path')}`)")
            g = context_dict.get("git")
            if g:
                parts.append(f"  * Git: branch `{g.get('branch')}`, clean: {g.get('clean')}")

        # Mission
        m = context_dict.get("mission")
        if m:
            parts.append(f"- **Mission**: {m.get('title')}")

        # Environment
        env = context_dict.get("environment")
        if env and (env.get("active_app") or env.get("window_title")):
            parts.append(f"- **Active Window**: {env.get('active_app')} - \"{env.get('window_title')}\"")

        # Relevant Memories
        mems = context_dict.get("memories", [])
        if mems:
            parts.append("- **Relevant Context & Memory**:")
            for item in mems:
                parts.append(f"  * [{item.get('type')}] {item.get('content')}")

        return "\n".join(parts)
