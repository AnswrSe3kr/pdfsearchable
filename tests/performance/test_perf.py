"""
Testes de Performance e Carga.
Medem tempo de resposta, throughput e estabilidade sob carga.
Não requerem recursos externos (offline).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import fitz
import pytest

from pdfsearchable.report import build_top_words, build_bigrams
from pdfsearchable.search import search_with_masks


# ---------------------------------------------------------------------------
# Benchmarks de texto / busca
# ---------------------------------------------------------------------------


@pytest.mark.performance
@pytest.mark.functional
def test_search_performance_large_text() -> None:
    """Busca em texto grande deve responder em < 1s."""
    text = "termo alvo " + "palavra " * 5000 + " termo alvo"
    start = time.perf_counter()
    list(search_with_masks("termo", text, use_masks=False))
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"Busca demorou {elapsed:.3f}s (limite: 1.0s)"


@pytest.mark.performance
def test_top_words_performance() -> None:
    """build_top_words em 2000 palavras deve completar em < 2s."""
    text = " ".join(["palavra"] * 2000 + ["rara"] * 100)
    start = time.perf_counter()
    result = build_top_words(text, top_n=50)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"build_top_words demorou {elapsed:.3f}s"
    assert len(result) > 0


@pytest.mark.performance
def test_bigrams_performance() -> None:
    """build_bigrams em 1000 pares deve completar em < 2s."""
    text = "nota fiscal " * 500 + "outro texto " * 500
    start = time.perf_counter()
    result = build_bigrams(text, top_n=30)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"build_bigrams demorou {elapsed:.3f}s"
    assert isinstance(result, list)


@pytest.mark.performance
def test_search_throughput_100_queries() -> None:
    """100 buscas sequenciais em texto médio devem completar em < 3s."""
    text = " ".join([f"palavra{i}" for i in range(500)]) + " alvo " * 50
    start = time.perf_counter()
    for _ in range(100):
        list(search_with_masks("alvo", text, use_masks=False))
    elapsed = time.perf_counter() - start
    assert elapsed < 3.0, f"100 buscas demoraram {elapsed:.3f}s (limite: 3s)"


# ---------------------------------------------------------------------------
# Performance do índice JSON
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_load_index_performance_with_100_files(isolated_store, monkeypatch) -> None:
    """load_index com 100 documentos deve completar em < 0.5s."""
    import pdfsearchable.store as store_mod

    monkeypatch.chdir(isolated_store)

    files = [
        {
            "id": f"file{i:04d}",
            "name": f"documento_{i:04d}.pdf",
            "path": f"/docs/documento_{i:04d}.pdf",
            "pages": 10,
            "content_hash": f"hash{i:064d}",
            "indexed_at": "2024-01-01T00:00:00Z",
        }
        for i in range(100)
    ]
    store_mod.save_index({"files": files, "version": 1})

    start = time.perf_counter()
    for _ in range(50):  # 50 loads
        store_mod.load_index()
    elapsed = time.perf_counter() - start
    assert elapsed < 0.5, f"50x load_index demorou {elapsed:.3f}s (limite: 0.5s)"


@pytest.mark.performance
def test_save_index_performance(isolated_store, monkeypatch) -> None:
    """save_index com 500 documentos deve completar em < 1s."""
    import pdfsearchable.store as store_mod

    monkeypatch.chdir(isolated_store)

    files = [{"id": f"file{i:04d}", "name": f"doc{i}.pdf", "pages": 5} for i in range(500)]
    start = time.perf_counter()
    store_mod.save_index({"files": files, "version": 1})
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"save_index(500 docs) demorou {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Performance FTS
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_fts_index_and_search_performance(isolated_store, monkeypatch) -> None:
    """Indexar 50 documentos no FTS e buscar deve completar em < 5s."""
    import pdfsearchable.store as store_mod

    monkeypatch.chdir(isolated_store)

    # Garantir que o directório existe e inicializar FTS
    (isolated_store / ".pdfsearchable").mkdir(exist_ok=True)

    # Indexar 50 docs usando API pública (page_num, text)
    start = time.perf_counter()
    for i in range(50):
        store_mod.fts_index_file(
            f"perftest{i:04d}",
            [(j + 1, f"documento de performance número {i} página {j}") for j in range(3)],
        )
    elapsed_index = time.perf_counter() - start
    assert elapsed_index < 5.0, f"Indexar 50 docs FTS demorou {elapsed_index:.3f}s"

    # Buscar
    start = time.perf_counter()
    for _ in range(20):
        store_mod.fts_search("performance", limit=10)
    elapsed_search = time.perf_counter() - start
    assert elapsed_search < 1.0, f"20x fts_search demorou {elapsed_search:.3f}s"


# ---------------------------------------------------------------------------
# Testes de carga (concorrência)
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_concurrent_load_index_throughput(isolated_store, monkeypatch) -> None:
    """30 threads a carregar o índice em simultâneo devem terminar em < 3s."""
    import pdfsearchable.store as store_mod

    monkeypatch.chdir(isolated_store)

    files = [{"id": f"f{i}", "name": f"d{i}.pdf"} for i in range(20)]
    store_mod.save_index({"files": files, "version": 1})

    errors: list[Exception] = []
    barrier = threading.Barrier(30)

    def _load():
        barrier.wait()  # todos começam ao mesmo tempo
        try:
            store_mod.load_index()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_load) for _ in range(30)]
    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - start

    assert not errors, f"Erros em carga concorrente: {errors}"
    assert elapsed < 3.0, f"30 loads concorrentes demoraram {elapsed:.3f}s"


@pytest.mark.performance
def test_concurrent_fts_search_throughput(isolated_store, monkeypatch) -> None:
    """20 threads a fazer FTS search simultâneo devem terminar em < 5s."""
    import pdfsearchable.store as store_mod

    monkeypatch.chdir(isolated_store)

    # Inicializar e popular FTS
    (isolated_store / ".pdfsearchable").mkdir(exist_ok=True)
    for i in range(10):
        store_mod.fts_index_file(f"load{i}", [(1, f"texto de carga {i} para pesquisa")])

    errors: list[Exception] = []
    barrier = threading.Barrier(20)

    def _search():
        barrier.wait()
        try:
            store_mod.fts_search("texto", limit=5)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_search) for _ in range(20)]
    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - start

    assert not errors
    assert elapsed < 5.0, f"20 FTS searches concorrentes demoraram {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Baseline de memória (sanidade)
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_large_text_processing_memory_stable() -> None:
    """
    Processar texto com 50k palavras não deve causar OOM.
    (Sanidade — não mede memória exacta, mas verifica que termina.)
    """
    big_text = " ".join([f"palavra{i % 1000}" for i in range(50_000)])
    start = time.perf_counter()
    build_top_words(big_text, top_n=100)
    build_bigrams(big_text[:10_000], top_n=50)
    elapsed = time.perf_counter() - start
    assert elapsed < 10.0, f"Processamento de texto grande demorou {elapsed:.3f}s"
