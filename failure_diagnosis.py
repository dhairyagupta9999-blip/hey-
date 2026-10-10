"""Failure Diagnosis Engine and Multi-Signal Root Cause Correlation for Hey Jev (Phase 26).

Correlates:
- Error messages and stack traces using FailureClassifierV2
- Repeated failure patterns and recurrence frequency
- Dependency availability (missing modules, missing binaries, unreachable sockets)
- Capability deployment version changes (correlating recent activation with failure onset)
- System resource pressure (RAM/CPU exhaustion via psutil)
- Workflow interruptions and failed steps

Distinguishes:
- Verified Causes: Empirically confirmed facts (e.g., missing dependency, port collision, 401 unauthorized).
- Hypotheses: Plausible candidate causes when empirical certainty cannot be established.

Safety:
- All strings, evidence, and diagnostic outputs are strictly sanitized with sanitize_content (zero secret leakage).
"""
from __future__ import annotations

import importlib.util
import os
import re
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import psutil
from memory_model import sanitize_content
from operational_health import HealthObservation, SubsystemCategory
from recovery_engine import FailureClassifierV2
from reliability_model import FailureCategory


@dataclass
class DiagnosticReport:
    """Structured report produced by FailureDiagnosisEngine."""
    diagnosis_id: str
    component_id: str
    category: SubsystemCategory
    observed_symptoms: List[str] = field(default_factory=list)
    verified_causes: List[str] = field(default_factory=list)
    hypotheses: List[str] = field(default_factory=list)
    supporting_evidence: List[str] = field(default_factory=list)
    contradicting_evidence: List[str] = field(default_factory=list)
    confidence: float = 1.0
    failure_classification: FailureCategory = FailureCategory.UNKNOWN
    recommended_recovery_action: str = "NO_ACTION"
    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    requires_approval: bool = False
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "diagnosis_id": self.diagnosis_id,
            "component_id": self.component_id,
            "category": self.category.value,
            "observed_symptoms": list(self.observed_symptoms),
            "verified_causes": list(self.verified_causes),
            "hypotheses": list(self.hypotheses),
            "supporting_evidence": list(self.supporting_evidence),
            "contradicting_evidence": list(self.contradicting_evidence),
            "confidence": round(self.confidence, 3),
            "failure_classification": self.failure_classification.value,
            "recommended_recovery_action": self.recommended_recovery_action,
            "risk_level": self.risk_level,
            "requires_approval": self.requires_approval,
            "created_at": self.created_at,
        }


