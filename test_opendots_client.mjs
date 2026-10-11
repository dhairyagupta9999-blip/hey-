/**
 * Authentic OpenDots MCP Client Integration Test (Phase 29.1)
 *
 * Implements the client behavior of OpenDots (CopilotKit/OpenDots) using
 * Node.js native fetch and Streamable HTTP transport patterns.
 *
 * Exercises:
 * 1. Security boundary: DNS rebinding rejection (Host header)
 * 2. Security boundary: Origin isolation rejection (Origin header)
 * 3. Security boundary: Authentication enforcement (Bearer token)
 * 4. Streamable HTTP MCP initialize handshake (negotiates 2025-03-26)
 * 5. Session ID tracking via Mcp-Session-Id header
 * 6. Notification dispatch (202 Accepted verification)
 * 7. Tools discovery and OpenDots readOnlyHint annotation validation
 * 8. Read-only tool execution (jev_health, jev_search_capabilities)
 * 9. Controlled tool execution (jev_create_mission_preview, jev_submit_mission)
 * 10. Session termination via DELETE /mcp (204 No Content)
 */

import { argv, exit } from "node:process";

// Parse CLI args: --port <port> --token <token>
let port = 8765;
let token = "";

for (let i = 2; i < argv.length; i++) {
  if (argv[i] === "--port" && argv[i + 1]) {
    port = parseInt(argv[i + 1], 10);
    i++;
  } else if (argv[i] === "--token" && argv[i + 1]) {
    token = argv[i + 1];
    i++;
  }
}

if (!token) {
  console.error("Error: --token <token> is required.");
  exit(1);
}

const baseUrl = `http://127.0.0.1:${port}`;
console.log(`[OpenDots Client] Connecting to Hey Jev Gateway at ${baseUrl}...`);

let sessionId = null;
let testsPassed = 0;
let testsFailed = 0;

function assert(condition, message) {
  if (!condition) {
    console.error(`  FAIL: ${message}`);
    testsFailed++;
    throw new Error(message);
  }
  console.log(`  PASS: ${message}`);
  testsPassed++;
}

import http from "node:http";

function rawHttpRequest(options, body) {
  return new Promise((resolve, reject) => {
    const req = http.request(options, (res) => {
      let data = "";
      res.on("data", (chunk) => { data += chunk; });
      res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body: data }));
    });
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

