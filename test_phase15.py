"""Phase 15: Jev Skill System & Dynamic Tool Registry Comprehensive Test Suite.

Tests:
1. ToolDefinition schema, risk classification, health states, serialization
2. ToolResult structured outputs, dry-run flags, and rollback metadata
3. ToolRegistry registration, capability indexing, category/permission searches
4. ToolMatcher multi-factor quality scoring, health penalties, cost/latency weights
5. ToolExecutor input schema validation (types & required fields)
6. ToolExecutor permission checking and risk approval gating
7. ToolExecutor dry-run preview simulation
8. ToolExecutor rollback metadata extraction
9. ToolExecutor automatic tool failover on unhealthy/failing tools
10. SkillDefinition schema, version pinning, trust tiers, and serialization
11. SkillRegistry semantic version-pinned retrieval and validation
12. SkillMatcher natural-language goal routing across domain patterns
13. SkillExecutor workflow requirements validation
14. SkillExecutor permission gating and untrusted skill restrictions
15. SkillExecutor conditional workflow branching
16. SkillExecutor context propagation ($key substitution) and failure lessons
17. ToolScanner dynamic tool discovery without code duplication
18. Built-in skill registration suite (9 skills)
19. Demo 1: "Open the fourth picture" (Goal -> Skill -> Tool -> Filter -> Verify)
20. Demo 2: "Research this topic deeply" (Goal -> Skill -> Search -> Synthesis -> Evidence)
21. Demo 3: "Organize test files" (Goal -> Skill -> Dry Run -> Execution -> Rollback)
22. Demo 4: Tool Failover (Tool A Unavailable -> Tool B Fallback -> Success)
23. Demo 5: Least Privilege Enforcement (Network Agent -> File Blocked, Web Allowed)
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

import pytest

# Ensure hey-jev root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_result import AgentResult, AgentResultStatus
from agent_task import TaskPermission
from builtin_skills import get_builtin_skills, register_builtin_skills
from event_bus import EventBus
from skill_executor import FailureLesson, SkillExecutor
from skill_matcher import ScoredSkill, SkillMatcher
from skill_model import SkillDefinition, SkillStep, SkillTrustLevel
from skill_registry import SkillRegistry
from tool_executor import ToolExecutor
from tool_matcher import ScoredTool, ToolMatcher
from tool_model import RiskLevel, ToolDefinition, ToolHealth, ToolResult
from tool_registry import ToolRegistry
from tool_scanner import ToolScanner


class TestPhase15SkillSystemAndToolRegistry:
    """Test suite for Phase 15 Skill System & Dynamic Tool Registry."""

    def setup_method(self) -> None:
        self.tool_reg = ToolRegistry()
        self.skill_reg = SkillRegistry()
        self.event_bus = EventBus()
        self.tool_executor = ToolExecutor(registry=self.tool_reg, event_bus=self.event_bus)
        self.skill_executor = SkillExecutor(
            tool_registry=self.tool_reg,
            tool_executor=self.tool_executor,
            event_bus=self.event_bus,
        )
        self.tool_matcher = ToolMatcher(registry=self.tool_reg)
        self.skill_matcher = SkillMatcher(registry=self.skill_reg)
        self.temp_dir = tempfile.mkdtemp(prefix="heyjev_phase15_test_")

    def teardown_method(self) -> None:
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -----------------------------------------------------------------
    # 1. ToolDefinition Schema & Serialization
    # -----------------------------------------------------------------
    def test_tool_definition_schema_and_serialization(self) -> None:
        tool = ToolDefinition(
            tool_id="test.sample_tool",
            name="Sample Tool",
            description="A test tool definition",
            version="1.2.0",
            provider="test_provider",
            category="testing",
            capabilities=["testing", "verification"],
            input_schema={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
            output_schema={"type": "object", "properties": {"res": {"type": "string"}}},
            permissions=[TaskPermission.READ_FILES],
            risk_level=RiskLevel.MEDIUM,
            cost_class="free",
            latency_class="fast",
            health=ToolHealth.HEALTHY,
            supports_dry_run=True,
            supports_rollback=True,
            license="MIT",
        )

        d = tool.to_dict()
        assert d["tool_id"] == "test.sample_tool"
        assert d["risk_level"] == "MEDIUM"
        assert d["health"] == "HEALTHY"
        assert d["permissions"] == ["READ_FILES"]
        assert d["supports_rollback"] is True

        reconstructed = ToolDefinition.from_dict(d)
        assert reconstructed.tool_id == tool.tool_id
        assert reconstructed.risk_level == RiskLevel.MEDIUM
        assert reconstructed.health == ToolHealth.HEALTHY
        assert reconstructed.permissions == [TaskPermission.READ_FILES]

    # -----------------------------------------------------------------
    # 2. ToolResult Structured Outputs & Dry-Run
    # -----------------------------------------------------------------
    def test_tool_result_schema_and_helpers(self) -> None:
        res_ok = ToolResult(
            tool_id="test.op",
            status="SUCCESS",
            output={"data": 42},
            evidence=["Generated 42"],
            before_state={"val": 0},
            after_state={"val": 42},
            rollback_metadata={"undo": "reset"},
        )
        assert res_ok.is_success is True
        assert res_ok.is_dry_run is False
        assert res_ok.rollback_metadata["undo"] == "reset"

        res_dry = ToolResult(
            tool_id="test.op",
            status="DRY_RUN",
            output="Preview only",
            is_dry_run=True,
        )
        assert res_dry.is_success is True
        assert res_dry.is_dry_run is True

        res_fail = ToolResult(
            tool_id="test.op",
            status="FAILURE",
            error="Device error",
        )
        assert res_fail.is_success is False

    # -----------------------------------------------------------------
    # 3. ToolRegistry Registration & Lookups
    # -----------------------------------------------------------------
    def test_tool_registry_registration_and_lookup(self) -> None:
        handler_called = False

        def sample_handler(**kwargs: Any) -> ToolResult:
            nonlocal handler_called
            handler_called = True
            return ToolResult(tool_id="calc.add", status="SUCCESS", output=kwargs.get("a", 0) + kwargs.get("b", 0))

        tool = ToolDefinition(
            tool_id="calc.add",
            name="Add Numbers",
            description="Addition utility",
            capabilities=["math", "arithmetic"],
            permissions=[],
            risk_level=RiskLevel.LOW,
        )
        self.tool_reg.register(tool, handler=sample_handler)

        assert len(self.tool_reg) == 1
        fetched = self.tool_reg.get("calc.add")
        assert fetched is not None
        assert fetched.name == "Add Numbers"

        handler = self.tool_reg.get_handler("calc.add")
        assert handler is not None
        res = handler(a=10, b=5)
        assert handler_called is True
        assert res.output == 15

        # Unregister
        assert self.tool_reg.unregister("calc.add") is True
        assert self.tool_reg.get("calc.add") is None
        assert len(self.tool_reg) == 0

    # -----------------------------------------------------------------
    # 4. ToolRegistry Search & Filtering
    # -----------------------------------------------------------------
    def test_tool_registry_search_and_filters(self) -> None:
        t1 = ToolDefinition(
            tool_id="fs.read",
            name="Read File",
            description="Read text file contents",
            category="filesystem",
            capabilities=["filesystem", "file_reading"],
            permissions=[TaskPermission.READ_FILES],
            risk_level=RiskLevel.LOW,
            provider="local",
        )
        t2 = ToolDefinition(
            tool_id="fs.delete",
            name="Delete File",
            description="Delete file from disk",
            category="filesystem",
            capabilities=["filesystem", "file_deletion"],
            permissions=[TaskPermission.WRITE_FILES],
            risk_level=RiskLevel.HIGH,
            provider="local",
        )
        t3 = ToolDefinition(
            tool_id="net.fetch",
            name="Fetch URL",
            description="Download web resource",
            category="network",
            capabilities=["web", "download"],
            permissions=[TaskPermission.NETWORK_ACCESS],
            risk_level=RiskLevel.LOW,
            provider="remote",
        )
        self.tool_reg.register(t1)
        self.tool_reg.register(t2)
        self.tool_reg.register(t3)

        assert len(self.tool_reg.list(category="filesystem")) == 2
        assert len(self.tool_reg.list(category="network")) == 1

        # Search
        search_res = self.tool_reg.search("delete")
        assert len(search_res) > 0
        assert search_res[0].tool_id == "fs.delete"

        # By capability
        fs_tools = self.tool_reg.find_by_capability("filesystem")
        assert len(fs_tools) == 2

        # By permission
        read_tools = self.tool_reg.find_by_permission(TaskPermission.READ_FILES)
        assert len(read_tools) == 1
        assert read_tools[0].tool_id == "fs.read"

        # By risk level (max LOW should exclude HIGH)
        low_tools = self.tool_reg.find_by_risk(RiskLevel.LOW)
        assert len(low_tools) == 2
        assert all(t.risk_level == RiskLevel.LOW for t in low_tools)

        # By provider
        remote_tools = self.tool_reg.find_by_provider("remote")
        assert len(remote_tools) == 1
        assert remote_tools[0].tool_id == "net.fetch"

    # -----------------------------------------------------------------
    # 5. ToolRegistry Health & State Tracking
    # -----------------------------------------------------------------
    def test_tool_registry_health_and_update(self) -> None:
        t = ToolDefinition(
            tool_id="srv.ping",
            name="Ping Server",
            description="Ping health probe",
            health=ToolHealth.HEALTHY,
        )
        self.tool_reg.register(t)
        assert self.tool_reg.health_check("srv.ping") == ToolHealth.HEALTHY

        # Update health
        assert self.tool_reg.update_health("srv.ping", ToolHealth.DEGRADED) is True
        assert self.tool_reg.health_check("srv.ping") == ToolHealth.DEGRADED

        assert self.tool_reg.health_check("non_existent") == ToolHealth.UNAVAILABLE

    # -----------------------------------------------------------------
    # 6. ToolMatcher Multi-Factor Quality Scoring
    # -----------------------------------------------------------------
    def test_tool_matcher_quality_scoring(self) -> None:
        t_healthy_free = ToolDefinition(
            tool_id="search.duckduckgo",
            name="DuckDuckGo Search",
            description="Free web search",
            capabilities=["web_search"],
            cost_class="free",
            latency_class="fast",
            health=ToolHealth.HEALTHY,
            supports_verification=True,
            supports_rollback=False,
            permissions=[TaskPermission.NETWORK_ACCESS],
        )
        t_healthy_paid = ToolDefinition(
            tool_id="search.google_api",
            name="Google API Search",
            description="Paid web search",
            capabilities=["web_search"],
            cost_class="high",
            latency_class="fast",
            health=ToolHealth.HEALTHY,
            permissions=[TaskPermission.NETWORK_ACCESS],
        )
        t_unhealthy = ToolDefinition(
            tool_id="search.broken_engine",
            name="Broken Search",
            description="Offline search engine",
            capabilities=["web_search"],
            health=ToolHealth.UNAVAILABLE,
            permissions=[TaskPermission.NETWORK_ACCESS],
        )
        self.tool_reg.register(t_healthy_free)
        self.tool_reg.register(t_healthy_paid)
        self.tool_reg.register(t_unhealthy)

        ranked = self.tool_matcher.match(
            required_capability="web_search",
            caller_permissions=[TaskPermission.NETWORK_ACCESS],
        )
        assert len(ranked) == 3
        # Free & healthy should rank #1
        assert ranked[0].tool.tool_id == "search.duckduckgo"
        assert ranked[0].quality_score > ranked[1].quality_score
        # Paid ranks #2
        assert ranked[1].tool.tool_id == "search.google_api"
        # Unhealthy gets 0.0 quality score
        assert ranked[2].tool.tool_id == "search.broken_engine"
        assert ranked[2].quality_score == 0.0

    # -----------------------------------------------------------------
    # 7. ToolMatcher Permission Penalization
    # -----------------------------------------------------------------
    def test_tool_matcher_permission_penalization(self) -> None:
        t = ToolDefinition(
            tool_id="admin.wipe",
            name="Wipe Disk",
            description="Dangerous admin operation",
            capabilities=["disk_mgmt"],
            permissions=[TaskPermission.WRITE_FILES, TaskPermission.EXECUTE_COMMAND],
        )
        self.tool_reg.register(t)

        # Matched with full permissions
        ranked_with_perm = self.tool_matcher.match(
            "disk_mgmt",
            caller_permissions=[TaskPermission.WRITE_FILES, TaskPermission.EXECUTE_COMMAND],
        )
        score_full = ranked_with_perm[0].quality_score

        # Matched without required permissions
        ranked_without_perm = self.tool_matcher.match(
            "disk_mgmt",
            caller_permissions=[],
        )
        score_penalized = ranked_without_perm[0].quality_score

        assert score_penalized == pytest.approx(score_full * 0.5, rel=1e-2)

    # -----------------------------------------------------------------
    # 8. ToolExecutor Input Schema Validation
    # -----------------------------------------------------------------
    def test_tool_executor_input_validation(self) -> None:
        tool = ToolDefinition(
            tool_id="test.validated_tool",
            name="Validated Tool",
            description="Tests input types",
            input_schema={
                "type": "object",
                "properties": {
                    "count": {"type": "integer"},
                    "name": {"type": "string"},
                    "flags": {"type": "array"},
                },
                "required": ["count", "name"],
            },
        )
        self.tool_reg.register(tool, handler=lambda **kw: ToolResult(tool_id="test.validated_tool", status="SUCCESS"))

        # Missing required parameter
        valid, errs = self.tool_executor.validate_input(tool, {"count": 10})
        assert valid is False
        assert any("Missing required parameter 'name'" in e for e in errs)

        # Wrong type
        valid, errs = self.tool_executor.validate_input(tool, {"count": "not_an_int", "name": "alice"})
        assert valid is False
        assert any("Parameter 'count' must be integer" in e for e in errs)

        # Valid input
        valid, errs = self.tool_executor.validate_input(tool, {"count": 5, "name": "bob", "flags": [1, 2]})
        assert valid is True
        assert len(errs) == 0

    # -----------------------------------------------------------------
    # 9. ToolExecutor Risk Gating & Human Approval Requirement
    # -----------------------------------------------------------------
    def test_tool_executor_risk_approval_gating(self) -> None:
        low_tool = ToolDefinition(
            tool_id="op.low",
            name="Low Risk",
            description="Safe read operation",
            risk_level=RiskLevel.LOW,
            permissions=[],
        )
        high_tool = ToolDefinition(
            tool_id="op.high",
            name="High Risk",
            description="Dangerous deletion operation",
            risk_level=RiskLevel.HIGH,
            permissions=[TaskPermission.WRITE_FILES],
        )
        self.tool_reg.register(low_tool, handler=lambda **kw: "low_ok")
        self.tool_reg.register(high_tool, handler=lambda **kw: "high_ok")

        # Low risk executes without approval
        res_low = self.tool_executor.execute("op.low", inputs={})
        assert res_low.status == "SUCCESS"
        assert res_low.output == "low_ok"

        # High risk BLOCKED when not approved
        res_high_blocked = self.tool_executor.execute(
            "op.high",
            inputs={},
            granted_permissions=[TaskPermission.WRITE_FILES],
            is_approved=False,
        )
        assert res_high_blocked.status == "BLOCKED"
        assert "requires explicit human approval" in res_high_blocked.error

        # High risk SUCCEEDS when explicitly approved
        res_high_approved = self.tool_executor.execute(
            "op.high",
            inputs={},
            granted_permissions=[TaskPermission.WRITE_FILES],
            is_approved=True,
        )
        assert res_high_approved.status == "SUCCESS"
        assert res_high_approved.output == "high_ok"

    # -----------------------------------------------------------------
    # 10. ToolExecutor Dry-Run Simulation
    # -----------------------------------------------------------------
    def test_tool_executor_dry_run(self) -> None:
        def custom_handler(**kwargs: Any) -> ToolResult:
            if kwargs.get("dry_run", False):
                return ToolResult(tool_id="db.reset", status="DRY_RUN", output="Would drop 5 tables", is_dry_run=True)
            return ToolResult(tool_id="db.reset", status="SUCCESS", output="Dropped 5 tables")

        tool = ToolDefinition(
            tool_id="db.reset",
            name="Reset DB",
            description="Database reset operation",
            supports_dry_run=True,
            input_schema={"type": "object", "properties": {"target": {"type": "string"}}},
        )
        self.tool_reg.register(tool, handler=custom_handler)

        dry_res = self.tool_executor.dry_run("db.reset", inputs={"target": "users"})
        assert dry_res.status == "DRY_RUN"
        assert dry_res.is_dry_run is True
        assert dry_res.output == "Would drop 5 tables"

    # -----------------------------------------------------------------
    # 11. ToolExecutor Rollback Metadata Generation
    # -----------------------------------------------------------------
    def test_tool_executor_rollback_metadata_generation(self) -> None:
        tool_move = ToolDefinition(
            tool_id="file.move",
            name="Move File",
            description="Move file utility",
            category="filesystem",
            supports_rollback=True,
        )
        self.tool_reg.register(
            tool_move,
            handler=lambda source, destination, **kw: ToolResult(
                tool_id="file.move",
                status="SUCCESS",
                output={"moved": True},
            ),
        )

        res = self.tool_executor.execute(
            "file.move",
            inputs={"source": "C:/a.txt", "destination": "C:/b.txt"},
            granted_permissions=[],
        )
        assert res.status == "SUCCESS"
        assert res.rollback_metadata["undo_action"] == "move"
        assert res.rollback_metadata["restore_source"] == "C:/b.txt"
        assert res.rollback_metadata["restore_destination"] == "C:/a.txt"

    # -----------------------------------------------------------------
    # 12. SkillDefinition Schema & Version-Pinned Retrieval
    # -----------------------------------------------------------------
    def test_skill_definition_schema_and_versioning(self) -> None:
        s_v1 = SkillDefinition(
            skill_id="data.cleaning",
            name="Data Cleaning",
            description="Clean test datasets",
            version="1.0.0",
            trust_level=SkillTrustLevel.COMMUNITY,
            license="Apache-2.0",
            permissions=[TaskPermission.READ_FILES],
        )
        s_v2 = SkillDefinition(
            skill_id="data.cleaning",
            name="Data Cleaning V2",
            description="Enhanced clean test datasets",
            version="2.0.0",
            trust_level=SkillTrustLevel.VERIFIED,
            license="Apache-2.0",
            permissions=[TaskPermission.READ_FILES, TaskPermission.WRITE_FILES],
        )

        assert s_v1.versioned_id == "data.cleaning@1.0.0"
        assert s_v2.versioned_id == "data.cleaning@2.0.0"

        self.skill_reg.register(s_v1)
        self.skill_reg.register(s_v2)

        # Pinned lookup
        assert self.skill_reg.get("data.cleaning@1.0.0").name == "Data Cleaning"
        assert self.skill_reg.get("data.cleaning@2.0.0").name == "Data Cleaning V2"
        # Default latest lookup
        assert self.skill_reg.get("data.cleaning").version == "2.0.0"
        # Version count
        assert len(self.skill_reg.list_all_versions("data.cleaning")) == 2

    # -----------------------------------------------------------------
    # 13. SkillRegistry Validation
    # -----------------------------------------------------------------
    def test_skill_registry_validation(self) -> None:
        invalid_skill = SkillDefinition(
            skill_id="invalid id with spaces!",
            name="",
            description="bad",
            license="",
        )
        valid, errs = self.skill_reg.validate(invalid_skill)
        assert valid is False
        assert any("Invalid skill_id" in e for e in errs)
        assert any("non-empty name" in e for e in errs)
        assert any("license" in e for e in errs)

    # -----------------------------------------------------------------
    # 14. SkillMatcher Natural-Language Goal Routing
    # -----------------------------------------------------------------
    def test_skill_matcher_natural_language_routing(self) -> None:
        register_builtin_skills(self.skill_reg)

        goals_expected = [
            ("Organize my messy Downloads folder", "downloads.organization"),
            ("Open the fourth picture from holiday", "windows.file_management"),
            ("Change my wallpaper to sunset.jpg", "windows.wallpaper"),
            ("Research best lightweight laptops in 2026", "web.research"),
            ("Navigate to https://news.ycombinator.com", "web.navigation"),
        ]

        for goal, expected_skill in goals_expected:
            matches = self.skill_matcher.match(goal)
            assert len(matches) > 0, f"No matches found for goal: {goal}"
            assert matches[0].skill.skill_id == expected_skill, (
                f"Expected top skill {expected_skill} for goal '{goal}', got {matches[0].skill.skill_id}"
            )

    # -----------------------------------------------------------------
    # 15. SkillExecutor Workflow Execution & Context Propagation
    # -----------------------------------------------------------------
    def test_skill_executor_context_propagation(self) -> None:
        # Step 1: produces count
        def step1_handler(**kw: Any) -> ToolResult:
            return ToolResult(tool_id="test.step1", status="SUCCESS", output={"file_count": 4})

        # Step 2: consumes count
        def step2_handler(**kw: Any) -> ToolResult:
            cnt = kw.get("file_count", 0)
            return ToolResult(tool_id="test.step2", status="SUCCESS", output={"doubled": cnt * 2})

        t1 = ToolDefinition(tool_id="test.step1", name="Step 1", description="Step 1 tool", capabilities=["cap1"])
        t2 = ToolDefinition(
            tool_id="test.step2",
            name="Step 2",
            description="Step 2 tool",
            capabilities=["cap2"],
            input_schema={"type": "object", "properties": {"file_count": {"type": "integer"}}},
        )
        self.tool_reg.register(t1, handler=step1_handler)
        self.tool_reg.register(t2, handler=step2_handler)

        workflow_skill = SkillDefinition(
            skill_id="test.calc_flow",
            name="Calc Flow",
            description="Two step calculation workflow",
            workflow=[
                {"step_id": "step_one", "tool_id": "test.step1"},
                {"step_id": "step_two", "tool_id": "test.step2", "inputs": {"file_count": "$file_count"}},
            ],
        )

        res = self.skill_executor.execute_skill(workflow_skill, inputs={})
        assert res.status == AgentResultStatus.SUCCESS
        assert res.result == {"doubled": 8}
        assert len(self.skill_executor.execution_history) == 1

    # -----------------------------------------------------------------
    # 16. SkillExecutor Conditional Branching
    # -----------------------------------------------------------------
    def test_skill_executor_conditional_branching(self) -> None:
        executed_branches = []

        def branch_handler(**kw: Any) -> ToolResult:
            name = kw.get("name", "branch")
            executed_branches.append(name)
            return ToolResult(tool_id="test.branch", status="SUCCESS", output=f"executed {name}")

        t = ToolDefinition(tool_id="test.branch", name="Branch", description="Branch test tool", capabilities=["branch"])
        self.tool_reg.register(t, handler=branch_handler)

        cond_skill = SkillDefinition(
            skill_id="test.conditional_flow",
            name="Conditional Flow",
            description="Workflow with condition",
            workflow=[
                {
                    "step_id": "branch_true",
                    "tool_id": "test.branch",
                    "inputs": {"name": "positive_branch"},
                    "condition": {"field": "target_count", "op": ">", "val": 0},
                },
                {
                    "step_id": "branch_false",
                    "tool_id": "test.branch",
                    "inputs": {"name": "negative_branch"},
                    "condition": {"field": "target_count", "op": "<=", "val": 0},
                },
            ],
        )

        # Run with target_count = 5 -> only branch_true should run
        executed_branches.clear()
        res1 = self.skill_executor.execute_skill(cond_skill, inputs={"target_count": 5})
        assert res1.status == AgentResultStatus.SUCCESS
        assert executed_branches == ["positive_branch"]

        # Run with target_count = -1 -> only branch_false should run
        executed_branches.clear()
        res2 = self.skill_executor.execute_skill(cond_skill, inputs={"target_count": -1})
        assert res2.status == AgentResultStatus.SUCCESS
        assert executed_branches == ["negative_branch"]

    # -----------------------------------------------------------------
    # 17. SkillExecutor Untrusted Skill Restrictions
    # -----------------------------------------------------------------
    def test_skill_executor_untrusted_skill_restrictions(self) -> None:
        t_high = ToolDefinition(
            tool_id="sys.rm",
            name="Delete",
            description="Dangerous",
            risk_level=RiskLevel.HIGH,
            permissions=[TaskPermission.WRITE_FILES],
        )
        self.tool_reg.register(t_high, handler=lambda **kw: "deleted")

        untrusted_skill = SkillDefinition(
            skill_id="untrusted.hack",
            name="Untrusted Attack",
            description="Untrusted skill attempting high risk operation",
            risk_level=RiskLevel.HIGH,
            trust_level=SkillTrustLevel.UNTRUSTED,
            workflow=[{"step_id": "s1", "tool_id": "sys.rm"}],
        )

        res = self.skill_executor.execute_skill(
            untrusted_skill,
            inputs={},
            granted_permissions=[TaskPermission.WRITE_FILES],
            is_approved=True,
        )
        assert res.status == AgentResultStatus.BLOCKED
        assert "Untrusted skills are prohibited" in res.error

    # -----------------------------------------------------------------
    # 18. SkillExecutor Failure Lesson Capture
    # -----------------------------------------------------------------
    def test_skill_executor_failure_lesson_capture(self) -> None:
        def failing_handler(**kw: Any) -> ToolResult:
            return ToolResult(tool_id="test.failing_tool", status="FAILURE", error="Network connection reset")

        t = ToolDefinition(tool_id="test.failing_tool", name="Failing Tool", description="Failing test tool", capabilities=["fail"])
        self.tool_reg.register(t, handler=failing_handler)

        fail_skill = SkillDefinition(
            skill_id="test.fail_flow",
            name="Failing Workflow",
            description="Workflow destined to fail",
            workflow=[{"step_id": "step_fail", "tool_id": "test.failing_tool"}],
        )

        res = self.skill_executor.execute_skill(fail_skill, inputs={})
        assert res.status == AgentResultStatus.FAILURE
        assert "Network connection reset" in res.error

        assert len(self.skill_executor.failure_lessons) == 1
        lesson = self.skill_executor.failure_lessons[0]
        assert lesson.tool_id == "test.failing_tool"
        assert "Network connection reset" in lesson.failure_reason

    # -----------------------------------------------------------------
    # 19. ToolScanner Dynamic Discovery
    # -----------------------------------------------------------------
    def test_tool_scanner_and_builtin_skills(self) -> None:
        scanner = ToolScanner(registry=self.tool_reg)
        count = scanner.scan_and_register_all()
        assert count >= 6
        assert self.tool_reg.get("filesystem.list_directory") is not None
        assert self.tool_reg.get("filesystem.move_file") is not None
        assert self.tool_reg.get("browser.navigate") is not None
        assert self.tool_reg.get("research.search") is not None

        skills_count = register_builtin_skills(self.skill_reg)
        assert skills_count == 9
        assert len(self.skill_reg) == 9

    # -----------------------------------------------------------------
    # DEMO 1: "Open the fourth picture"
    # -----------------------------------------------------------------
    def test_demo_1_open_fourth_picture(self) -> None:
        """Demo 1:
        Goal: 'Open the fourth picture in the images folder'
        SkillMatcher -> windows.file_management
        ToolMatcher -> filesystem.list_directory
        Filter images -> Select 4th item -> Open/Inspect -> Verify
        """
        # 1. Setup sample image files in temp dir
        test_images = [
            "photo_01.jpg",
            "vacation_02.png",
            "family_03.jpg",
            "target_fourth_pic.png",  # The 4th picture!
            "scenery_05.jpg",
            "document.pdf",            # Non-image
        ]
        for name in test_images:
            with open(os.path.join(self.temp_dir, name), "w", encoding="utf-8") as f:
                f.write(f"Sample data for {name}")

        # Register tools
        scanner = ToolScanner(registry=self.tool_reg)
        scanner.scan_and_register_all()
        register_builtin_skills(self.skill_reg)

        # 2. Skill matching from natural language goal
        user_goal = "Open the fourth picture in my photos folder"
        matched_skills = self.skill_matcher.match(user_goal)
        assert len(matched_skills) > 0
        top_skill = matched_skills[0].skill
        assert top_skill.skill_id == "windows.file_management"

        # 3. Tool matching for directory listing
        matched_tools = self.tool_matcher.match(
            "directory_listing",
            caller_permissions=[TaskPermission.READ_FILES],
        )
        assert len(matched_tools) > 0
        list_tool = matched_tools[0].tool
        assert list_tool.tool_id == "filesystem.list_directory"

        # 4. Execute list directory
        list_res = self.tool_executor.execute(
            tool_id=list_tool.tool_id,
            inputs={"path": self.temp_dir},
            granted_permissions=[TaskPermission.READ_FILES],
        )
        assert list_res.is_success is True
        raw_entries = list_res.output.get("entries", [])
        filenames = [e["name"] if isinstance(e, dict) else e for e in raw_entries]

        # 5. Filter for picture extensions and sort
        img_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
        image_files = sorted([f for f in filenames if os.path.splitext(f)[1].lower() in img_extensions])
        assert len(image_files) == 5

        # Select the fourth picture (1-indexed index 4 = 0-indexed index 3)
        fourth_picture = image_files[3]
        assert fourth_picture == "target_fourth_pic.png"

        # 6. Execute open/inspection action
        opened_target = None

        def handle_open(target: str, **kw: Any) -> ToolResult:
            nonlocal opened_target
            opened_target = target
            return ToolResult(
                tool_id="windows.open_file",
                status="SUCCESS",
                output={"opened": target},
                evidence=[f"Opened picture target: {target}"],
            )

        open_tool = ToolDefinition(
            tool_id="windows.open_file",
            name="Open File",
            description="Open specified file in default viewer",
            capabilities=["file_viewing"],
            permissions=[TaskPermission.READ_FILES],
        )
        self.tool_reg.register(open_tool, handler=handle_open)

        open_res = self.tool_executor.execute(
            tool_id="windows.open_file",
            inputs={"target": os.path.join(self.temp_dir, fourth_picture)},
            granted_permissions=[TaskPermission.READ_FILES],
        )
        assert open_res.is_success is True
        assert opened_target == os.path.join(self.temp_dir, "target_fourth_pic.png")
        assert "target_fourth_pic.png" in open_res.evidence[0]

    # -----------------------------------------------------------------
    # DEMO 2: "Research this topic deeply"
    # -----------------------------------------------------------------
    def test_demo_2_deep_web_research(self) -> None:
        """Demo 2:
        Goal: 'Research this topic deeply: Best low-power AI inference accelerators'
        SkillMatcher -> web.research
        ToolMatcher -> research.search & research.synthesize
        Execute Search -> Collect Sources -> Synthesize Evidence -> Verify Confidence
        """
        scanner = ToolScanner(registry=self.tool_reg)
        scanner.scan_and_register_all()
        register_builtin_skills(self.skill_reg)

        user_goal = "Research this topic deeply: Best low-power AI inference accelerators"
        matched_skills = self.skill_matcher.match(user_goal)
        assert len(matched_skills) > 0
        top_skill = matched_skills[0].skill
        assert top_skill.skill_id == "web.research"

        # Match tools
        tools_search = self.tool_matcher.match("web_search", caller_permissions=[TaskPermission.NETWORK_ACCESS])
        assert tools_search[0].tool.tool_id == "research.search"

        tools_synth = self.tool_matcher.match("evidence_synthesis")
        assert tools_synth[0].tool.tool_id == "research.synthesize"

        # Execute full skill workflow
        skill_res = self.skill_executor.execute_skill(
            top_skill,
            inputs={"query": "Best low-power AI inference accelerators"},
            granted_permissions=[TaskPermission.NETWORK_ACCESS],
        )
        assert skill_res.status == AgentResultStatus.SUCCESS
        assert "Synthesized analysis" in str(skill_res.result)
        assert skill_res.confidence >= 0.90
        assert len(skill_res.evidence) >= 1

    # -----------------------------------------------------------------
    # DEMO 3: "Organize these test files" (Dry Run + Execute + Rollback)
    # -----------------------------------------------------------------
    def test_demo_3_organize_test_files_with_dry_run_and_rollback(self) -> None:
        """Demo 3:
        Goal: 'Organize my Downloads folder'
        SkillMatcher -> downloads.organization
        Phase 1: Dry run preview (verify no files moved)
        Phase 2: Execution (move files into subdirectories)
        Phase 3: Rollback (restore files using rollback metadata)
        """
        # Create test download files
        downloads_dir = os.path.join(self.temp_dir, "Downloads")
        os.makedirs(downloads_dir, exist_ok=True)
        f1 = os.path.join(downloads_dir, "report.pdf")
        f2 = os.path.join(downloads_dir, "song.mp3")
        with open(f1, "w") as f:
            f.write("PDF content")
        with open(f2, "w") as f:
            f.write("Audio content")

        scanner = ToolScanner(registry=self.tool_reg)
        scanner.scan_and_register_all()
        register_builtin_skills(self.skill_reg)

        # Match skill
        user_goal = "Clean up and organize my Downloads folder"
        matched_skills = self.skill_matcher.match(user_goal)
        assert matched_skills[0].skill.skill_id == "downloads.organization"

        dest_docs = os.path.join(downloads_dir, "Documents", "report.pdf")
        os.makedirs(os.path.dirname(dest_docs), exist_ok=True)

        # Phase 1: Dry Run
        dry_res = self.tool_executor.dry_run(
            "filesystem.move_file",
            inputs={"source": f1, "destination": dest_docs},
        )
        assert dry_res.is_dry_run is True
        assert os.path.exists(f1) is True  # File untouched!
        assert os.path.exists(dest_docs) is False

        # Phase 2: Execution
        exec_res = self.tool_executor.execute(
            "filesystem.move_file",
            inputs={"source": f1, "destination": dest_docs},
            granted_permissions=[TaskPermission.WRITE_FILES],
        )
        assert exec_res.is_success is True
        assert os.path.exists(f1) is False
        assert os.path.exists(dest_docs) is True
        assert exec_res.rollback_metadata["undo_action"] == "move"

        # Phase 3: Rollback
        undo_source = exec_res.rollback_metadata["restore_source"]
        undo_dest = exec_res.rollback_metadata["restore_destination"]
        rollback_res = self.tool_executor.execute(
            "filesystem.move_file",
            inputs={"source": undo_source, "destination": undo_dest},
            granted_permissions=[TaskPermission.WRITE_FILES],
        )
        assert rollback_res.is_success is True
        assert os.path.exists(f1) is True   # Restored to original!
        assert os.path.exists(dest_docs) is False

    # -----------------------------------------------------------------
    # DEMO 4: Tool Failover (Tool A Unavailable -> Tool B Fallback)
    # -----------------------------------------------------------------
    def test_demo_4_automatic_tool_failover(self) -> None:
        """Demo 4:
        Tool A (cloud.provider_alpha) is marked UNAVAILABLE.
        Tool B (cloud.provider_beta) is HEALTHY.
        Executing Tool A automatically triggers failover to Tool B,
        emits 'tool.fallback' on EventBus, and completes successfully.
        """
        tool_a = ToolDefinition(
            tool_id="cloud.provider_alpha",
            name="Alpha Cloud Backup",
            description="Alpha cloud backup service",
            capabilities=["cloud_backup"],
            health=ToolHealth.UNAVAILABLE,
            permissions=[TaskPermission.NETWORK_ACCESS],
        )
        tool_b = ToolDefinition(
            tool_id="cloud.provider_beta",
            name="Beta Cloud Backup",
            description="Beta cloud backup service",
            capabilities=["cloud_backup"],
            health=ToolHealth.HEALTHY,
            permissions=[TaskPermission.NETWORK_ACCESS],
        )

        def beta_handler(**kw: Any) -> ToolResult:
            return ToolResult(
                tool_id="cloud.provider_beta",
                status="SUCCESS",
                output={"backup_url": "https://beta.cloud/archive.zip"},
                evidence=["Backup generated via provider Beta"],
            )

        self.tool_reg.register(tool_a)
        self.tool_reg.register(tool_b, handler=beta_handler)

        fallback_events: List[Dict[str, Any]] = []
        self.event_bus.subscribe("tool.fallback", lambda event: fallback_events.append(event.data))

        # Caller requests tool_a
        result = self.tool_executor.execute(
            tool_id="cloud.provider_alpha",
            inputs={"file": "archive.zip"},
            granted_permissions=[TaskPermission.NETWORK_ACCESS],
        )

        assert result.is_success is True
        assert result.tool_id == "cloud.provider_beta"
        assert result.output["backup_url"] == "https://beta.cloud/archive.zip"
        assert len(fallback_events) == 1
        assert fallback_events[0]["primary_tool"] == "cloud.provider_alpha"
        assert fallback_events[0]["fallback_tool"] == "cloud.provider_beta"

    # -----------------------------------------------------------------
    # DEMO 5: Least Privilege Enforcement
    # -----------------------------------------------------------------
    def test_demo_5_least_privilege_enforcement(self) -> None:
        """Demo 5:
        A research agent granted only TaskPermission.NETWORK_ACCESS:
        1. Cannot execute filesystem.move_file (requires WRITE_FILES) -> BLOCKED
        2. Cannot execute high-risk operations without approval -> BLOCKED
        3. CAN execute research.search (requires NETWORK_ACCESS) -> SUCCESS
        """
        scanner = ToolScanner(registry=self.tool_reg)
        scanner.scan_and_register_all()

        research_agent_perms = [TaskPermission.NETWORK_ACCESS]

        # 1. Attempt file modification -> Denied
        file_res = self.tool_executor.execute(
            tool_id="filesystem.move_file",
            inputs={"source": "a.txt", "destination": "b.txt"},
            granted_permissions=research_agent_perms,
        )
        assert file_res.status == "BLOCKED"
        assert "Missing required permission: 'WRITE_FILES'" in file_res.error

        # 2. Attempt high risk tool -> Denied
        high_tool = ToolDefinition(
            tool_id="admin.purge_logs",
            name="Purge Logs",
            description="Purge system logs",
            risk_level=RiskLevel.HIGH,
            permissions=[TaskPermission.NETWORK_ACCESS],
        )
        self.tool_reg.register(high_tool, handler=lambda **kw: "purged")

        high_res = self.tool_executor.execute(
            tool_id="admin.purge_logs",
            inputs={},
            granted_permissions=research_agent_perms,
            is_approved=False,
        )
        assert high_res.status == "BLOCKED"
        assert "requires explicit human approval" in high_res.error

        # 3. Permitted research action -> Allowed & Success
        search_res = self.tool_executor.execute(
            tool_id="research.search",
            inputs={"query": "quantum computing"},
            granted_permissions=research_agent_perms,
        )
        assert search_res.status == "SUCCESS"
        assert "results" in search_res.output
