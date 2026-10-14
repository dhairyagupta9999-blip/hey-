"""Built-in Reusable Skills for Hey Jev.

Composes concrete tools into verifiable, multi-step domain workflows:
1. windows.app_control
2. windows.file_management
3. downloads.organization
4. windows.wallpaper
5. windows.window_management
6. web.navigation
7. web.research
8. web.source_comparison
9. task.diagnostics
"""
from __future__ import annotations

from typing import List

from agent_task import TaskPermission
from skill_model import SkillDefinition, SkillTrustLevel
from skill_registry import SkillRegistry
from tool_model import RiskLevel


def get_builtin_skills() -> List[SkillDefinition]:
    """Return the standard suite of built-in skills."""
    skills = []

    # 1. windows.file_management
    s_file = SkillDefinition(
        skill_id="windows.file_management",
        name="Windows File Management",
        description="Inspect files in a directory, locate items, and organize or open targets",
        version="1.0.0",
        category="filesystem",
        required_capabilities=["filesystem", "directory_listing"],
        required_tools=["filesystem.list_directory"],
        permissions=[TaskPermission.READ_FILES],
        risk_level=RiskLevel.LOW,
        workflow=[
            {
                "step_id": "list_files",
                "tool_id": "filesystem.list_directory",
                "description": "Scan directory contents",
                "inputs": {"path": "$path"},
            },
        ],
        verification_strategy="entries_present",
        tags=["files", "directory", "pictures", "images", "documents"],
        trust_level=SkillTrustLevel.CORE,
    )
    skills.append(s_file)

    # 2. downloads.organization
    s_org = SkillDefinition(
        skill_id="downloads.organization",
        name="Organize Downloads Folder",
        description="List files in Downloads, group by category, and move files into subdirectories",
        version="1.0.0",
        category="filesystem",
        required_capabilities=["filesystem", "file_moving"],
        required_tools=["filesystem.list_directory", "filesystem.move_file"],
        permissions=[TaskPermission.READ_FILES, TaskPermission.WRITE_FILES],
        risk_level=RiskLevel.MEDIUM,
        supports_dry_run=True,
        workflow=[
            {
                "step_id": "list_downloads",
                "tool_id": "filesystem.list_directory",
                "description": "List files needing organization",
                "inputs": {"path": "$path"},
            },
            {
                "step_id": "move_target",
                "tool_id": "filesystem.move_file",
                "description": "Move file to target destination with rollback metadata",
                "inputs": {"source": "$source", "destination": "$destination"},
            },
        ],
        verification_strategy="files_moved",
        tags=["organize", "downloads", "cleanup", "sort"],
        trust_level=SkillTrustLevel.CORE,
    )
    skills.append(s_org)

    # 3. windows.wallpaper
    s_wall = SkillDefinition(
        skill_id="windows.wallpaper",
        name="Change Desktop Wallpaper",
        description="Inspect current wallpaper and apply new desktop background with rollback safety",
        version="1.0.0",
        category="desktop",
        required_capabilities=["wallpaper", "desktop_background"],
        required_tools=["windows.get_wallpaper", "windows.set_wallpaper"],
        permissions=[TaskPermission.CHANGE_SETTINGS],
        risk_level=RiskLevel.MEDIUM,
        supports_dry_run=True,
        workflow=[
            {
                "step_id": "observe_current",
                "tool_id": "windows.get_wallpaper",
                "description": "Observe previous desktop wallpaper path",
            },
            {
                "step_id": "apply_wallpaper",
                "tool_id": "windows.set_wallpaper",
                "description": "Set new desktop wallpaper",
                "inputs": {"path": "$path"},
            },
        ],
        tags=["wallpaper", "background", "desktop"],
        trust_level=SkillTrustLevel.CORE,
    )
    skills.append(s_wall)

    # 4. windows.app_control
    s_app = SkillDefinition(
        skill_id="windows.app_control",
        name="Windows Application Control",
        description="Inspect and control desktop application windows",
        version="1.0.0",
        category="window",
        required_capabilities=["window_inspection"],
        required_tools=["windows.get_active_window"],
        risk_level=RiskLevel.LOW,
        workflow=[
            {
                "step_id": "inspect_window",
                "tool_id": "windows.get_active_window",
                "description": "Inspect active application window",
            },
        ],
        tags=["app", "application", "window", "launch"],
        trust_level=SkillTrustLevel.CORE,
    )
    skills.append(s_app)

    # 5. windows.window_management
    s_win = SkillDefinition(
        skill_id="windows.window_management",
        name="Windows Window Management",
        description="Inspect, list, and switch desktop application windows",
        version="1.0.0",
        category="window",
        required_capabilities=["window_inspection"],
        required_tools=["windows.get_active_window"],
        risk_level=RiskLevel.LOW,
        workflow=[
            {
                "step_id": "inspect_active",
                "tool_id": "windows.get_active_window",
                "description": "Check current active window",
            },
        ],
        tags=["window", "switch", "minimize", "maximize"],
        trust_level=SkillTrustLevel.CORE,
    )
    skills.append(s_win)

    # 6. web.navigation
    s_nav = SkillDefinition(
        skill_id="web.navigation",
        name="Web Navigation",
        description="Open browser tab and navigate to destination URL",
        version="1.0.0",
        category="browser",
        required_capabilities=["tab_management", "web_navigation"],
        required_tools=["browser.open_tab", "browser.navigate"],
        permissions=[TaskPermission.CONTROL_BROWSER],
        risk_level=RiskLevel.LOW,
        workflow=[
            {
                "step_id": "open_tab",
                "tool_id": "browser.open_tab",
                "description": "Open new browser tab",
                "inputs": {"url": "$url"},
            },
            {
                "step_id": "navigate_target",
                "tool_id": "browser.navigate",
                "description": "Navigate to target URL",
                "inputs": {"url": "$url"},
            },
        ],
        tags=["browser", "web", "navigate", "open_url"],
        trust_level=SkillTrustLevel.CORE,
    )
    skills.append(s_nav)

    # 7. web.research
    s_res = SkillDefinition(
        skill_id="web.research",
        name="Web Research and Synthesis",
        description="Execute web searches, collect sources, and synthesize findings into verified evidence",
        version="1.0.0",
        category="research",
        required_capabilities=["web_search", "evidence_synthesis"],
        required_tools=["research.search", "research.synthesize"],
        permissions=[TaskPermission.NETWORK_ACCESS],
        risk_level=RiskLevel.LOW,
        workflow=[
            {
                "step_id": "search_query",
                "tool_id": "research.search",
                "description": "Execute search query across web sources",
                "inputs": {"query": "$query"},
            },
            {
                "step_id": "synthesize_evidence",
                "tool_id": "research.synthesize",
                "description": "Synthesize search findings into evidence",
                "inputs": {"topic": "$query"},
            },
        ],
        tags=["research", "search", "investigate", "compare", "hotels", "laptops"],
        trust_level=SkillTrustLevel.CORE,
    )
    skills.append(s_res)

    # 8. web.source_comparison
    s_comp = SkillDefinition(
        skill_id="web.source_comparison",
        name="Web Source Comparison",
        description="Compare disparate web sources to identify contradictions and consensus",
        version="1.0.0",
        category="research",
        required_capabilities=["web_search", "evidence_synthesis"],
        required_tools=["research.search", "research.synthesize"],
        permissions=[TaskPermission.NETWORK_ACCESS],
        risk_level=RiskLevel.LOW,
        workflow=[
            {
                "step_id": "search_sources",
                "tool_id": "research.search",
                "description": "Collect search results from diverse providers",
                "inputs": {"query": "$query"},
            },
            {
                "step_id": "compare_synthesis",
                "tool_id": "research.synthesize",
                "description": "Compare claims and highlight contradictions",
                "inputs": {"topic": "$query"},
            },
        ],
        tags=["compare", "sources", "contradiction", "fact_check"],
        trust_level=SkillTrustLevel.CORE,
    )
    skills.append(s_comp)

    # 9. task.diagnostics
    s_diag = SkillDefinition(
        skill_id="task.diagnostics",
        name="System & Task Diagnostics",
        description="Perform health checks and inspect telemetry",
        version="1.0.0",
        category="diagnostics",
        required_capabilities=["window_inspection"],
        required_tools=["windows.get_active_window"],
        risk_level=RiskLevel.LOW,
        workflow=[
            {
                "step_id": "check_active_window",
                "tool_id": "windows.get_active_window",
                "description": "Check active window state",
            },
        ],
        tags=["diagnostic", "health", "telemetry", "status"],
        trust_level=SkillTrustLevel.CORE,
    )
    skills.append(s_diag)

    return skills


def register_builtin_skills(registry: SkillRegistry) -> int:
    """Register all built-in skills into a SkillRegistry."""
    skills = get_builtin_skills()
    for s in skills:
        registry.register(s)
    return len(skills)
