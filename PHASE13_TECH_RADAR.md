# Hey Jev — Phase 13 Technology Radar: Browser Automation & Web Research Engines

**Target Platform:** Windows 10 Pro x64 / Windows 11  
**Hardware Profile:** Intel Core i3-5005U (2 physical cores / 4 threads @ 2.00 GHz), ~7.93 GB RAM, Intel HD Graphics 5500  
**Phase Objective:** Autonomous Browser Operator & Deep Research Engine  
**Date:** October 2026  

---

## 1. Executive Summary

Phase 13 equips Hey Jev with the ability to operate the web and conduct deep research. Operating a modern web browser on an Intel Core i3-5005U with 8 GB RAM requires extreme discipline regarding memory footprint, CPU utilization, and dependency breadth.

Rather than adopting heavy, vision-centric autonomous browser frameworks (like `browser-use`) that execute continuous screenshot inference loops, Hey Jev adopts a **Deterministic-First, Native-CDP Architecture**:
1. **Chrome DevTools Protocol (CDP):** Directly drives the user's pre-installed Google Chrome (`chrome.exe`) or Microsoft Edge (`msedge.exe`) over WebSocket/JSON-RPC without downloading redundant 350 MB browser binaries.
2. **Semantic DOM & Accessibility Tree Inspection:** Direct interaction with elements via roles, labels, names, and text rather than visual coordinate clicking.
3. **Lightweight HTTP/Search Layer:** Uses Python standard library and `requests`/`urllib3` for high-speed snippet retrieval, content extraction, and source verification.
4. **Selective Screenshots:** Captured on-demand solely for verification and canvas/visual debugging.

Below is the technology evaluation of browser automation libraries and agent frameworks.

---

## 2. In-Depth Evaluation of Candidate Technologies

### 1. Native Chrome / Edge DevTools Protocol (CDP via WebSocket)
* **Technology:** Chrome DevTools Protocol (CDP) / Microsoft Edge DevTools Protocol
* **License:** Built-in to Chromium (BSD-3-Clause / Proprietary Chrome & Edge)
* **Maintenance / Activity:** Industry standard maintained by Google and Microsoft.
* **Windows 10 Compatibility:** 100% native. Both Google Chrome and Microsoft Edge are already installed on this PC (`C:\Program Files\Google\Chrome\Application\chrome.exe` and `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`).
* **Python Compatibility:** Python 3.8 – 3.12+ (communicates over lightweight `websocket-client` or HTTP).
* **Dependency Footprint:** **Extremely Low (<100 kB).** Zero browser binary downloads required; reuses the OS browser.
* **CPU / RAM Requirements:** Very low. Headless Chrome consumes ~60 MB RAM; commands execute in 5–25 ms.
* **Capabilities:**
  * Direct tab creation, switching, and closing (`Target.*` domain)
  * Page navigation, reloading, history navigation (`Page.*` domain)
  * JavaScript evaluation and semantic DOM extraction (`Runtime.*`, `DOM.*` domains)
  * High-resolution screenshot capture (`Page.captureScreenshot`)
  * Download tracking, viewport emulation, and isolated profile directories
* **Security & Isolation:** Runs in an isolated user-data directory (`%TEMP%\HeyJev\phase13-test\profile`), completely protecting the user's personal cookies, history, and logins.
* **Decision:** **ADOPT (Primary Local Browser Backend).**

---

