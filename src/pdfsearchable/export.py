"""
Exportação de documentos indexados para formatos externos.

Formatos suportados:
  - json      : dump completo do índice (estrutura interna)
  - jsonl     : um documento por linha (fine-tuning de LLMs, pipelines RAG)
  - csv       : metadados tabulares (sem texto completo)
  - markdown  : um arquivo .md por documento (texto + metadados — ideal para RAG externo)

Uso programático:
    from pdfsearchable.export import export_jsonl, export_markdown, export_csv

Uso CLI:
    pdfsearchable export --format jsonl --output colecao.jsonl
    pdfsearchable export --format markdown --output pasta_md/
    pdfsearchable export --format csv --output metadados.csv
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from pdfsearchable.audit import get_logger as _get_logger
from pdfsearchable.store import load_index, load_file_text

_log = _get_logger("pdfsearchable.export")

# Campos de metadados incluídos no CSV e no cabeçalho Markdown
_META_FIELDS = (
    "id", "name", "doc_type", "language", "num_pages", "word_count",
    "indexed_at", "updated_at", "summary", "subject", "tags",
    "ocr_percentage", "ocr_avg_confidence", "content_hash", "original_path",
    "identified_dates",
)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _iter_docs_with_text(include_text: bool = True) -> list[dict[str, Any]]:
    """Itera sobre todos os documentos indexados, opcionalmente com texto completo."""
    idx = load_index()
    out: list[dict[str, Any]] = []
    for f in idx.get("files", []):
        doc: dict[str, Any] = {k: f.get(k) for k in _META_FIELDS}
        # Normalizar tags e datas como listas
        if not isinstance(doc.get("tags"), list):
            doc["tags"] = []
        if not isinstance(doc.get("identified_dates"), list):
            doc["identified_dates"] = []
        # Fórmulas (se detectadas com PDFSEARCHABLE_DETECT_FORMULAS=1)
        md_nested = f.get("metadata") or {}
        formulas = md_nested.get("formulas") if isinstance(md_nested, dict) else None
        doc["formulas"] = formulas if isinstance(formulas, dict) else None
        if include_text:
            text = load_file_text(f.get("id", ""))
            doc["text"] = (text or "").strip()
        out.append(doc)
    return out


def _safe_str(v: Any) -> str:
    """Converte valor para string segura para CSV/Markdown."""
    if v is None:
        return ""
    if isinstance(v, list):
        return "; ".join(str(x) for x in v)
    return str(v)


# ---------------------------------------------------------------------------
# Exportação JSON (dump do índice)
# ---------------------------------------------------------------------------

def export_json(output: Path) -> int:
    """
    Exporta o índice completo (sem texto de páginas) para JSON.
    Retorna o número de documentos exportados.
    """
    idx = load_index()
    files = idx.get("files", [])
    output.write_text(json.dumps({"files": files}, ensure_ascii=False, indent=2), encoding="utf-8")
    _log.info("Exportados %d documentos para %s (JSON)", len(files), output)
    return len(files)


# ---------------------------------------------------------------------------
# Exportação JSONL (fine-tuning / RAG)
# ---------------------------------------------------------------------------

def export_jsonl(output: Path, include_text: bool = True) -> int:
    """
    Exporta um documento por linha em formato JSONL.
    Cada linha: {"id": "...", "name": "...", "text": "...", "metadata": {...}}
    Útil para fine-tuning de LLMs e pipelines RAG externos.
    Retorna o número de documentos exportados.
    """
    docs = _iter_docs_with_text(include_text=include_text)
    with open(output, "w", encoding="utf-8") as f:
        for doc in docs:
            record = {
                "id": doc.get("id") or "",
                "name": doc.get("name") or "",
                "doc_type": doc.get("doc_type") or "documento",
                "language": doc.get("language") or "",
                "num_pages": doc.get("num_pages") or 0,
                "word_count": doc.get("word_count") or 0,
                "summary": doc.get("summary") or "",
                "subject": doc.get("subject") or "",
                "tags": doc.get("tags") or [],
                "identified_dates": doc.get("identified_dates") or [],
                "indexed_at": (doc.get("indexed_at") or "")[:10],
            }
            if include_text:
                record["text"] = doc.get("text") or ""
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    _log.info("Exportados %d documentos para %s (JSONL)", len(docs), output)
    return len(docs)


# ---------------------------------------------------------------------------
# Exportação CSV (metadados tabulares)
# ---------------------------------------------------------------------------

def export_csv(output: Path) -> int:
    """
    Exporta metadados de todos os documentos para CSV (sem texto completo).
    Colunas: id, name, doc_type, language, num_pages, word_count, indexed_at,
             updated_at, summary, subject, tags, ocr_percentage, content_hash,
             original_path, identified_dates.
    Retorna o número de documentos exportados.
    """
    docs = _iter_docs_with_text(include_text=False)
    fieldnames = list(_META_FIELDS)
    with open(output, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for doc in docs:
            writer.writerow({k: _safe_str(doc.get(k)) for k in fieldnames})
    _log.info("Exportados %d documentos para %s (CSV)", len(docs), output)
    return len(docs)


# ---------------------------------------------------------------------------
# Exportação Markdown (um arquivo por documento)
# ---------------------------------------------------------------------------

_MD_TEMPLATE = """\
# {name}

