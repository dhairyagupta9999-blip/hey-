"""Decision backend abstraction for Hey Jev.

Supports:
- "jev": TypeSafe hosted System 1 API (default parity target).
- "laya": Local open-weight Laya decision model running non-autoregressively in-process on CPU.
"""
import os
import re
import time
import json
import threading
from typing import Dict, Any, Tuple, Optional
import requests

from config import LOG_FILE
from secrets_store import get_secret
from logger import trace_line

class DecisionBackend:
    """Abstract base interface for decision backends."""
    name: str = "base"
    default_gate: float = 0.65

    def decide(self, questions: dict, utterance: str) -> Tuple[Dict[str, Tuple[Any, float]], int, float]:
        """Evaluate a question battery against an utterance.
        
        Args:
            questions: Dictionary defining the question battery.
            utterance: The user's input phrase.
            
        Returns:
            answers: Mapping of question_id -> (value, confidence)
            latency_ms: Execution time in milliseconds
            cost: Estimated cost in USD
        """
        raise NotImplementedError


class JevBackend(DecisionBackend):
    """TypeSafe hosted System 1 Jev decision backend."""
    name: str = "jev"
    default_gate: float = 0.65
    ENDPOINT: str = "https://api.typesafe.ai/v1/systemone"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or get_secret("TYPESAFE_API_KEY")

    def reload_key(self):
        self.api_key = get_secret("TYPESAFE_API_KEY")

    def decide(self, questions: dict, utterance: str) -> Tuple[Dict[str, Tuple[Any, float]], int, float]:
        t0 = time.time()
        if not self.api_key:
            self.reload_key()
        
        if not self.api_key:
            raise requests.exceptions.HTTPError(
                "Missing TypeSafe API key. Set TYPESAFE_API_KEY in .env or Settings.",
                response=None
            )

        resp = requests.post(
            self.ENDPOINT,
            json={"model": "jev-latest", "state": utterance, "questions": questions},
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()

        ans = {}
        for k, a in data["answers"].items():
            if a["type"] == "noul":
                val = a["noul"] >= 0.5
                conf = max(a["noul"], 1.0 - a["noul"])
                ans[k] = (val, conf)
            elif a["type"] == "score":
                score_int = int(round(a["score"]))
                legend = a.get("legend", {})
                val = legend.get(str(score_int), str(score_int))
                ans[k] = (val, a.get("confidence", 0.0))
            else:
                ans[k] = (a.get("choice", "none"), a.get("confidence", 0.0))

        cost = data.get("usage", {}).get("input_tokens", 0) * 0.042 / 1e6
        latency_ms = int((time.time() - t0) * 1000)
        return ans, latency_ms, cost


class LayaBackend(DecisionBackend):
    """Local open-weight Laya decision backend running non-autoregressively on CPU."""
    name: str = "laya"
    default_gate: float = 0.45  # Calibrated threshold for Laya's normalized probabilities

    def __init__(self, checkpoint: Optional[str] = None, fallback_to_jev: bool = True):
        self.checkpoint = checkpoint or os.getenv("LAYA_CHECKPOINT", "convaiinnovations/laya")
        self.fallback_to_jev = fallback_to_jev
        self._router = None
        self._loading = False
        self._load_error = None
        self._lock = threading.Lock()
        self._jev_fallback = None

    def preload_async(self):
        """Preload the model in a background thread so the main/event loop is never blocked."""
        t = threading.Thread(target=self._ensure_loaded, daemon=True, name="LayaPreloadThread")
        t.start()

    def _ensure_loaded(self):
        with self._lock:
            if self._router is not None:
                return self._router
            if self._load_error is not None:
                return None
            try:
                t0 = time.time()
                from laya import Router
                model_name = "english" if self.checkpoint in (None, "english", "convaiinnovations/laya") else self.checkpoint
                # preload must stay False. Upstream's Router(preload=True) loads
                # ALL THREE checkpoints up front -- roughly 2.5 GB of combined
                # weights -- when this backend only ever asks questions of one.
                # A measured run of the old line performed three separate
                # downloads (~4 min each) before answering a single question.
                # With preload=False only the checkpoint named above is fetched.
                self._router = Router(device="cpu", default=model_name, preload=False)
                load_time = time.time() - t0
                print(f"[laya] initialized router for {model_name!r} in {load_time:.2f}s on CPU "
                      f"(single checkpoint; set LAYA_CHECKPOINT=multilingual for the "
                      f"smaller/faster 322M model)")
                return self._router
            except Exception as exc:
                self._load_error = exc
                err_msg = f"[FALLBACK] Laya failed to load checkpoint {self.checkpoint!r}: {exc}. Falling back to Jev."
                print(err_msg)
                trace_line(f"  {err_msg}")
                return None

    def _get_jev_fallback(self) -> JevBackend:
        if self._jev_fallback is None:
            self._jev_fallback = JevBackend()
        return self._jev_fallback

    def calibrate_confidence(self, raw_prob: float, num_choices: int) -> float:
        """Calibrate uniform prior probability to [0.0, 1.0] scale."""
        if num_choices <= 1:
            return raw_prob
        # (N * p - 1) / (N - 1)
        scaled = (num_choices * raw_prob - 1.0) / (num_choices - 1.0)
        return max(0.0, min(1.0, scaled))

    def decide(self, questions: dict, utterance: str) -> Tuple[Dict[str, Tuple[Any, float]], int, float]:
        router = self._ensure_loaded()
        if router is None:
            if self.fallback_to_jev:
                trace_line(f"  [fallback]: routing turn to Jev backend due to Laya load error ({self._load_error})")
                return self._get_jev_fallback().decide(questions, utterance)
            raise RuntimeError(f"Laya failed to load: {self._load_error}")

        t0 = time.time()
        try:
            # Predict in a single forward pass over all questions
            result = router.predict(state=utterance, questions=questions)
            latency_ms = int((time.time() - t0) * 1000)
            
            raw_answers = result.get("answers", {})
            ans = {}
            for qid, qdef in questions.items():
                res = raw_answers.get(qid, {})
                qtype = qdef.get("type", "choice")
                
                if qtype == "noul":
                    noul_val = res.get("noul", 0.0)
                    val = noul_val >= 0.5
                    conf = max(noul_val, 1.0 - noul_val)
                    ans[qid] = (val, conf)
                elif qtype == "score":
                    score_val = res.get("score", 0.0)
                    criteria = qdef.get("criteria", [])
                    idx = int(round(score_val))
                    idx = max(0, min(len(criteria) - 1, idx))
                    label = criteria[idx] if criteria else str(idx)
                    ans[qid] = (label, res.get("confidence", 0.5))
                else:
                    choice = res.get("choice", "none")
                    prob = res.get("confidence", res.get("probability", 0.0))
                    # Number of choices in criteria
                    criteria = qdef.get("criteria", {})
                    num_c = len(criteria) if isinstance(criteria, dict) else len(criteria or [])
                    calibrated = self.calibrate_confidence(prob, num_c)
                    ans[qid] = (choice, calibrated)

            cost = 0.0  # Local inference has zero API cost
            return ans, latency_ms, cost

        except Exception as exc:
            trace_line(f"  [FALLBACK] Laya inference failed: {exc}. Falling back to Jev backend.")
            if self.fallback_to_jev:
                return self._get_jev_fallback().decide(questions, utterance)
            raise exc


# Singleton backends
_BACKENDS = {
    "jev": JevBackend(),
    "laya": LayaBackend()
}

class FallbackSlowError(RuntimeError):
    """The local fallback exists but is too slow to be usable on this machine."""


class FallbackBackend(DecisionBackend):
    """Primary backend with a local, open-weight fallback.

    Makes the fallback bidirectional. Previously only Laya -> Jev existed, so a
    user with no `TYPESAFE_API_KEY` got an auth error and silence, even though
    this repo has carried a complete Apache-2.0 local decision backend since
    Phase 6. For a project whose goal is "anyone can clone it and use it", that
    is the single most expensive failure in the codebase.

    The downgrade is deliberately NOT silent:
      * `name` becomes "<primary>+<fallback>" so every trace, cost line and log
        entry shows which engine actually answered.
      * `default_gate` is read from whichever backend answered -- Laya's
        calibrated probabilities use a different threshold (0.45 vs 0.65), and
        using the wrong one either over- or under-fires.
      * The switch is recorded on `self.switched_reason`.

    LATENCY GUARD (measured, not hypothetical)
    On hardware without AVX2 -- measured here on an Intel i3-2350M -- a Laya
    forward pass takes 36-190 s. Falling back unconditionally would replace a
    clear error message with a two-minute hang, which is worse: the user cannot
    tell a slow machine from a broken one. So the first fallback call is timed
    against `probe_budget_s`; if the local model is slower than that, the
    original error is re-raised with an explanation and the user is told the
    truth. An assistant that says "I cannot answer this quickly on this machine"
    is useful. One that freezes is not.
    """

    def __init__(
        self,
        primary: DecisionBackend,
        fallback_to: Optional[DecisionBackend] = None,
        probe_budget_s: float = 5.0,
    ):
        self.primary = primary
        self.fallback = fallback_to
        self.name = primary.name
        self.default_gate = getattr(primary, "default_gate", 0.65)
        self.switched_reason: Optional[str] = None
        self.probe_budget_s = probe_budget_s
        self._fallback_viable: Optional[bool] = None
        self._probe_seconds: Optional[float] = None
        self._probe_failure: Optional[str] = None
        self._lock = threading.Lock()

    def refresh_gate(self) -> float:
        """Return the gate of the backend that is actually answering.

        Call AFTER decide(): _activate() switches default_gate mid-turn, so a
        value captured beforehand is the primary's threshold and would
        over-fire or under-fire against a fallback with different calibration.
        """
        return self.default_gate

    def _activate(self, reason: str) -> None:
        with self._lock:
            if self.switched_reason is not None:
                return
            self.switched_reason = reason
            self.name = f"{self.primary.name}+{self.fallback.name}"
            self.default_gate = getattr(self.fallback, "default_gate", self.default_gate)
            print(f"[fallback] primary backend {self.primary.name!r} unavailable "
                  f"({reason}); trying local {self.fallback.name!r}. "
                  f"Traces will show {self.name!r}.")

    _PROBE_QUESTIONS_UNUSED = None
    def _probe_local_backend(self) -> None:
        """Time the local fallback once, and cache the verdict.

        Three outcomes are reported distinctly, because collapsing them lies to
        the user:
          * weights absent  -> tell them to install the model; do NOT trigger a
            silent multi-minute download as a side effect of a missing API key.
          * probe raised     -> report the real error (ImportError, bad
            checkpoint, offline). Never dress this up as "too slow".
          * probe too slow   -> refuse to fall back, and say why.
        """
        if self._fallback_viable is not None:
            return

        missing = self._fallback_missing_reason()
        if missing:
            self._fallback_viable = False
            self._probe_failure = missing
            print(f"[fallback] local model not available: {missing}")
            return

        t0 = time.time()
        try:
            self.fallback.decide(_PROBE_QUESTIONS, "open the calculator")
        except Exception as exc:
            self._probe_seconds = time.time() - t0
            self._fallback_viable = False
            self._probe_failure = (
                f"the local model raised {type(exc).__name__}: {exc}"
            )
            print(f"[fallback] local backend probe failed in "
                  f"{self._probe_seconds:.1f}s: {self._probe_failure}")
            return

        self._probe_seconds = time.time() - t0
        self._fallback_viable = self._probe_seconds <= self.probe_budget_s
        if not self._fallback_viable:
            self._probe_failure = (
                f"the local model took {self._probe_seconds:.1f}s for one probe "
                f"turn, over the {self.probe_budget_s:.1f}s budget"
            )
            print(f"[fallback] {self._probe_failure}. Not falling back -- an "
                  "assistant that freezes for minutes is worse than one that "
                  "reports the problem. Set HEYJEV_LOCAL_FALLBACK=0 to silence "
                  "this, or raise HEYJEV_FALLBACK_BUDGET_S on faster hardware.")

    def _fallback_missing_reason(self) -> Optional[str]:
        """Explain why the local fallback cannot be used, WITHOUT downloading it.

        Order matters: an obviously-unsupported CPU is reported immediately, so
        the user is not made to wait through a 60 s probe to be told the same
        thing.
        """
        fallback = self.fallback

        # Type check, not name check: a test double or an alternative local
        # backend may also call itself "laya", and the CPU rule applies to the
        # real Laya model only.
        if isinstance(fallback, LayaBackend) and not _cpu_has_avx2():
            return (
                "this CPU has no AVX2 (Intel Haswell, 2013 or newer is required). "
                "A Laya forward pass was measured at 36-190 s on such hardware, "
                "so local decisions are not usable here"
            )

        checker = getattr(fallback, "missing_reason", None)
        if callable(checker):
            return checker()
        return None

    def decide(self, questions: dict, utterance: str) -> Tuple[Dict[str, Tuple[Any, float]], int, float]:
        try:
            return self.primary.decide(questions, utterance)
        except Exception as exc:
            if self.fallback is None:
                raise
            reason = f"{type(exc).__name__}: {exc}"
            self._activate(reason)

            self._probe_local_backend()
            if not self._fallback_viable:
                # Report the ACTUAL reason. Saying "too slow" when the truth is
                # "the model is not installed" or "the hub is unreachable"
                # sends the user chasing the wrong problem.
                raise FallbackSlowError(
                    f"{reason} — and the local {self.fallback.name} model is "
                    f"unusable here: {self._probe_failure}. No decision was "
                    "made. Fix the API key, run 'python setup_assets.py', or use "
                    "hardware with AVX2 (Intel Haswell, 2013 or newer) for fast "
                    "local decisions."
                ) from exc

            return self.fallback.decide(questions, utterance)


_AUTO_FALLBACK = object()  # sentinel: distinguishes "not supplied" from "explicitly None"

# A real, non-empty question set used to time the local fallback.
#
# It CANNOT be empty. laya's agent short-circuits BEFORE tokenisation and
# inference when there are no questions, so a probe using {} returns in ~0 ms
# and reports "viable" on hardware where a real turn takes two minutes. That
# bug made the entire latency guard dead code -- it looked like it worked,
# because it returned True.
def _cpu_has_avx2() -> bool:
    """Best-effort AVX2 detection.

    Laya is a 322M-421M parameter transformer. Without AVX2 (Intel Haswell,
    2013, or newer) the int8/quantised operators are emulated and a single
    forward pass was measured at 36-190 s on an i3-2350M. Detecting that up
    front turns a two-minute freeze into an instant, explained refusal.

    Implemented via the registry rather than ctypes/__cpuid: the intrinsic is
    not reliably callable through windll with the right calling convention, and
    an earlier ctypes version reported AVX2 support on a Sandy Bridge CPU that
    definitively does not have it -- a false "yes" that would have let the
    useless fallback through.

    Returns True whenever the answer cannot be established, so a failed check
    degrades to "let the timed probe decide" rather than blocking the user.
    """
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        )
        try:
            identifier, _ = winreg.QueryValueEx(key, "Identifier")
        finally:
            winreg.CloseKey(key)

        match = re.search(r"Family\s+(\d+)\s+Model\s+(\d+)", str(identifier))
        if not match:
            return True

        family = int(match.group(1))
        model = int(match.group(2))
        vendor_is_intel = "Intel" in str(identifier)

        if not vendor_is_intel:
            # AMD: AVX2 from Excavator (Family 0x17) and Zen onward. Erring
            # toward True keeps a modern Ryzen usable.
            return family >= 0x17 or model >= 0x60

        if family != 6:
            return True

        # Intel Family 6: AVX2 begins at Haswell (model 60). Everything from
        # Nehalem (model 42, 2008) up to Sandy Bridge/Ivy Bridge lacks it.
        return model >= 60
    except Exception:
        return True