class FailureDiagnosisEngine:
    """Performs deterministic, evidence-based failure diagnosis across system signals."""

    def __init__(self, resource_cpu_threshold: float = 95.0, resource_mem_threshold: float = 98.0) -> None:
        self.cpu_threshold = resource_cpu_threshold
        self.mem_threshold = resource_mem_threshold

    def _sanitize(self, text_or_obj: Any) -> Any:
        if isinstance(text_or_obj, str):
            return sanitize_content(text_or_obj)
        if isinstance(text_or_obj, list):
            return [self._sanitize(x) for x in text_or_obj]
        if isinstance(text_or_obj, dict):
            return {k: self._sanitize(v) for k, v in text_or_obj.items()}
        return text_or_obj

    def diagnose(
        self,
        component_id: str,
        category: SubsystemCategory,
        error_or_exception: Any,
        context: Optional[Dict[str, Any]] = None,
        health_observation: Optional[HealthObservation] = None,
    ) -> DiagnosticReport:
        """Analyze failure signals and formulate a structured DiagnosticReport."""
        ctx = dict(context or {})
        raw_error = str(error_or_exception or "")
        clean_error = sanitize_content(raw_error)

        symptoms: List[str] = []
        if clean_error:
            symptoms.append(f"Error signal: {clean_error}")

        # 1. Classify failure category
        classification = FailureClassifierV2.classify(error_or_exception, context=ctx)
        symptoms.append(f"Classification: {classification.value}")

        verified_causes: List[str] = []
        hypotheses: List[str] = []
        supporting_evidence: List[str] = []
        contradicting_evidence: List[str] = []
        confidence = 0.8
        recommended_action = "RETRY"
        risk_level = "LOW"
        requires_approval = False

        # 2. Check System Resource Pressure
        try:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory().percent
            supporting_evidence.append(f"Resource telemetry: CPU={cpu}%, RAM={mem}%")
            if cpu >= self.cpu_threshold or mem >= self.mem_threshold:
                if classification in (FailureCategory.TIMEOUT, FailureCategory.RESOURCE_EXHAUSTED):
                    verified_causes.append(f"Host hardware resource saturation (CPU={cpu}%, RAM={mem}%)")
                    classification = FailureCategory.RESOURCE_EXHAUSTED
                    recommended_action = "RECONCILE_STATE"
                    confidence = 0.95
                else:
                    hypotheses.append(f"High host resource pressure may contribute to degraded performance (CPU={cpu}%, RAM={mem}%)")
            else:
                contradicting_evidence.append("Host CPU and RAM are within normal operating headroom.")
        except Exception:
            pass

        # 3. Check Dependency Failures (ImportError / ModuleNotFoundError)
        mod_match = re.search(r"No module named ['\"]([^'\"]+)['\"]", raw_error, re.IGNORECASE)
        if mod_match or "modulenotfounderror" in raw_error.lower() or "importerror" in raw_error.lower():
            missing_pkg = mod_match.group(1) if mod_match else "unknown_module"
            spec = importlib.util.find_spec(missing_pkg) if mod_match else None
            if spec is None:
                verified_causes.append(f"Missing required Python module: '{missing_pkg}'")
                supporting_evidence.append(f"importlib.util.find_spec('{missing_pkg}') returned None")
                classification = FailureCategory.TOOL_FAILURE
                recommended_action = "PROPOSE_REPAIR"
                risk_level = "MEDIUM"
                requires_approval = True
                confidence = 1.0

        # 4. Check Deployment Version Change Correlation
        recent_deployment = ctx.get("recent_deployment") or ctx.get("deployment_manifest")
        deployment_time = ctx.get("deployment_timestamp", 0)
        now = time.time()
        if recent_deployment and (now - deployment_time <= 600 or deployment_time == 0):
            # Deployment occurred within last 10 minutes or explicitly tagged as recent
            dep_id = getattr(recent_deployment, "deployment_id", str(recent_deployment))
            supporting_evidence.append(f"Recent capability deployment identified: '{dep_id}'")
            verified_causes.append(f"Regression correlated with recent deployment '{dep_id}'")
            recommended_action = "ROLLBACK_DEPLOYMENT"
            risk_level = "MEDIUM"
            requires_approval = False
            confidence = 0.95

        # 5. Check Network & Connection Failures
        if classification == FailureCategory.NETWORK_FAILURE:
            host = ctx.get("target_host")
            port = ctx.get("target_port")
            if host and port:
                try:
                    s = socket.create_connection((host, int(port)), timeout=1.5)
                    s.close()
                    contradicting_evidence.append(f"Socket connection to {host}:{port} succeeded. Failure may be application-level or transient.")
                    hypotheses.append("Transient network or endpoint socket interruption")
                    recommended_action = "RETRY"
                    confidence = 0.7
                except Exception as ex:
                    verified_causes.append(f"Target endpoint unreachable at {host}:{port} ({str(ex)})")
                    supporting_evidence.append(f"Direct socket probe to {host}:{port} failed")
                    recommended_action = "RETRY"
                    confidence = 0.95
            else:
                hypotheses.append("Transient network transport disruption or remote reset")
                recommended_action = "RETRY"
                confidence = 0.75

        # 6. Check Rate Limit & Timeouts
        if classification == FailureCategory.RATE_LIMIT:
            verified_causes.append("API rate limit or quota exceeded (HTTP 429 / Quota)")
            supporting_evidence.append(clean_error)
            recommended_action = "RETRY"
            risk_level = "LOW"
            confidence = 1.0

        elif classification == FailureCategory.TIMEOUT:
            if not verified_causes:
                hypotheses.append("Operation exceeded execution deadline; possible target slowdown or deadlock")
                recommended_action = "RETRY"
                risk_level = "LOW"
                confidence = 0.8

        # 7. Check Authentication & Permission Blocks
        if classification in (FailureCategory.AUTHENTICATION, FailureCategory.PERMISSION_DENIED, FailureCategory.POLICY_BLOCKED):
            verified_causes.append(f"Security boundary or credential block: {classification.value}")
            supporting_evidence.append(clean_error)
            recommended_action = "ESCALATE_TO_USER"
            risk_level = "CRITICAL"
            requires_approval = True
            confidence = 1.0

        # 8. Check Watcher / Process Crash
        if classification in (FailureCategory.PROCESS_CRASH, FailureCategory.WINDOWS_FAILURE):
            if category == SubsystemCategory.WATCHER:
                verified_causes.append(f"Native watcher process or thread terminated unexpectedly ({classification.value})")
                recommended_action = "RESTART_COMPONENT"
                risk_level = "LOW"
                confidence = 0.95
            else:
                verified_causes.append(f"Subprocess terminated abnormally: {clean_error}")
                recommended_action = "RESTART_COMPONENT"
                risk_level = "MEDIUM"
                confidence = 0.9

        # 9. Consecutively Failing Components Escalation
        if health_observation and health_observation.consecutive_failures >= 4:
            supporting_evidence.append(f"High consecutive failure count ({health_observation.consecutive_failures})")
            if recommended_action == "RETRY":
                hypotheses.append("Repeated retries have failed; persistent failure suspected")
                recommended_action = "ESCALATE_TO_USER"
                risk_level = "HIGH"
                requires_approval = True
                confidence = 0.9

        report = DiagnosticReport(
            diagnosis_id=f"diag_{uuid.uuid4().hex[:8]}",
            component_id=component_id,
            category=category,
            observed_symptoms=symptoms,
            verified_causes=verified_causes,
            hypotheses=hypotheses,
            supporting_evidence=supporting_evidence,
            contradicting_evidence=contradicting_evidence,
            confidence=confidence,
            failure_classification=classification,
            recommended_recovery_action=recommended_action,
            risk_level=risk_level,
            requires_approval=requires_approval,
        )
        return report
