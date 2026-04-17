"""
Benchmark reprodutível: PDF → Markdown (pdfsearchable vs baseline PyMuPDF).

Verifica que o percurso "usa texto cacheado + template" é significativamente
mais rápido que re-extrair cada página a cada chamada.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.performance


def _make_multi_page_pdf(pdf_path: Path, num_pages: int = 20) -> None:
    """Cria um PDF com `num_pages` páginas, cada uma com ~200 palavras."""
    import fitz

    doc = fitz.open()
    filler = (
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
        "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
        "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris "
        "nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in "
        "reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
        "pariatur. Excepteur sint occaecat cupidatat non proident, sunt in "
        "culpa qui officia deserunt mollit anim id est laborum. "
    ) * 3
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Página {i + 1}\n\n{filler}")
    doc.save(str(pdf_path))
    doc.close()


def test_benchmark_markdown_speedup(tmp_path, monkeypatch):
    """
    Indexa um PDF com 20 páginas, corre 3 iterações de cada estratégia
    e verifica que o pdfsearchable é pelo menos 5× mais rápido.
    """
    from pdfsearchable import store as store_mod
    from pdfsearchable.indexer import index_pdf
    from pdfsearchable.markdown_bench import benchmark_markdown

    # isola o store no tmp_path
    proj = tmp_path
    store_dir = proj / ".pdfsearchable"
    store_dir.mkdir(parents=True, exist_ok=True)
    (proj / "arquivos-processados").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store_mod, "STORE_DIR", store_dir)
    monkeypatch.setattr(store_mod, "META_FILE", store_dir / "index.json")
    monkeypatch.setattr(store_mod, "FTS_DB", store_dir / "fts.sqlite")
    monkeypatch.setattr(store_mod, "FILES_DIR", proj / "arquivos-processados")
    monkeypatch.setattr(store_mod, "OCR_CACHE_DIR", store_dir / "ocr_cache")
    # o indexer usa FILES_DIR / copy_pdf_to_store → mantém-se via store_mod

    monkeypatch.chdir(proj)

    # OCR desligado (OCR_ALWAYS=0) para não depender do Tesseract neste benchmark
    monkeypatch.setenv("PDFSEARCHABLE_OCR_ALWAYS", "0")

    pdf_path = proj / "bench.pdf"
    _make_multi_page_pdf(pdf_path, num_pages=20)

    rec = index_pdf(pdf_path)
    file_id = rec["id"]

    result = benchmark_markdown(pdf_path=pdf_path, file_id=file_id, iterations=3)

    # Ambas devem produzir tempos positivos
    assert result.baseline_avg_s > 0
    assert result.pdfsearchable_avg_s > 0
    # pdfsearchable deve ser MUITO mais rápido (re-parse PyMuPDF é caro em 20 páginas)
    assert result.speedup >= 5.0, (
        f"Speedup esperado >= 5×, medido {result.speedup:.2f}× "
        f"(baseline={result.baseline_avg_s:.4f}s, "
        f"pdfsearchable={result.pdfsearchable_avg_s:.4f}s)"
    )
    assert result.pages == 20
    assert len(result.baseline_times_s) == 3
    assert len(result.pdfsearchable_times_s) == 3


def test_benchmark_result_to_dict():
    """BenchmarkResult é serializável para JSON."""
    import json

    from pdfsearchable.markdown_bench import BenchmarkResult

    r = BenchmarkResult(
        pdf="x.pdf",
        pages=10,
        iterations=3,
        baseline_avg_s=0.1,
        pdfsearchable_avg_s=0.01,
        speedup=10.0,
        baseline_times_s=[0.1, 0.1, 0.1],
        pdfsearchable_times_s=[0.01, 0.01, 0.01],
    )
    d = r.to_dict()
    assert d["speedup"] == 10.0
    assert d["pages"] == 10
    # round-trip por JSON
    assert json.loads(json.dumps(d))["pdf"] == "x.pdf"
