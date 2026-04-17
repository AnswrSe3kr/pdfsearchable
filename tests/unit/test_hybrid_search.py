"""Testes de busca híbrida RRF."""

import pytest

from pdfsearchable.hybrid_search import RRF_K, _rrf_score, hybrid_search, rrf_fuse


# ---------- _rrf_score ----------


def test_rrf_score_decreases():
    """RRF deve ser monotonicamente decrescente com rank."""
    assert _rrf_score(1) > _rrf_score(2) > _rrf_score(10) > _rrf_score(100)


def test_rrf_score_at_rank_1():
    assert _rrf_score(1) == 1.0 / (RRF_K + 1)


# ---------- rrf_fuse ----------


def test_rrf_fuse_single_list():
    result = rrf_fuse(["a", "b", "c"])
    assert len(result) == 3
    # "a" tem rank 1, deve ser o primeiro
    assert result[0][0] == "a"
    assert result[-1][0] == "c"


def test_rrf_fuse_two_lists_consensus():
    """Item no topo de ambas as listas deve ter maior score."""
    list1 = ["x", "y", "z"]
    list2 = ["x", "z", "y"]
    result = rrf_fuse(list1, list2)
    assert result[0][0] == "x"


def test_rrf_fuse_weights():
    """Peso maior na lista 1 deve favorecer seu topo."""
    r1 = ["a", "b"]
    r2 = ["b", "a"]
    result = rrf_fuse(r1, r2, weights=[10.0, 1.0])
    assert result[0][0] == "a"


def test_rrf_fuse_empty():
    assert rrf_fuse() == []


def test_rrf_fuse_key_fn():
    items1 = [{"id": 1, "v": "alpha"}, {"id": 2, "v": "beta"}]
    items2 = [{"id": 2, "v": "beta"}, {"id": 1, "v": "alpha"}]
    result = rrf_fuse(items1, items2, key_fn=lambda x: x["id"])
    assert set(k for k, _ in result) == {1, 2}


# ---------- hybrid_search ----------


def test_hybrid_empty_query(isolated_store):
    assert hybrid_search("") == []


def test_hybrid_empty_store(isolated_store):
    """Store vazio: FTS retorna [], semantic retorna [] → resultado vazio."""
    r = hybrid_search("qualquer coisa", enable_semantic=False)
    assert r == []


def test_hybrid_fts_only(isolated_store):
    """Com semantic desligado, não deve tentar Ollama."""
    r = hybrid_search("teste", enable_semantic=False, top_k=5)
    assert isinstance(r, list)


def test_hybrid_top_k_respected(isolated_store, monkeypatch):
    """Top-k limita a saída."""
    # Monkey-patch fts_search para retornar 20 resultados fake
    import pdfsearchable.hybrid_search as hs

    fake_results = [("file_" + str(i), 1, f"snippet {i}") for i in range(20)]

    def fake_fts(q, limit=100):
        return fake_results

    from pdfsearchable import store

    monkeypatch.setattr(store, "fts_search", fake_fts)
    # também precisa interceptar no import dentro da função
    monkeypatch.setitem(__import__("sys").modules, "pdfsearchable.store", store)

    r = hs.hybrid_search("test", enable_semantic=False, top_k=5)
    assert len(r) <= 5


def test_hybrid_result_shape(isolated_store, monkeypatch):
    """Verifica estrutura do dict de resposta."""
    from pdfsearchable import store

    monkeypatch.setattr(
        store,
        "fts_search",
        lambda q, limit=100: [("f1", 2, "hello world")],
    )
    r = hybrid_search("hello", enable_semantic=False)
    assert len(r) == 1
    hit = r[0]
    assert hit["file_id"] == "f1"
    assert hit["page"] == 2
    assert hit["score"] > 0
    assert "fts" in hit["sources"]
    assert hit["fts_rank"] == 1
    assert hit["semantic_rank"] is None


def test_hybrid_consensus_scoring(isolated_store, monkeypatch):
    """Doc presente em ambos os rankings deve ter score maior que só num."""
    from pdfsearchable import store, semantic_search as ss

    monkeypatch.setattr(
        store,
        "fts_search",
        lambda q, limit=100: [("doc_a", 1, "a"), ("doc_b", 1, "b")],
    )
    monkeypatch.setattr(
        ss,
        "semantic_search",
        lambda q, **kw: [
            {"file_id": "doc_a", "page": 1, "snippet": "a", "similarity": 0.9},
            {"file_id": "doc_c", "page": 1, "snippet": "c", "similarity": 0.8},
        ],
    )
    r = hybrid_search("q", enable_semantic=True, top_k=5)
    by_id = {h["file_id"]: h for h in r}
    # doc_a está em ambos — deve ter score maior
    assert by_id["doc_a"]["score"] > by_id["doc_b"]["score"]
    assert by_id["doc_a"]["score"] > by_id["doc_c"]["score"]
    assert set(by_id["doc_a"]["sources"]) == {"fts", "semantic"}


