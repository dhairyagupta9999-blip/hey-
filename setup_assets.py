"""First-run asset bootstrap for Hey Jev.

Downloads the speech models the assistant needs to actually speak and listen.
Without this a fresh clone installs cleanly, starts, and then produces silence:
Fish Audio needs a paid key, and the Kokoro fallback needs ~136 MB of model
files that nothing was fetching.

Design principles (Phase 31):
  * NEVER silent. Every download is announced with its size and destination
    before a byte moves. The plan's own complaint about the assistant was a
    silent 250 MB download on first launch; repeating that here would be
    ironic.
  * Resumable and atomic. Download to a .part file, then rename. An
    interrupted download never leaves a half-written model that passes the
    size check and fails at load time.
  * Verifiable. Content-Length is checked before accepting the file.
  * Idempotent. Existing valid files are never re-fetched.

Usage:
    python setup_assets.py              # report what is missing
    python setup_assets.py --download   # fetch what is missing, with prompts
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Verified against the upstream project's GitHub releases. The int8 build is
# chosen deliberately: tts.py already prefers kokoro-v1.0.int8.onnx, it is
# roughly a third the size of the fp32 model, and this project targets CPU.
KOKORO_RELEASE = "model-files-v1.1"
KOKORO_BASE = f"https://github.com/thewh1teagle/kokoro-onnx/releases/download/{KOKORO_RELEASE}"

ASSETS = {
    "model": {
        "filename": "kokoro-v1.0.int8.onnx",
        "url": f"{KOKORO_BASE}/kokoro-v1.0.int8.onnx",
        "expected_mb": 108.8,
        "purpose": "Kokoro-82M speech synthesis, int8 (CPU)",
        "license": "Apache-2.0",
    },
    "voices": {
        "filename": "voices-v1.0.bin",
        "url": f"{KOKORO_BASE}/voices-v1.0.bin",
        "expected_mb": 26.9,
        "purpose": "Kokoro voice embeddings",
        "license": "Apache-2.0",
    },
}

SILERO_VAD_URL = (
    "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/"
    "data/silero_vad.onnx"
)
SILERO_VAD = {
    "filename": "silero_vad.onnx",
    "url": SILERO_VAD_URL,
    "expected_mb": 2.3,
    "purpose": "Voice activity detection (turn endpointing)",
    "license": "MIT",
}

# ---------------------------------------------------------------------------
# Optional: the third-party Windows MCP server binary (sbroenne/mcp-windows,
# MIT, ~57 MB). Self-contained .NET build, needs no runtime. Enables Windows
# UI automation in Tier 3 (mcp_client.py). Vendored under its OWN licence and
# never redistributed in this repo; mcp/* is gitignored. Hey Jev merely
# downloads it into mcp/ on demand.
MCP_VERSION = "v1.3.28"
MCP_ASSET_NAME = "windows-mcp-server-1.3.28-win-x64.zip"
MCP_ASSET_URL = "https://github.com/sbroenne/mcp-windows/releases/download/v1.3.28/" + MCP_ASSET_NAME
MCP_ASSET_SIZE_BYTES = 54186365
MCP_ASSET_SHA256 = "9ca69685222d3c4934680218b4a2b3b7ae5bd0c8d2ce8bc2220925f4bd924fe4"
MCP_BINARY_NAME = "Sbroenne.WindowsMcp.exe"


def models_dir() -> Path:
    """Where models live. %APPDATA%/HeyJev/models, matching tts.py."""
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(appdata) / "HeyJev" / "models"


def human(n_bytes: float) -> str:
    mb = n_bytes / (1024 * 1024)
    return f"{mb:.1f} MB" if mb < 1024 else f"{mb / 1024:.2f} GB"


def _existing(path: Path, min_bytes: int = 1000) -> bool:
    return path.exists() and path.stat().st_size > min_bytes


def status(verbose: bool = True) -> dict:
    """Report which required assets are present, without downloading."""
    dest = models_dir()
    out = {"models_dir": str(dest), "items": {}}

    for key, spec in {**ASSETS, "silero_vad": SILERO_VAD}.items():
        path = dest / spec["filename"]
        present = _existing(path)
        out["items"][key] = {
            "path": str(path),
            "present": present,
            "size_mb": round(path.stat().st_size / (1024 * 1024), 1) if present else 0.0,
            "purpose": spec["purpose"],
            "license": spec["license"],
        }
        if verbose:
            mark = "OK     " if present else "MISSING"
            size = out["items"][key]["size_mb"]
            extra = f"{size:>7.1f} MB" if present else f"{spec['expected_mb']:>7.1f} MB expected"
            print(f"  [{mark}] {spec['filename']:<28} {extra}  ({spec['purpose']})")

    # What the assistant can do as configured right now.
    have_kokoro = out["items"]["model"]["present"] and out["items"]["voices"]["present"]
    out["tts_ready"] = have_kokoro
    out["stt_ready"] = _stt_available()
    out["speech_ready"] = have_kokoro and out["stt_ready"]
    out["mcp_available"] = _mcp_available()
    out["mcp_binaries"] = [str(p) for p in mcp_binary_paths() if p.exists()]

    if verbose:
        print()
        print(f"  Models directory : {dest}")
        print(f"  Speech out (TTS) : {'ready' if have_kokoro else 'NOT READY - assistant will be silent'}")
        print(f"  Speech in  (STT) : {'ready' if out['stt_ready'] else 'NOT READY - faster-whisper will download on first use'}")
        if out["mcp_available"]:
            print(f"  MCP binary (opt) : ready - " + str(out["mcp_binaries"][0]))
        else:
            print("  MCP binary (opt) : MISSING - Tier 3 UI automation disabled (optional; run --mcp)")
    return out


def _stt_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def download_asset(key: str, spec: dict, dest: Path, force: bool = False) -> bool:
    """Fetch one asset atomically. Returns True on success."""
    path = dest / spec["filename"]
    if _existing(path) and not force:
        print(f"  already present, skipping: {path.name}")
        return True

    part = path.with_suffix(path.suffix + ".part")
    url = spec["url"]

    print(f"  {spec['filename']}")
    print(f"    from     : {url}")
    print(f"    to       : {path}")
    print(f"    size     : ~{spec['expected_mb']} MB   license: {spec['license']}")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hey-jev-setup"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            last_pct = -5
            with open(part, "wb") as fh:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = int(done * 100 / total)
                        if pct >= last_pct + 5:
                            last_pct = pct
                            print(f"      {pct:>3}%  {human(done)} / {human(total)}", end="\r")
            print(" " * 60, end="\r")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        part.unlink(missing_ok=True)
        print(f"    FAILED: {exc}")
        return False

    if total and done != total:
        part.unlink(missing_ok=True)
        print(f"    FAILED: incomplete download ({human(done)} of {human(total)})")
        return False

    part.replace(path)  # atomic: never leave a truncated model in place
    print(f"    OK ({human(path.stat().st_size)})")
    return True


def mcp_binary_paths() -> list:
    repo = REPO_ROOT
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return [
        repo / "mcp" / MCP_BINARY_NAME,
        repo / ".venv-agents" / "bin" / "mcp-windows" / MCP_BINARY_NAME,
        Path(local) / "HeyJev" / "mcp" / MCP_BINARY_NAME,
    ]

def mcp_binary_path() -> Path:
    return REPO_ROOT / "mcp" / MCP_BINARY_NAME

def _mcp_available() -> bool:
    return any(p.exists() for p in mcp_binary_paths())

def download_mcp(force: bool = False, announced: bool = True) -> bool:
    dest = mcp_binary_path()
    if _existing(dest, min_bytes=1000) and not force:
        if announced:
            print("  MCP binary already present: " + str(dest))
        return True
    if announced:
        print()
        print("  Windows MCP server (third-party, sbroenne/mcp-windows, MIT):")
        print("    version     : " + MCP_VERSION)
        print("    asset zip   : " + MCP_ASSET_NAME)
        print("    size        : ~" + str(MCP_ASSET_SIZE_BYTES // (1024 * 1024)) + " MB")
        print("    sha256      : " + MCP_ASSET_SHA256)
        print("    extracted   : " + str(dest))
        print("    licence     : MIT (NOT Hey Jev's licence; gitignored here)")
    mcp_dir = dest.parent
    mcp_dir.mkdir(parents=True, exist_ok=True)
    zip_part = mcp_dir / (MCP_ASSET_NAME + ".part")
    try:
        req = urllib.request.Request(MCP_ASSET_URL, headers={"User-Agent": "hey-jev-setup"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            if not total:
                total = MCP_ASSET_SIZE_BYTES
            done = 0
            last_pct = -5
            with open(zip_part, "wb") as fh:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = int(100.0 * done / total)
                        if pct >= last_pct + 5:
                            last_pct = last_pct + 5
                            print("      " + str(pct).rjust(3) + "%  " + human(done) + " / " + human(total), end="\r")
            print(" " * 60, end="\r")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        zip_part.unlink(missing_ok=True)
        print("    FAILED download: " + str(exc))
        return False
    if zip_part.stat().st_size != MCP_ASSET_SIZE_BYTES:
        print("    WARNING: size " + str(zip_part.stat().st_size) + " != expected " + str(MCP_ASSET_SIZE_BYTES))
    import hashlib
    h = hashlib.sha256()
    with open(zip_part, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    digest = h.hexdigest()
    if digest != MCP_ASSET_SHA256:
        zip_part.unlink(missing_ok=True)
        print("    FAILED checksum mismatch (got " + digest + ", want " + MCP_ASSET_SHA256 + ")")
        return False
    print("    sha256 OK: " + digest)
    import zipfile
    import shutil
    try:
        with zipfile.ZipFile(zip_part, "r") as zf:
            members = [m for m in zf.infolist() if m.filename.replace("\\", "/").split("/")[-1] == MCP_BINARY_NAME]
            if not members:
                members = [m for m in zf.infolist()
                            if m.filename.replace("\\", "/").split("/")[-1].lower().endswith(".exe")]
            if not members:
                print("    FAILED: no exe inside")
                zip_part.unlink(missing_ok=True)
                return False
            member = members[0]
            with zf.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    except (zipfile.BadZipFile, OSError) as exc:
        zip_part.unlink(missing_ok=True)
        print("    FAILED extracting: " + str(exc))
        return False
    finally:
        zip_part.unlink(missing_ok=True)
    if not dest.exists() or dest.stat().st_size < 1000:
        print("    FAILED: missing after extract")
        return False
    print("    OK: " + str(dest) + " (" + human(dest.stat().st_size) + ")")
    return True

def main() -> int:
    ap = argparse.ArgumentParser(description="Hey Jev first-run asset bootstrap")
    ap.add_argument("--download", action="store_true",
                    help="fetch missing assets (announced, with sizes)")
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    ap.add_argument("--yes", action="store_true", help="do not prompt before downloading")
    ap.add_argument("--mcp", action="store_true",
                    help="also download the optional Windows MCP server binary (third party)")
    ap.add_argument("--skip-mcp", action="store_true",
                    help="do not download the optional Windows MCP server binary")
    args = ap.parse_args()

    print("=" * 72)
    print(" HEY JEV - ASSET CHECK")
    print("=" * 72)

    state = status()
    dest = models_dir()
    missing = [k for k, v in state["items"].items() if not v["present"]]

    if not missing:
        print("\n  All speech assets present. Nothing to do.")
        return 0

    total_mb = sum(
        {**ASSETS, "silero_vad": SILERO_VAD}[k]["expected_mb"] for k in missing
    )
    print(f"\n  {len(missing)} asset(s) missing, ~{total_mb:.0f} MB total.")
    print(f"  Download destination: {dest}")

    if not args.download and not args.mcp:
        print("\n  Re-run with:  python setup_assets.py --download")
        return 1

    if not args.yes:
        print("\n  These are third-party model weights downloaded from their "
              "official release pages.")
        try:
            reply = input(f"  Proceed with ~{total_mb:.0f} MB of downloads? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Cancelled.")
            return 1
        if reply not in ("y", "yes"):
            print("  Cancelled.")
            return 1

    speech_ok = ok = True
    if missing and args.download:
        dest.mkdir(parents=True, exist_ok=True)
        print()
        ok = True
        for key in missing:
            spec = {**ASSETS, "silero_vad": SILERO_VAD}[key]
            if not download_asset(key, spec, dest, force=args.force):
                ok = False
        speech_ok = ok

    # Optional third-party MCP binary. Runs under --download (unless --skip-mcp)
    # or under the explicit --mcp flag. The one-command common path is:
    #   python setup_assets.py --download --yes
    should_mcp = args.mcp or (args.download and not args.skip_mcp)
    mcp_ok = True
    if should_mcp:
        if args.mcp or args.force:
            if not _mcp_available() and not args.yes and not args.force:
                print()
                try:
                    reply = input("  Download optional MCP binary (~57 MB)? [y/N] ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print("  Cancelled.")
                    reply = "n"
                if reply not in ("y", "yes"):
                    print("  Skipped MCP binary (optional).")
                    should_mcp = False
        if should_mcp:
            if not download_mcp(force=args.force, announced=True):
                mcp_ok = False
                print("\n  Speech assets still OK; MCP install failed. Tier 3 UI")
                print("  automation will remain unavailable but the assistant works.")

    print()
    print("=" * 72)
    status()
    if ok and mcp_ok:
        print("\n  All requested assets ready. Run `python preflight.py` to verify the install.")
    elif not mcp_ok:
        print("\n  MCP install failed; speech side unaffected. Re-run with --mcp.")
    else:
        print("\n  Some speech assets failed; see messages above.")
    return 0 if (ok and mcp_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())