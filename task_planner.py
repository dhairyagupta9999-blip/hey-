"""Task Decomposition and Dependency-Aware Task Graph for Hey Jev.

Supports DAG-based parallel, sequential, dependent, and parent/child tasks.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Set

from agent_task import AgentTask, TaskPriority, TaskStatus


class CyclicDependencyError(ValueError):
    """Raised when a task dependency creates a cycle in the task graph."""
    pass


class TaskGraph:
    """Directed Acyclic Graph (DAG) representing tasks and execution dependencies."""

    def __init__(self) -> None:
        self.tasks: Dict[str, AgentTask] = {}
        # edges: task_id -> set of task_ids that it depends on
        self._dependencies: Dict[str, Set[str]] = {}
        # reverse edges: task_id -> set of task_ids waiting for it
        self._dependents: Dict[str, Set[str]] = {}

    def add_task(self, task: AgentTask) -> None:
        """Add a task node to the graph."""
        self.tasks[task.task_id] = task
        if task.task_id not in self._dependencies:
            self._dependencies[task.task_id] = set()
        if task.task_id not in self._dependents:
            self._dependents[task.task_id] = set()

        # Wire any dependencies listed on the task
        for dep_id in task.dependencies:
            self.add_dependency(task.task_id, dep_id)

    def add_dependency(self, task_id: str, depends_on_id: str) -> None:
        """Declare that `task_id` cannot run until `depends_on_id` is COMPLETED."""
        if task_id not in self._dependencies:
            self._dependencies[task_id] = set()
        if depends_on_id not in self._dependents:
            self._dependents[depends_on_id] = set()

        self._dependencies[task_id].add(depends_on_id)
        self._dependents[depends_on_id].add(task_id)

        # Synchronize task's internal dependencies list
        if task_id in self.tasks:
            if depends_on_id not in self.tasks[task_id].dependencies:
                self.tasks[task_id].dependencies.append(depends_on_id)

        if not self.is_acyclic():
            # Rollback
            self._dependencies[task_id].remove(depends_on_id)
            self._dependents[depends_on_id].remove(task_id)
            if task_id in self.tasks and depends_on_id in self.tasks[task_id].dependencies:
                self.tasks[task_id].dependencies.remove(depends_on_id)
            raise CyclicDependencyError(f"Adding dependency {task_id} -> {depends_on_id} would create a cycle.")

    def get_dependencies(self, task_id: str) -> Set[str]:
        """Return the set of tasks that `task_id` depends on."""
        return set(self._dependencies.get(task_id, set()))

    def get_dependents(self, task_id: str) -> Set[str]:
        """Return the set of tasks that depend on `task_id`."""
        return set(self._dependents.get(task_id, set()))

    def get_children(self, parent_id: str) -> List[AgentTask]:
        """Return all tasks whose parent_task_id equals parent_id."""
        return [t for t in self.tasks.values() if t.parent_task_id == parent_id]

    def get_ready_tasks(self) -> List[AgentTask]:
        """Return all QUEUED tasks whose dependencies are all COMPLETED."""
        ready: List[AgentTask] = []
        for task_id, task in self.tasks.items():
            if task.status != TaskStatus.QUEUED:
                continue

            deps = self._dependencies.get(task_id, set())
            if not deps:
                ready.append(task)
            else:
                all_completed = True
                for dep_id in deps:
                    dep_task = self.tasks.get(dep_id)
                    if not dep_task or dep_task.status != TaskStatus.COMPLETED:
                        all_completed = False
                        break
                if all_completed:
                    ready.append(task)
        return ready

    def is_acyclic(self) -> bool:
        """Check if graph is a valid DAG (has no directed cycles)."""
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def dfs(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            for neighbor in self._dependencies.get(node, set()):
                if neighbor not in visited:
                    if not dfs(neighbor):
                        return False
                elif neighbor in rec_stack:
                    return False
            rec_stack.remove(node)
            return True

        for node in list(self._dependencies.keys()):
            if node not in visited:
                if not dfs(node):
                    return False
        return True

    def topological_sort(self) -> List[str]:
        """Return task IDs in topological execution order (dependencies first)."""
        if not self.is_acyclic():
            raise CyclicDependencyError("Cannot topologically sort a graph with cycles.")

        # In-degree computation where in-degree is number of unsatisfied dependencies
        in_degrees: Dict[str, int] = {t_id: len(deps) for t_id, deps in self._dependencies.items()}
        zero_in = [t_id for t_id, deg in in_degrees.items() if deg == 0]
        order: List[str] = []

        while zero_in:
            node = zero_in.pop(0)
            order.append(node)
            for dep_of in self._dependents.get(node, set()):
                in_degrees[dep_of] -= 1
                if in_degrees[dep_of] == 0:
                    zero_in.append(dep_of)

        return order


class TaskPlanner:
    """Decomposes high-level goals into dependency-aware TaskGraphs."""

    def decompose_goal(self, goal: str, context: Optional[Dict[str, Any]] = None) -> TaskGraph:
        """Generate a TaskGraph decomposition for a given goal."""
        graph = TaskGraph()
        ctx = dict(context or {})

        # Create root parent task
        root_id = f"task_{uuid.uuid4().hex[:10]}"
        root_task = AgentTask(
            task_id=root_id,
            goal=goal,
            description=f"Root goal: {goal}",
            status=TaskStatus.QUEUED,
            priority=TaskPriority.NORMAL,
            context=ctx,
        )
        graph.add_task(root_task)

        goal_lower = goal.lower()

        # Pattern 1: Debugging / Fixing problem
        if any(w in goal_lower for w in ["fix", "bug", "error", "problem", "broken", "issue"]):
            t1 = AgentTask(
                task_id=f"{root_id}_inspect",
                parent_task_id=root_id,
                goal=f"Inspect project status and logs for: {goal}",
                description="Diagnose logs, status, and error stacktraces",
                status=TaskStatus.QUEUED,
            )
            t2 = AgentTask(
                task_id=f"{root_id}_identify",
                parent_task_id=root_id,
                goal="Identify root cause of failure",
                description="Pinpoint defect location and cause",
                status=TaskStatus.QUEUED,
            )
            t3 = AgentTask(
                task_id=f"{root_id}_research",
                parent_task_id=root_id,
                goal="Research likely cause and solution options",
                description="Examine documentation and code patterns",
                status=TaskStatus.QUEUED,
            )
            t4 = AgentTask(
                task_id=f"{root_id}_propose",
                parent_task_id=root_id,
                goal="Propose fix and review approach",
                description="Formulate concrete repair strategy",
                status=TaskStatus.QUEUED,
            )
            t5 = AgentTask(
                task_id=f"{root_id}_implement",
                parent_task_id=root_id,
                goal="Implement fix",
                description="Apply code or system changes",
                status=TaskStatus.QUEUED,
            )
            t6 = AgentTask(
                task_id=f"{root_id}_test",
                parent_task_id=root_id,
                goal="Run tests to verify fix",
                description="Execute test suite and regressions",
                status=TaskStatus.QUEUED,
            )
            t7 = AgentTask(
                task_id=f"{root_id}_verify",
                parent_task_id=root_id,
                goal="Verify overall result against goal",
                description="Independent deterministic outcome validation",
                status=TaskStatus.QUEUED,
            )

            for t in [t1, t2, t3, t4, t5, t6, t7]:
                graph.add_task(t)

            # Dependencies: t1 -> t2 -> t3 -> t4 -> t5 -> t6 -> t7
            graph.add_dependency(t2.task_id, t1.task_id)
            graph.add_dependency(t3.task_id, t2.task_id)
            graph.add_dependency(t4.task_id, t3.task_id)
            graph.add_dependency(t5.task_id, t4.task_id)
            graph.add_dependency(t6.task_id, t5.task_id)
            graph.add_dependency(t7.task_id, t6.task_id)

        # Pattern 2: Deep research / synthesis (parallel fan-out, fan-in)
        elif any(w in goal_lower for w in ["research", "compare", "hotels", "investigate"]):
            sub_a = AgentTask(
                task_id=f"{root_id}_res_a",
                parent_task_id=root_id,
                goal=f"Research primary query: {goal}",
                status=TaskStatus.QUEUED,
            )
            sub_b = AgentTask(
                task_id=f"{root_id}_res_b",
                parent_task_id=root_id,
                goal=f"Research alternatives and price benchmarks for: {goal}",
                status=TaskStatus.QUEUED,
            )
            sub_syn = AgentTask(
                task_id=f"{root_id}_synthesis",
                parent_task_id=root_id,
                goal="Synthesize research findings into actionable recommendations",
                status=TaskStatus.QUEUED,
            )
            sub_ver = AgentTask(
                task_id=f"{root_id}_verify",
                parent_task_id=root_id,
                goal="Verify citations and completeness",
                status=TaskStatus.QUEUED,
            )

            for t in [sub_a, sub_b, sub_syn, sub_ver]:
                graph.add_task(t)

            # Parallel sub_a and sub_b converge on sub_syn, then sub_ver
            graph.add_dependency(sub_syn.task_id, sub_a.task_id)
            graph.add_dependency(sub_syn.task_id, sub_b.task_id)
            graph.add_dependency(sub_ver.task_id, sub_syn.task_id)

        # Default: Two-stage plan -> execute & verify
        else:
            step_exec = AgentTask(
                task_id=f"{root_id}_execute",
                parent_task_id=root_id,
                goal=f"Execute: {goal}",
                status=TaskStatus.QUEUED,
            )
            step_ver = AgentTask(
                task_id=f"{root_id}_verify",
                parent_task_id=root_id,
                goal=f"Verify execution of: {goal}",
                status=TaskStatus.QUEUED,
            )
            graph.add_task(step_exec)
            graph.add_task(step_ver)
            graph.add_dependency(step_ver.task_id, step_exec.task_id)

        return graph
