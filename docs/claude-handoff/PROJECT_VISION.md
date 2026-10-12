# Hey Jev: Project Vision & Product Direction

## 1. Executive Summary

**Hey Jev** is a general-purpose, voice-first Windows 10/11 AI desktop assistant that bridges the gap between fast, hands-free voice control and autonomous, goal-driven desktop agent execution.

Unlike cloud-dependent voice assistants (such as Siri, Alexa, or Cortana) that offer limited desktop interaction, and unlike developer CLI agents (such as Claude Code or OpenCode) that lack native voice and real-time desktop UI integration, Hey Jev provides:
- **Sub-second local voice reflexes** for common desktop commands (opening apps, controlling media, system settings, window management, timers, volume).
- **Deep Windows OS automation** using native Win32 APIs, UI Automation (UIA), and Chrome DevTools Protocol (CDP).
- **Persistent, goal-driven autonomy (System 3 Missions)** capable of planning multi-step task DAGs, executing actions with human approval checkpoints, persisting state across restarts, and recovering from failures.
- **Transparent security and safety boundaries** with granular risk classification, an undo stack, action audit logging, and strict data preservation.

---

## 2. Intended User Experience

The user interacts with Hey Jev primarily through spoken voice, backed by a visual desktop GUI:

### Voice Interaction Models
1. **Push-to-Talk (PTT):** The user holds the **Right Alt** key, speaks naturally, and releases the key. A visual waveform indicator and desktop notification provide instant feedback.
2. **Wake-Word Mode:** The user says `"Hey Jev"` followed by a command. A lightweight, local wake-word detector (`openWakeWord` or gated Whisper VAD) detects the trigger and immediately arms the listening window.
3. **Dictation Mode:** Saying `"start transcribing"` enables continuous voice-to-text dictation into the focused active window until the user says `"stop transcribing"`.

### Visual Surface & Feedback
- **Native 7-Tab PySide6 GUI:** A native desktop control center providing:
  - **Status Tab:** Real-time state (Ready, Listening, Thinking, Doing it), last spoken command, and assistant response.
  - **Memory Tab:** Inspect and manage persistent contextual memories.
  - **Projects Tab:** Registered developer codebases and project awareness.
  - **Tasks Tab:** Scheduled background automation jobs and directory watchers.
  - **Swarms Tab:** Multi-agent collaboration logs and agent rosters.
  - **Missions Tab (Mission Center):** Direct launcher and monitor for persistent autonomous missions with DAG visualizer, criteria checklist, and execution controls.
  - **Keys / Settings Tab:** Configuration of API keys and operational parameters.
- **System Tray Icon (`pystray`):** Allows minimizing to tray, quick mode switching, and quiet background operation.
- **Audio Feedback:** Non-intrusive chimes indicate wake recognition, listening start, action completion, and timer alarms. Spoken vocal replies use neural TTS (Fish Audio cloud or Kokoro local).

---

## 3. The Three-Tier / Multi-Brain Architectural Philosophy

Hey Jev divides cognitive and operational complexity into three distinct tiers:

