# Hey Jev — Phase 11 Technology Radar: Agent Standards & Harness Evaluation

**Hardware Environment:** Intel Core i3-5005U (2 physical cores / 4 logical threads @ 2.00 GHz), 7.93 GB RAM, Windows 10 Pro x64  
**Architectural Role of Jev:** General-Purpose Local Agent Orchestrator  
**Date:** October 2026

---

## Executive Summary

Phase 11 lays the foundation for transforming Hey Jev from a single-turn voice assistant into a general-purpose, multi-agent orchestrator. Rather than adopting monolithic, heavyweight third-party multi-agent frameworks that would overwhelm this dual-core 8 GB machine with transitive dependency bloat, Jev defines a lean, provider-neutral architecture based on open standards:
1. **MCP (Model Context Protocol):** Adopted as the primary standard for **tool, resource, and computer interaction**.
2. **A2A (Agent-to-Agent Protocols):** Studied and positioned as the target standard for **cross-agent handoff, delegation, and context propagation**.
3. **Core Philosophy:** Antigravity, Claude, Codex, and local models are **providers**, while Jev remains the **orchestrator**.

---

## Standard Protocols Evaluation

### 1. Model Context Protocol (MCP)
* **Project / URL:** Model Context Protocol ([https://github.com/modelcontextprotocol](https://github.com/modelcontextprotocol) / [https://modelcontextprotocol.io](https://modelcontextprotocol.io))
* **License:** MIT
* **Purpose:** Open standard connecting AI models to local tools, context, databases, and runtime services over JSON-RPC 2.0 (stdio or SSE).
* **Maturity:** High / Industry Standard. Supported across Claude Desktop, Antigravity, Zed, Cursor, and enterprise developer tools.
* **Usefulness to Jev:** Very High. Already leveraged in Hey Jev Phase 7 (`mcp_client.py`) for Windows automation (`sbroenne/mcp-windows`). Allows Jev to attach local OS tools without writing bespoke adapters for every application.
* **Dependency Cost:** Low. Can be operated directly over raw standard I/O (stdin/stdout) via JSON-RPC without requiring the heavyweight full TypeScript runtime if written cleanly in Python.
* **Security Concerns:** MCP tools can execute arbitrary code or shell commands if unvetted. Hey Jev gates all MCP operations through its deterministic `safety_engine.py` (foreground-window checks, blocked commands, confirmation dialogs).
* **Verdict:** **ADOPT** (Core Tool/Resource Layer).

---

### 2. Google A2A (Agent-to-Agent Protocol & Schemas)
* **Project / URL:** Agent-to-Agent Protocol / Multi-Agent Communication Specifications ([https://github.com/google/a2a](https://github.com/google) / Agent CommunicationRFCs)
* **License:** Apache-2.0
* **Purpose:** Standardized schema and interaction envelope for heterogeneous agents to discover, negotiate, delegate, and hand off tasks.
* **Maturity:** Emerging / Standardizing. Rapidly evolving as industry consensus forms around inter-agent handoff contracts.
* **Usefulness to Jev:** Very High. Jev requires a structured envelope (`HandoffEnvelope`) rather than uncontrolled prose string passing when delegating between Antigravity, Claude, and local specialized agents.
* **Dependency Cost:** Zero runtime dependency if implemented as provider-neutral dataclasses/JSON schemas.
* **Security Concerns:** Cross-agent prompt injection, untrusted execution chains, authorization delegation escalation. Mitigated in Jev via explicit `PermissionPolicy` and `TaskVerifier` boundaries between handoffs.
* **Verdict:** **ADOPT CONCEPTS & ALIGN** (Core Inter-Agent Handoff Layer).

---

## Multi-Agent Frameworks Evaluation

### 3. AG2 / AutoGen (Microsoft / AG2 Community)
* **Project / URL:** AG2 / AutoGen ([https://github.com/ag2ai/ag2](https://github.com/ag2ai/ag2))
* **License:** Apache-2.0
* **Purpose:** Multi-agent conversation and task orchestration framework enabling conversational workflows between LLMs and tools.
* **Maturity:** High (Active community, 30k+ stars).
* **Usefulness to Jev:** Low to Moderate. Designed predominantly for cloud-based conversational loops and web apps; assumes ample memory and Python package flexibility.
* **Dependency Cost:** **Very High.** Pulls hundreds of transitive packages (Pydantic v2, Docker SDK, FastAPI, large typing extensions, async networking stacks). 
* **Hardware Impact on 2C/8GB RAM:** High memory footprint (~350MB+ idle memory per runner); introduces thread pool overhead that would cause severe CPU contention on an Intel i3-5005U.
* **Security Concerns:** AutoGen's default pattern allows code execution agents that run generated code in local subshells without strict foreground window or blocked command policies.
* **Verdict:** **REJECT for Runtime / STUDY for Patterns.** Jev borrows its turn-taking and conversational handoff concepts while maintaining zero-dependency native implementation.

---

### 4. CrewAI
* **Project / URL:** CrewAI ([https://github.com/crewAIInc/crewAI](https://github.com/crewAIInc/crewAI))
* **License:** MIT
* **Purpose:** Role-based autonomous agent orchestration focusing on structured crews, processes (sequential, hierarchical), and task delegation.
* **Maturity:** High.
* **Usefulness to Jev:** Moderate. CrewAI’s concept of explicit agent roles, goals, capabilities, and hierarchical delegation is conceptually identical to Jev’s `AgentRegistry` and `TaskPlanner`.
* **Dependency Cost:** **Excessive.** Deeply tied to LangChain ecosystem; installs dozens of external LLM integrations, vector stores, telemetry packages, and embedding engines.
* **Hardware Impact on 2C/8GB RAM:** Unacceptable for local voice assistant packaging. Exceeds binary footprint limits and would degrade faster-whisper and Kokoro TTS CPU availability.
* **Security Concerns:** Automatic delegation loops can trigger recursive LLM calls and rapid budget burn without hardware-level safety interrupts.
* **Verdict:** **REJECT for Runtime / STUDY for Role Architecture.** Jev implements lean role metadata in `AgentMetadata` and DAG-based decomposition in `TaskPlanner` natively.

---

### 5. LangGraph (LangChain)
* **Project / URL:** LangGraph ([https://github.com/langchain-ai/langgraph](https://github.com/langchain-ai/langgraph))
* **License:** MIT
* **Purpose:** Cyclic and state-graph agent orchestration framework modeling multi-agent workflows as stateful graphs.
* **Maturity:** High / Production.
* **Usefulness to Jev:** Moderate. LangGraph’s persistence model (checkpointers, state machines, interrupt-driven human-in-the-loop approvals) aligns closely with Jev’s SQLite task engine and approval state requirements.
* **Dependency Cost:** High. Requires LangChain Core, Pydantic, and multiple graph state utilities.
* **Hardware Impact on 2C/8GB RAM:** Moderate to High overhead.
* **Security Concerns:** State graphs can conceal complex execution loops if state validation is not strictly deterministic.
* **Verdict:** **STUDY for State Machine Patterns / REJECT Runtime.** Jev’s `TaskGraph` and `TaskStorage` provide direct SQLite persistence and DAG topological sorting with zero third-party dependencies.

---

### 6. Semantic Kernel (Microsoft)
* **Project / URL:** Semantic Kernel ([https://github.com/microsoft/semantic-kernel](https://github.com/microsoft/semantic-kernel))
* **License:** MIT
* **Purpose:** Enterprise orchestrator integrating LLMs, plugins, native code, and agent personas.
* **Maturity:** High.
* **Usefulness to Jev:** Low. Primarily targeted at enterprise C#/.NET ecosystems and enterprise Azure environments. The Python SDK lags C# and is overly verbose.
* **Dependency Cost:** High.
* **Security Concerns:** Heavy telemetry and enterprise credential coupling.
* **Verdict:** **REJECT.**

---

### 7. OpenHands (formerly OpenDevin)
* **Project / URL:** OpenHands ([https://github.com/All-Hands-AI/OpenHands](https://github.com/All-Hands-AI/OpenHands))
* **License:** MIT
* **Purpose:** Autonomous software engineering agent capable of browsing, editing files, running bash commands, and building projects.
* **Maturity:** Medium to High.
* **Usefulness to Jev:** High as an external provider reference. Jev can orchestrate coding agents like Antigravity, OpenHands, or Codex using the universal `AgentAdapter` interface.
* **Dependency Cost:** Very High (Docker requirement, web sandbox, heavy full-stack infrastructure).
* **Hardware Impact on 2C/8GB RAM:** Cannot run locally inside Hey Jev's process. Must be treated strictly as an external remote provider or subprocess service.
* **Security Concerns:** Requires full sandbox isolation to prevent destructive filesystem modifications.
* **Verdict:** **STUDY as Provider Target.** Jev’s `AgentAdapter` will support connecting to OpenHands in future phases via HTTP/IPC.

---

### 8. PydanticAI
* **Project / URL:** PydanticAI ([https://github.com/pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai))
* **License:** MIT
* **Purpose:** Fast, type-safe Python agent framework with strict model output validation and dependency injection.
* **Maturity:** Medium (Rapidly growing).
* **Usefulness to Jev:** High for structured schemas. PydanticAI validates that structured outputs strictly conform to defined schemas.
* **Dependency Cost:** Low to Moderate (principally Pydantic v2).
* **Hardware Impact on 2C/8GB RAM:** Minimal. Compiled Rust core makes validation fast.
* **Security Concerns:** Low. Focuses purely on schema serialization and typing.
* **Verdict:** **STUDY for Phase 12+ Typed Agent Schemas.** In Phase 11, standard library dataclasses provide sufficient type guarantees without adding dependencies.

---

### 9. Smolagents (HuggingFace)
* **Project / URL:** Smolagents ([https://github.com/huggingface/smolagents](https://github.com/huggingface/smolagents))
* **License:** Apache-2.0
* **Purpose:** Extremely lightweight, minimal-code agent library executing actions as raw Python code snippets inside a secure AST sandbox.
* **Maturity:** Medium.
* **Usefulness to Jev:** Moderate. Explores the concept of code-as-action instead of JSON tool calling.
* **Dependency Cost:** Very Low (<1000 lines of core logic).
* **Hardware Impact on 2C/8GB RAM:** Negligible.
* **Security Concerns:** Sandboxed code execution in Python can be prone to jailbreaks if AST inspection has bypasses.
* **Verdict:** **STUDY for Lightweight Sandbox Execution.**

---

## Architectural Decision Matrix

| Technology | Standard / Framework | License | Memory Impact | CPU Overhead | Hey Jev Role | Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **MCP** | Standard (Anthropic) | MIT | Negligible | Low | Tool & Resource Protocol | **ADOPT** |
| **A2A** | Standard (Google) | Apache-2.0 | Zero (Schema) | Zero | Agent-to-Agent Handoff Protocol | **ADOPT** |
| **AG2 / AutoGen** | Framework (Microsoft) | Apache-2.0 | Heavy (>350MB) | High | Conversational Multi-Agent | **REJECT (Study)** |
| **CrewAI** | Framework | MIT | Excessive | High | Role-Based Crews | **REJECT (Study)** |
| **LangGraph** | Framework (LangChain) | MIT | Moderate | Medium | State Machine Graph | **REJECT (Study)** |
| **Semantic Kernel** | Framework (Microsoft) | MIT | Heavy | Medium | Enterprise Copilot | **REJECT** |
| **OpenHands** | Coding Agent Service | MIT | Heavy (Docker) | High | External Coding Provider | **STUDY (Provider)** |
| **PydanticAI** | Framework | MIT | Minimal | Low | Type-safe Model IO | **STUDY** |
| **Smolagents** | Framework (HF) | Apache-2.0 | Low | Low | Code Agents | **STUDY** |

---

## Strategic Guidance for Hey Jev

1. **Zero External Dependency Harness:**  
   Phase 11 implements the universal task engine, state machine, SQLite storage, DAG planner, event bus, and verifier using **100% Python standard library (`sqlite3`, `threading`, `dataclasses`, `enum`, `json`)**. This preserves the PyInstaller binary size and guarantees zero memory bloat.

2. **Provider Pluggability:**  
   Every external system (Antigravity, Claude, Codex, Local Ollama, Tier 3 PC Control) plugs into `AgentAdapter`. The orchestrator never imports provider SDKs directly into core logic.

3. **Concurrency Ceiling:**  
   Strict hardware constraint (`max_local_agents = 1`) enforced by `ConcurrencyController` ensures that background agent operations never starve the real-time voice pipeline (STT, VAD, TTS).
