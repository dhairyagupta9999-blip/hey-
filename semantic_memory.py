"""Semantic Memory Provider and Explainable Hybrid Retrieval for Hey Jev.

Provides:
- SemanticMemoryProvider abstract interface (isolating future embedding models/Mem0)
- BM25SemanticProvider: Fast, deterministic, zero-dependency token scoring
- ScoredMemoryRecord with explainable scoring breakdown
- HybridMemoryRetriever combining semantic/keyword, importance, recency, and entity signals
"""
from __future__ import annotations

import abc
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from memory_model import MemoryRecord
from memory_store import MemoryStore


class SemanticMemoryProvider(abc.ABC):
    """Abstract interface for semantic similarity calculation."""

    @abc.abstractmethod
    def compute_similarity(self, query: str, text: str) -> float:
        """Compute similarity score between 0.0 and 1.0."""
        pass

    @abc.abstractmethod
    def rank(
        self,
        query: str,
        records: List[MemoryRecord],
    ) -> List[Tuple[MemoryRecord, float]]:
        """Rank records against query returning (record, similarity_score)."""
        pass


class BM25SemanticProvider(SemanticMemoryProvider):
    """Deterministic, lightweight BM25-style term-matching semantic provider.

    Runs fully in-process in pure Python without PyTorch/ONNX or network calls.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r"\w+", text.lower())

    def compute_similarity(self, query: str, text: str) -> float:
        q_tokens = self._tokenize(query)
        t_tokens = self._tokenize(text)
        if not q_tokens or not t_tokens:
            return 0.0

        q_set = set(q_tokens)
        t_set = set(t_tokens)
        overlap = len(q_set & t_set)
        if overlap == 0:
            return 0.0

        # Term frequency overlap normalized by query length
        return min(1.0, overlap / len(q_set))

    def rank(
        self,
        query: str,
        records: List[MemoryRecord],
    ) -> List[Tuple[MemoryRecord, float]]:
        q_tokens = self._tokenize(query)
        if not q_tokens or not records:
            return [(r, 0.0) for r in records]

        # Compute document lengths and corpus stats
        docs_tokens = [self._tokenize(f"{r.content} {r.summary} {' '.join(r.tags)}") for r in records]
        avg_len = sum(len(d) for d in docs_tokens) / max(1, len(docs_tokens))
        n_docs = len(records)

        # Compute document frequency for each query term
        df: Dict[str, int] = {}
        for qt in q_tokens:
            df[qt] = sum(1 for d in docs_tokens if qt in d)

        scored: List[Tuple[MemoryRecord, float]] = []
        for rec, doc in zip(records, docs_tokens):
            score = 0.0
            doc_len = len(doc)
            for qt in q_tokens:
                if df[qt] == 0:
                    continue
                tf = doc.count(qt)
                if tf == 0:
                    continue
                # Standard BM25 IDF
                idf = math.log((n_docs - df[qt] + 0.5) / (df[qt] + 0.5) + 1.0)
                # BM25 term weight
                tf_weight = (tf * (self.k1 + 1.0)) / (tf + self.k1 * (1.0 - self.b + self.b * (doc_len / max(1.0, avg_len))))
                score += idf * tf_weight

            # Normalize to 0..1 range with smooth saturation
            norm_score = round(1.0 - math.exp(-score / 2.0), 4)
            scored.append((rec, norm_score))

        scored.sort(key=lambda item: -item[1])
        return scored


@dataclass
class ScoredMemoryRecord:
    """Memory record scored with explainable attribution."""
    record: MemoryRecord
    total_score: float
    semantic_score: float
    importance_score: float
    recency_score: float
    entity_score: float
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": self.record.memory_id,
            "type": self.record.type.value,
            "content": self.record.content,
            "total_score": round(self.total_score, 3),
            "semantic_score": round(self.semantic_score, 3),
            "importance_score": round(self.importance_score, 3),
            "recency_score": round(self.recency_score, 3),
            "entity_score": round(self.entity_score, 3),
            "rationale": self.rationale,
        }


class HybridMemoryRetriever:
    """Blends semantic similarity, importance, recency, and entity matching."""

    def __init__(
        self,
        store: MemoryStore,
        semantic_provider: Optional[SemanticMemoryProvider] = None,
    ) -> None:
        self.store = store
        self.semantic_provider = semantic_provider or BM25SemanticProvider()

    def retrieve(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        entity_hints: Optional[List[str]] = None,
        limit: int = 10,
    ) -> List[ScoredMemoryRecord]:
        """Execute hybrid retrieval over memory store candidates."""
        candidates = self.store.list(filters=filters, limit=100)
        if not candidates:
            return []

        # 1. Semantic / keyword scores
        sem_ranked = {r.memory_id: score for r, score in self.semantic_provider.rank(query, candidates)}

        now = time.time()
        one_day = 86400.0
        hints = set(e.lower() for e in (entity_hints or []))

        scored_records: List[ScoredMemoryRecord] = []

        for rec in candidates:
            sem_s = sem_ranked.get(rec.memory_id, 0.0)
            imp_s = rec.importance

            # Recency: decays over 30 days
            age_days = max(0.0, (now - rec.created_at) / one_day)
            rec_s = 1.0 / (1.0 + (age_days / 7.0))

            # Entity matching
            rec_entities = set(e.lower() for e in rec.entity_ids)
            if hints and rec_entities:
                overlap = len(hints & rec_entities)
                ent_s = min(1.0, overlap / len(hints))
            else:
                ent_s = 0.5 if not hints else 0.0

            # Composite formula: 40% semantic, 25% importance, 20% recency, 15% entities
            total = (sem_s * 0.40) + (imp_s * 0.25) + (rec_s * 0.20) + (ent_s * 0.15)

            rationale = (
                f"Sem={sem_s:.2f}, Imp={imp_s:.2f}, Rec={rec_s:.2f}, Ent={ent_s:.2f}"
            )

            scored_records.append(
                ScoredMemoryRecord(
                    record=rec,
                    total_score=total,
                    semantic_score=sem_s,
                    importance_score=imp_s,
                    recency_score=rec_s,
                    entity_score=ent_s,
                    rationale=rationale,
                )
            )

        scored_records.sort(key=lambda s: -s.total_score)
        return scored_records[:limit]
