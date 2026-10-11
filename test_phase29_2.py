"""Phase 29.2 Test Suite: Actual OpenDots SDK and Application Integration Verification.

Validates the Hey Jev MCP Interoperability Gateway against:
1. The exact official @modelcontextprotocol/sdk v1.31.0 resolved by OpenDots.
2. OpenDots' actual ConnectionService class and WorkspaceStore.
3. OpenDots' live Hono application HTTP server routes and prerequisite diagnostics.
4. Stage 5 authorization boundaries: UI toggle ("Ask first") cannot bypass Jev's internal safety gates.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from typing import Any, Dict

import pytest

from capability_model import CapabilityDefinition, CapabilityType, Reversibility
from capability_registry import CapabilityRegistry
from event_bus import EventBus
from mission_coordinator import MissionCoordinator
from mission_interpreter import MissionInterpreter
from mission_model import MissionMode, MissionStatus
from mission_planner import MissionPlanner
from mission_store import SqliteMissionStore
from opendots_gateway import (
    MCP_TOOLS,
    OpenDotsGatewayConfig,
    OpenDotsGatewayService,
    OpenDotsMCPGateway,
)
from operational_health import (
    HealthObservation,
    HealthState,
    OperationalHealthCoordinator,
    SubsystemCategory,
)
from resource_governor import ResourceGovernor
from tool_model import RiskLevel
from workflow_engine import WorkflowEngine


# Path to external OpenDots repository
OPENDOTS_DIR = r"C:\Users\prabh\.gemini\antigravity\scratch\OpenDots"


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="hey_jev_p29_2_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def gateway_env(temp_dir):
    """Sets up an isolated, full-stack Hey Jev environment for Phase 29.2 testing."""
    db_path = os.path.join(temp_dir, "test_p29_2_missions.db")
    store = SqliteMissionStore(db_path=db_path)
    event_bus = EventBus()
    registry = CapabilityRegistry()

    # Register sample capabilities
    cap_inspect = CapabilityDefinition(
        capability_id="system:inspect_disk",
        name="Inspect Disk",
        description="Inspect available disk space.",
        capability_type=CapabilityType.TOOL,
        risk_level=RiskLevel.LOW,
        reversibility=Reversibility.READ_ONLY,
    )
    cap_clean = CapabilityDefinition(
        capability_id="system:clean_temp_files",
        name="Clean Temp Files",
        description="Delete temporary files older than threshold.",
        capability_type=CapabilityType.TOOL,
        risk_level=RiskLevel.MEDIUM,
        reversibility=Reversibility.REVERSIBLE,
    )
    cap_purge = CapabilityDefinition(
        capability_id="system:purge_archive",
        name="Purge Archive",
        description="Irreversibly delete historical system archives.",
        capability_type=CapabilityType.TOOL,
        risk_level=RiskLevel.HIGH,
        reversibility=Reversibility.IRREVERSIBLE,
    )
    registry.register(cap_inspect)
    registry.register(cap_clean)
    registry.register(cap_purge)

    governor = ResourceGovernor(foreground_slots=1, background_slots=1)
    health_coord = OperationalHealthCoordinator(event_bus=event_bus)
    health_coord.record_observation(
        component_id="runtime_core",
        category=SubsystemCategory.RUNTIME_COMPONENT,
        state=HealthState.HEALTHY,
        evidence=["Subsystem initialized successfully."],
    )

    workflow_engine = WorkflowEngine(registry=registry)
    coordinator = MissionCoordinator(
        registry=registry,
        workflow_engine=workflow_engine,
        mission_store=store,
        event_bus=event_bus,
        resource_governor=governor,
    )
    interpreter = MissionInterpreter(registry=registry)
    planner = MissionPlanner(registry=registry, resource_governor=governor)

    token = "test-audit-token-" + uuid.uuid4().hex
    config = OpenDotsGatewayConfig(
        enabled=True,
        host="127.0.0.1",
        port=0,  # Ephemeral port
        bearer_token=token,
    )

    gateway = OpenDotsMCPGateway(
        config=config,
        mission_store=store,
        coordinator=coordinator,
        interpreter=interpreter,
        planner=planner,
        capability_registry=registry,
        health_coordinator=health_coord,
        resource_governor=governor,
    )

    return {
        "gateway": gateway,
        "token": token,
        "store": store,
        "coordinator": coordinator,
        "registry": registry,
        "health": health_coord,
    }


# ===========================================================================
# 1. OpenDots Repository and Version Audit
# ===========================================================================

def test_01_opendots_repository_and_resolved_sdk_version():
    """Verify that OpenDots exists outside Hey Jev and resolves @modelcontextprotocol/sdk v1.31.0."""
    assert os.path.exists(OPENDOTS_DIR), f"OpenDots repository not found at {OPENDOTS_DIR}"

    # Lockfile check
    lockfile_path = os.path.join(OPENDOTS_DIR, "package-lock.json")
    assert os.path.exists(lockfile_path), "package-lock.json not found in OpenDots"

    with open(lockfile_path, "r", encoding="utf-8") as f:
        lock_data = json.load(f)

    # Check resolved version of @modelcontextprotocol/sdk
    mcp_pkg = (
        lock_data.get("packages", {}).get("node_modules/@modelcontextprotocol/sdk")
        or lock_data.get("dependencies", {}).get("@modelcontextprotocol/sdk")
    )
    assert mcp_pkg is not None, "@modelcontextprotocol/sdk not found in package-lock.json"
    resolved_version = mcp_pkg.get("version")
    assert resolved_version == "1.31.0", f"Expected SDK version 1.31.0, got {resolved_version}"


def test_02_sdk_protocol_version_negotiation_decision(gateway_env):
    """Verify that Jev gateway and official SDK support Streamable HTTP 2025-03-26."""
    gateway = gateway_env["gateway"]

    # Verify Jev gateway protocol baseline
    assert "2025-03-26" in gateway.SUPPORTED_PROTOCOL_VERSIONS
    assert gateway.DEFAULT_PROTOCOL_VERSION == "2025-03-26"

    # Test initialization request from modern SDK (which advertises 2025-11-25)
    init_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "opendots", "version": "0.1.0"},
        },
    }
    response = gateway.handle_jsonrpc(init_request)
    assert response is not None
    assert response.get("jsonrpc") == "2.0"
    result = response.get("result", {})
    # Jev negotiates 2025-03-26 which SDK v1.31.0 supports (SUPPORTED_PROTOCOL_VERSIONS list)
    assert result.get("protocolVersion") == "2025-03-26"
    assert "serverInfo" in result
    assert result["serverInfo"]["name"] == "hey-jev-gateway"


# ===========================================================================
# 2. Official MCP SDK Client Harness Execution (Stage 3)
# ===========================================================================

def test_03_official_mcp_sdk_client_harness(gateway_env):
    """Execute official @modelcontextprotocol/sdk v1.31.0 test harness against live gateway."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js binary not available on runner.")

    gateway = gateway_env["gateway"]
    token = gateway_env["token"]
    service = OpenDotsGatewayService(gateway)

    try:
        service.start()
        port = service.bound_port

        script_path = os.path.join(OPENDOTS_DIR, "test_official_sdk_client.mjs")
        assert os.path.exists(script_path), f"Missing harness script: {script_path}"

        cmd = [
            node_bin,
            "test_official_sdk_client.mjs",
            "--port",
            str(port),
            "--token",
            token,
        ]
        proc = subprocess.run(cmd, cwd=OPENDOTS_DIR, capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, f"Harness failed (code {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        assert "Total Checks:   55" in proc.stdout
        assert "Passed:         55" in proc.stdout
        assert "Failed:         0" in proc.stdout
        assert "All official MCP SDK integration checks PASSED successfully!" in proc.stdout
    finally:
        service.stop()


# ===========================================================================
# 3. OpenDots ConnectionService Class Integration (Stage 4)
# ===========================================================================

def test_04_opendots_connection_service_integration(gateway_env):
    """Execute OpenDots ConnectionService integration test against live gateway."""
    npx_bin = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx_bin:
        pytest.skip("npx not available on runner.")

    gateway = gateway_env["gateway"]
    token = gateway_env["token"]
    service = OpenDotsGatewayService(gateway)

    try:
        service.start()
        port = service.bound_port

        script_path = os.path.join(OPENDOTS_DIR, "test_opendots_connection_service.ts")
        assert os.path.exists(script_path), f"Missing ConnectionService test script: {script_path}"

        cmd = [
            npx_bin,
            "tsx",
            "test_opendots_connection_service.ts",
            "--port",
            str(port),
            "--token",
            token,
        ]
        proc = subprocess.run(cmd, cwd=OPENDOTS_DIR, capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, f"ConnectionService test failed (code {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        assert "Total Checks:   22" in proc.stdout
        assert "Passed:         22" in proc.stdout
        assert "Failed:         0" in proc.stdout
        assert "All OpenDots ConnectionService integration checks PASSED successfully!" in proc.stdout
    finally:
        service.stop()


# ===========================================================================
# 4. OpenDots Live Application HTTP Route Integration (Stage 4)
# ===========================================================================

def test_05_opendots_live_app_http_route_integration(gateway_env):
    """Execute OpenDots live Hono application HTTP server test against live gateway."""
    npx_bin = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx_bin:
        pytest.skip("npx not available on runner.")

    gateway = gateway_env["gateway"]
    token = gateway_env["token"]
    service = OpenDotsGatewayService(gateway)

    try:
        service.start()
        gw_port = service.bound_port

        script_path = os.path.join(OPENDOTS_DIR, "test_opendots_live_app.ts")
        assert os.path.exists(script_path), f"Missing live app test script: {script_path}"

        cmd = [
            npx_bin,
            "tsx",
            "test_opendots_live_app.ts",
            "--gateway-port",
            str(gw_port),
            "--token",
            token,
            "--app-port",
            "4335",
        ]
        proc = subprocess.run(cmd, cwd=OPENDOTS_DIR, capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, f"Live app test failed (code {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        assert "Total Checks:   16" in proc.stdout
        assert "Passed:         16" in proc.stdout
        assert "Failed:         0" in proc.stdout
        assert "All live OpenDots application route integration checks PASSED!" in proc.stdout
    finally:
        service.stop()


# ===========================================================================
# 5. Stage 5 Authorization Boundaries & Invariants
# ===========================================================================

def test_06_authorization_ui_toggle_cannot_bypass_jev_checkpoints(gateway_env):
    """Verify that disabling OpenDots approval requirement cannot bypass Jev's internal safety gates."""
    gateway = gateway_env["gateway"]
    store = gateway_env["store"]

    # Even if OpenDots client calls submit directly without UI approval,
    # Jev must evaluate the mission's risk and mode and hold in WAITING_FOR_APPROVAL
    submit_req = {
        "jsonrpc": "2.0",
        "id": 101,
        "method": "tools/call",
        "params": {
            "name": "jev_submit_mission",
            "arguments": {
                "objective": "Purge system archives permanently",
                "mode": "COPILOT",
            },
        },
    }

    resp = gateway.handle_jsonrpc(submit_req)
    assert resp is not None
    assert "result" in resp
    content = resp["result"]["content"][0]["text"]
    data = json.loads(content)

    assert data["status"] == "WAITING_FOR_APPROVAL"
    assert data["requires_approval"] is True
    assert data["persisted"] is True

    # Confirm in SQLite store
    m = store.get_mission(data["mission_id"])
    assert m is not None
    assert m.status == MissionStatus.WAITING_FOR_APPROVAL


def test_07_preview_zero_side_effects_guarantee(gateway_env):
    """Verify that jev_create_mission_preview never persists or executes tasks."""
    gateway = gateway_env["gateway"]
    store = gateway_env["store"]

    initial_missions = store.list_missions()
    initial_count = len(initial_missions)

    preview_req = {
        "jsonrpc": "2.0",
        "id": 102,
        "method": "tools/call",
        "params": {
            "name": "jev_create_mission_preview",
            "arguments": {
                "objective": "Clean all temporary cache files",
                "mode": "COPILOT",
            },
        },
    }

    resp = gateway.handle_jsonrpc(preview_req)
    assert resp is not None
    assert "result" in resp
    content = resp["result"]["content"][0]["text"]
    data = json.loads(content)

    assert data["preview_only"] is True
    assert data["persisted"] is False
    assert data["executed"] is False
    assert len(data["planned_tasks"]) > 0

    # Ensure zero changes in database
    after_missions = store.list_missions()
    assert len(after_missions) == initial_count


def test_08_no_approval_bypass_endpoint_exposed(gateway_env):
    """Verify that gateway exposes no endpoint to directly alter approvals or bypass checkpoints."""
    for tool in MCP_TOOLS:
        name = tool["name"]
        # Ensure no arbitrary approval mutation or execution endpoints
        assert "approve" not in name.lower()
        assert "execute_arbitrary" not in name.lower()
        assert "shell" not in name.lower()