# ---------- _semantic_available (lines 91-92, 99) ----------


def test_semantic_available_health_check_true(monkeypatch):
    """_semantic_available returns True when ollama_health_check returns True (lines 91-92)."""
    import pdfsearchable.content_extractors as ce

    monkeypatch.setattr(ce, "ollama_health_check", lambda: True)
    from pdfsearchable.hybrid_search import _semantic_available

    assert _semantic_available() is True


def test_semantic_available_health_check_false(monkeypatch):
    """_semantic_available returns False when ollama_health_check returns False (lines 91-92)."""
    import pdfsearchable.content_extractors as ce

    monkeypatch.setattr(ce, "ollama_health_check", lambda: False)
    from pdfsearchable.hybrid_search import _semantic_available

    assert _semantic_available() is False


def test_semantic_available_exception(monkeypatch):
    """_semantic_available returns False on exception (line 92 except)."""
    import pdfsearchable.content_extractors as ce

    monkeypatch.setattr(
        ce, "ollama_health_check", lambda: (_ for _ in ()).throw(RuntimeError("fail"))
    )
    from pdfsearchable.hybrid_search import _semantic_available

    assert _semantic_available() is False


# ---------- hybrid_search — semantic branch (lines 99-117) ----------


def test_hybrid_semantic_auto_detect_false(isolated_store, monkeypatch):
    """enable_semantic=None + _semantic_available()=False → no semantic (line 99)."""
    import pdfsearchable.hybrid_search as hs

    monkeypatch.setattr(hs, "_semantic_available", lambda: False)
    r = hs.hybrid_search("query text", enable_semantic=None, top_k=5)
    assert isinstance(r, list)


def test_hybrid_semantic_auto_detect_true(isolated_store, monkeypatch):
    """enable_semantic=None + _semantic_available()=True → tries semantic (lines 99-117)."""
    import pdfsearchable.hybrid_search as hs
    from pdfsearchable import store

    monkeypatch.setattr(hs, "_semantic_available", lambda: True)
    monkeypatch.setattr(store, "fts_search", lambda q, limit=100: [])

    import sys
    import types

    fake_ss = types.ModuleType("pdfsearchable.semantic_search")
    fake_ss.semantic_search = lambda q, **kw: [
        {"file_id": "sem1", "page": 1, "snippet": "sem text", "similarity": 0.8},
    ]
    monkeypatch.setitem(sys.modules, "pdfsearchable.semantic_search", fake_ss)

    r = hs.hybrid_search("query", enable_semantic=None, top_k=5)
    assert any(h["file_id"] == "sem1" for h in r)


def test_hybrid_semantic_failure_fallback(isolated_store, monkeypatch):
    """Semantic search raises → warning logged, only FTS results returned (line 116-117)."""
    import pdfsearchable.hybrid_search as hs
    from pdfsearchable import store

    monkeypatch.setattr(hs, "_semantic_available", lambda: True)
    monkeypatch.setattr(store, "fts_search", lambda q, limit=100: [("fts1", 1, "fts snippet")])

    import sys
    import types

    fake_ss = types.ModuleType("pdfsearchable.semantic_search")
    fake_ss.semantic_search = lambda q, **kw: (_ for _ in ()).throw(RuntimeError("sem fail"))
    monkeypatch.setitem(sys.modules, "pdfsearchable.semantic_search", fake_ss)

    r = hs.hybrid_search("query", enable_semantic=True, top_k=5)
    # FTS result still present
    assert any(h["file_id"] == "fts1" for h in r)


def test_hybrid_fts_exception_logs_warning(isolated_store, monkeypatch):
    """FTS raises → warning logged, result list is empty (line 91-92 in hybrid_search)."""
    import pdfsearchable.hybrid_search as hs
    from pdfsearchable import store

    monkeypatch.setattr(
        store, "fts_search", lambda q, limit=100: (_ for _ in ()).throw(RuntimeError("fts fail"))
    )
    r = hs.hybrid_search("query", enable_semantic=False, top_k=5)
    assert r == []


