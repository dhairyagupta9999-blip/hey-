# Hey Jev — Phase 14 Technology Radar: Multi-Agent Orchestration & Interoperability

## 1. Executive Summary

Phase 14 transitions Hey Jev from a single-agent task runner into a **Coordinator of Agents**.
The central architecture decouples Jev from any single model or framework:
- **Jev is the Orchestrator:** Manages high-level missions, task decomposition, agent routing, ownership, permissions, review loops, contradiction adjudication, and independent verification.
- **Providers are Specialists:** Antigravity (coding/repo), Claude/OpenRouter (review/reasoning), Browser Agent (web operations), Windows Agent (OS/desktop automation), Research Agent (web research & evidence extraction).

This radar surveys state-of-the-art agent interoperability protocols, multi-agent frameworks, and SDKs against Hey Jev's strict resource budget (Intel Core i3-5005U, 2C/4T, ~7.93 GB RAM, Windows 10 Pro x64, Python 3.11.9).

---

## 2. Framework & Protocol Evaluations

### 2.1 A2A Protocol (Agent-to-Agent Protocol 1.0.0)
- **Specification / Repository:** [google/a2a](https://github.com/google/a2a) / [a2a-protocol.org](https://a2a-protocol.org)
- **Current Version:** 1.0.0 Draft Specification
- **License:** Apache-2.0
- **Maintenance:** Active consortium backed by major AI organizations.
- **Python / Windows Compatibility:** Excellent (JSON-over-HTTP/WebSocket transport, protocol buffers, or simple REST/JSON schemas).
- **Dependency Footprint:** Very low (<50 kB pure schema / HTTP client).
- **Core Strengths:** Standardized `AgentMessage`, task delegation envelopes, capabilities discovery (`/.well-known/agent.json`), state exchange, and provider-agnostic handoffs.
- **Weaknesses:** Early ecosystem adoption; heavy reliance on remote networking unless mapped to local IPC.
- **Role in Jev:** **Adopt as Primary Conceptual Standard.** Jev’s `AgentMessage` envelope directly maps to A2A 1.0.0 message structures, enabling zero-friction remote agent interoperability.

---

### 2.2 Model Context Protocol (MCP)
- **Repository:** [modelcontextprotocol/specification](https://github.com/modelcontextprotocol)
- **Current Version:** 2024-11-05 Specification
- **License:** MIT
- **Maintenance:** High (Anthropic + open-source community).
- **Python / Windows Compatibility:** Native Python SDK (`mcp`), runs over stdio and SSE on Windows.
- **Dependency Footprint:** ~1.2 MB.
- **Core Distinction in Jev:**
  - **A2A:** Agent $\leftrightarrow$ Agent collaboration (intent, task ownership, review, synthesis).
  - **MCP:** Agent $\leftrightarrow$ Tools/Resources (file reading, window automation, system metrics).
- **Role in Jev:** **Maintain strict architectural separation.** Jev does not confuse MCP tool calling with agent ownership semantics. MCP is strictly the tool bus.

---

### 2.3 OpenAI Agents SDK
- **Repository:** [openai/openai-agents-python](https://github.com/openai/agents-sdk)
- **Current Version:** 0.1.x
- **License:** Apache-2.0
- **Maintenance:** Actively developed by OpenAI.
- **Python / Windows Compatibility:** Python 3.10+, runs cleanly on Windows.
- **Dependency Footprint:** Moderate (~15 MB, requires `pydantic`, `openai`, `httpx`).
- **State Management & Handoffs:** Uses explicit `Agent` class with `handoffs=[agent_b]` returning control via function calling.
- **Limitations for Jev:** Tightly coupled to OpenAI API semantics, function calling schemas, and async event loops; lacks independent verification gates and multi-provider offline execution.
- **Role in Jev:** **Reference Implementation / Optional Backend Adapter.** Borrow the clean handoff paradigm, but implement it natively in Jev's provider-neutral core.

---

### 2.4 LangGraph
- **Repository:** [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph)
- **Current Version:** 0.2.x
- **License:** MIT
- **Maintenance:** Very high.
- **Python / Windows Compatibility:** Python 3.9+, full Windows support.
- **Dependency Footprint:** Heavy (>80 MB with transitive LangChain dependencies).
- **State Management:** Graph-based cyclical DAGs with Checkpointers (SQLite, Postgres).
- **Limitations for Jev:** Substantial runtime memory overhead (~150 MB RAM per instance), high CPU utilization during graph compilation, and heavy abstraction layers that complicate local low-spec execution.
- **Role in Jev:** **Do not adopt as core dependency.** Jev's custom `TaskGraph` and SQLite `TaskStorage` provide identical DAG cycles and checkpointer recovery in <100 kB with <0.1 MB RAM overhead.

---

### 2.5 AG2 / AutoGen (v0.4+)
- **Repository:** [ag2ai/ag2](https://github.com/ag2ai/ag2) / [microsoft/autogen](https://github.com/microsoft/autogen)
- **Current Version:** 0.4.x (event-driven rewrite)
- **License:** Apache-2.0
- **Maintenance:** Active Microsoft & open-source community.
- **Python / Windows Compatibility:** Python 3.10+, Windows supported.
- **Dependency Footprint:** Heavy (>120 MB).
- **Multi-Agent Model:** Conversational group chats, round-robin, swarm pattern.
- **Limitations for Jev:** Prone to conversational loops, message bloating, and token runaway; difficult to bound on a 2-core CPU without extensive custom governors.
- **Role in Jev:** **Too heavy for core.** Pattern reference for multi-opinion debate and group reflection.

---

### 2.6 CrewAI
- **Repository:** [crewAIInc/crewAI](https://github.com/crewAIInc/crewAI)
- **Current Version:** 0.80.x
- **License:** MIT
- **Maintenance:** High.
- **Python / Windows Compatibility:** Python 3.10+, runs on Windows.
- **Dependency Footprint:** Very heavy (>150 MB, downloads LangChain, ChromaDB, etc.).
- **Multi-Agent Model:** Role-playing agents (Crew -> Agents -> Tasks) with sequential/hierarchical execution.
- **Limitations for Jev:** Exceedingly heavy memory footprint (>250 MB idle), slow cold starts (>4s on i3 CPU), and opinionated LLM-driven planner.
- **Role in Jev:** **Unsuitable for low-power host.** Concepts of agent roles and manager review adopted natively without external packages.

---

### 2.7 PydanticAI
- **Repository:** [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai)
- **Current Version:** 0.0.18+
- **License:** MIT
- **Maintenance:** Fast-growing, backed by Pydantic team.
- **Python / Windows Compatibility:** Python 3.10+, seamless Windows compatibility.
- **Dependency Footprint:** Low-to-moderate (~8 MB, built directly on `pydantic` v2 and `httpx`).
- **Core Strengths:** Fast type-safe structured outputs, dependency injection, model-agnostic, low latency.
- **Role in Jev:** **Strong Candidate for Future Typed Provider Bindings.** Clean design principles mirrored in Jev's `AgentMessage` and `AgentResult` schemas.

---

### 2.8 OpenHands (formerly OpenDevin)
- **Repository:** [All-Hands-AI/OpenHands](https://github.com/All-Hands-AI/OpenHands)
- **Current Version:** 0.15.x
- **License:** MIT
- **Maintenance:** Very high.
- **Focus:** Autonomous software development agent operating within Docker sandboxes.
- **Limitations for Jev:** Requires Docker Desktop (unsupported / too resource-intensive on 8 GB RAM dual-core Windows laptop).
- **Role in Jev:** **Conceptual architecture only.** Jev uses local Antigravity and native Windows subprocess sandboxing rather than container runtimes.

---

## 3. Comparison Matrix

| Technology | License | Dependency Size | RAM Footprint | Windows Native | Handoff Support | Multi-Provider | Jev Suitability |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **A2A Protocol** | Apache-2.0 | <50 kB | <1 MB | Yes (HTTP/IPC) | Native | Provider-Agnostic | **Core Standard** |
| **MCP** | MIT | ~1.2 MB | <5 MB | Yes (Stdio/SSE) | N/A (Tool bus) | Universal Tools | **Core Tool Bus** |
| **OpenAI Agents SDK**| Apache-2.0 | ~15 MB | ~25 MB | Yes | Explicit | OpenAI-biased | Adapter / Reference |
| **LangGraph** | MIT | ~85 MB | ~150 MB | Yes | Graph Edges | Model-agnostic | Too heavy for core |
| **AG2 / AutoGen** | Apache-2.0 | ~120 MB | ~200 MB | Yes | GroupChat/Swarm | Model-agnostic | Too heavy for core |
| **CrewAI** | MIT | ~150 MB | ~250 MB | Yes | Hierarchical | Model-agnostic | Too heavy for core |
| **PydanticAI** | MIT | ~8 MB | ~15 MB | Yes | Via Tools | Model-agnostic | Optional Provider |
| **Jev Native Core** | MIT | **0 MB** | **<0.5 MB** | **Native Win32** | **First-Class** | **Universal** | **Primary System** |

---

## 4. Architectural Decision for Hey Jev

1. **Jev Core Remains Pure & Independent:**
   No heavy external framework (`langchain`, `crewai`, `autogen`) will be installed into Jev's core production environment. All orchestration, task DAGs, message envelopes, and state persistence remain native pure-Python with SQLite and standard libraries.
2. **A2A Protocol Alignment:**
   Hey Jev implements the `AgentMessage` model following the A2A 1.0.0 specification (JSON schema, message types, task envelopes, and capabilities discovery).
3. **Transport Separation:**
   Agent adapters communicate across distinct transports:
   - Direct In-Process / CLI (`AntigravityAdapter`, `WindowsComputerAdapter`, `BrowserAgent`)
   - Direct HTTP / REST (`OpenAICompatibleAdapter`, `AnthropicCompatibleAdapter`)
   - Protocol Transport (`A2ATransport`, `MCPTransport`)
   - Browser Fallback Transport (`BrowserTransport` — deferred for future manual fallback).