async function runAudit() {
  // -------------------------------------------------------------------------
  // Test 1: DNS Rebinding Protection (Attacker Host header)
  // -------------------------------------------------------------------------
  console.log("\n[Check 1] Verifying DNS rebinding defense (invalid Host header)...");
  {
    // Use raw HTTP request because WHATWG fetch forbids/overrides the Host header
    const res = await rawHttpRequest({
      hostname: "127.0.0.1",
      port: port,
      path: "/mcp",
      method: "POST",
      headers: {
        Host: `evil-attacker.site:${port}`,
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
    }, JSON.stringify({ jsonrpc: "2.0", id: 1, method: "ping" }));
    assert(res.status === 403, `Attacker Host header returns 403 Forbidden (got ${res.status})`);
  }

  // -------------------------------------------------------------------------
  // Test 2: Origin Header Isolation (Untrusted external web origin)
  // -------------------------------------------------------------------------
  console.log("\n[Check 2] Verifying Origin header isolation (untrusted Origin)...");
  {
    const res = await fetch(`${baseUrl}/mcp`, {
      method: "POST",
      headers: {
        Host: `127.0.0.1:${port}`,
        Origin: "https://malicious-website.org",
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ jsonrpc: "2.0", id: 2, method: "ping" }),
    });
    assert(res.status === 403, `Untrusted Origin header returns 403 Forbidden (got ${res.status})`);
  }

  // -------------------------------------------------------------------------
  // Test 3: Authentication Enforcement (Missing/Invalid Token)
  // -------------------------------------------------------------------------
  console.log("\n[Check 3] Verifying Bearer authentication enforcement...");
  {
    const resNoAuth = await fetch(`${baseUrl}/mcp`, {
      method: "POST",
      headers: {
        Host: `127.0.0.1:${port}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ jsonrpc: "2.0", id: 3, method: "ping" }),
    });
    assert(resNoAuth.status === 401, `Missing Authorization returns 401 Unauthorized (got ${resNoAuth.status})`);

    const resBadAuth = await fetch(`${baseUrl}/mcp`, {
      method: "POST",
      headers: {
        Host: `127.0.0.1:${port}`,
        Authorization: "Bearer invalid-token-xyz",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ jsonrpc: "2.0", id: 4, method: "ping" }),
    });
    assert(resBadAuth.status === 401, `Invalid Authorization returns 401 Unauthorized (got ${resBadAuth.status})`);
  }

  // -------------------------------------------------------------------------
  // Test 4: MCP Initialize Handshake & Protocol Negotiation
  // -------------------------------------------------------------------------
  console.log("\n[Check 4] Performing Streamable HTTP initialize handshake...");
  {
    const initPayload = {
      jsonrpc: "2.0",
      id: "init-req",
      method: "initialize",
      params: {
        protocolVersion: "2025-03-26",
        capabilities: {},
        clientInfo: { name: "OpenDots-AuditClient", version: "1.0.0" },
      },
    };

    const res = await fetch(`${baseUrl}/mcp`, {
      method: "POST",
      headers: {
        Host: `127.0.0.1:${port}`,
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        Accept: "application/json, text/event-stream",
      },
      body: JSON.stringify(initPayload),
    });

    assert(res.status === 200, `Initialize handshake returns HTTP 200 OK (got ${res.status})`);
    sessionId = res.headers.get("mcp-session-id") || res.headers.get("Mcp-Session-Id");
    assert(Boolean(sessionId && sessionId.length > 8), `Received valid Mcp-Session-Id header: ${sessionId}`);

    const data = await res.json();
    assert(data.jsonrpc === "2.0", `JSON-RPC version is 2.0`);
    assert(data.id === "init-req", `Matching request id in response`);
    assert(data.result?.protocolVersion === "2025-03-26", `Negotiated protocolVersion is 2025-03-26`);
    assert(data.result?.serverInfo?.name === "hey-jev-gateway", `Server name is hey-jev-gateway`);
  }

  // -------------------------------------------------------------------------
  // Test 5: Notification Dispatch (notifications/initialized)
  // -------------------------------------------------------------------------
  console.log("\n[Check 5] Dispatching MCP notification (notifications/initialized)...");
  {
    const notifPayload = {
      jsonrpc: "2.0",
      method: "notifications/initialized",
    };

    const res = await fetch(`${baseUrl}/mcp`, {
      method: "POST",
      headers: {
        Host: `127.0.0.1:${port}`,
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "Mcp-Session-Id": sessionId,
      },
      body: JSON.stringify(notifPayload),
    });

    assert(res.status === 202, `Notification returns HTTP 202 Accepted (got ${res.status})`);
    const bodyText = await res.text();
    assert(bodyText.length === 0, `Notification response body is empty (length 0)`);
  }

  // -------------------------------------------------------------------------
  // Test 6: Tool Discovery & OpenDots readOnlyHint Validation
  // -------------------------------------------------------------------------
  console.log("\n[Check 6] Discovering gateway tools and verifying OpenDots approval policy...");
  let discoveredTools = [];
  {
    const res = await fetch(`${baseUrl}/mcp`, {
      method: "POST",
      headers: {
        Host: `127.0.0.1:${port}`,
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "Mcp-Session-Id": sessionId,
      },
      body: JSON.stringify({ jsonrpc: "2.0", id: "tools-1", method: "tools/list" }),
    });

    assert(res.status === 200, `tools/list returns HTTP 200 OK`);
    const data = await res.json();
    discoveredTools = data.result?.tools || [];
    assert(discoveredTools.length === 10, `Discovered exactly 10 MCP tools (got ${discoveredTools.length})`);

    const readOnlyTools = [];
    const approvalRequiredTools = [];

    for (const tool of discoveredTools) {
      // OpenDots native approval derivation logic:
      // const readOnly = tool.annotations?.readOnlyHint === true;
      // const requiresApproval = !readOnly;
      const readOnly = tool.annotations?.readOnlyHint === true;
      const requiresApproval = !readOnly;

      if (requiresApproval) {
        approvalRequiredTools.push(tool.name);
      } else {
        readOnlyTools.push(tool.name);
      }
    }

    assert(readOnlyTools.length === 5, `OpenDots categorizes exactly 5 tools as read-only: ${readOnlyTools.join(", ")}`);
    assert(approvalRequiredTools.length === 5, `OpenDots categorizes exactly 5 tools as requiring approval: ${approvalRequiredTools.join(", ")}`);

    assert(readOnlyTools.includes("jev_health"), "jev_health is marked read-only");
    assert(readOnlyTools.includes("jev_list_missions"), "jev_list_missions is marked read-only");
    assert(readOnlyTools.includes("jev_get_mission"), "jev_get_mission is marked read-only");
    assert(readOnlyTools.includes("jev_get_mission_timeline"), "jev_get_mission_timeline is marked read-only");
    assert(readOnlyTools.includes("jev_search_capabilities"), "jev_search_capabilities is marked read-only");

    assert(approvalRequiredTools.includes("jev_create_mission_preview"), "jev_create_mission_preview requires approval");
    assert(approvalRequiredTools.includes("jev_submit_mission"), "jev_submit_mission requires approval");
    assert(approvalRequiredTools.includes("jev_request_pause"), "jev_request_pause requires approval");
    assert(approvalRequiredTools.includes("jev_request_resume"), "jev_request_resume requires approval");
    assert(approvalRequiredTools.includes("jev_request_cancellation"), "jev_request_cancellation requires approval");
  }

  // -------------------------------------------------------------------------
  // Test 7: Execute Read-Only Tool (jev_health)
  // -------------------------------------------------------------------------
  console.log("\n[Check 7] Calling read-only tool jev_health...");
  {
    const res = await fetch(`${baseUrl}/mcp`, {
      method: "POST",
      headers: {
        Host: `127.0.0.1:${port}`,
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "Mcp-Session-Id": sessionId,
      },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: "call-health",
        method: "tools/call",
        params: { name: "jev_health", arguments: {} },
      }),
    });

    assert(res.status === 200, `jev_health call returns HTTP 200 OK`);
    const data = await res.json();
    assert(!data.result.isError, `jev_health completed without error`);
    const healthJson = JSON.parse(data.result.content[0].text);
    assert(["healthy", "degraded"].includes(healthJson.status), `Health status is valid: ${healthJson.status}`);
    assert(typeof healthJson.subsystems === "object", `Subsystem observations present`);
  }

  // -------------------------------------------------------------------------
  // Test 8: Execute Read-Only Tool (jev_search_capabilities)
  // -------------------------------------------------------------------------
  console.log("\n[Check 8] Calling read-only tool jev_search_capabilities...");
  {
    const res = await fetch(`${baseUrl}/mcp`, {
      method: "POST",
      headers: {
        Host: `127.0.0.1:${port}`,
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "Mcp-Session-Id": sessionId,
      },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: "call-search",
        method: "tools/call",
        params: { name: "jev_search_capabilities", arguments: { query: "disk" } },
      }),
    });

    assert(res.status === 200, `jev_search_capabilities returns HTTP 200 OK`);
    const data = await res.json();
    const searchJson = JSON.parse(data.result.content[0].text);
    assert(searchJson.total_matches >= 1, `Found ${searchJson.total_matches} capabilities matching 'disk'`);
  }

  // -------------------------------------------------------------------------
  // Test 9: Execute Preview Tool (jev_create_mission_preview)
  // -------------------------------------------------------------------------
  console.log("\n[Check 9] Calling controlled tool jev_create_mission_preview...");
  {
    const res = await fetch(`${baseUrl}/mcp`, {
      method: "POST",
      headers: {
        Host: `127.0.0.1:${port}`,
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "Mcp-Session-Id": sessionId,
      },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: "call-preview",
        method: "tools/call",
        params: {
          name: "jev_create_mission_preview",
          arguments: {
            objective: "Inspect available disk drive space",
            mode: "COPILOT",
          },
        },
      }),
    });

    assert(res.status === 200, `jev_create_mission_preview returns HTTP 200 OK`);
    const data = await res.json();
    const previewJson = JSON.parse(data.result.content[0].text);
    assert(previewJson.preview_only === true, `Preview is flagged preview_only=true`);
    assert(previewJson.persisted === false, `Preview is not persisted in database`);
    assert(previewJson.executed === false, `Preview is not executed`);
    assert(previewJson.total_tasks >= 1, `Preview generated planned DAG tasks`);
  }

  // -------------------------------------------------------------------------
  // Test 10: Execute Submit Tool (jev_submit_mission)
  // -------------------------------------------------------------------------
  console.log("\n[Check 10] Calling controlled tool jev_submit_mission with approval gate...");
  let createdMissionId = null;
  {
    const res = await fetch(`${baseUrl}/mcp`, {
      method: "POST",
      headers: {
        Host: `127.0.0.1:${port}`,
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "Mcp-Session-Id": sessionId,
      },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: "call-submit",
        method: "tools/call",
        params: {
          name: "jev_submit_mission",
          arguments: {
            objective: "Inspect available disk drive space",
            mode: "COPILOT",
            auto_execute: true,
          },
        },
      }),
    });

    assert(res.status === 200, `jev_submit_mission returns HTTP 200 OK`);
    const data = await res.json();
    const submitJson = JSON.parse(data.result.content[0].text);
    assert(Boolean(submitJson.mission_id), `Created mission has mission_id: ${submitJson.mission_id}`);
    assert(submitJson.requires_approval === true, `Governance enforces requires_approval=true for COPILOT mode`);
    assert(submitJson.executed === false, `Mission was not executed before approval`);
    assert(submitJson.status === "WAITING_FOR_APPROVAL", `Mission status is WAITING_FOR_APPROVAL`);
    createdMissionId = submitJson.mission_id;
  }

  // -------------------------------------------------------------------------
  // Test 11: Inspect Created Mission (jev_get_mission)
  // -------------------------------------------------------------------------
  console.log("\n[Check 11] Inspecting created mission via jev_get_mission...");
  {
    const res = await fetch(`${baseUrl}/mcp`, {
      method: "POST",
      headers: {
        Host: `127.0.0.1:${port}`,
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "Mcp-Session-Id": sessionId,
      },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: "call-get-mission",
        method: "tools/call",
        params: {
          name: "jev_get_mission",
          arguments: { mission_id: createdMissionId },
        },
      }),
    });

    assert(res.status === 200, `jev_get_mission returns HTTP 200 OK`);
    const data = await res.json();
    const missionJson = JSON.parse(data.result.content[0].text);
    assert(missionJson.mission_id === createdMissionId, `Retrieved correct mission ID`);
    assert(missionJson.status === "WAITING_FOR_APPROVAL", `Persisted status is WAITING_FOR_APPROVAL`);
  }

  // -------------------------------------------------------------------------
  // Test 12: Session Termination via DELETE /mcp
  // -------------------------------------------------------------------------
  console.log("\n[Check 12] Terminating MCP session via DELETE /mcp...");
  {
    const res = await fetch(`${baseUrl}/mcp`, {
      method: "DELETE",
      headers: {
        Host: `127.0.0.1:${port}`,
        Authorization: `Bearer ${token}`,
        "Mcp-Session-Id": sessionId,
      },
    });

    assert(res.status === 204, `DELETE /mcp returns HTTP 204 No Content (got ${res.status})`);
  }

  // -------------------------------------------------------------------------
  // Final Results
  // -------------------------------------------------------------------------
  console.log("\n" + "=".repeat(60));
  console.log(`[OpenDots Client Audit Summary]`);
  console.log(`  Total Checks: ${testsPassed + testsFailed}`);
  console.log(`  Passed:       ${testsPassed}`);
  console.log(`  Failed:       ${testsFailed}`);
  console.log("=".repeat(60));

  if (testsFailed > 0) {
    exit(1);
  }
}

runAudit().catch((err) => {
  console.error("\nUnexpected error during OpenDots client execution:", err);
  exit(1);
});
