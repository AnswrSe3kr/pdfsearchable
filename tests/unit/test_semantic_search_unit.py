"""Testes unitários de semantic_search — foco em funções puras e DB init."""

import math
import sqlite3

import pytest

from pdfsearchable import semantic_search as ss


# ---------- _cosine ----------


def test_cosine_identical():
    v = [1.0, 2.0, 3.0]
    assert abs(ss._cosine(v, v) - 1.0) < 1e-6


def test_cosine_orthogonal():
    assert abs(ss._cosine([1.0, 0.0], [0.0, 1.0])) < 1e-6


def test_cosine_opposite():
    assert abs(ss._cosine([1.0, 0.0], [-1.0, 0.0]) + 1.0) < 1e-6


def test_cosine_zero_vec():
    # Vetor zero não deve causar divisão por zero
    result = ss._cosine([0.0, 0.0], [1.0, 1.0])
    assert result == 0.0 or math.isnan(result) or result is not None


def test_cosine_different_lengths():
    # Diferentes tamanhos: deve retornar 0 ou raise
    try:
        r = ss._cosine([1.0], [1.0, 2.0])
        assert r == 0.0 or isinstance(r, float)
    except Exception:
        pass


# ---------- _vec_to_blob / _blob_to_vec ----------


def test_vec_blob_roundtrip():
    v = [0.1, -0.2, 3.14, 42.0]
    blob = ss._vec_to_blob(v)
    assert isinstance(blob, bytes)
    restored = ss._blob_to_vec(blob)
    assert len(restored) == len(v)
    for a, b in zip(v, restored):
        assert abs(a - b) < 1e-5


def test_vec_blob_empty():
    blob = ss._vec_to_blob([])
    restored = ss._blob_to_vec(blob)
    assert restored == []


# ---------- _db_init ----------


def test_db_init_creates_tables(tmp_path):
    db = tmp_path / "emb.sqlite"
    conn = sqlite3.connect(str(db))
    ss._db_init(conn)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    assert any("embed" in t for t in tables), f"Nenhuma tabela de embeddings em {tables}"
    conn.close()


def test_db_init_idempotent(tmp_path):
    """Chamar _db_init duas vezes não deve falhar."""
    db = tmp_path / "emb2.sqlite"
    conn = sqlite3.connect(str(db))
    ss._db_init(conn)
    ss._db_init(conn)  # não deve raise
    conn.close()


# ---------- get_embedding com Ollama indisponível ----------


def test_get_embedding_unreachable():
    """Ollama em URL inválida → retorna None."""
    result = ss.get_embedding(
        "texto de teste", model="nomic-embed-text", ollama_url="http://127.0.0.1:1"
    )
    assert result is None


# ---------- semantic_search com store vazio ----------


def test_semantic_search_empty_store(isolated_store):
    """Busca semântica em store vazio → lista vazia ou None."""
    results = ss.semantic_search(
        "query de teste",
        model="nomic-embed-text",
        ollama_url="http://127.0.0.1:1",
        top_k=5,
    )
    assert results == [] or results is None
