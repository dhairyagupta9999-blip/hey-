# Phase 16 Technology Radar: Persistent Memory & Agent-Context Frameworks

## 1. Executive Context & Hardware Constraints

Hey Jev is a voice-first, local-first agent orchestrator running on low-resource desktop hardware:
- **Processor**: Intel Core i3-5005U (2 physical cores / 4 threads @ 2.00 GHz, Broadwell architecture, no AVX2 FMA3 optimizations, no GPU acceleration)
- **RAM**: ~7.93 GB physical memory
- **Operating System**: Windows 10 Pro 64-bit
- **Python**: 3.11.9
- **Key Invariant**: Real-time STT (`faster-whisper`), VAD (`silero-vad`), and TTS (`kokoro` / SAPI fallback) must maintain sub-second responsiveness without memory pressure or thread starvation.

Any memory infrastructure must satisfy:
1. **Zero background compute drain**: No idle embedding daemon, continuous vector clustering, or GPU dependencies.
2. **Deterministic, local-first persistence**: All facts stored locally on disk under user control; zero cloud leakage.
3. **Sub-millisecond retrieval**: Retrieval must execute in $< 5$ ms to avoid bloating agent response latency.
4. **Minimal RAM footprint**: $< 20$ MB overhead for memory structures.

---

## 2. Comparative Evaluation Matrix

| Framework / Architecture | Current Version | License | Windows / Py3.11 | RAM Footprint | CPU Cost (2C/4T) | Local/Offline | Semantic Search | Keyword / Structured | Temporal & Entity | Suitability for Hey Jev | Decision |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **SQLite WAL + Relational / FTS (Native)** | SQLite 3.45+ | Public Domain | Native (C / stdlib) | **< 4 MB** | **Negligible (<0.1 ms)** | **100% Local** | Pluggable provider | **Native, indexed, microsecond** | **Explicit schema supported** | **Ideal**: zero extra dependencies, ACID transactions, atomic writes, zero latency impact. | **ADOPT (Primary Core)** |
| **Mem0 (mem0ai)** | v0.1.30+ | Apache-2.0 | Compatible | ~150 - 350 MB | Moderate to High (vector embeddings) | Partial (requires local Qdrant/Ollama or Cloud) | Native (dense vectors) | Weak structured filtering | Graph / user memory (basic) | **Heavy**: requires vector store + embedding model. Would exhaust CPU/RAM during voice loops. | **STUDY / DEFER** |
| **Letta (formerly MemGPT)** | v0.5.0+ | Apache-2.0 | Compatible | ~400 - 800 MB | High (server process, background LLM calls) | Requires local LLM / server daemon | Native | Basic | Hierarchical / Core vs Recall | **Excessive**: architectural mismatch. Letta is a standalone monolithic server, not an embeddable library. | **REJECT as Core** |
| **LangMem (LangChain)** | v0.0.10+ | MIT | Compatible | ~100 - 250 MB | Moderate | Dependent on LangChain / embeddings | Native | LangChain dependent | Conversation memory threads | **Coupled**: heavily coupled to LangGraph/LangChain abstractions. Jev uses custom orchestrator. | **DEFER** |
| **ChromaDB / FAISS (Vector DBs)** | Chroma 0.5+ | Apache-2.0 | Compatible | ~200 - 450 MB | Moderate to High (ONNX/PyTorch embeddings) | 100% Local | Native | Poor structured relations | No native temporal awareness | **Sub-optimal**: vector distances alone fail on exact project names, paths, and temporal supersession. | **DEFER** |
| **Zep / Graphiti (Knowledge Graphs)** | Graphiti 0.1+ | Apache-2.0 | Compatible | ~300 - 600 MB | High (LLM-based entity extraction) | Requires Neo4j / FalkorDB | Graph + Vector | Graph traversal | Strong entity linking | **Resource heavy**: entity extraction requires continuous LLM inference, unviable on local dual-core CPU. | **DEFER** |

---

## 3. Deep-Dive Evaluations

### 3.1 SQLite WAL with Structured Relational Schema & Inverted Indexing
- **Repository**: Built into Python standard library (`sqlite3`)
- **License**: Public Domain
- **Footprint**: $< 4$ MB RAM, single file database (`%APPDATA%\HeyJev\memory.db`).
- **Performance**:
  - Insert: $< 0.5$ ms
  - Primary Key / Index Lookup: $< 0.05$ ms
  - Structured queries (by project, type, status, recency): $< 0.2$ ms
- **Strengths**: Atomic commits, zero external processes, WAL journal mode prevents concurrency locks, native temporal queries (`valid_from <= t <= valid_until`), zero privacy exposure.
- **Verdict**: **ADOPT** as the foundation of Hey Jev Phase 16 Persistent Memory.

### 3.2 Mem0 (mem0ai)
- **Repository**: `github.com/mem0ai/mem0`
- **License**: Apache-2.0
- **Analysis**: Mem0 provides multi-tier memory (user, session, assistant) with graph extensions. However, its default deployment requires vector databases (Qdrant, Chroma) and vector embedding models (`sentence-transformers` or OpenAI API). On a 2-core i3 machine without GPU, local sentence embedding takes 100–300 ms per text snippet, competing directly with `faster-whisper` STT audio processing.
- **Verdict**: **STUDY / DEFER**. Isolate memory access behind a `MemoryStore` interface so a Mem0 adapter can be evaluated in future phases if dedicated embedding acceleration is introduced.

### 3.3 Letta (formerly MemGPT)
- **Repository**: `github.com/letta-ai/letta`
- **License**: Apache-2.0
- **Analysis**: Letta manages stateful agents with virtual context windows (paging memory between working context and archival storage via recursive tool calls). It operates as a heavy standalone server process with PostgreSQL / Chroma backends. Integrating Letta would invert Jev's architecture, making Jev a client of a heavy external server.
- **Verdict**: **REJECT as Core**. Jev already has its own lightweight `JevOrchestrator`, `TaskStorage`, and multi-agent coordination.

### 3.4 Hybrid Retrieval Strategy for Hey Jev
Pure vector retrieval has severe failure modes for operating system agents:
1. It confuses exact file paths and project names with semantically similar phrases.
2. It lacks temporal ordering (cannot distinguish "Day 1 failing test" from "Day 2 passing test").
3. It cannot enforce least-privilege security boundaries deterministically.

Phase 16 adopts a **Deterministic Multi-Factor Hybrid Ranker**:
$$\text{Score} = (\text{Keyword/Token Overlap} \times 0.40) + (\text{Importance} \times 0.25) + (\text{Recency} \times 0.20) + (\text{Entity Relevance} \times 0.15)$$
This delivers explainable, sub-millisecond retrieval without requiring heavy neural embedding runtimes.