# ---------- hybrid_search — semantic snippet prefer FTS (lines 152-153) ----------


def test_hybrid_semantic_snippet_uses_fts_if_available(isolated_store, monkeypatch):
    """When FTS and semantic both match, FTS snippet is preferred (line 152-153)."""
    from pdfsearchable import store, semantic_search as ss

    monkeypatch.setattr(
        store, "fts_search", lambda q, limit=100: [("doc_shared", 1, "fts_snippet_here")]
    )
    monkeypatch.setattr(
        ss,
        "semantic_search",
        lambda q, **kw: [
            {"file_id": "doc_shared", "page": 1, "snippet": "sem_snippet", "similarity": 0.9},
        ],
    )
    r = hybrid_search("q", enable_semantic=True, top_k=5)
    # doc_shared in both; snippet from FTS should be kept (non-empty FTS snippet)
    hit = next(h for h in r if h["file_id"] == "doc_shared")
    assert hit["snippet"] == "fts_snippet_here"


def test_hybrid_semantic_snippet_falls_back_when_fts_empty(isolated_store, monkeypatch):
    """When FTS snippet is empty, semantic snippet is used (line 152-153)."""
    from pdfsearchable import store

    monkeypatch.setattr(store, "fts_search", lambda q, limit=100: [])
    import pdfsearchable.hybrid_search as hs

    import sys
    import types

    fake_ss = types.ModuleType("pdfsearchable.semantic_search")
    fake_ss.semantic_search = lambda q, **kw: [
        {"file_id": "sem_only", "page": 2, "snippet": "sem_snippet_used", "similarity": 0.7},
    ]
    monkeypatch.setitem(sys.modules, "pdfsearchable.semantic_search", fake_ss)

    r = hs.hybrid_search("q", enable_semantic=True, top_k=5)
    hit = next(h for h in r if h["file_id"] == "sem_only")
    assert hit["snippet"] == "sem_snippet_used"


# ---------- _cross_encoder_rerank (lines 176-207) ----------


def test_hybrid_rerank_called_when_true(isolated_store, monkeypatch):
    """rerank=True invokes _cross_encoder_rerank (lines 158-162)."""
    from pdfsearchable import store
    import pdfsearchable.hybrid_search as hs

    monkeypatch.setattr(
        store,
        "fts_search",
        lambda q, limit=100: [(f"doc{i}", 1, f"snippet {i}") for i in range(10)],
    )

    rerank_called = {"n": 0}

    def fake_rerank(query, candidates):
        rerank_called["n"] += 1
        return candidates  # passthrough

    monkeypatch.setattr(hs, "_cross_encoder_rerank", fake_rerank)
    r = hs.hybrid_search("q", enable_semantic=False, top_k=5, rerank=True)
    assert rerank_called["n"] == 1


def test_hybrid_rerank_exception_logged(isolated_store, monkeypatch):
    """rerank=True with _cross_encoder_rerank raising → logged info (lines 159-162)."""
    from pdfsearchable import store
    import pdfsearchable.hybrid_search as hs

    monkeypatch.setattr(
        store, "fts_search", lambda q, limit=100: [(f"doc{i}", 1, f"s{i}") for i in range(5)]
    )

    def fail_rerank(query, candidates):
        raise ImportError("no model")

    monkeypatch.setattr(hs, "_cross_encoder_rerank", fail_rerank)
    r = hs.hybrid_search("q", enable_semantic=False, top_k=3, rerank=True)
    # Should still return results even if reranker fails
    assert len(r) <= 3


def test_cross_encoder_rerank_no_sentence_transformers(monkeypatch):
    """_cross_encoder_rerank returns candidates unchanged when library missing (lines 182-185)."""
    import sys
    import pdfsearchable.hybrid_search as hs

    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    candidates = [{"file_id": "a", "snippet": "text a"}, {"file_id": "b", "snippet": "text b"}]
    result = hs._cross_encoder_rerank("query", candidates)
    assert result == candidates


def test_cross_encoder_rerank_model_load_failure(monkeypatch):
    """_cross_encoder_rerank returns candidates when CrossEncoder load fails (lines 193-195)."""
    import sys
    import types
    import pdfsearchable.hybrid_search as hs

    fake_st = types.ModuleType("sentence_transformers")

    class BrokenCrossEncoder:
        def __init__(self, name):
            raise RuntimeError("cannot load model")

    fake_st.CrossEncoder = BrokenCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

    candidates = [{"file_id": "x", "snippet": "txt"}]
    result = hs._cross_encoder_rerank("query", candidates)
    assert result == candidates