_PROBE_QUESTIONS = {
    "_probe": {
        "type": "choice",
        "instructions": "Is this a command to the computer?",
        "criteria": {
            "command": "asks the computer to do something",
            "other": "anything else",
        },
    },
}


def get_backend_with_fallback(
    primary: Optional[DecisionBackend] = None,
    fallback_to=_AUTO_FALLBACK,
) -> FallbackBackend:
    """Compose the configured backend with a local fallback.

    `fallback_to` accepts:
      * omitted  -> construct LayaBackend automatically (lazy; no weights are
                    fetched until a call actually needs it)
      * None     -> explicitly NO fallback; errors propagate unchanged
      * backend  -> use this one

    The distinction matters. Treating an explicit None as "auto" means a caller
    who asked for no fallback silently gets an 800 MB model load -- which is
    how the first version of this function made the test suite take nine
    minutes and download half a gigabyte.

    Set HEYJEV_LOCAL_FALLBACK=0 to disable automatic construction entirely.
    """
    if primary is None:
        primary = get_backend()

    if fallback_to is _AUTO_FALLBACK:
        if os.getenv("HEYJEV_LOCAL_FALLBACK", "1") in ("0", "false", "no"):
            fallback_to = None
        else:
            try:
                fallback_to = LayaBackend(fallback_to_jev=False)
            except Exception as exc:
                print(f"[fallback] local backend unavailable: {exc}")
                fallback_to = None

    try:
        budget = float(os.getenv("HEYJEV_FALLBACK_BUDGET_S", "5.0"))
    except ValueError:
        budget = 5.0

    return FallbackBackend(primary, fallback_to, probe_budget_s=budget)


# Process-wide composite cache. handle() used to call get_backend_with_fallback()
# per utterance, which threw away the probe verdict and rebuilt the Laya Router
# (and therefore reloaded model weights) on every single turn. One composite per
# process is what makes the latency guard worth having.
_COMPOSITE_LOCK = threading.Lock()
_COMPOSITE: Optional[FallbackBackend] = None


def get_shared_backend() -> FallbackBackend:
    """Return the process-wide composite backend, building it once."""
    global _COMPOSITE
    with _COMPOSITE_LOCK:
        if _COMPOSITE is None:
            _COMPOSITE = get_backend_with_fallback()
        return _COMPOSITE


def get_backend(name: Optional[str] = None) -> DecisionBackend:
    """Resolve active decision backend by name or HEYJEV_BACKEND env var (default 'jev')."""
    chosen = (name or os.getenv("HEYJEV_BACKEND", "jev")).lower().strip()
    if chosen not in _BACKENDS:
        print(f"[warning] Unknown backend {chosen!r}, falling back to 'jev'")
        chosen = "jev"
    return _BACKENDS[chosen]
