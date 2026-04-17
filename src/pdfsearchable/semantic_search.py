"""
Busca semântica via embeddings Ollama: geração, armazenamento e consulta de embeddings.

Refactora a lógica dispersa em cli.py para um módulo reutilizável, sem dependências
externas além de stdlib + sqlite3 + struct + urllib.
"""

import json
import math
import sqlite3
import struct
import threading
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pdfsearchable.audit import audit, get_logger
from pdfsearchable.store import STORE_DIR, load_index, load_file_text, load_page_text

_log = get_logger("pdfsearchable.semantic_search")

# RLock: reentrante para suportar chamadas aninhadas da mesma thread.
# A DB usa WAL mode — permite leituras concorrentes com um escritor activo.
_emb_lock = threading.RLock()

# Schema: file_id é chave primária; embedding é float32 empacotado com struct.pack.
_CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS embeddings "
    "(file_id TEXT PRIMARY KEY, embedding BLOB, model TEXT, indexed_at TEXT)"
)
# Tabela de chunks por página (RAG fino): (file_id, page) composto; embedding por página.
_CREATE_CHUNKS_SQL = (
    "CREATE TABLE IF NOT EXISTS embeddings_chunks "
    "(file_id TEXT, page INTEGER, embedding BLOB, model TEXT, indexed_at TEXT, "
    "PRIMARY KEY (file_id, page, model))"
)
_CREATE_CHUNKS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_chunks_file ON embeddings_chunks(file_id)"
)
_WAL_PRAGMAS = ("PRAGMA journal_mode=WAL", "PRAGMA synchronous=NORMAL")


# ---------------------------------------------------------------------------
# Funções auxiliares privadas
# ---------------------------------------------------------------------------


def _embeddings_db_path() -> Path:
    """Retorna o caminho para embeddings.sqlite dentro de STORE_DIR."""
    return STORE_DIR / "embeddings.sqlite"


def _db_init(conn: sqlite3.Connection) -> None:
    """Aplica WAL mode e cria a tabela se não existir."""
    for pragma in _WAL_PRAGMAS:
        conn.execute(pragma)
    conn.execute(_CREATE_SQL)
    conn.execute(_CREATE_CHUNKS_SQL)
    conn.execute(_CREATE_CHUNKS_IDX)
    conn.commit()


