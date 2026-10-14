"""Windows File Finder with Search Indexer & Local Fallback (Tier 2).

Queries the Windows Search Index (SystemIndex via ADODB / Search.CollatorDSO)
by name, extension/type, and date modified. Falls back to scanning:
  - ~/Desktop
  - ~/Documents
  - ~/Downloads
  - %APPDATA%/Microsoft/Windows/Recent

Safety rule:
  Never open .exe, .bat, .ps1, .cmd, .msi or .lnk files without confirmation.
"""
from __future__ import annotations

import os
import sys
import time
import glob
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

DANGEROUS_EXTENSIONS = {
    ".exe", ".bat", ".ps1", ".cmd", ".msi", ".lnk",
    ".vbs", ".wsf", ".scr", ".pif", ".hta", ".cpl", ".reg"
}

TYPE_MAP = {
    "pdf": [".pdf"],
    "word": [".docx", ".doc"],
    "document": [".docx", ".doc", ".pdf", ".txt", ".rtf"],
    "spreadsheet": [".xlsx", ".xls", ".csv"],
    "excel": [".xlsx", ".xls", ".csv"],
    "presentation": [".pptx", ".ppt"],
    "slides": [".pptx", ".ppt"],
    "powerpoint": [".pptx", ".ppt"],
    "text": [".txt", ".md"],
    "image": [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"],
    "photo": [".png", ".jpg", ".jpeg"],
    "picture": [".png", ".jpg", ".jpeg"],
    "video": [".mp4", ".mkv", ".mov", ".avi"],
    "audio": [".mp3", ".wav", ".flac", ".m4a"],
}

DEFAULT_SCAN_DIRS = [
    os.path.expanduser("~/Downloads"),
    os.path.expanduser("~/Documents"),
    os.path.expanduser("~/Desktop"),
    os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Recent"),
]

def parse_file_query(text: str) -> Dict[str, Any]:
    """Parse a file request into name keywords, extension types, and date filters."""
    t = text.lower().strip()
    # Strip leading phrases like "open my", "open the", "find", "show me"
    t = re.sub(r"^(?:open|find|show\s+me|get|locate)\s+", "", t).strip()
    t = re.sub(r"^(?:my|the|a|an)\s+", "", t).strip()

    date_filter: Optional[Tuple[float, float]] = None
    now = datetime.now()

    if "downloaded yesterday" in t or "from yesterday" in t or "yesterday" in t:
        yesterday_start = datetime(now.year, now.month, now.day) - timedelta(days=1)
        yesterday_end = yesterday_start + timedelta(days=1)
        date_filter = (yesterday_start.timestamp(), yesterday_end.timestamp())
        t = re.sub(r"\b(?:downloaded\s+)?yesterday\b", "", t).strip()
    elif "today" in t:
        today_start = datetime(now.year, now.month, now.day)
        date_filter = (today_start.timestamp(), now.timestamp() + 3600)
        t = re.sub(r"\b(?:downloaded\s+)?today\b", "", t).strip()
    elif "last week" in t or "past week" in t:
        week_start = now - timedelta(days=7)
        date_filter = (week_start.timestamp(), now.timestamp() + 3600)
        t = re.sub(r"\b(?:downloaded\s+)?(?:last|past)\s+week\b", "", t).strip()

    # Detect file type / extension
    exts: List[str] = []
    for type_word, ext_list in TYPE_MAP.items():
        if re.search(rf"\b{type_word}\b", t):
            exts.extend(ext_list)
            # Remove word from keyword query if it's purely generic type
            t = re.sub(rf"\b{type_word}\b", "", t).strip()

    # Clean leftover modifiers
    t = re.sub(r"\b(?:last|latest|recent|file|document|i\s+downloaded|my|the|a|an)\b", "", t).strip()
    keywords = [w for w in re.split(r"\s+", t) if w]

    return {
        "keywords": keywords,
        "raw_keywords": " ".join(keywords),
        "extensions": list(set(exts)),
        "date_filter": date_filter
    }

def search_windows_indexer(parsed: Dict[str, Any], max_results: int = 10) -> List[Dict[str, Any]]:
    """Query Windows Search Indexer via ADODB / Search.CollatorDSO."""
    try:
        import win32com.client
        conn = win32com.client.Dispatch("ADODB.Connection")
        conn.Open('Provider=Search.CollatorDSO;Extended Properties="Application=Windows"')
        rs = win32com.client.Dispatch("ADODB.Recordset")

        where_clauses = ["SCOPE='file:'"]
        if parsed["keywords"]:
            for kw in parsed["keywords"]:
                safe_kw = kw.replace("'", "''")
                where_clauses.append(f"System.ItemName LIKE '%{safe_kw}%'")

        if parsed["extensions"]:
            ext_clauses = [f"System.FileExtension='{ext}'" for ext in parsed["extensions"]]
            where_clauses.append(f"({' OR '.join(ext_clauses)})")

        where_sql = " AND ".join(where_clauses)
        sql = f"SELECT TOP {max_results} System.ItemName, System.ItemPathDisplay, System.ItemDateModified FROM SystemIndex WHERE {where_sql} ORDER BY System.ItemDateModified DESC"

        rs.Open(sql, conn)
        results = []
        while not rs.EOF:
            name = rs.Fields("System.ItemName").Value
            path = rs.Fields("System.ItemPathDisplay").Value
            if path and os.path.exists(path) and os.path.isfile(path):
                ext = os.path.splitext(path)[1].lower()
                mtime = os.path.getmtime(path)
                results.append({
                    "name": name or os.path.basename(path),
                    "path": path,
                    "ext": ext,
                    "date_modified": mtime,
                    "is_dangerous": ext in DANGEROUS_EXTENSIONS
                })
            rs.MoveNext()
        rs.Close()
        conn.Close()
        return results
    except Exception as exc:
        return []

def search_local_fallback(parsed: Dict[str, Any], scan_dirs: Optional[List[str]] = None, max_results: int = 10) -> List[Dict[str, Any]]:
    """Fallback scanner searching Downloads, Documents, Desktop, and Recent."""
    dirs = scan_dirs or DEFAULT_SCAN_DIRS
    matches: List[Dict[str, Any]] = []

    for d in dirs:
        if not os.path.exists(d):
            continue
        try:
            for root, subdirs, files in os.walk(d):
                # Prune deep dependency and hidden directories for performance
                subdirs[:] = [
                    s for s in subdirs
                    if not s.startswith(".")
                    and s.lower() not in ("node_modules", "venv", ".venv", "__pycache__", "site-packages", "appdata")
                ]
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if parsed["extensions"] and ext not in parsed["extensions"]:
                        continue

                    f_lower = f.lower()
                    if parsed["keywords"] and not any(kw.lower() in f_lower for kw in parsed["keywords"]):
                        continue

                    full_path = os.path.join(root, f)
                    try:
                        mtime = os.path.getmtime(full_path)
                    except OSError:
                        continue

                    if parsed["date_filter"]:
                        t_min, t_max = parsed["date_filter"]
                        if not (t_min <= mtime <= t_max):
                            continue

                    matches.append({
                        "name": f,
                        "path": full_path,
                        "ext": ext,
                        "date_modified": mtime,
                        "is_dangerous": ext in DANGEROUS_EXTENSIONS
                    })
        except Exception:
            continue

    # Sort newest first
    matches.sort(key=lambda x: x["date_modified"], reverse=True)
    return matches[:max_results]

def find_files(query_phrase: str, scan_dirs: Optional[List[str]] = None, max_results: int = 5) -> List[Dict[str, Any]]:
    """Unified file finder: searches Windows Indexer first, falling back to local scan."""
    parsed = parse_file_query(query_phrase)
    # 1. Try Windows Search index
    results = search_windows_indexer(parsed, max_results=max_results)
    if not results:
        # 2. Fall back to direct directory traversal
        results = search_local_fallback(parsed, scan_dirs=scan_dirs, max_results=max_results)
    return results

def open_file_safe(path: str, confirmed: bool = False) -> Dict[str, Any]:
    """Open a file with its default application, enforcing dangerous extension security."""
    if not os.path.exists(path):
        return {"success": False, "error": f"File does not exist: {path}"}

    ext = os.path.splitext(path)[1].lower()
    if ext in DANGEROUS_EXTENSIONS and not confirmed:
        filename = os.path.basename(path)
        return {
            "needs_confirmation": True,
            "risk_level": "HIGH",
            "action": "open_dangerous_file",
            "path": path,
            "filename": filename,
            "message": f"Opening executable file '{filename}' requires confirmation. Do you want to open it?"
        }

    try:
        os.startfile(path)
        return {
            "success": True,
            "action": "open_file",
            "path": path,
            "filename": os.path.basename(path),
            "line": f"Opening {os.path.basename(path)}."
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
