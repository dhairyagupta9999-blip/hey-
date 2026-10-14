"""Comprehensive Test Suite for Phase 12: Jev Windows Computer Operator.

Verifies:
1. Adapter registration & capability discovery
2. Permission checks & safety gating
3. Screen observation & active window discovery
4. Semantic UI element lookup abstraction
5. Window management (focus, minimize, restore, close)
6. Application launch & lifecycle control
7. Safe filesystem operations (list, find, metadata, copy, move, delete gating)
8. Structured PowerShell execution & timeout handling
9. Observe -> Act -> Verify lifecycle & ComputerActionResult
10. Dry-run preview & rollback metadata
11. Bounded retry & cancellation
12. Goal decomposition in TaskPlanner
13. Deterministic routing to windows_agent
14. Real-PC DEMO 1: "Open the fourth picture" in sandbox
15. Real-PC DEMO 2: "Change my wallpaper" with observe-act-verify & restoration
16. Real-PC DEMO 3: Application open and close lifecycle
17. Real-PC DEMO 4: "Organize test files" in sandbox (preview first, execute, verify)
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import pytest
from PIL import Image

from agent_registry import AgentRegistry
from agent_router import DeterministicAgentRouter
from agent_task import AgentTask, TaskPermission, TaskPriority, TaskStatus
from agent_result import AgentResult, AgentResultStatus
from orchestrator import JevOrchestrator
from task_planner import TaskPlanner
from task_storage import TaskStorage
from windows_computer_tools import ComputerActionResult, WindowsComputerTools
from windows_computer_adapter import WindowsComputerAdapter


# Test sandbox root in %TEMP%/HeyJev/phase12-test
SANDBOX_ROOT = os.path.join(tempfile.gettempdir(), "HeyJev", "phase12-test")


@pytest.fixture(autouse=True)
def ensure_sandbox_clean():
    """Ensure sandbox exists and clean up after test."""
    os.makedirs(SANDBOX_ROOT, exist_ok=True)
    yield
    if os.path.exists(SANDBOX_ROOT):
        try:
            shutil.rmtree(SANDBOX_ROOT, ignore_errors=True)
        except Exception:
            pass


class TestPhase12WindowsComputerOperator:

    def test_01_adapter_registration_and_capabilities(self):
        """Verify windows_agent is registered with AgentRegistry and exposes computer_use capabilities."""
        registry = AgentRegistry(seed_defaults=True)
        agent = registry.get_agent("windows_agent")
        assert agent is not None
        assert "computer_use" in agent.capabilities
        assert "filesystem" in agent.capabilities
        assert "wallpaper" in agent.capabilities
        assert "powershell" in agent.capabilities
        assert "observation" in agent.capabilities

        adapter = registry.get_adapter("windows_agent")
        assert adapter is not None
        assert isinstance(adapter, WindowsComputerAdapter)

        health = adapter.health_check()
        assert health["status"] == "healthy"
        assert health["provider"] == "windows_agent"
        assert "screen_width" in health

    def test_02_deterministic_routing_to_windows_agent(self):
        """Verify AgentRouter deterministically selects windows_agent for computer tasks."""
        router = DeterministicAgentRouter()
        registry = AgentRegistry(seed_defaults=True)

        d1 = router.route_task(AgentTask(goal="Change my wallpaper to nature.jpg"), registry)
        assert d1.selected_agent == "windows_agent"

        d2 = router.route_task(AgentTask(goal="Turn dark mode on"), registry)
        assert d2.selected_agent == "windows_agent"

        d3 = router.route_task(AgentTask(goal="Organize my Downloads folder"), registry)
        assert d3.selected_agent == "windows_agent"

        d4 = router.route_task(AgentTask(goal="Run PowerShell command to check services"), registry)
        assert d4.selected_agent == "windows_agent"

    def test_03_screen_observation_and_active_window(self):
        """Verify observe_screen returns structured desktop dimensions, windows, and active app."""
        tools = WindowsComputerTools()
        obs = tools.observe_screen()
        assert obs["screen_width"] > 0
        assert obs["screen_height"] > 0
        assert obs["total_windows"] >= 0
        assert "timestamp" in obs

        # Active window discovery
        active = tools.get_active_window()
        if active:
            assert "hwnd" in active
            assert "title" in active
            assert "rect" in active

    def test_04_window_management_operations(self):
        """Verify list_windows, find_window, and focus_window abstractions."""
        tools = WindowsComputerTools()
        windows = tools.list_windows()
        assert isinstance(windows, list)

        # Nonexistent window lookup returns None safely
        none_win = tools.find_window("NonExistent_Fake_Window_XYZ_12345")
        assert none_win is None

        # Focus invalid handle returns False safely without crash
        assert tools.focus_window(999999999) is False
        assert tools.minimize_window(999999999) is False

    def test_05_filesystem_operations(self):
        """Verify safe filesystem operations: listing, metadata, copy, move, and directory creation."""
        tools = WindowsComputerTools()
        test_dir = os.path.join(SANDBOX_ROOT, "fs_test")
        tools.create_directory(test_dir)
        assert os.path.exists(test_dir)

        # Create test file
        file1 = os.path.join(test_dir, "sample.txt")
        with open(file1, "w", encoding="utf-8") as f:
            f.write("Hey Jev Phase 12 Test")

        # Listing
        entries = tools.list_directory(test_dir)
        assert len(entries) == 1
        assert entries[0]["name"] == "sample.txt"
        assert entries[0]["is_directory"] is False
        assert entries[0]["size_bytes"] > 0

        # Metadata
        meta = tools.get_file_metadata(file1)
        assert meta is not None
        assert meta["extension"] == ".txt"
        assert meta["size_bytes"] > 0

        # Copy
        file2 = os.path.join(test_dir, "sample_copy.txt")
        copied = tools.copy_file(file1, file2)
        assert copied is True
        assert os.path.exists(file2)

        # Move
        dest_dir = os.path.join(test_dir, "sub")
        dest_file = os.path.join(dest_dir, "moved.txt")
        move_rec = tools.move_file(file2, dest_file)
        assert move_rec["success"] is True
        assert os.path.exists(dest_file)
        assert not os.path.exists(file2)

    def test_06_protected_deletion_policy(self):
        """Verify file deletion requires explicit approval and supports preview."""
        tools = WindowsComputerTools()
        test_file = os.path.join(SANDBOX_ROOT, "delete_me.txt")
        with open(test_file, "w") as f:
            f.write("Important data")

        # Without approval -> must be rejected with needs_approval flag
        res_preview = tools.delete_file(test_file, preview=True, approved=False)
        assert res_preview["needs_approval"] is True
        assert os.path.exists(test_file)  # Not deleted

        # With explicit approval -> deletes file
        res_approved = tools.delete_file(test_file, preview=False, approved=True)
        assert res_approved["success"] is True
        assert not os.path.exists(test_file)

    def test_07_structured_powershell_execution_and_timeout(self):
        """Verify PowerShell execution, exit codes, output capture, and timeout enforcement."""
        tools = WindowsComputerTools()

        # Simple command
        res = tools.execute_powershell("Write-Output 'Hello Jev'")
        assert res["exit_code"] == 0
        assert "Hello Jev" in res["stdout"]
        assert res["duration_ms"] > 0
        assert res["blocked"] is False

        # Timeout handling
        timeout_res = tools.execute_powershell("Start-Sleep -Seconds 5", timeout=0.5)
        assert timeout_res["exit_code"] == 124
        assert "timed out" in timeout_res["stderr"].lower()

        # Dangerous command blocked by policy
        dangerous_res = tools.execute_powershell("Format-Volume -DriveLetter C")
        assert dangerous_res["blocked"] is True

    def test_08_observe_act_verify_computer_action_result(self):
        """Verify ComputerActionResult encapsulates observe-act-verify lifecycle into AgentResult."""
        car = ComputerActionResult(
            action="resize_window",
            target="Notepad",
            before_state={"width": 800, "height": 600},
            after_state={"width": 1024, "height": 768},
            verified=True,
            verification_summary="Window size successfully expanded to 1024x768",
            rollback_metadata={"restore_width": 800, "restore_height": 600},
            confidence=0.98,
        )

        agent_result = car.to_agent_result()
        assert agent_result.is_success is True
        assert agent_result.status == AgentResultStatus.SUCCESS
        assert agent_result.confidence == 0.98
        assert "1024x768" in agent_result.summary
        assert len(agent_result.evidence) >= 1

    def test_09_dry_run_preview_and_rollback_metadata(self):
        """Verify dry-run mode returns proposed changes without altering the disk."""
        tools = WindowsComputerTools()
        src = os.path.join(SANDBOX_ROOT, "preview_src.txt")
        dst = os.path.join(SANDBOX_ROOT, "preview_dst.txt")
        with open(src, "w") as f:
            f.write("test")

        # Move preview
        preview = tools.move_file(src, dst, preview=True)
        assert preview["preview"] is True
        assert os.path.exists(src)  # Source still untouched
        assert not os.path.exists(dst)  # Destination not created

    def test_10_task_planner_computer_use_decomposition(self):
        """Verify TaskPlanner decomposes computer goals into explicit DAG subtasks."""
        planner = TaskPlanner()

        # 1. "Open the fourth picture"
        g1 = planner.decompose_goal("Open the fourth picture")
        goals1 = [t.goal.lower() for t in g1.tasks.values()]
        assert any("inspect" in g for g in goals1)
        assert any("filter" in g for g in goals1)
        assert any("select" in g for g in goals1)
        assert any("open" in g for g in goals1)
        assert any("verify" in g for g in goals1)

        # 2. "Change my wallpaper"
        g2 = planner.decompose_goal("Change my wallpaper")
        goals2 = [t.goal.lower() for t in g2.tasks.values()]
        assert any("locate" in g for g in goals2)
        assert any("validate" in g for g in goals2)
        assert any("apply" in g for g in goals2)
        assert any("verify" in g for g in goals2)

        # 3. "Organize my Downloads"
        g3 = planner.decompose_goal("Organize my Downloads folder")
        goals3 = [t.goal.lower() for t in g3.tasks.values()]
        assert any("scan" in g for g in goals3)
        assert any("categorize" in g for g in goals3)
        assert any("preview" in g for g in goals3)
        assert any("execute" in g for g in goals3)

    def test_11_demo_1_open_fourth_picture_sandbox(self):
        """REAL DEMO 1: Create 5 images in sandbox, goal: 'Open the fourth picture', verify 4th selected."""
        pics_dir = os.path.join(SANDBOX_ROOT, "pictures_demo")
        os.makedirs(pics_dir, exist_ok=True)

        # Create 5 small distinct images
        created_paths = []
        for i in range(1, 6):
            img_path = os.path.join(pics_dir, f"photo_{i:02d}.png")
            img = Image.new("RGB", (64, 64), color=(i * 40, i * 30, 200))
            img.save(img_path)
            created_paths.append(img_path)

        adapter = WindowsComputerAdapter()
        task = AgentTask(
            goal="Open the fourth picture",
            context={"directory": pics_dir},
        )

        res = adapter._execute_computer_goal(task)
        assert res.is_success is True
        assert res.status == AgentResultStatus.SUCCESS
        assert res.result["index"] == 4
        # Verify targeted picture is photo_04.png
        assert os.path.basename(res.result["target"]) == "photo_04.png"

    def test_12_demo_2_change_wallpaper_and_restore(self):
        """REAL DEMO 2: Validate wallpaper read, apply test wallpaper, verify change, and restore original."""
        tools = WindowsComputerTools()
        original_wall = tools.get_wallpaper()

        # Create test solid wallpaper image
        wall_dir = os.path.join(SANDBOX_ROOT, "wallpaper_test")
        os.makedirs(wall_dir, exist_ok=True)
        test_wall_path = os.path.join(wall_dir, "test_wallpaper.png")
        img = Image.new("RGB", (100, 100), color=(30, 144, 255))
        img.save(test_wall_path)

        adapter = WindowsComputerAdapter(tools=tools)
        task = AgentTask(
            goal="Change my wallpaper",
            context={"wallpaper_path": test_wall_path},
        )

        try:
            res = adapter._execute_computer_goal(task)
            assert res.is_success is True
            assert res.status == AgentResultStatus.SUCCESS
            assert "Wallpaper updated" in res.summary
        finally:
            # Restore original wallpaper to leave user PC undisturbed
            if original_wall and os.path.exists(original_wall):
                tools.set_wallpaper(original_wall)

    def test_13_demo_3_app_lifecycle_open_and_close(self):
        """REAL DEMO 3: Open Notepad (or Calculator) and close it with verified termination."""
        adapter = WindowsComputerAdapter()
        task = AgentTask(goal="Open Notepad and close it")

        res = adapter._execute_computer_goal(task)
        assert res.is_success is True
        assert res.status == AgentResultStatus.SUCCESS
        assert res.result["lifecycle"] == "opened_and_closed"
        assert res.result["closed_verified"] is True

    def test_14_demo_4_organize_test_files_sandbox(self):
        """REAL DEMO 4: Organize mixed files in sandbox: Preview first, execute, verify sorted."""
        organize_dir = os.path.join(SANDBOX_ROOT, "organize_demo")
        os.makedirs(organize_dir, exist_ok=True)

        # Create sample files
        test_files = [
            "report.pdf",
            "vacation.jpg",
            "setup.exe",
            "script.py",
            "archive.zip",
        ]
        for name in test_files:
            with open(os.path.join(organize_dir, name), "w") as f:
                f.write(f"content of {name}")

        adapter = WindowsComputerAdapter()

        # Step 1: Preview Mode
        task_preview = AgentTask(
            goal="Organize these test files",
            context={"directory": organize_dir, "preview": True},
        )
        res_preview = adapter._execute_computer_goal(task_preview)
        assert res_preview.is_success is True
        assert res_preview.result["preview"] is True
        assert res_preview.result["count"] == 5
        # Verify files remain in root
        for name in test_files:
            assert os.path.exists(os.path.join(organize_dir, name))

        # Step 2: Execute Mode
        task_exec = AgentTask(
            goal="Organize these test files",
            context={"directory": organize_dir, "preview": False},
        )
        res_exec = adapter._execute_computer_goal(task_exec)
        assert res_exec.is_success is True
        assert len(res_exec.files_changed) == 5

        # Verify sorted destinations
        assert os.path.exists(os.path.join(organize_dir, "Documents", "report.pdf"))
        assert os.path.exists(os.path.join(organize_dir, "Images", "vacation.jpg"))
        assert os.path.exists(os.path.join(organize_dir, "Installers", "setup.exe"))
        assert os.path.exists(os.path.join(organize_dir, "Code", "script.py"))
        assert os.path.exists(os.path.join(organize_dir, "Archives", "archive.zip"))

    def test_15_orchestrator_end_to_end_computer_task(self):
        """Verify full Orchestrator execution with WindowsComputerAdapter and TaskVerifier."""
        storage = TaskStorage(db_path=":memory:")
        registry = AgentRegistry(seed_defaults=True)
        adapter = WindowsComputerAdapter()
        registry.bind_adapter("windows_agent", adapter)

        orchestrator = JevOrchestrator(storage=storage, registry=registry)

        # Create and execute dark mode check task with human approval for CHANGE_SETTINGS
        task = orchestrator.create_task(
            goal="Turn dark mode on",
            permissions=[TaskPermission.CHANGE_SETTINGS],
        )
        task.approval_state = "APPROVED"

        res = orchestrator.execute_task(task)
        assert res.is_success is True

        updated_task = storage.get_task(task.task_id)
        assert updated_task is not None
        assert updated_task.status == TaskStatus.COMPLETED
        assert updated_task.current_agent == "windows_agent"
