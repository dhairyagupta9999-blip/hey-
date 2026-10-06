"""Phase 16: Jev Persistent Memory & Project Awareness Comprehensive Test Suite.

Tests:
1. MemoryRecord schema, validation, credential sanitization, and checksums
2. SqliteMemoryStore insertion, retrieval, search, structured filtering, and counting
3. SqliteMemoryStore least-privilege sensitivity filtering (PUBLIC -> SENSITIVE)
4. SqliteMemoryStore atomic supersession and non-destructive historical tracking
5. SqliteMemoryStore safe pruning invariants (preserves user preferences & decisions)
6. SqliteMemoryStore JSON export and atomic consolidation
7. BM25SemanticProvider and HybridMemoryRetriever scoring
8. Project model serialization and ProjectRegistry persistent management
9. ProjectScanner bounded filesystem scanning and root directory rejection
10. GitInspector safe read-only repository status inspection
11. ProjectHealthAssessor state evaluation (HEALTHY vs BROKEN)
12. ContextSnapshot environment capture and ContextBuilder prioritization
13. ContextBuilder least-privilege memory sensitivity gating
14. UserMemoryController conversational interface (remember, forget, explain)
15. MemoryConflictResolver evidence-based contradiction resolution
16. Demo 1: User Memory Flow (remember -> search -> forget -> verify)
17. Demo 2: Project Memory (Project HeyJev -> skills memory retrieval)
18. Demo 3: Temporal Memory (historical failing test vs current passing state)
19. Demo 4: Conflict Resolution (valid git path vs invalid directory path)
20. Demo 5: Project Health (clean state vs dirty/failing state diagnostics)
21. Demo 6: Context Builder Prioritization (full agent handoff prompt injection)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from typing import Any, Dict, List

import pytest

# Ensure hey-jev root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_task import AgentTask, TaskPermission, TaskPriority
from context_builder import ContextBuilder
from context_snapshot import ContextSnapshot
from event_bus import EventBus
from git_inspector import GitInspector
from memory_conflict_resolver import MemoryConflictResolver
from memory_model import (
    EpistemicStatus,
    MemoryRecord,
    MemorySensitivity,
    MemorySource,
    MemoryStatus,
    MemoryType,
)
from memory_store import SqliteMemoryStore
from mission import Mission
from project_health import ProjectHealthAssessor, ProjectHealthSnapshot
from project_model import Project
from project_registry import ProjectRegistry
from project_scanner import ProjectScanner
from semantic_memory import BM25SemanticProvider, HybridMemoryRetriever
from user_memory_controller import UserMemoryController


class TestPhase16MemoryAndProjectAwareness:
    """Comprehensive test suite for Phase 16 Persistent Memory & Project Awareness."""

    def setup_method(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="heyjev_test_p16_")
        self.mem_db_path = os.path.join(self.temp_dir, "test_memories.db")
        self.proj_db_path = os.path.join(self.temp_dir, "test_projects.db")
        self.event_bus = EventBus()
        self.store = SqliteMemoryStore(db_path=self.mem_db_path, event_bus=self.event_bus)
        self.project_registry = ProjectRegistry(db_path=self.proj_db_path)
        self.git_inspector = GitInspector()
        self.context_builder = ContextBuilder(
            memory_store=self.store,
            git_inspector=self.git_inspector,
        )
        self.user_controller = UserMemoryController(store=self.store)
        self.conflict_resolver = MemoryConflictResolver(
            store=self.store,
            git_inspector=self.git_inspector,
        )

    def teardown_method(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. MemoryRecord & Sanitization
    # -------------------------------------------------------------------------

    def test_memory_record_creation_and_defaults(self) -> None:
        rec = MemoryRecord(
            type=MemoryType.USER_FACT,
            content="User prefers dark mode across all editors.",
            summary="Dark mode preference",
        )
        assert rec.memory_id.startswith("mem_")
        assert rec.status == MemoryStatus.ACTIVE
        assert rec.epistemic_status == EpistemicStatus.FACT
        assert rec.sensitivity == MemorySensitivity.NORMAL
        assert rec.confidence == 1.0
        assert rec.importance == 0.5
        assert rec.checksum != ""
        assert not rec.is_expired()

    def test_memory_record_credential_sanitization(self) -> None:
        fake_token = "sk-" + "ant-" + "mocktoken1234567890abcdef1234567890"
        unsafe_content = f"Configured token: {fake_token} and password=SuperSecretPassword123"
        rec = MemoryRecord(
            type=MemoryType.PROJECT_STATE,
            content=unsafe_content,
            summary="Auth configuration: bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz",
        )
        assert "mocktoken" not in rec.content
        assert "[REDACTED_CREDENTIAL]" in rec.content
        assert "SuperSecretPassword123" not in rec.content
        assert "eyJhbGci" not in rec.summary
        assert "[REDACTED_CREDENTIAL]" in rec.summary

    def test_memory_record_serialization(self) -> None:
        rec = MemoryRecord(
            type=MemoryType.PROJECT_DECISION,
            content="Use SQLite WAL mode for persistent memory.",
            summary="Adopt SQLite WAL",
            project_id="proj_heyjev",
            tags=["database", "architecture"],
            importance=0.9,
        )
        d = rec.to_dict()
        assert d["type"] == "PROJECT_DECISION"
        assert d["project_id"] == "proj_heyjev"
        assert "database" in d["tags"]

        restored = MemoryRecord.from_dict(d)
        assert restored.memory_id == rec.memory_id
        assert restored.type == MemoryType.PROJECT_DECISION
        assert restored.content == rec.content
        assert restored.importance == 0.9

    # -------------------------------------------------------------------------
    # 2. SqliteMemoryStore Operations
    # -------------------------------------------------------------------------

    def test_store_add_get_and_count(self) -> None:
        rec = MemoryRecord(
            type=MemoryType.USER_PREFERENCE,
            content="Preferred test runner is pytest with -v flag.",
            summary="Pytest runner preference",
        )
        mid = self.store.add(rec)
        assert mid == rec.memory_id

        fetched = self.store.get(mid)
        assert fetched is not None
        assert fetched.content == rec.content
        assert fetched.type == MemoryType.USER_PREFERENCE

        assert self.store.count() == 1
        assert self.store.count({"type": MemoryType.USER_PREFERENCE}) == 1
        assert self.store.count({"type": MemoryType.ENVIRONMENT_FACT}) == 0

    def test_store_keyword_search_and_filters(self) -> None:
        r1 = MemoryRecord(
            type=MemoryType.PROJECT_DECISION,
            content="Decided to implement BM25 for deterministic search relevance.",
            summary="BM25 implementation",
            project_id="proj_alpha",
            tags=["search", "bm25"],
            importance=0.8,
        )
        r2 = MemoryRecord(
            type=MemoryType.USER_FACT,
            content="User runs Windows 10 with 8GB of RAM on dual core processor.",
            summary="Hardware specs",
            project_id="proj_beta",
            tags=["hardware", "windows"],
            importance=0.6,
        )
        self.store.add(r1)
        self.store.add(r2)

        results = self.store.search("BM25 deterministic", filters={"project_id": "proj_alpha"})
        assert len(results) == 1
        assert results[0].memory_id == r1.memory_id

        # Query across tags
        tag_results = self.store.search("hardware specs")
        assert len(tag_results) == 1
        assert tag_results[0].memory_id == r2.memory_id

    def test_store_sensitivity_filtering(self) -> None:
        public_rec = MemoryRecord(
            type=MemoryType.ENVIRONMENT_FACT,
            content="Public documentation is located at docs.example.com",
            sensitivity=MemorySensitivity.PUBLIC,
        )
        normal_rec = MemoryRecord(
            type=MemoryType.PROJECT_STATE,
            content="Phase 16 development is in progress.",
            sensitivity=MemorySensitivity.NORMAL,
        )
        private_rec = MemoryRecord(
            type=MemoryType.USER_PREFERENCE,
            content="User home directory is C:\\Users\\developer",
            sensitivity=MemorySensitivity.PRIVATE,
        )
        sensitive_rec = MemoryRecord(
            type=MemoryType.PROJECT_DECISION,
            content="Internal staging server IP is 192.168.1.150",
            sensitivity=MemorySensitivity.SENSITIVE,
        )

        for r in [public_rec, normal_rec, private_rec, sensitive_rec]:
            self.store.add(r)

        # 1. PUBLIC max sensitivity: only PUBLIC
        pub_list = self.store.list(filters={"max_sensitivity": MemorySensitivity.PUBLIC})
        assert len(pub_list) == 1
        assert pub_list[0].memory_id == public_rec.memory_id

        # 2. NORMAL max sensitivity: PUBLIC + NORMAL
        norm_list = self.store.list(filters={"max_sensitivity": MemorySensitivity.NORMAL})
        assert len(norm_list) == 2
        ids = [m.memory_id for m in norm_list]
        assert public_rec.memory_id in ids
        assert normal_rec.memory_id in ids

        # 3. PRIVATE max sensitivity: PUBLIC + NORMAL + PRIVATE
        priv_list = self.store.list(filters={"max_sensitivity": MemorySensitivity.PRIVATE})
        assert len(priv_list) == 3

        # 4. SENSITIVE max sensitivity: all 4
        sens_list = self.store.list(filters={"max_sensitivity": MemorySensitivity.SENSITIVE})
        assert len(sens_list) == 4

    def test_store_atomic_supersession(self) -> None:
        old_rec = MemoryRecord(
            type=MemoryType.PROJECT_STATE,
            content="Project test baseline is 271 passed.",
            summary="Phase 14 baseline",
        )
        self.store.add(old_rec)

        new_rec = MemoryRecord(
            type=MemoryType.PROJECT_STATE,
            content="Project test baseline is 295 passed.",
            summary="Phase 15 baseline",
        )

        new_id = self.store.supersede(old_rec.memory_id, new_rec)
        assert new_id == new_rec.memory_id

        # Old record must be marked SUPERSEDED
        old_fetched = self.store.get(old_rec.memory_id)
        assert old_fetched is not None
        assert old_fetched.status == MemoryStatus.SUPERSEDED
        assert old_fetched.superseded_by == new_rec.memory_id
        assert old_fetched.superseded_at is not None

        # New record must be ACTIVE
        new_fetched = self.store.get(new_rec.memory_id)
        assert new_fetched is not None
        assert new_fetched.status == MemoryStatus.ACTIVE

        # Active search must only find the new record by default
        active_records = self.store.list()
        assert len(active_records) == 1
        assert active_records[0].memory_id == new_rec.memory_id

    def test_store_prune_safety_invariants(self) -> None:
        # Create an expired low-importance normal fact
        expired_fact = MemoryRecord(
            type=MemoryType.TEMPORAL_EVENT,
            content="CPU spike observed at 14:00.",
            importance=0.1,
            expires_at=time.time() - 100,
        )
        # Create an expired USER_PREFERENCE (MUST NOT be pruned)
        expired_pref = MemoryRecord(
            type=MemoryType.USER_PREFERENCE,
            content="User prefers quiet audio mode.",
            importance=0.1,
            expires_at=time.time() - 100,
        )
        # Create an old PROJECT_DECISION (MUST NOT be pruned)
        old_decision = MemoryRecord(
            type=MemoryType.PROJECT_DECISION,
            content="Use Python 3.11 for runtime baseline.",
            importance=0.1,
            created_at=time.time() - 100000,
            status=MemoryStatus.SUPERSEDED,
        )

        self.store.add(expired_fact)
        self.store.add(expired_pref)
        self.store.add(old_decision)

        pruned = self.store.prune({"max_age_seconds": 500, "min_importance": 0.5})
        assert pruned >= 1

        # Expired observation was deleted
        assert self.store.get(expired_fact.memory_id) is None
        # User preference and project decision MUST STILL EXIST
        assert self.store.get(expired_pref.memory_id) is not None
        assert self.store.get(old_decision.memory_id) is not None

    def test_store_export_and_consolidation(self) -> None:
        r1 = MemoryRecord(
            type=MemoryType.TOOL_LESSON,
            content="Tool curl failed due to connection timeout on port 8080.",
            summary="Curl timeout",
            tags=["networking", "timeout"],
            importance=0.7,
        )
        r2 = MemoryRecord(
            type=MemoryType.TOOL_LESSON,
            content="Tool ping succeeded on port 8080 after server startup.",
            summary="Server startup verified",
            tags=["networking", "healthcheck"],
            importance=0.6,
        )
        self.store.add(r1)
        self.store.add(r2)

        # Export test
        export_json = self.store.export()
        data = json.loads(export_json)
        assert data["count"] == 2

        # Consolidation test
        consolidated = self.store.consolidate(
            records=[r1, r2],
            consolidated_content="Server on port 8080 takes 5s to boot; verify with ping before requesting via curl.",
            summary="Port 8080 startup procedure",
            record_type=MemoryType.PROJECT,
        )
        assert consolidated.status == MemoryStatus.ACTIVE
        assert consolidated.importance == 0.7
        assert "networking" in consolidated.tags
        assert "timeout" in consolidated.tags

        # Both source records must now be SUPERSEDED
        assert self.store.get(r1.memory_id).status == MemoryStatus.SUPERSEDED
        assert self.store.get(r2.memory_id).status == MemoryStatus.SUPERSEDED

    # -------------------------------------------------------------------------
    # 3. Semantic Memory & Hybrid Retrieval
    # -------------------------------------------------------------------------

    def test_bm25_semantic_provider(self) -> None:
        provider = BM25SemanticProvider()
        r1 = MemoryRecord(
            type=MemoryType.PROJECT,
            content="Faster-whisper runs on CPU with int8 quantization and 2 threads.",
            summary="STT configuration",
        )
        r2 = MemoryRecord(
            type=MemoryType.PROJECT,
            content="Playwright browser automation runs Chromium headless on Windows.",
            summary="Browser automation",
        )
        ranked = provider.rank("whisper quantization threads", [r1, r2])
        assert ranked[0][0].memory_id == r1.memory_id
        assert ranked[0][1] > ranked[1][1]

    def test_hybrid_memory_retriever(self) -> None:
        retriever = HybridMemoryRetriever(store=self.store)
        r1 = MemoryRecord(
            type=MemoryType.PROJECT_DECISION,
            content="We decided to use SQLite WAL mode for low latency local memory.",
            summary="SQLite WAL Decision",
            importance=0.9,
            entity_ids=["sqlite", "wal"],
            created_at=time.time(),
        )
        r2 = MemoryRecord(
            type=MemoryType.TEMPORAL_EVENT,
            content="SQLite WAL temporary checkpoint performed.",
            summary="Checkpoint note",
            importance=0.2,
            entity_ids=["sqlite"],
            created_at=time.time() - 50000,
        )
        self.store.add(r1)
        self.store.add(r2)

        results = retriever.retrieve("SQLite WAL mode architecture", limit=2)
        assert len(results) >= 1
        # r1 has higher importance and recency, so it should rank first
        assert results[0].record.memory_id == r1.memory_id
        assert results[0].total_score > 0.4

    # -------------------------------------------------------------------------
    # 4. Project Model & Project Registry
    # -------------------------------------------------------------------------

    def test_project_registry_crud_and_search(self) -> None:
        proj = Project(
            project_id="proj_heyjev",
            name="Hey Jev",
            root_path="C:\\test\\hey-jev",
            languages=["python"],
            frameworks=["pytest"],
            tags=["voice", "agent", "windows"],
        )
        self.project_registry.register(proj)

        fetched = self.project_registry.get("proj_heyjev")
        assert fetched is not None
        assert fetched.name == "Hey Jev"
        assert fetched.languages == ["python"]

        by_path = self.project_registry.get_by_path("C:\\test\\hey-jev")
        assert by_path is not None
        assert by_path.project_id == "proj_heyjev"

        search_res = self.project_registry.search("voice agent")
        assert len(search_res) == 1
        assert search_res[0].project_id == "proj_heyjev"

        # Active project state
        assert self.project_registry.get_active_project() is None
        self.project_registry.set_active_project("proj_heyjev")
        active = self.project_registry.get_active_project()
        assert active is not None
        assert active.project_id == "proj_heyjev"

    # -------------------------------------------------------------------------
    # 5. Project Scanner
    # -------------------------------------------------------------------------

    def test_project_scanner_bounded_discovery(self) -> None:
        scanner = ProjectScanner(registry=self.project_registry)

        # Create nested directory tree
        scan_root = os.path.join(self.temp_dir, "workspace")
        p1 = os.path.join(scan_root, "project_one")
        os.makedirs(p1, exist_ok=True)
        with open(os.path.join(p1, "requirements.txt"), "w") as f:
            f.write("pytest>=8.0.0\nfastapi\n")
        with open(os.path.join(p1, "main.py"), "w") as f:
            f.write("print('hello')\n")

        # Create ignored folder
        ignored = os.path.join(scan_root, ".venv")
        os.makedirs(ignored, exist_ok=True)
        with open(os.path.join(ignored, "pyvenv.cfg"), "w") as f:
            f.write("home = /usr/bin\n")

        found = scanner.scan_directory(scan_root, max_depth=2)
        assert len(found) == 1
        assert found[0].name == "project_one"
        assert "Python" in found[0].languages

    def test_project_scanner_rejects_root_drive(self) -> None:
        scanner = ProjectScanner(registry=self.project_registry)
        with pytest.raises(ValueError, match="rejected by safety policy"):
            scanner.scan_directory("C:\\")

    # -------------------------------------------------------------------------
    # 6. Git Inspector (Read-Only)
    # -------------------------------------------------------------------------

    def test_git_inspector_non_git_directory(self) -> None:
        non_git_dir = os.path.join(self.temp_dir, "empty_dir")
        os.makedirs(non_git_dir, exist_ok=True)
        info = self.git_inspector.inspect(non_git_dir)
        assert info["is_git"] is False
        assert info["clean"] is True
        assert info["branch"] == ""

    def test_git_inspector_real_repository(self) -> None:
        # Inspect current hey-jev repo
        repo_root = os.path.dirname(os.path.abspath(__file__))
        info = self.git_inspector.inspect(repo_root)
        if self.git_inspector.is_git_repository(repo_root):
            assert info["is_git"] is True
            assert info["branch"] != ""
            assert len(info["commit"]) >= 7

    # -------------------------------------------------------------------------
    # 7. Project Health Assessor
    # -------------------------------------------------------------------------

    def test_project_health_assessment(self) -> None:
        assessor = ProjectHealthAssessor(git_inspector=self.git_inspector)
        proj = Project(
            project_id="proj_sample",
            name="Sample Project",
            root_path=self.temp_dir,
        )

        # 1. Healthy assessment
        healthy_snap = assessor.assess(
            project=proj,
            last_test_result="PASSED",
            recent_failures=[],
        )
        assert healthy_snap.known_build_status == "HEALTHY"

        # 2. Broken assessment due to failed tests
        broken_snap = assessor.assess(
            project=proj,
            last_test_result="FAILED",
            recent_failures=["AssertionError: test_phase16 failed"],
        )
        assert broken_snap.known_build_status == "BROKEN"
        assert broken_snap.is_working is False
        assert len(broken_snap.warnings) >= 1

    # -------------------------------------------------------------------------
    # 8. Context Snapshot & Context Builder
    # -------------------------------------------------------------------------

    def test_context_builder_and_least_privilege(self) -> None:
        proj = Project(
            project_id="proj_test",
            name="Testing Workspace",
            root_path=self.temp_dir,
        )
        task = AgentTask(
            goal="Refactor memory module for speed",
            description="Optimize retrieval algorithms",
        )
        mission = Mission(
            mission_id="m_arch",
            title="Architecture Optimization",
            description="Improve overall system throughput",
        )
        env = ContextSnapshot(
            active_app="Code.exe",
            window_title="hey-jev - Visual Studio Code",
        )

        # Add normal vs private memories
        r_normal = MemoryRecord(
            type=MemoryType.PROJECT_DECISION,
            content="Use pure Python BM25 for lightweight search.",
            summary="BM25 decision",
            project_id=proj.project_id,
            sensitivity=MemorySensitivity.NORMAL,
        )
        r_private = MemoryRecord(
            type=MemoryType.USER_PREFERENCE,
            content="User prefers verbose debugging memory logs enabled.",
            summary="Verbose logs preference",
            project_id=proj.project_id,
            tags=["memory"],
            sensitivity=MemorySensitivity.PRIVATE,
        )
        self.store.add(r_normal)
        self.store.add(r_private)

        # Caller without READ_FILES permission should only receive NORMAL
        ctx_restricted = self.context_builder.build_context(
            task=task,
            project=proj,
            mission=mission,
            env_snapshot=env,
            caller_permissions=[TaskPermission.NETWORK_ACCESS],
        )
        mem_contents = [m["content"] for m in ctx_restricted["memories"]]
        assert r_normal.content in mem_contents
        assert r_private.content not in mem_contents

        # Caller with READ_FILES permission should receive both NORMAL and PRIVATE
        ctx_elevated = self.context_builder.build_context(
            task=task,
            project=proj,
            mission=mission,
            env_snapshot=env,
            caller_permissions=[TaskPermission.READ_FILES],
        )
        mem_contents_elevated = [m["content"] for m in ctx_elevated["memories"]]
        assert r_normal.content in mem_contents_elevated
        assert r_private.content in mem_contents_elevated

        # Verify prompt markdown formatting
        md_prompt = self.context_builder.format_prompt_context(ctx_elevated)
        assert "### Context" in md_prompt
        assert "- **Goal**: Refactor memory module for speed" in md_prompt
        assert "- **Project**: Testing Workspace" in md_prompt
        assert "- **Mission**: Architecture Optimization" in md_prompt
        assert "BM25" in md_prompt

    # -------------------------------------------------------------------------
    # 9. UserMemoryController Conversational Interface
    # -------------------------------------------------------------------------

    def test_user_memory_controller_remember_forget_explain(self) -> None:
        rec = self.user_controller.remember(
            content="Always run tests before committing code changes.",
            category=MemoryType.USER_PREFERENCE,
            importance=0.9,
        )
        assert rec.memory_id is not None
        assert self.store.count() == 1

        # Show memories
        active = self.user_controller.show_memory(category=MemoryType.USER_PREFERENCE)
        assert len(active) == 1
        assert active[0].memory_id == rec.memory_id

        # Explain memory provenance
        explanation = self.user_controller.explain(rec.memory_id)
        assert explanation["memory_id"] == rec.memory_id
        assert explanation["source"] == MemorySource.USER.value
        assert explanation["epistemic_status"] == EpistemicStatus.FACT.value
        assert explanation["importance"] == 0.9

        # Forget memory by query match
        forgotten = self.user_controller.forget("committing code changes")
        assert forgotten is True
        assert self.store.count() == 0

    # -------------------------------------------------------------------------
    # 10. Memory Conflict Resolver
    # -------------------------------------------------------------------------

    def test_memory_conflict_resolver_disk_evidence(self) -> None:
        real_dir = os.path.join(self.temp_dir, "real_project")
        os.makedirs(real_dir, exist_ok=True)
        fake_dir = os.path.join(self.temp_dir, "ghost_project")

        mem_fake = MemoryRecord(
            type=MemoryType.PROJECT_STATE,
            content=f"Project root is at {fake_dir}",
            summary="Fake project path",
        )
        mem_real = MemoryRecord(
            type=MemoryType.PROJECT_STATE,
            content=f"Project root is at {real_dir}",
            summary="Real project path",
        )
        self.store.add(mem_fake)
        self.store.add(mem_real)

        winner, loser, reason = self.conflict_resolver.resolve_path_conflict(
            memory_a=mem_fake,
            memory_b=mem_real,
            path_a=fake_dir,
            path_b=real_dir,
        )
        assert winner.memory_id == mem_real.memory_id
        assert loser.memory_id == mem_fake.memory_id
        assert "exists on disk" in reason

        # Winner must be updated, loser superseded
        assert self.store.get(mem_real.memory_id).status == MemoryStatus.ACTIVE
        assert self.store.get(mem_fake.memory_id).status == MemoryStatus.SUPERSEDED

    # =========================================================================
    # THE 6 REQUIRED PHASE 16 DEMONSTRATIONS
    # =========================================================================

    def test_demo_1_user_memory_flow(self) -> None:
        """DEMO 1: User Memory Flow.

        remember -> search -> forget -> verify 0 active.
        """
        # Step 1: User asks Jev to remember preference
        rec = self.user_controller.remember(
            content="I prefer dark mode in all editors and tools.",
            category=MemoryType.USER_PREFERENCE,
            importance=0.9,
            tags=["theme", "ui"],
        )
        assert rec.status == MemoryStatus.ACTIVE

        # Step 2: Search memory
        found = self.store.search("dark mode theme")
        assert len(found) == 1
        assert found[0].memory_id == rec.memory_id
        assert "dark mode" in found[0].content

        # Step 3: Forget memory
        success = self.user_controller.forget("dark mode")
        assert success is True

        # Step 4: Verify 0 active records
        active = self.store.list()
        assert len(active) == 0

    def test_demo_2_project_memory(self) -> None:
        """DEMO 2: Project Memory.

        Project HeyJev -> memory 'Phase 15 added skill system' -> search retrieved.
        """
        proj = Project(
            project_id="proj_heyjev",
            name="Hey Jev",
            root_path="C:\\scratch\\hey-jev",
        )
        self.project_registry.register(proj)

        # Store project decision/state memory
        rec = MemoryRecord(
            type=MemoryType.PROJECT_DECISION,
            content="Phase 15 added skill system and dynamic tool registry with risk tiers.",
            summary="Phase 15 Skill System Architecture",
            project_id="proj_heyjev",
            tags=["phase15", "skills", "tools"],
            importance=0.85,
        )
        self.store.add(rec)

        # Search within project context
        results = self.store.search(
            query="skill system dynamic tool registry",
            filters={"project_id": "proj_heyjev"},
        )
        assert len(results) >= 1
        top_match = results[0]
        assert top_match.memory_id == rec.memory_id
        assert top_match.project_id == "proj_heyjev"
        assert "skill system" in top_match.content

    def test_demo_3_temporal_memory(self) -> None:
        """DEMO 3: Temporal Memory.

        Day 1 tests failing vs Day 2 tests passing -> distinguish historical vs current.
        """
        now = time.time()
        day1_time = now - 86400  # 1 day ago

        day1_rec = MemoryRecord(
            type=MemoryType.PROJECT_STATE,
            content="Test suite execution: 12 failed, 140 passed.",
            summary="Day 1 Test Run",
            project_id="proj_heyjev",
            importance=0.7,
            created_at=day1_time,
            valid_from=day1_time,
        )
        self.store.add(day1_rec)

        day2_rec = MemoryRecord(
            type=MemoryType.PROJECT_STATE,
            content="Test suite execution: 0 failed, 295 passed. Full suite clean.",
            summary="Day 2 Test Run",
            project_id="proj_heyjev",
            importance=0.9,
            created_at=now,
            valid_from=now,
        )

        # Atomically supersede day 1 record with day 2 record
        self.store.supersede(day1_rec.memory_id, day2_rec)

        # Active state shows Day 2 passing result
        current_state = self.store.list(filters={"project_id": "proj_heyjev", "status": MemoryStatus.ACTIVE})
        assert len(current_state) == 1
        assert "295 passed" in current_state[0].content

        # Historical state still preserves Day 1 failing record as SUPERSEDED
        historical = self.store.get(day1_rec.memory_id)
        assert historical is not None
        assert historical.status == MemoryStatus.SUPERSEDED
        assert historical.superseded_by == day2_rec.memory_id
        assert "12 failed" in historical.content

    def test_demo_4_conflict_resolution(self) -> None:
        """DEMO 4: Conflict Resolution.

        Memory A root=X vs Memory B root=Y; Y exists with git -> Y current, X superseded.
        """
        # Create valid mock git directory for Y
        path_y = os.path.join(self.temp_dir, "valid_repo_y")
        os.makedirs(os.path.join(path_y, ".git"), exist_ok=True)
        # Create non-git directory for X
        path_x = os.path.join(self.temp_dir, "non_git_dir_x")
        os.makedirs(path_x, exist_ok=True)

        mem_x = MemoryRecord(
            type=MemoryType.PROJECT_STATE,
            content=f"Hey Jev root repository is located at {path_x}",
            summary="Repository path observation X",
            project_id="proj_heyjev",
            confidence=0.6,
        )
        mem_y = MemoryRecord(
            type=MemoryType.PROJECT_STATE,
            content=f"Hey Jev root repository is located at {path_y}",
            summary="Repository path observation Y",
            project_id="proj_heyjev",
            confidence=0.6,
        )
        self.store.add(mem_x)
        self.store.add(mem_y)

        winner, loser, reason = self.conflict_resolver.resolve_path_conflict(
            memory_a=mem_x,
            memory_b=mem_y,
            path_a=path_x,
            path_b=path_y,
        )

        assert winner.memory_id == mem_y.memory_id
        assert loser.memory_id == mem_x.memory_id
        assert "verified git repository" in reason

        # Winner is active with confidence 1.0; loser is superseded
        assert self.store.get(mem_y.memory_id).status == MemoryStatus.ACTIVE
        assert self.store.get(mem_y.memory_id).confidence == 1.0
        assert self.store.get(mem_x.memory_id).status == MemoryStatus.SUPERSEDED

    def test_demo_5_project_health(self) -> None:
        """DEMO 5: Project Health.

        Clean repo -> dirty working tree + failing test -> diagnostics reflected.
        """
        assessor = ProjectHealthAssessor(git_inspector=self.git_inspector)
        proj = Project(
            project_id="proj_demo",
            name="Demo Application",
            root_path=self.temp_dir,
        )

        # Baseline: healthy state
        snap_clean = assessor.assess(
            project=proj,
            last_test_result="PASSED",
            recent_failures=[],
        )
        assert snap_clean.known_build_status == "HEALTHY"
        assert snap_clean.is_working is True
        assert len(snap_clean.warnings) == 0

        # Degraded state: test failure and unresolved issues
        snap_degraded = assessor.assess(
            project=proj,
            last_test_result="FAILED",
            recent_failures=["ZeroDivisionError: division by zero in calculate_metrics"],
        )
        assert snap_degraded.known_build_status == "BROKEN"
        assert snap_degraded.is_working is False
        assert "Latest test execution reported failures." in snap_degraded.warnings
        assert "1 unresolved failure(s) recorded." in snap_degraded.warnings

    def test_demo_6_context_builder_prioritization(self) -> None:
        """DEMO 6: Context Builder Prioritization.

        Task + Mission + Project + Memory + Git packaged into compact prompt.
        """
        proj = Project(
            project_id="proj_demo6",
            name="Hey Jev Core",
            root_path="C:\\scratch\\hey-jev",
            languages=["python"],
        )
        task = AgentTask(
            goal="Refactor project memory indexer",
            priority=TaskPriority.HIGH,
        )
        mission = Mission(
            mission_id="m_perf",
            title="System Performance Uplift",
        )
        env = ContextSnapshot(
            active_app="powershell.exe",
            window_title="Administrator: Windows PowerShell",
        )

        # Decision memory
        r_decision = MemoryRecord(
            type=MemoryType.PROJECT_DECISION,
            content="Use SQLite WAL mode and synchronous=NORMAL for concurrent memory access.",
            summary="SQLite WAL Concurrency",
            project_id=proj.project_id,
            importance=0.9,
        )
        # Lesson memory
        r_lesson = MemoryRecord(
            type=MemoryType.TOOL_LESSON,
            content="Subprocess git calls take 400ms on Windows; batch or cache git results.",
            summary="Git subprocess latency lesson",
            project_id=proj.project_id,
            tags=["memory", "indexer"],
            importance=0.75,
        )
        self.store.add(r_decision)
        self.store.add(r_lesson)

        ctx = self.context_builder.build_context(
            task=task,
            project=proj,
            mission=mission,
            env_snapshot=env,
            caller_permissions=[TaskPermission.READ_FILES],
        )

        assert ctx["task"]["goal"] == "Refactor project memory indexer"
        assert ctx["project"]["name"] == "Hey Jev Core"
        assert ctx["mission"]["title"] == "System Performance Uplift"
        assert ctx["environment"]["active_app"] == "powershell.exe"
        assert len(ctx["memories"]) == 2

        formatted = self.context_builder.format_prompt_context(ctx)
        assert "### Context" in formatted
        assert "- **Goal**: Refactor project memory indexer" in formatted
        assert "- **Project**: Hey Jev Core" in formatted
        assert "- **Mission**: System Performance Uplift" in formatted
        assert "SQLite WAL mode" in formatted
        assert "Subprocess git calls" in formatted