**Tipo:** {doc_type}
**Idioma:** {language}
**Páginas:** {num_pages}
**Palavras:** {word_count}
**Indexado em:** {indexed_at}
{subject_line}{tags_line}{dates_line}{summary_block}
---

{text}
"""


def export_markdown(output_dir: Path, max_chars_per_doc: int = 200_000) -> int:
    """
    Exporta cada documento como um arquivo Markdown em `output_dir`.
    O nome do arquivo é `<id>_<nome_sanitizado>.md`.
    Inclui metadados no cabeçalho e o texto completo extraído.
    Útil para RAG externo (LlamaIndex, LangChain, etc.).
    Retorna o número de documentos exportados.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    docs = _iter_docs_with_text(include_text=True)
    count = 0
    for doc in docs:
        file_id = doc.get("id") or "unknown"
        name = doc.get("name") or file_id
        # Nome de arquivo seguro: substituir caracteres proibidos
        safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        md_path = output_dir / f"{file_id}_{safe_name}.md"

        text = (doc.get("text") or "").strip()
        if len(text) > max_chars_per_doc:
            text = text[:max_chars_per_doc] + f"\n\n[Texto truncado: {max_chars_per_doc:,} de {len(doc.get('text', '')):,} caracteres]"

        subject = doc.get("subject") or ""
        tags = doc.get("tags") or []
        dates = doc.get("identified_dates") or []
        summary = doc.get("summary") or ""

        subject_line = f"**Assunto:** {subject}  \n" if subject else ""
        tags_line = f"**Tags:** {', '.join(tags)}  \n" if tags else ""
        dates_line = f"**Datas:** {', '.join(dates[:10])}  \n" if dates else ""
        summary_block = f"\n**Resumo:** {summary}\n" if summary else ""

        # Fórmulas (apêndice): preservadas em LaTeX quando disponíveis
        formulas_block = ""
        fdict = doc.get("formulas")
        if isinstance(fdict, dict) and fdict.get("total", 0) > 0:
            try:
                from pdfsearchable.formulas import FormulaHit, FormulaReport, render_markdown_section
                hits = [
                    FormulaHit(
                        page=int(h.get("page", 0) or 0),
                        raw=str(h.get("raw", "")),
                        kind=str(h.get("kind", "inline")),
                        latex=str(h.get("latex", "")),
                    )
                    for h in (fdict.get("hits") or [])
                ]
                rep = FormulaReport(
                    total=int(fdict.get("total", 0) or 0),
                    by_kind=dict(fdict.get("by_kind") or {}),
                    hits=hits,
                )
                section = render_markdown_section(rep)
                if section:
                    formulas_block = "\n\n---\n\n" + section + "\n"
            except Exception as _e:  # pragma: no cover
                _log.debug("Falha ao renderizar fórmulas em markdown: %s", _e)

        content = _MD_TEMPLATE.format(
            name=name,
            doc_type=doc.get("doc_type") or "documento",
            language=doc.get("language") or "—",
            num_pages=doc.get("num_pages") or 0,
            word_count=doc.get("word_count") or 0,
            indexed_at=(doc.get("indexed_at") or "")[:10] or "—",
            subject_line=subject_line,
            tags_line=tags_line,
            dates_line=dates_line,
            summary_block=summary_block,
            text=(text or "_Sem texto extraído._") + formulas_block,
        )
        try:
            md_path.write_text(content, encoding="utf-8")
            count += 1
        except OSError as e:
            _log.warning("Falha ao escrever %s: %s", md_path, e)

    _log.info("Exportados %d documentos para %s (Markdown)", count, output_dir)
    return count


# ---------------------------------------------------------------------------
# Entry point unificado
# ---------------------------------------------------------------------------

def export(
    fmt: str,
    output: Path,
    *,
    include_text: bool = True,
    max_chars_per_doc: int = 200_000,
) -> int:
    """
    Ponto de entrada único para exportação.
    fmt: "json" | "jsonl" | "csv" | "markdown"
    Retorna o número de documentos exportados.
    Levanta ValueError para formatos desconhecidos.
    """
    fmt = fmt.lower().strip()
    if fmt == "json":
        return export_json(output)
    elif fmt == "jsonl":
        return export_jsonl(output, include_text=include_text)
    elif fmt == "csv":
        return export_csv(output)
    elif fmt in ("markdown", "md"):
        return export_markdown(output, max_chars_per_doc=max_chars_per_doc)
    else:
        raise ValueError(
            f"Formato desconhecido: '{fmt}'. "
            "Use: json | jsonl | csv | markdown"
        )
