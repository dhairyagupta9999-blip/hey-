"""Tier 2 Open-Vocabulary Local Resolver for Hey Jev.

Resolves requests locally without an LLM when Tier 1 is unclear or the target
is outside the hardcoded Tier 1 battery:
  - Open any application via AppIndex (with fuzzy & phonetic matching)
  - Close any running application via process/window matching
  - Handle 'close everything' with confirmation
  - On-demand app index refresh ('refresh apps')
  - Ambiguity detection and two-choice clarification
  - Windows file finder (Windows Search index + local fallback)
  - System targets: ms-settings URIs, folders, administrative tools, websites
  - Window management: focus, minimize, maximize, restore, snap left/right
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

import app_index
from app_index import get_app_index, refresh_apps
import target_extractor
import system_targets
import file_finder
import window_manager
import actions_win
from tts import say_line
import logger
import safety_engine


def resolve_tier2(text: str) -> Optional[Dict[str, Any]]:
    """Attempt to resolve a spoken command via Tier 2 local resolvers.

    Returns a dict with execution details, or None if Tier 2 cannot handle it.
    """
    t0 = time.perf_counter()

    # A. Window Management Commands (e.g. "snap Chrome to the left", "maximize Notepad")
    win_res = window_manager.resolve_window_command(text)
    if win_res:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        safety_engine.log_action(2, f"window_{win_res['action']}", {"target": win_res["target"]}, safety_engine.SAFE, "success" if win_res["success"] else "failed")
        logger.trace_line(f"  [tier 2: window_manager] {latency_ms}ms  $0.000000  action: {win_res['action']} on {win_res['target']}")
        return {
            "tier": 2,
            "status": "done" if win_res["success"] else "failed",
            "action": f"window_{win_res['action']}",
            "target": win_res["target"],
            "line": win_res["line"],
            "latency_ms": latency_ms
        }

    # B. System Targets (e.g. "open Bluetooth settings", "open Downloads folder", "open youtube", "open https://...")
    sys_res = system_targets.resolve_system_target(text, execute=False)
    if sys_res:
        system_targets.execute_system_target(sys_res)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        safety_engine.log_action(2, f"system_{sys_res['type']}", {"target": sys_res["label"]}, safety_engine.SAFE, "success")
        logger.trace_line(f"  [tier 2: system_target] {latency_ms}ms  $0.000000  type: {sys_res['type']} -> {sys_res['label']}")
        return {
            "tier": 2,
            "status": "done",
            "action": f"system_{sys_res['type']}",
            "target": sys_res["label"],
            "line": sys_res["line"],
            "latency_ms": latency_ms
        }

    # C. Intent and Target Extraction (open / close / focus / refresh)
    action, target = target_extractor.extract_target(text)
    if not action:
        return None

    idx = get_app_index()

    # 1. Refresh apps command
    if action == "refresh_apps":
        count = refresh_apps()
        latency_ms = int((time.perf_counter() - t0) * 1000)
        safety_engine.log_action(2, "refresh_apps", {"count": count}, safety_engine.SAFE, "success")
        line = f"Refreshed app index, found {count} applications."
        logger.trace_line(f"  [tier 2: app_index] {latency_ms}ms  $0.000000  action: refresh_apps")
        return {"tier": 2, "status": "done", "action": "refresh_apps", "line": line, "latency_ms": latency_ms}

    # 2. Close everything / close all windows (Always requires confirmation)
    if action == "close_all":
        res = idx.close_all_apps(confirmed=False)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        logger.trace_line(f"  [tier 2: app_control] {latency_ms}ms  $0.000000  action: close_all (needs_confirmation)")
        return {
            "tier": 2,
            "status": "needs_confirmation",
            "action": "close_all",
            "risk_level": "HIGH",
            "message": res["message"],
            "line": res["message"],
            "latency_ms": latency_ms,
            "execute_fn": lambda: (idx.close_all_apps(confirmed=True), safety_engine.log_action(2, "close_all", {}, safety_engine.HIGH, "success"))
        }

    # 3. Open / launch request
    if action == "open" and target:
        # 3a. Check if target is a system setting or folder phrased as "open X"
        sys_sub = system_targets.resolve_system_target(target, execute=False)
        if sys_sub:
            system_targets.execute_system_target(sys_sub)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            safety_engine.log_action(2, f"system_{sys_sub['type']}", {"target": sys_sub["label"]}, safety_engine.SAFE, "success")
            logger.trace_line(f"  [tier 2: system_target] {latency_ms}ms  $0.000000  type: {sys_sub['type']} -> {sys_sub['label']}")
            return {
                "tier": 2,
                "status": "done",
                "action": f"system_{sys_sub['type']}",
                "target": sys_sub["label"],
                "line": sys_sub["line"],
                "latency_ms": latency_ms
            }

        # 3b. Check AppIndex
        app_entry, score, amb = idx.find_app(target)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        # Ambiguity check for apps
        if amb and len(amb) >= 2:
            choice_a, choice_b = amb[0]["name"], amb[1]["name"]
            msg = f"Did you mean {choice_a} or {choice_b}?"
            logger.trace_line(f"  [tier 2: app_index] {latency_ms}ms  $0.000000  ambiguity: {choice_a} vs {choice_b}")
            return {
                "tier": 2,
                "status": "ambiguous",
                "action": "clarify_app",
                "choices": [choice_a, choice_b],
                "message": msg,
                "line": msg,
                "latency_ms": latency_ms
            }

        if app_entry and score >= 70.0:
            success = idx.launch_app(app_entry)
            display_name = app_entry["name"]
            line = say_line("app_open", app=display_name)
            safety_engine.log_action(2, "open_app", {"app": display_name}, safety_engine.SAFE, "success" if success else "failed")
            logger.trace_line(f"  [tier 2: app_index] {latency_ms}ms  $0.000000  launch: {display_name} (score {score:.1f})")
            return {
                "tier": 2,
                "status": "done" if success else "failed",
                "action": "app_open",
                "app": display_name,
                "entry": app_entry,
                "line": line,
                "latency_ms": latency_ms
            }

        # 3c. Check File Finder (e.g. "my resume", "the PDF I downloaded yesterday")
        files = file_finder.find_files(target)
        if files:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            if len(files) == 1:
                file_info = files[0]
                res_open = file_finder.open_file_safe(file_info["path"])
                if res_open.get("needs_confirmation"):
                    logger.trace_line(f"  [tier 2: file_finder] {latency_ms}ms  $0.000000  dangerous file confirmation: {file_info['name']}")
                    return {
                        "tier": 2,
                        "status": "needs_confirmation",
                        "risk_level": "HIGH",
                        "action": "open_dangerous_file",
                        "path": file_info["path"],
                        "message": res_open["message"],
                        "line": res_open["message"],
                        "latency_ms": latency_ms,
                        "execute_fn": lambda: (file_finder.open_file_safe(file_info["path"], confirmed=True), safety_engine.log_action(2, "open_file", {"path": file_info["path"]}, safety_engine.HIGH, "success"))
                    }
                else:
                    safety_engine.log_action(2, "open_file", {"path": file_info["path"]}, safety_engine.SAFE, "success" if res_open.get("success") else "failed")
                    logger.trace_line(f"  [tier 2: file_finder] {latency_ms}ms  $0.000000  open file: {file_info['name']}")
                    line = res_open.get("line") or res_open.get("error") or f"Opening {file_info['name']}."
                    return {
                        "tier": 2,
                        "status": "done" if res_open.get("success") else "failed",
                        "action": "open_file",
                        "path": file_info["path"],
                        "line": line,
                        "latency_ms": latency_ms
                    }
            else:
                # Multiple matches: speak top 3 and ask which
                top3 = [f["name"] for f in files[:3]]
                top3_str = ", ".join(top3[:-1]) + f" or {top3[-1]}" if len(top3) > 1 else top3[0]
                msg = f"Found {len(files)} files: {top3_str}. Which one would you like to open?"
                logger.trace_line(f"  [tier 2: file_finder] {latency_ms}ms  $0.000000  ambiguity: {len(files)} files matching '{target}'")
                return {
                    "tier": 2,
                    "status": "ambiguous",
                    "action": "clarify_file",
                    "choices": top3,
                    "message": msg,
                    "line": msg,
                    "latency_ms": latency_ms
                }
        elif any(w in target.lower() for w in ("file", "document", "resume", "pdf", "docx", "notes", "my ")) or "." in target:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            line = f"Couldn't find any file matching '{target}'."
            logger.trace_line(f"  [tier 2: file_finder] {latency_ms}ms  $0.000000  not found: {target}")
            return {
                "tier": 2,
                "status": "not_found",
                "action": "find_files",
                "target": target,
                "line": line,
                "latency_ms": latency_ms
            }

    # 4. Close / quit application
    if action == "close" and target:
        res = idx.close_app(target)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if res.get("success"):
            line = say_line("app_quit", app=target)
            safety_engine.log_action(2, "close_app", {"target": target}, safety_engine.MEDIUM, "success", undoable=True, undo_data={"action": "open_app", "target": target})
            logger.trace_line(f"  [tier 2: app_control] {latency_ms}ms  $0.000000  closed: {target} (procs: {res.get('matched_processes')})")
            return {
                "tier": 2,
                "status": "done",
                "action": "app_quit",
                "app": target,
                "line": line,
                "latency_ms": latency_ms
            }
        else:
            line = f"{target} is not open."
            safety_engine.log_action(2, "close_app", {"target": target}, safety_engine.MEDIUM, "not_running")
            logger.trace_line(f"  [tier 2: app_control] {latency_ms}ms  $0.000000  not running: {target}")
            return {
                "tier": 2,
                "status": "done",
                "action": "app_quit",
                "app": target,
                "line": line,
                "latency_ms": latency_ms
            }

    # 5. Focus / switch to application or window
    if action == "focus" and target:
        # Try window manager first
        if window_manager.focus_window(target):
            latency_ms = int((time.perf_counter() - t0) * 1000)
            line = say_line("app_focus", app=target)
            safety_engine.log_action(2, "app_focus", {"target": target}, safety_engine.SAFE, "success")
            logger.trace_line(f"  [tier 2: window_manager] {latency_ms}ms  $0.000000  focus: {target}")
            return {
                "tier": 2,
                "status": "done",
                "action": "app_focus",
                "app": target,
                "line": line,
                "latency_ms": latency_ms
            }

        app_entry, score, _ = idx.find_app(target)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if app_entry:
            proc_target = app_entry.get("process") or app_entry["name"]
            actions_win.focus_app(proc_target)
            line = say_line("app_focus", app=app_entry["name"])
            safety_engine.log_action(2, "app_focus", {"target": app_entry["name"]}, safety_engine.SAFE, "success")
            logger.trace_line(f"  [tier 2: app_control] {latency_ms}ms  $0.000000  focus: {app_entry['name']}")
            return {
                "tier": 2,
                "status": "done",
                "action": "app_focus",
                "app": app_entry["name"],
                "line": line,
                "latency_ms": latency_ms
            }

    return None
