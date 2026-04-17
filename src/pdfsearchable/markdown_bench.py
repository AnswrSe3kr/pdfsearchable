"""
Benchmark reprodutível de conversão PDF → Markdown.

Compara duas estratégias sobre o **mesmo PDF**:

  * ``baseline``: abre o PDF do zero com PyMuPDF, extrai texto página a página
    e escreve um ``.md`` minimal. Este é o percurso que uma pipeline ingênua
    (ou `pdf2md`, `marker` sem cache, etc.) percorre a cada chamada.
  * ``pdfsearchable``: usa o texto **pré-extraído e cacheado** em
    ``arquivos-processados/<id>/`` (ou ``full.txt.gz``), mais o template do
    :mod:`export` com cabeçalho de metadados. Nenhuma página do PDF é
    re-parseada.

O speedup medido corresponde ao ganho real para pipelines que **já indexaram**
a coleção uma vez e fazem múltiplas exportações (RAG, LlamaIndex, Obsidian).

A API devolve um dicionário com tempos por iteração e médias, para que o
teste de performance (`tests/performance/test_markdown_bench.py`) possa
asserçar um speedup mínimo (>= 5× em docs de 10+ páginas com texto cacheado).
"""

from __future__ import annotations

import logging
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("pdfsearchable.markdown_bench")

__all__ = ["BenchmarkResult", "benchmark_markdown", "_baseline_pymupdf_to_markdown"]


@dataclass
class BenchmarkResult:
    pdf: str
    pages: int
    iterations: int
    baseline_avg_s: float
    pdfsearchable_avg_s: float
    speedup: float
    baseline_times_s: list[float]
    pdfsearchable_times_s: list[float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Baseline (ingênuo): re-parse do PDF a cada chamada
# ---------------------------------------------------------------------------


def _baseline_pymupdf_to_markdown(pdf_path: Path, out_path: Path) -> int:
    """
    Converte um PDF em Markdown re-extraindo o texto do PDF **do zero** a cada
    chamada. Representa o custo de uma pipeline sem cache.
    Retorna o número de páginas.
    """
    import fitz  # lazy import — PyMuPDF só se necessário

    doc = fitz.open(str(pdf_path))
    try:
        lines: list[str] = [f"# {pdf_path.stem}", "", f"**Páginas:** {doc.page_count}", ""]
        for i, page in enumerate(doc):
            txt = page.get_text("text") or ""
            lines.append(f"## Página {i + 1}")
            lines.append("")
            lines.append(txt.strip())
            lines.append("")
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return doc.page_count
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# pdfsearchable: lê texto já extraído do store
# ---------------------------------------------------------------------------


def _pdfsearchable_to_markdown(file_id: str, out_path: Path) -> int:
    """
    Gera Markdown a partir do texto **pré-extraído** em
    ``arquivos-processados/<id>/full.txt[.gz]`` + template do módulo export.
    Representa o custo de uma segunda (ou n-ésima) exportação.
    Retorna o número de páginas segundo o índice (ou 0 se não encontrado).
    """
    from pdfsearchable.store import load_file_text, load_index

    idx = load_index()
    doc_meta: dict[str, Any] | None = None
    for f in idx.get("files", []):
        if f.get("id") == file_id:
            doc_meta = f
            break
    if not doc_meta:
        raise FileNotFoundError(f"file_id não encontrado no índice: {file_id}")
    text = load_file_text(file_id) or ""
    name = doc_meta.get("name") or file_id
    pages = int(doc_meta.get("num_pages") or 0)
    header = (
        f"# {name}\n\n"
        f"**Tipo:** {doc_meta.get('doc_type') or 'documento'}\n"
        f"**Idioma:** {doc_meta.get('language') or '—'}\n"
        f"**Páginas:** {pages}\n"
        f"**Palavras:** {doc_meta.get('word_count') or 0}\n"
        f"**Indexado em:** {(doc_meta.get('indexed_at') or '')[:10]}\n\n"
        "---\n\n"
    )
    out_path.write_text(header + text, encoding="utf-8")
    return pages


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def benchmark_markdown(
    pdf_path: Path,
    file_id: str,
    *,
    iterations: int = 5,
    work_dir: Path | None = None,
) -> BenchmarkResult:
    """
    Corre ``iterations`` vezes cada estratégia e devolve tempos + speedup.
    ``file_id`` é o id do documento no índice (deve já ter sido indexado).

    Não cria processos extra — tempos medidos via :func:`time.perf_counter`.
    """
    if iterations < 1:
        raise ValueError("iterations deve ser >= 1")
    pdf_path = pdf_path.resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF não encontrado: {pdf_path}")
    work_dir = (work_dir or Path(".pdfsearchable") / ".bench").resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    # Warm-up (1 chamada em cada, não contabilizada)
    try:
        _baseline_pymupdf_to_markdown(pdf_path, work_dir / "baseline_warmup.md")
    except Exception as _e:  # pragma: no cover
        logger.warning("Falha no warm-up baseline: %s", _e)
    try:
        _pdfsearchable_to_markdown(file_id, work_dir / "pdfs_warmup.md")
    except Exception as _e:  # pragma: no cover
        logger.warning("Falha no warm-up pdfsearchable: %s", _e)

    baseline_times: list[float] = []
    pdfs_times: list[float] = []
    pages = 0
    for i in range(iterations):
        t0 = time.perf_counter()
        pages = _baseline_pymupdf_to_markdown(pdf_path, work_dir / f"baseline_{i}.md")
        baseline_times.append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        _pdfsearchable_to_markdown(file_id, work_dir / f"pdfs_{i}.md")
        pdfs_times.append(time.perf_counter() - t0)

    base_avg = statistics.mean(baseline_times)
    pdfs_avg = statistics.mean(pdfs_times)
    speedup = base_avg / pdfs_avg if pdfs_avg > 0 else float("inf")
    logger.info(
        "benchmark markdown: pdf=%s pages=%d iter=%d baseline=%.4fs pdfsearchable=%.4fs speedup=%.2fx",
        pdf_path.name,
        pages,
        iterations,
        base_avg,
        pdfs_avg,
        speedup,
    )
    return BenchmarkResult(
        pdf=str(pdf_path),
        pages=pages,
        iterations=iterations,
        baseline_avg_s=base_avg,
        pdfsearchable_avg_s=pdfs_avg,
        speedup=speedup,
        baseline_times_s=baseline_times,
        pdfsearchable_times_s=pdfs_times,
    )
