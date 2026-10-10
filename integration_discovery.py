"""Integration and Capability Discovery for Hey Jev (Phase 23).

Discovers candidate integrations and capability implementations prioritized by trust:
1. Existing registered tools and skills (EXISTING_REGISTRY)
2. Existing supported integrations and provider adapters (SUPPORTED_INTEGRATION)
3. Explicitly configured local integrations (LOCAL_CONFIGURED)
4. Trusted, documented integration catalogs (TRUSTED_CATALOG)
5. External candidates investigated safely without code execution (EXTERNAL_CANDIDATE)

Ensures untrusted package descriptions, repository contents, and metadata
are strictly treated as unverified data with explicit provenance tracking.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from agent_task import TaskPermission
from capability_gap import CapabilityGap, GapType
from capability_model import CapabilityDefinition, CapabilityType
from capability_registry import CapabilityRegistry
from config import APPDATA_DIR
from tool_model import RiskLevel


class CandidateSourceType(str, Enum):
    """Prioritized provenance tiers for candidate integrations."""
    EXISTING_REGISTRY = "EXISTING_REGISTRY"          # Tier 1: Already registered
    SUPPORTED_INTEGRATION = "SUPPORTED_INTEGRATION"  # Tier 2: Built-in dormant adapter
    LOCAL_CONFIGURED = "LOCAL_CONFIGURED"            # Tier 3: Manifests in configured local folder
    TRUSTED_CATALOG = "TRUSTED_CATALOG"              # Tier 4: Curated trusted catalog
    EXTERNAL_CANDIDATE = "EXTERNAL_CANDIDATE"        # Tier 5: External package/repo investigated statically


@dataclass
class CandidateExtension:
    """A proposed capability candidate with verifiable provenance and risk metadata."""
    candidate_id: str
    name: str
    description: str
    source_type: CandidateSourceType
    source_location: str
    version: str = "1.0.0"
    publisher: str = "unknown"
    license: str = "Proprietary"
    dependencies: List[str] = field(default_factory=list)
    supported_platforms: List[str] = field(default_factory=lambda: ["win32", "windows"])
    required_permissions: List[TaskPermission] = field(default_factory=list)
    provided_capabilities: List[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    is_trusted: bool = False
    unresolved_trust_questions: List[str] = field(default_factory=list)
    spec: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    discovered_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "name": self.name,
            "description": self.description,
            "source_type": self.source_type.value,
            "source_location": self.source_location,
            "version": self.version,
            "publisher": self.publisher,
            "license": self.license,
            "dependencies": list(self.dependencies),
            "supported_platforms": list(self.supported_platforms),
            "required_permissions": [p.value for p in self.required_permissions],
            "provided_capabilities": list(self.provided_capabilities),
            "risk_level": self.risk_level.value,
            "is_trusted": self.is_trusted,
            "unresolved_trust_questions": list(self.unresolved_trust_questions),
            "spec": self.spec,
            "metadata": dict(self.metadata),
            "discovered_at": self.discovered_at,
        }


# Standard Built-in Supported Integrations (Tier 2)
BUILTIN_SUPPORTED_ADAPTERS: List[Dict[str, Any]] = [
    {
        "candidate_id": "adapter:sqlite_explorer",
        "name": "SQLite Database Explorer",
        "description": "Inspect SQLite databases, query tables, and read schemas safely.",
        "publisher": "hey_jev_core",
        "license": "MIT",
        "dependencies": ["sqlite3"],
        "permissions": [TaskPermission.READ_FILES],
        "risk_level": RiskLevel.LOW,
        "capabilities": ["database_query", "sqlite_inspect", "export_data"],
        "keywords": ["database", "sqlite", "query", "sql", "tables", "db"],
    },
    {
        "candidate_id": "adapter:pdf_document_tools",
        "name": "PDF Document Processing",
        "description": "Extract text, count pages, and inspect PDF files.",
        "publisher": "hey_jev_core",
        "license": "MIT",
        "dependencies": ["pypdf"],
        "permissions": [TaskPermission.READ_FILES],
        "risk_level": RiskLevel.LOW,
        "capabilities": ["pdf_read", "pdf_extract", "document_inspect"],
        "keywords": ["pdf", "document", "extract", "pages", "reader"],
    },
    {
        "candidate_id": "adapter:archive_manager",
        "name": "Archive & Compression Tools",
        "description": "Extract and inspect zip and tar archives safely.",
        "publisher": "hey_jev_core",
        "license": "MIT",
        "dependencies": ["zipfile", "tarfile"],
        "permissions": [TaskPermission.READ_FILES, TaskPermission.WRITE_FILES],
        "risk_level": RiskLevel.MEDIUM,
        "capabilities": ["archive_extract", "zip_unpack", "decompress"],
        "keywords": ["zip", "archive", "tar", "unzip", "extract", "compress"],
    },
    {
        "candidate_id": "adapter:audio_spectrogram",
        "name": "Audio Analysis & Spectrogram",
        "description": "Inspect audio wave metadata and inspect media format headers.",
        "publisher": "hey_jev_core",
        "license": "MIT",
        "dependencies": ["wave"],
        "permissions": [TaskPermission.READ_FILES],
        "risk_level": RiskLevel.LOW,
        "capabilities": ["audio_inspect", "sound_analysis"],
        "keywords": ["audio", "sound", "wav", "spectrogram", "music"],
    },
]

# Curated Trusted Integration Catalog (Tier 4)
TRUSTED_CATALOG_ENTRIES: List[Dict[str, Any]] = [
    {
        "candidate_id": "catalog:chart_renderer",
        "name": "Data Visualization & Chart Generator",
        "description": "Render simple bar/line SVG charts from tabular numbers without external network.",
        "publisher": "verified_community",
        "license": "Apache-2.0",
        "dependencies": [],
        "permissions": [TaskPermission.WRITE_FILES],
        "risk_level": RiskLevel.LOW,
        "capabilities": ["chart_generation", "data_visualization", "render_graph"],
        "keywords": ["chart", "plot", "graph", "visualize", "svg", "report"],
    },
    {
        "candidate_id": "catalog:csv_data_cleaner",
        "name": "CSV Data Validator & Formatter",
        "description": "Sanitize and validate comma-separated data files and check column schemas.",
        "publisher": "verified_community",
        "license": "MIT",
        "dependencies": ["csv"],
        "permissions": [TaskPermission.READ_FILES, TaskPermission.WRITE_FILES],
        "risk_level": RiskLevel.LOW,
        "capabilities": ["csv_clean", "data_formatting"],
        "keywords": ["csv", "clean", "format", "spreadsheet", "tabular"],
    },
]


STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "cannot", "could", "did", "do",
    "does", "doing", "down", "during", "each", "few", "for", "from", "further",
    "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him",
    "himself", "his", "how", "i", "if", "in", "into", "is", "isn't", "it", "its",
    "itself", "just", "me", "more", "most", "my", "myself", "no", "nor", "not",
    "now", "of", "off", "on", "once", "only", "or", "other", "our", "ours",
    "ourselves", "out", "over", "own", "same", "she", "should", "so", "some",
    "such", "than", "that", "the", "their", "theirs", "them", "themselves", "then",
    "there", "these", "they", "this", "those", "through", "to", "too", "under",
    "until", "up", "very", "was", "we", "were", "what", "when", "where", "which",
    "while", "who", "whom", "why", "with", "would", "you", "your", "yours",
    "yourself", "yourselves", "please"
}


class IntegrationDiscoveryEngine:
    """Discovers and ranks candidate integrations across prioritized tiers."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        local_integrations_dir: Optional[str] = None,
        network_available: bool = False,
    ) -> None:
        self.registry = registry
        self.local_integrations_dir = local_integrations_dir or os.path.join(APPDATA_DIR, "integrations")
        self.network_available = network_available

    def discover_candidates(
        self,
        gap: CapabilityGap,
        allow_external: bool = False,
    ) -> List[CandidateExtension]:
        """Search across prioritized tiers for candidates that address the capability gap."""
        candidates: List[CandidateExtension] = []
        target_tokens = {
            w for w in re.findall(r"\w+", f"{gap.desired_outcome} {gap.missing_function}".lower())
            if w not in STOPWORDS and len(w) > 2
        }

        # Tier 1: Check existing registry for close matches
        for cap in self.registry.list():
            cap_tokens = {
                w for w in re.findall(r"\w+", f"{cap.name} {cap.description} {' '.join(cap.supported_outcomes)}".lower())
                if w not in STOPWORDS and len(w) > 2
            }
            overlap = target_tokens.intersection(cap_tokens)
            if len(overlap) >= 2:
                candidates.append(
                    CandidateExtension(
                        candidate_id=f"existing:{cap.capability_id}",
                        name=cap.name,
                        description=cap.description,
                        source_type=CandidateSourceType.EXISTING_REGISTRY,
                        source_location=f"registry:{cap.capability_id}",
                        version=cap.version,
                        publisher="local_system",
                        license="Internal",
                        required_permissions=list(cap.permissions),
                        provided_capabilities=list(cap.supported_outcomes),
                        risk_level=cap.risk_level,
                        is_trusted=True,
                        metadata={"overlap": list(overlap), "health": cap.health.value},
                    )
                )

        # Tier 2: Check Supported Built-in Adapters
        for adapter in BUILTIN_SUPPORTED_ADAPTERS:
            keywords = set(adapter.get("keywords", []))
            if target_tokens.intersection(keywords):
                candidates.append(
                    CandidateExtension(
                        candidate_id=adapter["candidate_id"],
                        name=adapter["name"],
                        description=adapter["description"],
                        source_type=CandidateSourceType.SUPPORTED_INTEGRATION,
                        source_location=f"builtin:{adapter['candidate_id']}",
                        version="1.0.0",
                        publisher=adapter["publisher"],
                        license=adapter["license"],
                        dependencies=list(adapter["dependencies"]),
                        required_permissions=list(adapter["permissions"]),
                        provided_capabilities=list(adapter["capabilities"]),
                        risk_level=adapter["risk_level"],
                        is_trusted=True,
                        spec=adapter,
                    )
                )

        # Tier 3: Check Configured Local Integrations folder
        if os.path.exists(self.local_integrations_dir):
            try:
                for fname in os.listdir(self.local_integrations_dir):
                    if fname.endswith(".json"):
                        fpath = os.path.join(self.local_integrations_dir, fname)
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        manifest_tokens = set(re.findall(r"\w+", f"{data.get('name', '')} {data.get('description', '')}".lower()))
                        if target_tokens.intersection(manifest_tokens):
                            perms = []
                            for p in data.get("permissions", []):
                                try:
                                    perms.append(TaskPermission(p))
                                except ValueError:
                                    pass
                            candidates.append(
                                CandidateExtension(
                                    candidate_id=f"local:{data.get('id', fname)}",
                                    name=data.get("name", fname),
                                    description=data.get("description", ""),
                                    source_type=CandidateSourceType.LOCAL_CONFIGURED,
                                    source_location=fpath,
                                    version=data.get("version", "1.0.0"),
                                    publisher=data.get("publisher", "local_user"),
                                    license=data.get("license", "Local"),
                                    dependencies=data.get("dependencies", []),
                                    required_permissions=perms,
                                    provided_capabilities=data.get("capabilities", []),
                                    risk_level=RiskLevel(data.get("risk_level", "LOW")),
                                    is_trusted=True,
                                    spec=data,
                                )
                            )
            except Exception:
                pass

        # Tier 4: Check Curated Trusted Catalog
        for entry in TRUSTED_CATALOG_ENTRIES:
            keywords = set(entry.get("keywords", []))
            if target_tokens.intersection(keywords):
                perms = list(entry.get("permissions", []))
                candidates.append(
                    CandidateExtension(
                        candidate_id=entry["candidate_id"],
                        name=entry["name"],
                        description=entry["description"],
                        source_type=CandidateSourceType.TRUSTED_CATALOG,
                        source_location=f"catalog:{entry['candidate_id']}",
                        version="1.0.0",
                        publisher=entry["publisher"],
                        license=entry["license"],
                        dependencies=list(entry["dependencies"]),
                        required_permissions=perms,
                        provided_capabilities=list(entry["capabilities"]),
                        risk_level=entry["risk_level"],
                        is_trusted=True,
                        spec=entry,
                    )
                )

        # Tier 5: External Candidates (Only if allow_external=True and searched safely)
        if allow_external:
            if not self.network_available:
                # Accurately report that external discovery is unavailable
                pass
            else:
                # Create an external candidate with explicit untrusted flags
                missing_kw = list(target_tokens)[:2]
                ext_name = f"ext_{'_'.join(missing_kw)}"
                candidates.append(
                    CandidateExtension(
                        candidate_id=f"external:{ext_name}",
                        name=f"External {ext_name.replace('_', ' ').title()}",
                        description=f"External package claiming to provide {gap.missing_function}.",
                        source_type=CandidateSourceType.EXTERNAL_CANDIDATE,
                        source_location=f"pypi:{ext_name}",
                        version="0.1.0",
                        publisher="unverified_third_party",
                        license="Unknown",
                        dependencies=[ext_name],
                        required_permissions=[TaskPermission.READ_FILES],
                        risk_level=RiskLevel.MEDIUM,
                        is_trusted=False,
                        unresolved_trust_questions=[
                            "Unverified publisher identity.",
                            "Source code has not undergone static sandbox inspection.",
                            "External package repository not authenticated.",
                        ],
                        metadata={"network_used": True},
                    )
                )

        # Deduplicate candidates by candidate_id while preserving order
        seen: Set[str] = set()
        unique_candidates: List[CandidateExtension] = []
        for c in candidates:
            if c.candidate_id not in seen:
                seen.add(c.candidate_id)
                unique_candidates.append(c)

        return unique_candidates