```
+-------------------------------------------------------------------------+
|                              USER VOICE / INPUT                         |
+-------------------------------------------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
|  TIER 1: SYSTEM 1 REFLEX (Sub-second Speculative Battery)               |
|  - Fast local keyword/regex shortcuts                                   |
|  - Remote speculative battery (TypeSafe API / Laya)                     |
|  - Handles: media, volume, timers, standard app launches, core actions  |
+-------------------------------------------------------------------------+
                                     | (if unhandled / ambiguous / open-vocabulary)
                                     v
+-------------------------------------------------------------------------+
|  TIER 2: SYSTEM 2 OPEN-VOCABULARY LOCAL RESOLVER (Deterministic OS)     |
|  - Open-vocabulary App Index (fuzzy matching, aliases, UWP apps)        |
|  - Windows Settings URIs (ms-settings:) and System Folders              |
|  - Window Management (focus, snap, minimize, maximize)                  |
|  - File Finder (Windows Search Index + fallback)                        |
|  - Safety Risk Classifier & Interactive Confirmation Manager             |
+-------------------------------------------------------------------------+
                                     | (if complex multi-step / autonomous goal)
                                     v
+-------------------------------------------------------------------------+
|  TIER 3 / SYSTEM 3: AUTONOMOUS MISSIONS & TOOL-CALLING AGENTS           |
|  - Mission Coordinator & Deterministic DAG Planner                      |
|  - UI Automation (UIA Operator 2.0) & Browser Agent (CDP)              |
|  - Multi-agent collaboration, Swarm Engine, and Provider Gateway        |
|  - Human Approval Checkpoints & Persistent SQLite WAL Mission Store     |
|  - OpenDots Interoperability Gateway (MCP Streamable HTTP)              |
+-------------------------------------------------------------------------+
```

### Tier 1: Sub-Second Reflex
Designed for high-frequency daily operations. Target latency is under 1,000ms end-to-end. Evaluates common intents:
- Media transport (play, pause, next, previous) via WinRT SMTC and `keybd_event`.
- Master and application volume control via `pycaw`.
- System toggles (lock screen, sleep, dark mode).
- Timers and reminders with live countdown and audio alarms.
- Known application launches (`apps.json`).

### Tier 2: Open-Vocabulary Local Resolvers
Executes when Tier 1 is unclear or the target application is outside the hardcoded battery:
- Dynamic discovery of installed Win32, UWP, and packaged applications via Start Menu and `shell:AppsFolder`.
- Phonetic and fuzzy matching with ambiguity clarification ("Did you mean X or Y?").
- System utility execution (Task Manager, Control Panel, Device Manager).
- Window layout control and Snap Assist operations.
- Desktop file search without external cloud calls.

### Tier 3 / System 3: Autonomous Missions & Tool-Calling Agents
Executes when a command requires multi-step reasoning, external research, UI automation, or long-running workflows:
- Goal interpretation and decomposition into structured tasks with defined acceptance criteria.
- Autonomous execution across tool registries (UIA, Browser CDP, Terminal, System).
- Human-in-the-loop approval gates for destructive or external operations.
- Continuous state persistence in SQLite WAL databases to survive application crashes and restarts.
- Optional external access via Model Context Protocol (MCP) clients such as OpenDots.

---

## 4. Product Principles

1. **Honesty Over Optimism:** Never report that an action succeeded unless verified by the system (e.g., verifying a process is running, a window is focused, or a file exists).
2. **Deterministic Precedence:** Local deterministic rules always take precedence over non-deterministic LLM calls for unambiguous commands.
3. **Safety First, Always:** Dangerous operations (process killing, file deletions, system reboots, external API spend) must be gated by safety checks, user confirmation, and undo logging.
4. **Resilience Across Restarts:** The user's active missions, tasks, background jobs, and memories must persist reliably across application restarts.
5. **No Data Destruction:** Hey Jev must never delete user files, overwrite user databases, or destroy local context.
6. **Local-First Independence:** Hey Jev must remain functional offline for core desktop tasks, relying on cloud LLMs only for open-domain chit-chat, high-level planning, or synthesis.

---

## 5. Long-Term Roadmap

- **Latency Optimization:** Reduce voice response latency on Windows to sub-500ms for common commands.
- **Computer Operator 3.0:** High-reliability UI Automation with visual grounding and resilient recovery when UI controls change.
- **Ecosystem Interoperability:** Clean boundary gateways allowing external clients (OpenDots, CopilotKit, Claude Code) to orchestrate Jev's native Windows capabilities safely.
- **Local Neural Intelligence:** Migration to quantized local models (Ollama, llama.cpp, Laya) to eliminate cloud dependencies for planning and decision-making.
