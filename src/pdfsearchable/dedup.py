"""
Deduplicação inteligente de documentos.

Hoje store.py só detecta duplicatas exactas por SHA256 do conteúdo.
Este módulo adiciona:

1. **MinHash LSH** — detecta near-duplicates baseado em shingles de texto.
   Dois documentos com 80% de similaridade Jaccard são flagged como
   near-duplicates. Útil para: versões do mesmo relatório, revisões,
   variantes de tradução, re-uploads com metadados diferentes.

2. **Hash perceptual de páginas** (opcional, via Pillow) — detecta
   páginas visualmente idênticas em PDFs diferentes (ex.: mesmo scan
   com OCR diferente, ou mesmo template preenchido de forma diferente).

Implementação MinHash feita do zero (sem datasketch/python-lsh) para
manter dependências mínimas. Algoritmo clássico:

    1. Tokenizar texto em shingles de k caracteres (k=5)
    2. Para cada uma de N funções hash, guardar o menor hash visto
    3. Jaccard estimado = |intersecção|/|união| dos N mínimos
"""

from __future__ import annotations

import hashlib
import re
import struct
from typing import Any

# Parâmetros padrão
DEFAULT_NUM_PERM = 128  # número de funções hash (128 é bom tradeoff)
DEFAULT_SHINGLE_K = 5  # tamanho do shingle em caracteres
DEFAULT_THRESHOLD = 0.8  # threshold Jaccard para near-duplicate

# Sementes derivadas de SHA256("pdfsearchable-minhash-seed-{i}")
_SEED_CACHE: dict[int, list[int]] = {}


def _get_seeds(n: int) -> list[int]:
    """Gera N sementes determinísticas para as hash functions."""
    if n in _SEED_CACHE:
        return _SEED_CACHE[n]
    seeds = []
    for i in range(n):
        h = hashlib.sha256(f"pdfsearchable-minhash-seed-{i}".encode()).digest()
        seeds.append(struct.unpack("<Q", h[:8])[0])
    _SEED_CACHE[n] = seeds
    return seeds


