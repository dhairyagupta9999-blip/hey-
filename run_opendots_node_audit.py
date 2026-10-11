"""Runner for authentic OpenDots Node.js MCP Client Integration Audit.

Binds an isolated Hey Jev gateway on an ephemeral loopback port with
fresh SQLite database, capability registry, and Bearer token, then
executes `node test_opendots_client.mjs` against it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from capability_model import CapabilityDefinition, CapabilityType, Reversibility
from capability_registry import CapabilityRegistry
from event_bus import EventBus
from mission_coordinator import MissionCoordinator
from mission_interpreter import MissionInterpreter
from mission_planner import MissionPlanner
from mission_store import SqliteMissionStore
from opendots_gateway import OpenDotsGatewayConfig, OpenDotsGatewayService, OpenDotsMCPGateway
from operational_health import HealthState, OperationalHealthCoordinator, SubsystemCategory
from resource_governor import ResourceGovernor
from tool_model import RiskLevel
from workflow_engine import WorkflowEngine


def main() -> int:
    temp_dir = tempfile.mkdtemp(prefix="hey_jev_opendots_node_")
    db_path = os.path.join(temp_dir, "node_audit_missions.db")
    print(f"[Audit Harness] Temp directory: {temp_dir}")

    service = None
    try:
        # Initialize Hey Jev core components
        store = SqliteMissionStore(db_path=db_path)
        event_bus = EventBus()
        registry = CapabilityRegistry()

        cap_inspect = CapabilityDefinition(
            capability_id="system:inspect_disk",
            name="Inspect Disk",
            description="Inspect available disk drive space.",
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

        governor = ResourceGovernor(foreground_slots=2, background_slots=2)
        health_coord = OperationalHealthCoordinator(event_bus=event_bus)
        health_coord.record_observation(
            component_id="runtime_core",
            category=SubsystemCategory.RUNTIME_COMPONENT,
            state=HealthState.HEALTHY,
            evidence=["Subsystem operational."],
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

        bearer_token = "audit-token-" + uuid.uuid4().hex
        config = OpenDotsGatewayConfig(
            enabled=True,
            host="127.0.0.1",
            port=0,  # OS auto-picks ephemeral port
            bearer_token=bearer_token,
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

        service = OpenDotsGatewayService(gateway)
        service.start()
        port = service.bound_port
        print(f"[Audit Harness] Hey Jev Gateway running on http://127.0.0.1:{port}")

        # Run Node.js client
        cmd = [
            "node",
            "test_opendots_client.mjs",
            "--port",
            str(port),
            "--token",
            bearer_token,
        ]
        print(f"[Audit Harness] Executing: {' '.join(cmd)}")
        proc = subprocess.run(cmd, capture_output=True, text=True)

        print("\n--- Node.js OpenDots Client Output ---")
        print(proc.stdout)
        if proc.stderr:
            print("--- Node.js Stderr ---")
            print(proc.stderr)
        print("--------------------------------------\n")

        if proc.returncode != 0:
            print(f"[Audit Harness] Node.js test FAILED with returncode {proc.returncode}")
            return 1

        print("[Audit Harness] Node.js OpenDots test PASSED successfully!")
        return 0

    finally:
        if service:
            service.stop()
        shutil.rmtree(temp_dir, ignore_errors=True)
        print("[Audit Harness] Cleaned up temporary files.")


if __name__ == "__main__":
    sys.exit(main())