def _cosine(a: list[float], b: list[float]) -> float:
    """
    Similaridade cosine entre dois vectores.
    Retorna 0.0 se qualquer norma for zero (vector nulo).
    """
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _vec_to_blob(vec: list[float]) -> bytes:
    """Serializa vector float32 para bytes (struct.pack)."""
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes) -> list[float]:
    """Desserializa bytes para vector float32."""
    if len(blob) % 4 != 0:
        raise ValueError(f"Blob de embedding corrompido: {len(blob)} bytes não divisível por 4")
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _load_all_embeddings(model: str | None = None) -> dict[str, list[float]]:
    """
    Carrega todos os embeddings da DB em memória como dict {file_id: vector}.
    Se model for especificado, filtra por esse modelo.
    Retorna dict vazio se a DB não existir ou não houver linhas.
    """
    db_path = _embeddings_db_path()
    if not db_path.exists():
        return {}
    with _emb_lock:
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            _db_init(conn)
            if model:
                rows = conn.execute(
                    "SELECT file_id, embedding FROM embeddings WHERE model=?", (model,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT file_id, embedding FROM embeddings"
                ).fetchall()
        finally:
            conn.close()
    result: dict[str, list[float]] = {}
    for fid, blob in rows:
        try:
            result[fid] = _blob_to_vec(blob)
        except Exception as exc:
            _log.debug("Falha ao desserializar embedding de %s: %s", fid, exc)
    return result


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosseno entre dois vectores. Retorna 0.0 se dimensões diferem ou norma nula."""
    if len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    import math as _m
    return dot / (_m.sqrt(na) * _m.sqrt(nb))


def find_semantic_duplicates(
    threshold: float = 0.98,
    model: str = "nomic-embed-text",
) -> list[dict[str, Any]]:
    """
    Devolve pares de documentos com similaridade cosine >= threshold
    (texto semanticamente duplicado, mesmo com content_hash diferentes).

    Requer que embeddings tenham sido gerados previamente
    (``pdfsearchable embed``). Retorna lista de dicts com:
      {a: file_id, b: file_id, score: float}
    """
    embs = _load_all_embeddings(model=model)
    if len(embs) < 2:
        return []
    items = list(embs.items())
    pairs: list[dict[str, Any]] = []
    for i in range(len(items)):
        fid_a, vec_a = items[i]
        for j in range(i + 1, len(items)):
            fid_b, vec_b = items[j]
            s = _cosine(vec_a, vec_b)
            if s >= threshold:
                pairs.append({"a": fid_a, "b": fid_b, "score": round(s, 4)})
    pairs.sort(key=lambda p: p["score"], reverse=True)
    return pairs


def get_embedding(text: str, model: str, ollama_url: str) -> list[float] | None:
    """
    Solicita o embedding de *text* ao Ollama e retorna o vector float32.

    Parâmetros:
        text: texto a embedir (truncado a 8000 caracteres internamente).
        model: nome do modelo Ollama (ex.: 'nomic-embed-text').
        ollama_url: URL base do Ollama (ex.: 'http://localhost:11434').

    Retorna None se o pedido falhar ou o modelo não devolver vector.
    """
    url = ollama_url.rstrip("/") + "/api/embeddings"
    payload = json.dumps({"model": model, "prompt": text[:8000]}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec
            data = json.loads(resp.read())
            vec: list[float] | None = data.get("embedding")
            if not vec:
                _log.warning("get_embedding: modelo '%s' não devolveu vector.", model)
            return vec or None
    except urllib.error.URLError as exc:
        _log.warning("get_embedding: erro de rede ao chamar Ollama (%s): %s", url, exc)
        return None
    except Exception as exc:
        _log.warning("get_embedding: erro inesperado: %s", exc)
        return None


def embed_document(
    file_id: str,
    text: str,
    model: str,
    ollama_url: str,
) -> bool:
    """
    Gera e guarda o embedding de um documento em embeddings.sqlite.

    Parâmetros:
        file_id: identificador do documento (16 hex).
        text: texto completo do documento.
        model: modelo Ollama de embeddings.
        ollama_url: URL base do Ollama.

    Retorna True se o embedding foi gerado e persistido com sucesso.
    """
    vec = get_embedding(text, model, ollama_url)
    if not vec:
        return False

    blob = _vec_to_blob(vec)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db_path = _embeddings_db_path()

    # Garantir que STORE_DIR existe antes de criar a DB
    STORE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)

    with _emb_lock:
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            _db_init(conn)
            conn.execute(
                "INSERT OR REPLACE INTO embeddings(file_id, embedding, model, indexed_at) "
                "VALUES (?, ?, ?, ?)",
                (file_id, blob, model, now),
            )
            conn.commit()
            return True
        except sqlite3.Error as exc:
            _log.warning("embed_document: falha ao gravar embedding de %s: %s", file_id, exc)
            return False
        finally:
            conn.close()


def embed_document_pages(
    file_id: str,
    num_pages: int,
    model: str,
    ollama_url: str,
    *,
    max_chars_per_chunk: int = 1500,
) -> int:
    """
    Gera embeddings por página (chunks RAG finos) e persiste em embeddings_chunks.

    Cada página é truncada a max_chars_per_chunk antes do embedding. Para páginas
    vazias ou ausentes o chunk é ignorado. Retorna o número de chunks gravados.
    """
    if num_pages <= 0:
        return 0
    STORE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    db_path = _embeddings_db_path()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    saved = 0
    rows: list[tuple[str, int, bytes, str, str]] = []
    for p in range(1, num_pages + 1):
        try:
            ptext = load_page_text(file_id, p)
        except Exception as _e:
            _log.debug("embed_document_pages: falha ao ler página %d de %s: %s", p, file_id, _e)
            continue
        ptext = (ptext or "").strip()
        if len(ptext) < 30:
            continue
        vec = get_embedding(ptext[:max_chars_per_chunk], model, ollama_url)
        if not vec:
            continue
        rows.append((file_id, p, _vec_to_blob(vec), model, now))
    if not rows:
        audit("embed_chunks_empty", {"file_id": file_id, "num_pages": num_pages}, level="info")
        return 0
    with _emb_lock:
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            _db_init(conn)
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings_chunks"
                "(file_id, page, embedding, model, indexed_at) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
            saved = len(rows)
        except sqlite3.Error as exc:
            _log.warning("embed_document_pages: falha ao gravar chunks de %s: %s", file_id, exc)
            audit(
                "embed_chunks_error",
                {"file_id": file_id, "error": str(exc)[:200]},
                level="error",
            )
        finally:
            conn.close()
    if saved:
        audit(
            "embed_chunks_saved",
            {"file_id": file_id, "chunks": saved, "model": model},
            level="info",
        )
    return saved


def _load_all_chunks(model: str) -> list[tuple[str, int, list[float]]]:
    """Carrega todos os chunks (file_id, page, vec) para um modelo específico."""
    db_path = _embeddings_db_path()
    if not db_path.exists():
        return []
    with _emb_lock:
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            _db_init(conn)
            rows = conn.execute(
                "SELECT file_id, page, embedding FROM embeddings_chunks WHERE model=?",
                (model,),
            ).fetchall()
        finally:
            conn.close()
    out: list[tuple[str, int, list[float]]] = []
    for fid, page, blob in rows:
        try:
            out.append((fid, int(page), _blob_to_vec(blob)))
        except Exception as _e:
            _log.debug("Falha ao desserializar chunk %s:%s: %s", fid, page, _e)
    return out


def embed_all_documents(
    *,
    force: bool = False,
    model: str = "nomic-embed-text",
    ollama_url: str = "http://localhost:11434",
) -> tuple[int, int]:
    """
    Gera embeddings para todos os documentos no índice.

    Parâmetros:
        force: se True, re-gera embeddings mesmo para documentos já processados.
        model: modelo Ollama de embeddings (padrão: 'nomic-embed-text').
        ollama_url: URL base do Ollama.

    Retorna:
        Tupla (ok, failed) com contagens de sucessos e falhas.
    """
    try:
        idx = load_index()
    except Exception as exc:
        _log.error("embed_all_documents: falha ao carregar índice: %s", exc)
        return (0, 0)

    files: list[dict[str, Any]] = idx.get("files", [])
    if not files:
        _log.info("embed_all_documents: nenhum documento no índice.")
        return (0, 0)

    # Determinar quais documentos já têm embedding (a menos que force=True)
    if not force:
        db_path = _embeddings_db_path()
        existing: set[str] = set()
        if db_path.exists():
            with _emb_lock:
                conn = sqlite3.connect(db_path, timeout=30)
                try:
                    _db_init(conn)
                    rows = conn.execute(
                        "SELECT file_id FROM embeddings WHERE model=?", (model,)
                    ).fetchall()
                    existing = {r[0] for r in rows}
                except sqlite3.Error:
                    existing = set()
                finally:
                    conn.close()
        files = [f for f in files if f.get("id") not in existing]

    ok = 0
    failed = 0
    for f in files:
        fid = f.get("id", "")
        if not fid:
            continue
        text = load_file_text(fid)
        if not text or not text.strip():
            _log.debug("embed_all_documents: sem texto para %s — ignorado.", fid)
            failed += 1
            continue
        success = embed_document(fid, text, model, ollama_url)
        if success:
            ok += 1
            # Também gera chunks por página (RAG fino) — melhor recall e snippets precisos
            try:
                num_pages = int(f.get("num_pages", 0))
                if num_pages > 0:
                    embed_document_pages(fid, num_pages, model, ollama_url)
            except Exception as _e:
                _log.debug("embed_all_documents: falha ao gerar chunks de %s: %s", fid, _e)
        else:
            failed += 1
            _log.warning("embed_all_documents: falha ao embedir %s.", fid)

    _log.info(
        "embed_all_documents: concluído — ok=%d, failed=%d, modelo=%s",
        ok,
        failed,
        model,
    )
    audit(
        "embed_all_documents",
        {"ok": ok, "failed": failed, "model": model, "force": force},
        level="info",
    )
    return (ok, failed)


def semantic_search(
    query: str,
    *,
    model: str,
    ollama_url: str,
    top_k: int = 10,
    doc_type_filter: str | None = None,
) -> list[dict[str, Any]]:
    """
    Pesquisa semântica por similaridade cosine.

    Parâmetros:
        query: texto da pesquisa.
        model: modelo Ollama de embeddings.
        ollama_url: URL base do Ollama.
        top_k: número máximo de resultados a devolver.
        doc_type_filter: filtrar por tipo de documento (ex.: 'contrato').

    Retorna:
        Lista de dicts ordenada por score descendente, cada um com:
        {file_id, name, doc_type, score, snippet}.
        snippet é o início do texto do documento (até 200 caracteres).
    """
    if not query.strip():
        return []

    # Embedding da query
    q_vec = get_embedding(query, model, ollama_url)
    if not q_vec:
        _log.warning("semantic_search: não foi possível obter embedding da query.")
        return []

    # Carregar metadados do índice
    try:
        idx = load_index()
    except Exception as exc:
        _log.error("semantic_search: falha ao carregar índice: %s", exc)
        return []

    files: list[dict[str, Any]] = idx.get("files", [])
    if doc_type_filter:
        files = [f for f in files if f.get("doc_type") == doc_type_filter]
    allowed_ids = {f.get("id") for f in files if f.get("id")}
    id_to_meta: dict[str, dict[str, Any]] = {
        f["id"]: f for f in files if f.get("id")
    }

    if not allowed_ids:
        return []

    # Tentar primeiro chunks por página (RAG fino) — melhor recall e snippet relevante
    chunks = _load_all_chunks(model=model)
    if chunks:
        best_per_doc: dict[str, tuple[float, int]] = {}
        for fid, page, vec in chunks:
            if fid not in allowed_ids:
                continue
            try:
                sim = _cosine(q_vec, vec)
            except Exception as _e:
                _log.debug("semantic_search(chunks): erro ao calcular cosine para %s p%s: %s", fid, page, _e)
                continue
            cur = best_per_doc.get(fid)
            if cur is None or sim > cur[0]:
                best_per_doc[fid] = (sim, page)
        if best_per_doc:
            ranked = sorted(best_per_doc.items(), key=lambda kv: kv[1][0], reverse=True)[:top_k]
            chunk_results: list[dict[str, Any]] = []
            for fid, (sim, page) in ranked:
                meta = id_to_meta.get(fid, {})
                snippet = ""
                try:
                    page_text = load_page_text(fid, page)
                    if page_text:
                        snippet = page_text.strip()[:300]
                except Exception as _e:
                    _log.debug("semantic_search(chunks): falha ao ler página %d de %s: %s", page, fid, _e)
                chunk_results.append(
                    {
                        "file_id": fid,
                        "name": meta.get("name", fid),
                        "doc_type": meta.get("doc_type") or "documento",
                        "score": round(sim, 6),
                        "snippet": snippet,
                        "page": page,
                    }
                )
            audit(
                "semantic_search_chunks",
                {
                    "query_len": len(query),
                    "hits": len(chunk_results),
                    "top_score": chunk_results[0]["score"] if chunk_results else None,
                    "doc_type_filter": doc_type_filter,
                },
                level="info",
            )
            return chunk_results

    # Fallback: embeddings por documento (legacy)
    all_embeddings = _load_all_embeddings(model=model)
    scores: list[tuple[float, str]] = []
    for fid, vec in all_embeddings.items():
        if fid not in allowed_ids:
            continue
        try:
            sim = _cosine(q_vec, vec)
            scores.append((sim, fid))
        except Exception as exc:
            _log.debug("semantic_search: erro ao calcular cosine para %s: %s", fid, exc)

    if not scores:
        return []

    scores.sort(reverse=True)
    top = scores[:top_k]

    results: list[dict[str, Any]] = []
    for sim, fid in top:
        meta = id_to_meta.get(fid, {})
        # Snippet: início do texto do documento truncado a 200 caracteres
        snippet = ""
        try:
            text = load_file_text(fid)
            if text:
                snippet = text.strip()[:200]
        except Exception as _e:
            _log.debug("semantic_search: falha ao carregar texto de %s: %s", fid, _e)
        results.append(
            {
                "file_id": fid,
                "name": meta.get("name", fid),
                "doc_type": meta.get("doc_type") or "documento",
                "score": round(sim, 6),
                "snippet": snippet,
            }
        )

    return results


def find_semantic_duplicate_groups(
    threshold: float = 0.92,
    *,
    model: str = "nomic-embed-text",
) -> list[list[str]]:
    """
    Encontra grupos de documentos com similaridade cosine >= threshold (pairwise O(n²)).

    Parâmetros:
        threshold: limiar de similaridade (0-1); padrão 0.92.
        model: modelo Ollama usado para filtrar embeddings da DB.

    Retorna:
        Lista de grupos (listas de file_ids) com similaridade >= threshold.
        Documentos com apenas uma ocorrência não são incluídos.
        Adequado para índices com até ~10 000 documentos.
    """
    all_embeddings = _load_all_embeddings(model=model)
    if len(all_embeddings) < 2:
        return []

    items = list(all_embeddings.items())  # [(fid, vec), ...]
    n = len(items)

    # Union-Find para agrupar documentos similares
    parent = list(range(n))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(x: int, y: int) -> None:
        rx, ry = _find(x), _find(y)
        if rx != ry:
            parent[rx] = ry

    for i in range(n):
        for j in range(i + 1, n):
            try:
                sim = _cosine(items[i][1], items[j][1])
                if sim >= threshold:
                    _union(i, j)
            except Exception as _e:
                _log.debug("find_semantic_duplicates: erro ao calcular cosine para par (%s, %s): %s", items[i][0], items[j][0], _e)
                continue

    # Recolher grupos
    groups: dict[int, list[str]] = defaultdict(list)
    for i, (fid, _) in enumerate(items):
        groups[_find(i)].append(fid)

    return [g for g in groups.values() if len(g) >= 2]