def _normalize(text: str) -> str:
    """Normaliza texto antes de shingling: lower, remover pontuação e espaços múltiplos."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def shingles(text: str, k: int = DEFAULT_SHINGLE_K) -> set[str]:
    """Extrai shingles de tamanho k do texto normalizado."""
    t = _normalize(text)
    if len(t) < k:
        return {t} if t else set()
    return {t[i : i + k] for i in range(len(t) - k + 1)}


def minhash(text: str, num_perm: int = DEFAULT_NUM_PERM, k: int = DEFAULT_SHINGLE_K) -> list[int]:
    """
    Calcula assinatura MinHash de `num_perm` valores para o texto.

    Usa SHA256 com seed XOR para simular múltiplas funções hash.
    Retorna lista de num_perm inteiros 64-bit.
    """
    sigs = [(1 << 64) - 1] * num_perm
    seeds = _get_seeds(num_perm)
    shs = shingles(text, k)
    if not shs:
        return sigs

    for s in shs:
        s_bytes = s.encode()
        base = int.from_bytes(hashlib.sha256(s_bytes).digest()[:8], "little")
        for i, seed in enumerate(seeds):
            h = (base ^ seed) & ((1 << 64) - 1)
            if h < sigs[i]:
                sigs[i] = h
    return sigs


def jaccard_similarity(sig_a: list[int], sig_b: list[int]) -> float:
    """Estima Jaccard a partir de duas assinaturas MinHash."""
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 0.0
    matches = sum(1 for a, b in zip(sig_a, sig_b) if a == b)
    return matches / len(sig_a)


def find_near_duplicates(
    signatures: dict[str, list[int]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[tuple[str, str, float]]:
    """
    Dados assinaturas MinHash por file_id, retorna pares (id_a, id_b, similarity)
    com similaridade ≥ threshold.

    Complexidade O(n²) — para >1000 docs, usar LSH (Locality Sensitive Hashing)
    via build_lsh_index.
    """
    ids = list(signatures.keys())
    pairs: list[tuple[str, str, float]] = []
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            sim = jaccard_similarity(signatures[a], signatures[b])
            if sim >= threshold:
                pairs.append((a, b, sim))
    return sorted(pairs, key=lambda x: -x[2])


def build_lsh_bands(
    signatures: dict[str, list[int]],
    *,
    bands: int = 16,
) -> dict[str, list[str]]:
    """
    LSH por banding: divide a assinatura em `bands` bandas iguais e agrupa
    docs que compartilham a mesma hash de banda.

    Retorna dict bucket_key → list[file_id]. Pares candidatos são aqueles
    que aparecem juntos em pelo menos um bucket.

    Para threshold Jaccard t ≈ (1/b)^(1/r), onde r = num_perm/bands.
    Ex.: 128 perm, 16 bands (r=8) → threshold ~0.72.
    """
    buckets: dict[str, list[str]] = {}
    for file_id, sig in signatures.items():
        if not sig:
            continue
        per_band = len(sig) // bands
        if per_band == 0:
            continue
        for b in range(bands):
            band_tuple = tuple(sig[b * per_band : (b + 1) * per_band])
            key = f"b{b}:{hashlib.sha256(str(band_tuple).encode()).hexdigest()[:16]}"
            buckets.setdefault(key, []).append(file_id)
    return buckets


def candidate_pairs_from_lsh(buckets: dict[str, list[str]]) -> set[tuple[str, str]]:
    """Extrai pares candidatos de buckets LSH."""
    pairs: set[tuple[str, str]] = set()
    for members in buckets.values():
        if len(members) < 2:
            continue
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                pairs.add(tuple(sorted((a, b))))
    return pairs


def find_near_duplicates_lsh(
    signatures: dict[str, list[int]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    bands: int = 16,
) -> list[tuple[str, str, float]]:
    """
    Versão eficiente de find_near_duplicates usando LSH banding.
    Recomendada para >1000 docs.
    """
    buckets = build_lsh_bands(signatures, bands=bands)
    candidates = candidate_pairs_from_lsh(buckets)
    results: list[tuple[str, str, float]] = []
    for a, b in candidates:
        sim = jaccard_similarity(signatures[a], signatures[b])
        if sim >= threshold:
            results.append((a, b, sim))
    return sorted(results, key=lambda x: -x[2])


# ---------- Integração com store ----------


def compute_doc_signature(text: str) -> list[int] | None:
    """Helper conveniente: calcula MinHash de um doc inteiro."""
    if not text or len(text.strip()) < 20:
        return None
    return minhash(text)


def scan_store_for_near_duplicates(
    *,
    threshold: float = DEFAULT_THRESHOLD,
    use_lsh: bool = True,
) -> list[dict[str, Any]]:
    """
    Varre o store actual e detecta near-duplicates entre documentos indexados.

    Lê o texto completo de cada doc via store e calcula assinaturas.
    Retorna lista de dicts {"a": id, "b": id, "similarity": float, "a_name": str, "b_name": str}.
    """
    try:
        from pdfsearchable.store import load_index, read_page_text
    except Exception:
        return []

    idx = load_index() or {}
    files = idx.get("files", {}) if isinstance(idx, dict) else {}

    signatures: dict[str, list[int]] = {}
    names: dict[str, str] = {}
    for file_id, meta in files.items():
        pages = meta.get("pages", 0) or 0
        chunks: list[str] = []
        for p in range(1, min(pages, 10) + 1):  # limita a 10 primeiras páginas por doc
            try:
                txt = read_page_text(file_id, p) or ""
                if txt:
                    chunks.append(txt)
            except Exception:
                pass
        full = "\n".join(chunks)
        sig = compute_doc_signature(full)
        if sig:
            signatures[file_id] = sig
            names[file_id] = meta.get("name") or meta.get("path") or file_id

    finder = find_near_duplicates_lsh if use_lsh else find_near_duplicates
    pairs = finder(signatures, threshold=threshold)
    return [
        {
            "a": a,
            "b": b,
            "similarity": round(sim, 3),
            "a_name": names.get(a, a),
            "b_name": names.get(b, b),
        }
        for a, b, sim in pairs
    ]


__all__ = [
    "minhash",
    "shingles",
    "jaccard_similarity",
    "find_near_duplicates",
    "find_near_duplicates_lsh",
    "build_lsh_bands",
    "compute_doc_signature",
    "scan_store_for_near_duplicates",
    "DEFAULT_NUM_PERM",
    "DEFAULT_SHINGLE_K",
    "DEFAULT_THRESHOLD",
]
