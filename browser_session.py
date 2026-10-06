"""Browser Session and Chrome DevTools Protocol (CDP) Backend for Hey Jev.

Provides deterministic, lightweight browser automation over native Chrome/Edge CDP:
- Multi-tab management (open, switch, close, list)
- Semantic DOM inspection (title, URL, text, accessibility tree, forms)
- Semantic element interaction (click, type, fill, submit)
- Sandboxed downloads & selective screenshot capture
- Isolated profile management protecting the user's primary browser profile
- Observe -> Decide -> Act -> Observe Again -> Verify loop
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import websocket
except ImportError:
    websocket = None


@dataclass
class BrowserTab:
    """Represents an individual browser tab/target."""
    tab_id: str
    url: str
    title: str
    ws_url: str
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tab_id": self.tab_id,
            "url": self.url,
            "title": self.title,
            "created_at": self.created_at,
        }


class BrowserBackend(ABC):
    """Abstract interface for browser automation backends."""

    @abstractmethod
    def launch(self, user_data_dir: str, headless: bool = True) -> bool:
        pass

    @abstractmethod
    def close(self) -> None:
        pass

    @abstractmethod
    def list_tabs(self) -> List[BrowserTab]:
        pass

    @abstractmethod
    def open_tab(self, url: str = "about:blank") -> Optional[BrowserTab]:
        pass

    @abstractmethod
    def switch_tab(self, tab_id: str) -> bool:
        pass

    @abstractmethod
    def close_tab(self, tab_id: str) -> bool:
        pass

    @abstractmethod
    def navigate(self, url: str, timeout: float = 10.0) -> bool:
        pass

    @abstractmethod
    def get_page_info(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_page_text(self) -> str:
        pass

    @abstractmethod
    def find_element(self, locator: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def click(self, locator: Dict[str, Any]) -> bool:
        pass

    @abstractmethod
    def type_text(self, locator: Dict[str, Any], text: str) -> bool:
        pass

    @abstractmethod
    def fill_form(self, form_data: Dict[str, str]) -> bool:
        pass

    @abstractmethod
    def submit_form(self, submit_locator: Optional[Dict[str, Any]] = None) -> bool:
        pass

    @abstractmethod
    def take_screenshot(self, output_path: str) -> bool:
        pass


class CDPBrowserBackend(BrowserBackend):
    """Native Chrome / Microsoft Edge automation via Chrome DevTools Protocol."""

    CHROME_CANDIDATES = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]

    def __init__(self, port: int = 9555, executable_path: Optional[str] = None) -> None:
        self.port = port
        self.executable_path = executable_path or self._find_browser_executable()
        self.process: Optional[subprocess.Popen] = None
        self.user_data_dir: Optional[str] = None
        self.active_tab_id: Optional[str] = None
        self._msg_id: int = 1

    def _find_browser_executable(self) -> str:
        for p in self.CHROME_CANDIDATES:
            if os.path.exists(p):
                return p
        return "chrome.exe"

    def launch(self, user_data_dir: str, headless: bool = True) -> bool:
        """Launch browser with remote debugging port and isolated user data directory."""
        self.user_data_dir = os.path.abspath(user_data_dir)
        os.makedirs(self.user_data_dir, exist_ok=True)

        cmd = [
            self.executable_path,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.user_data_dir}",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-translate",
            "about:blank",
        ]
        if headless:
            cmd.insert(3, "--headless=new")

        try:
            self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Poll for CDP readiness
            deadline = time.time() + 8.0
            while time.time() < deadline:
                try:
                    req = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/version", timeout=1.0)
                    if req.status == 200:
                        tabs = self.list_tabs()
                        if tabs:
                            self.active_tab_id = tabs[0].tab_id
                        return True
                except Exception:
                    time.sleep(0.2)
            return False
        except Exception:
            return False

    def close(self) -> None:
        """Terminate the browser process cleanly."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None

    def list_tabs(self) -> List[BrowserTab]:
        """Query open page targets from CDP /json/list."""
        try:
            req = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=2.0)
            data = json.loads(req.read().decode())
            tabs: List[BrowserTab] = []
            for item in data:
                if item.get("type") == "page":
                    tabs.append(BrowserTab(
                        tab_id=item.get("id", ""),
                        url=item.get("url", ""),
                        title=item.get("title", ""),
                        ws_url=item.get("webSocketDebuggerUrl", ""),
                    ))
            return tabs
        except Exception:
            return []

    def open_tab(self, url: str = "about:blank") -> Optional[BrowserTab]:
        """Open a new tab via CDP /json/new."""
        try:
            encoded_url = urllib.request.quote(url, safe=":/?#[]@!$&'()*+,;=")
            req = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/new?{encoded_url}", timeout=3.0)
            data = json.loads(req.read().decode())
            tab = BrowserTab(
                tab_id=data.get("id", ""),
                url=data.get("url", ""),
                title=data.get("title", ""),
                ws_url=data.get("webSocketDebuggerUrl", ""),
            )
            self.active_tab_id = tab.tab_id
            return tab
        except Exception:
            return None

    def switch_tab(self, tab_id: str) -> bool:
        """Activate a tab via CDP /json/activate."""
        try:
            req = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/activate/{tab_id}", timeout=2.0)
            self.active_tab_id = tab_id
            return req.status == 200
        except Exception:
            return False

    def close_tab(self, tab_id: str) -> bool:
        """Close a tab via CDP /json/close."""
        try:
            req = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/close/{tab_id}", timeout=2.0)
            if self.active_tab_id == tab_id:
                tabs = self.list_tabs()
                self.active_tab_id = tabs[0].tab_id if tabs else None
            return req.status == 200
        except Exception:
            return False

    def _get_active_tab(self) -> Optional[BrowserTab]:
        tabs = self.list_tabs()
        if not tabs:
            return None
        if self.active_tab_id:
            for t in tabs:
                if t.tab_id == self.active_tab_id:
                    return t
        self.active_tab_id = tabs[0].tab_id
        return tabs[0]

    def _send_cdp(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        """Send a command over WebSocket to the active tab."""
        tab = self._get_active_tab()
        if not tab or not tab.ws_url or websocket is None:
            return {"error": "Active tab or websocket unavailable"}

        self._msg_id += 1
        msg = {
            "id": self._msg_id,
            "method": method,
            "params": params or {},
        }

        try:
            ws = websocket.create_connection(tab.ws_url, timeout=timeout)
            ws.send(json.dumps(msg))
            resp = json.loads(ws.recv())
            ws.close()
            return resp
        except Exception as e:
            return {"error": str(e)}

    def navigate(self, url: str, timeout: float = 10.0) -> bool:
        """Navigate active tab to target URL."""
        res = self._send_cdp("Page.navigate", {"url": url}, timeout=timeout)
        time.sleep(0.5)
        return "error" not in res

    def get_page_info(self) -> Dict[str, Any]:
        """Retrieve current title, URL, and document ready state."""
        script = """
        (() => {
            return {
                title: document.title,
                url: window.location.href,
                readyState: document.readyState
            };
        })()
        """
        res = self._send_cdp("Runtime.evaluate", {"expression": script, "returnByValue": True})
        val = res.get("result", {}).get("result", {}).get("value")
        if isinstance(val, dict):
            return val
        tab = self._get_active_tab()
        return {"title": tab.title if tab else "", "url": tab.url if tab else ""}

    def get_page_text(self) -> str:
        """Extract inner text of document body."""
        script = "document.body ? document.body.innerText : ''"
        res = self._send_cdp("Runtime.evaluate", {"expression": script, "returnByValue": True})
        val = res.get("result", {}).get("result", {}).get("value")
        return str(val or "")

    def find_element(self, locator: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Find semantic DOM element by role, text, label, name, or placeholder."""
        loc_json = json.dumps(locator)
        script = f"""
        (() => {{
            const loc = {loc_json};
            const role = (loc.role || '').toLowerCase();
            const text = (loc.text || '').toLowerCase();
            const name = (loc.name || '').toLowerCase();
            const placeholder = (loc.placeholder || '').toLowerCase();
            const tag = (loc.tag || '*').toLowerCase();

            const elements = Array.from(document.querySelectorAll(tag));
            for (const el of elements) {{
                const elRole = (el.getAttribute('role') || el.tagName).toLowerCase();
                const elText = (el.innerText || el.textContent || '').trim().toLowerCase();
                const elName = (el.getAttribute('name') || el.getAttribute('id') || '').toLowerCase();
                const elPlaceholder = (el.getAttribute('placeholder') || '').toLowerCase();

                let match = true;
                if (role && elRole !== role) match = false;
                if (text && !elText.includes(text)) match = false;
                if (name && elName !== name) match = false;
                if (placeholder && elPlaceholder !== placeholder) match = false;

                if (match && (role || text || name || placeholder)) {{
                    const rect = el.getBoundingClientRect();
                    return {{
                        found: true,
                        tagName: el.tagName,
                        role: elRole,
                        text: el.innerText ? el.innerText.substring(0, 100) : '',
                        rect: {{x: rect.x, y: rect.y, width: rect.width, height: rect.height}}
                    }};
                }}
            }}
            return null;
        }})()
        """
        res = self._send_cdp("Runtime.evaluate", {"expression": script, "returnByValue": True})
        return res.get("result", {}).get("result", {}).get("value")

    def click(self, locator: Dict[str, Any]) -> bool:
        """Click semantic DOM element."""
        loc_json = json.dumps(locator)
        script = f"""
        (() => {{
            const loc = {loc_json};
            const role = (loc.role || '').toLowerCase();
            const text = (loc.text || '').toLowerCase();
            const name = (loc.name || '').toLowerCase();
            const tag = (loc.tag || '*').toLowerCase();

            const elements = Array.from(document.querySelectorAll(tag));
            for (const el of elements) {{
                const elRole = (el.getAttribute('role') || el.tagName).toLowerCase();
                const elText = (el.innerText || el.textContent || '').trim().toLowerCase();
                const elName = (el.getAttribute('name') || el.getAttribute('id') || '').toLowerCase();

                let match = true;
                if (role && elRole !== role) match = false;
                if (text && !elText.includes(text)) match = false;
                if (name && elName !== name) match = false;

                if (match && (role || text || name)) {{
                    el.click();
                    return true;
                }}
            }}
            return false;
        }})()
        """
        res = self._send_cdp("Runtime.evaluate", {"expression": script, "returnByValue": True})
        val = res.get("result", {}).get("result", {}).get("value")
        return bool(val)

    def type_text(self, locator: Dict[str, Any], text: str) -> bool:
        """Type text into a designated input element."""
        loc_json = json.dumps(locator)
        val_json = json.dumps(text)
        script = f"""
        (() => {{
            const loc = {loc_json};
            const val = {val_json};
            const name = (loc.name || '').toLowerCase();
            const placeholder = (loc.placeholder || '').toLowerCase();

            const inputs = Array.from(document.querySelectorAll('input, textarea'));
            for (const el of inputs) {{
                const elName = (el.getAttribute('name') || el.getAttribute('id') || '').toLowerCase();
                const elPl = (el.getAttribute('placeholder') || '').toLowerCase();

                let match = true;
                if (name && elName !== name) match = false;
                if (placeholder && elPl !== placeholder) match = false;

                if (match && (name || placeholder || inputs.length === 1)) {{
                    el.value = val;
                    el.dispatchEvent(new Event('input', {{bubbles: true}}));
                    el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return true;
                }}
            }}
            return false;
        }})()
        """
        res = self._send_cdp("Runtime.evaluate", {"expression": script, "returnByValue": True})
        val = res.get("result", {}).get("result", {}).get("value")
        return bool(val)

    def fill_form(self, form_data: Dict[str, str]) -> bool:
        """Fill multiple form inputs by field name/id."""
        data_json = json.dumps(form_data)
        script = f"""
        (() => {{
            const data = {data_json};
            let filledCount = 0;
            for (const [k, v] of Object.entries(data)) {{
                const el = document.querySelector(`[name="${{k}}"], #${{k}}`);
                if (el) {{
                    el.value = v;
                    el.dispatchEvent(new Event('input', {{bubbles: true}}));
                    el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    filledCount++;
                }}
            }}
            return filledCount > 0;
        }})()
        """
        res = self._send_cdp("Runtime.evaluate", {"expression": script, "returnByValue": True})
        val = res.get("result", {}).get("result", {}).get("value")
        return bool(val)

    def submit_form(self, submit_locator: Optional[Dict[str, Any]] = None) -> bool:
        """Submit the active form or click designated submit button."""
        if submit_locator:
            return self.click(submit_locator)
        script = """
        (() => {
            const form = document.querySelector('form');
            if (form) {
                form.submit();
                return true;
            }
            const btn = document.querySelector('button[type="submit"], input[type="submit"]');
            if (btn) {
                btn.click();
                return true;
            }
            return false;
        })()
        """
        res = self._send_cdp("Runtime.evaluate", {"expression": script, "returnByValue": True})
        val = res.get("result", {}).get("result", {}).get("value")
        return bool(val)

    def take_screenshot(self, output_path: str) -> bool:
        """Capture page screenshot and save PNG artifact to disk."""
        res = self._send_cdp("Page.captureScreenshot", {"format": "png"})
        b64_data = res.get("result", {}).get("data")
        if not b64_data:
            return False
        try:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(base64.b64decode(b64_data))
            return os.path.exists(output_path) and os.path.getsize(output_path) > 0
        except Exception:
            return False


class BrowserSessionManager:
    """Manages browser lifecycle, isolated profiles, tab state, and observe-act-verify loop."""

    def __init__(self, backend: Optional[BrowserBackend] = None, profile_dir: Optional[str] = None) -> None:
        self.profile_dir = profile_dir or os.path.join(os.environ.get("TEMP", r"C:\TEMP"), "HeyJev", "browser_session")
        self.backend = backend or CDPBrowserBackend()
        self.is_running = False

    def start(self, headless: bool = True) -> bool:
        if not self.is_running:
            ok = self.backend.launch(self.profile_dir, headless=headless)
            self.is_running = ok
            return ok
        return True

    def stop(self) -> None:
        if self.is_running:
            self.backend.close()
            self.is_running = False

    def execute_observe_act_verify(
        self,
        action_name: str,
        action_fn: Any,
        verify_fn: Any,
    ) -> Dict[str, Any]:
        """Execute an action strictly following Observe -> Act -> Observe Again -> Verify."""
        before_state = self.backend.get_page_info()
        action_ok = action_fn()
        time.sleep(0.3)
        after_state = self.backend.get_page_info()
        verified, summary = verify_fn(before_state, after_state, action_ok)

        return {
            "action": action_name,
            "action_ok": action_ok,
            "before": before_state,
            "after": after_state,
            "verified": verified,
            "summary": summary,
        }


class MockBrowserBackend(BrowserBackend):
    """Deterministic, in-memory browser backend for testing and headless verification."""

    def __init__(self) -> None:
        self.is_launched = False
        self.user_data_dir: Optional[str] = None
        self._tabs: Dict[str, BrowserTab] = {}
        self.active_tab_id: Optional[str] = None
        self._page_texts: Dict[str, str] = {}
        self._form_state: Dict[str, str] = {}
        self._submitted: bool = False
        self._counter: int = 1

    def launch(self, user_data_dir: str, headless: bool = True) -> bool:
        self.user_data_dir = user_data_dir
        self.is_launched = True
        first_tab = BrowserTab(
            tab_id="tab-1",
            url="about:blank",
            title="Blank",
            ws_url="ws://127.0.0.1:9555/devtools/page/tab-1",
        )
        self._tabs[first_tab.tab_id] = first_tab
        self.active_tab_id = first_tab.tab_id
        self._page_texts[first_tab.tab_id] = "Blank Page"
        return True

    def close(self) -> None:
        self._tabs.clear()
        self.active_tab_id = None
        self.is_launched = False

    def list_tabs(self) -> List[BrowserTab]:
        return list(self._tabs.values())

    def open_tab(self, url: str = "about:blank") -> Optional[BrowserTab]:
        self._counter += 1
        t_id = f"tab-{self._counter}"
        title = url.split("/")[-1] or "New Tab"
        tab = BrowserTab(
            tab_id=t_id,
            url=url,
            title=title,
            ws_url=f"ws://127.0.0.1:9555/devtools/page/{t_id}",
        )
        self._tabs[t_id] = tab
        self.active_tab_id = t_id
        self._page_texts[t_id] = f"Content of {url}"
        return tab

    def switch_tab(self, tab_id: str) -> bool:
        if tab_id in self._tabs:
            self.active_tab_id = tab_id
            return True
        return False

    def close_tab(self, tab_id: str) -> bool:
        if tab_id in self._tabs:
            del self._tabs[tab_id]
            if self.active_tab_id == tab_id:
                self.active_tab_id = list(self._tabs.keys())[0] if self._tabs else None
            return True
        return False

    def navigate(self, url: str, timeout: float = 10.0) -> bool:
        if not self.active_tab_id or self.active_tab_id not in self._tabs:
            return False
        tab = self._tabs[self.active_tab_id]
        tab.url = url
        # Derive title
        clean_name = url.replace("https://", "").replace("http://", "").split("/")[0]
        tab.title = f"Page: {clean_name}"
        self._page_texts[tab.tab_id] = f"Simulated content from {url} with documentation, articles, and reviews."
        return True

    def get_page_info(self) -> Dict[str, Any]:
        if not self.active_tab_id or self.active_tab_id not in self._tabs:
            return {"title": "", "url": "", "readyState": "uninitialized"}
        tab = self._tabs[self.active_tab_id]
        return {
            "title": tab.title,
            "url": tab.url,
            "readyState": "complete",
        }

    def get_page_text(self) -> str:
        if not self.active_tab_id:
            return ""
        return self._page_texts.get(self.active_tab_id, "")

    def set_mock_page_text(self, text: str) -> None:
        if self.active_tab_id:
            self._page_texts[self.active_tab_id] = text

    def find_element(self, locator: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # Check if locator matches
        text = locator.get("text") or locator.get("name") or locator.get("role") or "element"
        return {
            "found": True,
            "tagName": locator.get("tag", "BUTTON").upper(),
            "role": locator.get("role", "button"),
            "text": str(text),
            "rect": {"x": 100, "y": 200, "width": 80, "height": 30},
        }

    def click(self, locator: Dict[str, Any]) -> bool:
        # Simulate click
        return bool(locator)

    def type_text(self, locator: Dict[str, Any], text: str) -> bool:
        field_name = locator.get("name") or locator.get("placeholder") or "input"
        self._form_state[field_name] = text
        return True

    def fill_form(self, form_data: Dict[str, str]) -> bool:
        self._form_state.update(form_data)
        return True

    def submit_form(self, submit_locator: Optional[Dict[str, Any]] = None) -> bool:
        self._submitted = True
        return True

    def take_screenshot(self, output_path: str) -> bool:
        # Generate valid minimal 1x1 PNG bytes
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00"
            b"\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        try:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(png_bytes)
            return True
        except Exception:
            return False

