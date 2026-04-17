"""
Exportação "dossier" — PDF compilado de resultados de busca com
metadados, capas, índice e snippets destacados.

Usa PyMuPDF puro (zero deps extras). O dossier contém:

    1. Capa com título, data de geração, query, nº de resultados
    2. Índice com links para cada doc
    3. Para cada doc: página de metadados + primeiras páginas relevantes
       com highlights amarelos nos matches

API:
    generate_dossier(results, output_path, *, title, query) -> Path
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import fitz


def generate_dossier(
    results: list[dict[str, Any]],
    output_path: str | Path,
    *,
    title: str = "Dossiê de Resultados",
    query: str = "",
    include_pages: int = 2,
) -> Path:
    """
    Gera PDF dossier a partir de uma lista de resultados de busca.

    Args:
        results: lista de dicts com pelo menos {file_id, file_name, page, snippet}
        output_path: destino do PDF
        title: título da capa
        query: texto da consulta (informativo na capa)
        include_pages: quantas páginas de cada doc incluir (a partir da primeira match)

    Returns:
        Path do PDF gerado.
    """
    output_path = Path(output_path)
    dossier = fitz.open()

    # --- Capa ---
    cover = dossier.new_page(width=595, height=842)  # A4
    cover.insert_text(
        (72, 100),
        title,
        fontsize=24,
        fontname="helv",
    )
    meta_text = [
        f"Data de geração: {dt.datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"Consulta: {query}" if query else "",
        f"Total de resultados: {len(results)}",
        "",
        "Gerado por pdfsearchable",
    ]
    y = 160
    for line in meta_text:
        if line:
            cover.insert_text((72, y), line, fontsize=11)
        y += 18

    # --- Índice (TOC) ---
    toc_page = dossier.new_page(width=595, height=842)
    toc_page.insert_text((72, 80), "Índice", fontsize=18, fontname="helv")
    y = 120
    for i, r in enumerate(results, start=1):
        name = (r.get("file_name") or r.get("name") or r.get("file_id", "?"))[:60]
        line = f"{i}. {name}  (pág. {r.get('page', '?')})"
        toc_page.insert_text((72, y), line, fontsize=10)
        y += 16
        if y > 780:
            toc_page = dossier.new_page(width=595, height=842)
            y = 80

    # --- Sections por resultado ---
    seen_files: dict[str, Any] = {}
    for i, r in enumerate(results, start=1):
        file_id = r.get("file_id") or ""
        page_num = int(r.get("page", 1) or 1)
        snippet = r.get("snippet", "")
        name = r.get("file_name") or r.get("name") or file_id

        # Página de metadados do resultado
        section = dossier.new_page(width=595, height=842)
        section.insert_text((72, 72), f"[{i}] {name[:70]}", fontsize=14, fontname="helv")
        y = 110
        section.insert_text((72, y), f"file_id: {file_id}", fontsize=9)
        y += 14
        section.insert_text((72, y), f"Página do match: {page_num}", fontsize=9)
        y += 20

        # Snippet (limpando tags HTML <mark>)
        clean_snippet = (snippet or "").replace("<mark>", "").replace("</mark>", "")
        if clean_snippet:
            rect = fitz.Rect(72, y, 523, y + 200)
            section.insert_textbox(rect, clean_snippet[:800], fontsize=10)

        # Incluir algumas páginas reais do doc original se possível
        if file_id and file_id not in seen_files:
            try:
                src = _open_source_pdf(file_id)
                if src:
                    seen_files[file_id] = True
                    start = max(1, page_num) - 1
                    end = min(src.page_count, start + include_pages)
                    dossier.insert_pdf(src, from_page=start, to_page=end - 1)
                    src.close()
            except Exception:
                pass

    dossier.save(str(output_path))
    dossier.close()
    return output_path


def _open_source_pdf(file_id: str):
    """Abre o PDF original a partir do file_id via store."""
    try:
        from pdfsearchable.store import FILES_DIR, load_index

        idx = load_index() or {}
        files = idx.get("files", {}) if isinstance(idx, dict) else {}
        meta = files.get(file_id)
        if not meta:
            return None
        # O PDF armazenado é normalmente FILES_DIR/file_id.pdf
        candidate = Path(FILES_DIR) / f"{file_id}.pdf"
        if candidate.exists():
            return fitz.open(str(candidate))
        # Fallback: caminho original (pode estar fora do store)
        orig = meta.get("path")
        if orig and Path(orig).exists():
            return fitz.open(orig)
    except Exception:
        return None
    return None


__all__ = ["generate_dossier"]