### 2. Microsoft Playwright (`playwright-python`)
* **Repository / URL:** [https://github.com/microsoft/playwright-python](https://github.com/microsoft/playwright-python)
* **License:** Apache-2.0
* **Maintenance / Activity:** Very active; tier-1 automation tool for end-to-end testing and web scraping.
* **Windows 10 Compatibility:** Compatible with Windows 10 (version 1809+).
* **Python Compatibility:** Python 3.8+.
* **Dependency Footprint:** **Heavy.** Requires installing package + downloading ~350 MB custom Chromium, Firefox, and WebKit binaries via `playwright install`.
* **CPU / RAM Requirements:** Moderate to high (~150 MB RAM per browser context).
* **Capabilities:** Full semantic locators (`getByRole`, `getByText`), network interception, tracing, multi-tab contexts.
* **Suitability for Hey Jev:** High architectural value, but downloading extra custom browser binaries on a machine that already possesses Chrome and Edge adds unnecessary disk and packaging bloat.
* **Decision:** **STUDY & DESIGN AS OPTIONAL BACKEND PLUG-IN.** (Native CDP is faster, lighter, and requires zero binary downloads).

---

### 3. `browser-use` (Agentic Web Framework)
* **Repository / URL:** [https://github.com/browser-use/browser-use](https://github.com/browser-use/browser-use)
* **License:** MIT
* **Maintenance / Activity:** Extremely active community; popular autonomous web agent.
* **Windows 10 Compatibility:** Compatible with Python 3.11 on Windows 10.
* **Dependency Footprint:** **Excessive (>500 MB).** Requires Playwright, LangChain, Pydantic, Pillow, and multiple external LLM provider packages.
* **CPU / RAM Requirements:** **Unacceptable for local dual-core CPU.** Captures full-page screenshots every step, encodes them to base64, runs multimodal vision models, and runs continuous agent loops. Would freeze faster-whisper and Kokoro voice threads.
* **Security Concerns:** Autonomous agents navigating the open web can be vulnerable to prompt injection embedded in webpage text.
* **Decision:** **REJECT for Runtime / STUDY for Agentic Planning Patterns.** Jev implements its own deterministic Observe → Decide → Act → Verify loop.

---

### 4. Selenium WebDriver
* **Repository / URL:** [https://github.com/SeleniumHQ/selenium](https://github.com/SeleniumHQ/selenium)
* **License:** Apache-2.0
* **Maintenance / Activity:** Legacy industry standard.
* **Windows 10 Compatibility:** High.
* **Dependency Footprint:** Moderate; requires external `chromedriver.exe` or `msedgedriver.exe` matching exact browser versions.
* **Drawbacks for Hey Jev:** Driver mismatch crashes are frequent whenever Chrome auto-updates in the background. Slower than direct CDP.
* **Decision:** **REJECT.**

---

### 5. `pychrome` / Lightweight CDP Wrappers
* **Repository / URL:** [https://github.com/fate0/pychrome](https://github.com/fate0/pychrome)
* **License:** BSD-3-Clause
* **Maintenance / Activity:** Low (infrequent updates), but clean protocol reference.
* **Decision:** **STUDY for CDP framing; implement clean native CDP client in Jev.**

---

## 3. Technology Comparison Matrix

| Technology | Category | License | Binary Download Size | Memory Footprint | Windows 10 Native? | Voice Pipeline Safe (2C/8GB)? | Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Native Chrome/Edge CDP** | OS Browser Protocol | BSD/Chrome | **0 MB (Pre-installed)** | ~60 MB | **Yes (Built-in)** | **Yes (Fast & Lean)** | **ADOPT (Primary)** |
| **Playwright Python** | Test Automation | Apache-2.0 | ~350 MB | ~150 MB | Yes | Moderate | **STUDY (Plug-in)** |
| **Browser Use** | Multimodal Agent | MIT | >500 MB + Vision | >400 MB | Yes | No (CPU Throttling) | **REJECT (Study)** |
| **Selenium** | WebDriver | Apache-2.0 | Requires Driver | ~100 MB | Yes | Moderate | **REJECT** |
| **pychrome** | CDP Wrapper | BSD-3 | Minimal | ~30 MB | Yes | Yes | **STUDY** |

---

## 4. Architectural Selection for Hey Jev

1. **`CDPBrowserBackend` (Primary):**
   * Uses local `chrome.exe` or `msedge.exe` with `--remote-debugging-port`.
   * Operates over standard JSON-RPC WebSocket using the lightweight `websocket-client` library.
   * Full multi-tab management, semantic element interaction, navigation verification, form filling, and selective screenshots.
2. **`ResearchEngine`:**
   * Structured search provider abstraction (`SearchProvider` ABC with DuckDuckGo / Public Search / Mock implementations).
   * URL deduplication and normalization.
   * Source collection and quality scoring.
   * Fact vs. Inference vs. Uncertainty representation.
   * Contradiction detection between heterogeneous sources.
   * Integration with Phase 11 DAG task graphs for parallel research subtasks.
3. **Safety & Profile Isolation:**
   * Separate browser automation profile in `%TEMP%\HeyJev\phase13-test\profile`.
   * Anti-prompt-injection boundary: all web content is wrapped in `<DATA>` tags and treated as untrusted input.
   * High-risk external actions (purchases, account changes, emails) require human approval.