def test_cross_encoder_rerank_predict_failure(monkeypatch):
    """_cross_encoder_rerank returns candidates when predict fails (lines 200-202)."""
    import sys
    import types
    import pdfsearchable.hybrid_search as hs

    fake_st = types.ModuleType("sentence_transformers")

    class FakeCrossEncoder:
        def __init__(self, name):
            pass

        def predict(self, pairs):
            raise RuntimeError("predict failed")

    fake_st.CrossEncoder = FakeCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

    candidates = [{"file_id": "y", "snippet": "txt"}]
    result = hs._cross_encoder_rerank("query", candidates)
    assert result == candidates


def test_cross_encoder_rerank_success(monkeypatch):
    """_cross_encoder_rerank reorders candidates by score (lines 204-207)."""
    import sys
    import types
    import pdfsearchable.hybrid_search as hs

    fake_st = types.ModuleType("sentence_transformers")

    class FakeCrossEncoder:
        def __init__(self, name):
            pass

        def predict(self, pairs):
            # Return score 1.0 for second, 0.5 for first → should reverse order
            return [0.5, 1.0]

    fake_st.CrossEncoder = FakeCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

    candidates = [
        {"file_id": "low", "snippet": "low rel"},
        {"file_id": "high", "snippet": "high rel"},
    ]
    result = hs._cross_encoder_rerank("query", candidates)
    assert result[0]["file_id"] == "high"
    assert result[0]["rerank_score"] == 1.0


# ---------- hybrid_search semantic hit with page_num key (line 111) ----------


def test_hybrid_semantic_page_num_key(isolated_store, monkeypatch):
    """semantic_search hit uses page_num key when page is absent (line 111)."""
    from pdfsearchable import store
    import pdfsearchable.hybrid_search as hs
    import sys
    import types

    monkeypatch.setattr(store, "fts_search", lambda q, limit=100: [])
    fake_ss = types.ModuleType("pdfsearchable.semantic_search")
    fake_ss.semantic_search = lambda q, **kw: [
        {"file_id": "pn_doc", "page_num": 5, "text": "page_num text", "score": 0.6},
    ]
    monkeypatch.setitem(sys.modules, "pdfsearchable.semantic_search", fake_ss)

    r = hs.hybrid_search("q", enable_semantic=True, top_k=5)
    hit = next(h for h in r if h["file_id"] == "pn_doc")
    assert hit["page"] == 5


def test_hybrid_rerank_exception_swallowed(isolated_store, monkeypatch):
    """hybrid_search: reranker exception is swallowed (line 153/161-162)."""
    from pdfsearchable import store
    import pdfsearchable.hybrid_search as hs

    monkeypatch.setattr(
        store,
        "fts_search",
        lambda q, limit=100: [
            ("doc1", 1, "snippet text"),
        ],
    )

    def bad_reranker(query, candidates):
        raise RuntimeError("no sentence-transformers")

    import unittest.mock as mock

    with mock.patch.object(hs, "_cross_encoder_rerank", bad_reranker):
        results = hs.hybrid_search("query", rerank=True, top_k=5)
    assert len(results) >= 1  # still returns results after exception


def test_hybrid_semantic_fills_empty_fts_snippet(isolated_store, monkeypatch):
    """Semantic result fills empty FTS snippet (line 153)."""
    import sys
    import types
    from pdfsearchable import store
    import pdfsearchable.hybrid_search as hs

    # FTS returns a hit with empty snippet for doc1
    monkeypatch.setattr(
        store,
        "fts_search",
        lambda q, limit=100: [
            ("doc1", 1, ""),  # empty snippet
        ],
    )

    # Semantic returns same doc with a non-empty snippet
    fake_ss = types.ModuleType("pdfsearchable.semantic_search")
    fake_ss.semantic_search = lambda q, **kw: [
        {"file_id": "doc1", "page": 1, "snippet": "semantic snippet text", "score": 0.9},
    ]
    monkeypatch.setitem(sys.modules, "pdfsearchable.semantic_search", fake_ss)

    results = hs.hybrid_search("q", enable_semantic=True, top_k=5)
    hit = next((r for r in results if r["file_id"] == "doc1"), None)
    assert hit is not None
    assert hit["snippet"] == "semantic snippet text"
