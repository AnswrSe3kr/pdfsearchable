"""
Busca híbrida — combina FTS (BM25 via SQLite FTS5) com busca semântica
(embeddings Ollama) usando Reciprocal Rank Fusion (RRF).

RRF é robusto a escalas heterogéneas: cada resultado recebe score
1/(k + rank) onde k=60 (padrão Cormack/Clarke/Buettcher 2009). A soma
dos scores por documento/página produz ranking final sem precisar
normalizar BM25 vs. cosine similarity.

API principal:
    hybrid_search(query, top_k=10) -> list[dict]

Cada resultado:
    {
        "file_id": str,
        "page": int,
        "score": float,
        "snippet": str,
        "sources": list[str],  # ["fts", "semantic"] indicando de onde veio
        "fts_rank": int | None,
        "semantic_rank": int | None,
    }
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("pdfsearchable.hybrid")

# Constante RRF padrão da literatura (Cormack et al. 2009)
RRF_K = 60


def _rrf_score(rank: int, k: int = RRF_K) -> float:
    """Reciprocal Rank Fusion: 1/(k + rank)."""
    return 1.0 / (k + rank)


def hybrid_search(
    query: str,
    *,
    top_k: int = 10,
    fts_limit: int = 100,
    semantic_limit: int = 50,
    fts_weight: float = 1.0,
    semantic_weight: float = 1.0,
    enable_semantic: bool | None = None,
    rerank: bool = False,
    model: str | None = None,
    ollama_url: str | None = None,
) -> list[dict[str, Any]]:
    """
    Executa busca híbrida FTS + semântica e combina via RRF.

    Args:
        query: texto da consulta.
        top_k: número de resultados finais.
        fts_limit: quantos candidatos buscar no FTS antes de fundir.
        semantic_limit: quantos candidatos buscar no semantic antes de fundir.
        fts_weight: peso do score FTS (multiplicador).
        semantic_weight: peso do score semântico.
        enable_semantic: None = auto-detect (tenta, cai para só FTS se falhar).
                         True = força tentativa, False = só FTS.
        rerank: se True, aplica cross-encoder sentence-transformers se disponível.
        model: override do modelo de embeddings Ollama.
        ollama_url: override da URL Ollama.

    Returns:
        Lista de resultados ordenados por score RRF decrescente.
    """
    query = (query or "").strip()
    if not query:
        return []

    # --- FTS branch ---
    fts_results: list[dict[str, Any]] = []
    try:
        from pdfsearchable.store import fts_search

        fts_raw = fts_search(query, limit=fts_limit) or []
        for rank, (file_id, page_num, snippet) in enumerate(fts_raw, start=1):
            fts_results.append(
                {
                    "file_id": file_id,
                    "page": page_num,
                    "snippet": snippet,
                    "rank": rank,
                }
            )
    except Exception as e:
        logger.warning("hybrid_search: FTS falhou: %s", e)

    # --- Semantic branch ---
    semantic_results: list[dict[str, Any]] = []
    do_semantic = enable_semantic
    if do_semantic is None:
        # Auto: só tenta se Ollama estiver acessível
        do_semantic = _semantic_available()
    if do_semantic:
        try:
            from pdfsearchable.semantic_search import semantic_search as sem_search

            _model = model or os.environ.get("PDFSEARCHABLE_EMBED_MODEL", "nomic-embed-text")
            _url = ollama_url or os.environ.get(
                "PDFSEARCHABLE_OLLAMA_URL", "http://localhost:11434"
            )
            sem_raw = sem_search(query, model=_model, ollama_url=_url, top_k=semantic_limit) or []
            for rank, hit in enumerate(sem_raw, start=1):
                # semantic_search retorna dicts com file_id + outros; adapta
                semantic_results.append(
                    {
                        "file_id": hit.get("file_id", ""),
                        "page": hit.get("page", hit.get("page_num", 1)),
                        "snippet": hit.get("snippet", hit.get("text", ""))[:200],
                        "rank": rank,
                        "similarity": hit.get("similarity", hit.get("score")),
                    }
                )
        except Exception as e:
            logger.warning("hybrid_search: semântica falhou: %s", e)

    # --- RRF fusion ---
    fused: dict[tuple[str, int], dict[str, Any]] = {}

    for r in fts_results:
        key = (r["file_id"], int(r.get("page", 0)))
        entry = fused.setdefault(
            key,
            {
                "file_id": r["file_id"],
                "page": key[1],
                "score": 0.0,
                "snippet": r.get("snippet", ""),
                "sources": [],
                "fts_rank": None,
                "semantic_rank": None,
            },
        )
        entry["score"] += fts_weight * _rrf_score(r["rank"])
        entry["sources"].append("fts")
        entry["fts_rank"] = r["rank"]

    for r in semantic_results:
        key = (r["file_id"], int(r.get("page", 0)))
        entry = fused.setdefault(
            key,
            {
                "file_id": r["file_id"],
                "page": key[1],
                "score": 0.0,
                "snippet": r.get("snippet", ""),
                "sources": [],
                "fts_rank": None,
                "semantic_rank": None,
            },
        )
        entry["score"] += semantic_weight * _rrf_score(r["rank"])
        entry["sources"].append("semantic")
        entry["semantic_rank"] = r["rank"]
        # Preferir snippet FTS se já existir (tem highlights)
        if not entry["snippet"]:
            entry["snippet"] = r.get("snippet", "")

    ranked = sorted(fused.values(), key=lambda x: x["score"], reverse=True)

    # --- Optional reranker ---
    if rerank and ranked:
        try:
            ranked = _cross_encoder_rerank(query, ranked[: top_k * 3])
        except Exception as e:
            logger.info("hybrid_search: reranker indisponível: %s", e)

    return ranked[:top_k]


def _semantic_available() -> bool:
    """Checa se Ollama está acessível sem falhar duro."""
    try:
        from pdfsearchable.content_extractors import ollama_health_check

        return bool(ollama_health_check())
    except Exception:
        return False


def _cross_encoder_rerank(query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Re-ranking opcional com cross-encoder (sentence-transformers).
    Usa ms-marco-MiniLM-L-6-v2 como default (pequeno, 80MB, efectivo).
    Se sentence-transformers não estiver instalado, retorna candidatos como estão.
    """
    try:
        from sentence_transformers import CrossEncoder  # type: ignore
    except ImportError:
        return candidates

    model_name = os.environ.get(
        "PDFSEARCHABLE_RERANK_MODEL",
        "cross-encoder/ms-marco-MiniLM-L-6-v2",
    )
    try:
        ce = CrossEncoder(model_name)
    except Exception as e:
        logger.warning("cross_encoder: falha ao carregar %s: %s", model_name, e)
        return candidates

    pairs = [(query, c.get("snippet", "") or "") for c in candidates]
    try:
        scores = ce.predict(pairs)
    except Exception as e:
        logger.warning("cross_encoder: predict falhou: %s", e)
        return candidates

    for c, s in zip(candidates, scores):
        c["rerank_score"] = float(s)

    return sorted(candidates, key=lambda x: x.get("rerank_score", 0.0), reverse=True)


def rrf_fuse(
    *ranked_lists: list[tuple[Any, ...]],
    key_fn=None,
    weights: list[float] | None = None,
    k: int = RRF_K,
) -> list[tuple[Any, float]]:
    """
    Utilitário genérico de RRF. Aceita N listas ordenadas e retorna
    lista fundida de (key, score).

    Args:
        *ranked_lists: listas ordenadas (melhor primeiro).
        key_fn: função que extrai a chave de cada item (default = item).
        weights: peso por lista (default = todos 1.0).
        k: constante RRF.
    """
    if not ranked_lists:
        return []
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if key_fn is None:
        key_fn = lambda x: x  # noqa: E731

    scores: dict[Any, float] = {}
    for lst, w in zip(ranked_lists, weights):
        for rank, item in enumerate(lst, start=1):
            key = key_fn(item)
            scores[key] = scores.get(key, 0.0) + w * (1.0 / (k + rank))

    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


__all__ = ["hybrid_search", "rrf_fuse", "RRF_K"]
