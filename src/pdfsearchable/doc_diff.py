"""
Diff entre versões de documentos.

Quando múltiplas versões do mesmo documento estão indexadas (ex.:
"relatorio_v1.pdf" e "relatorio_v2.pdf", ou reuploads com MinHash
similar), este módulo gera um diff textual entre eles — página por
página, linha por linha.

Usa difflib.unified_diff da stdlib (sem deps extras).

API:
    diff_documents(file_id_a, file_id_b) -> dict com:
        - pages_a, pages_b
        - page_diffs: list[{page, additions, deletions, unified}]
        - summary: {added_lines, removed_lines, modified_pages, identical_pages}
"""

from __future__ import annotations

import difflib
from typing import Any


def diff_texts(text_a: str, text_b: str, *, context_lines: int = 2) -> dict[str, Any]:
    """
    Gera diff entre dois blocos de texto.

    Returns:
        {
            "unified": list[str] (linhas do unified diff),
            "additions": int,
            "deletions": int,
            "identical": bool,
        }
    """
    lines_a = (text_a or "").splitlines()
    lines_b = (text_b or "").splitlines()
    if lines_a == lines_b:
        return {"unified": [], "additions": 0, "deletions": 0, "identical": True}

    unified = list(
        difflib.unified_diff(
            lines_a,
            lines_b,
            fromfile="a",
            tofile="b",
            n=context_lines,
            lineterm="",
        )
    )
    additions = sum(1 for ln in unified if ln.startswith("+") and not ln.startswith("+++"))
    deletions = sum(1 for ln in unified if ln.startswith("-") and not ln.startswith("---"))
    return {
        "unified": unified,
        "additions": additions,
        "deletions": deletions,
        "identical": False,
    }


def diff_documents(file_id_a: str, file_id_b: str) -> dict[str, Any]:
    """
    Diff página-a-página entre dois documentos indexados.

    Retorna:
        {
            "file_a": str, "file_b": str,
            "pages_a": int, "pages_b": int,
            "page_diffs": list[{page, ...}],
            "summary": {...}
        }
    """
    try:
        from pdfsearchable.store import load_index, read_page_text
    except Exception as e:
        return {"error": f"store indisponível: {e}"}

    idx = load_index() or {}
    files = idx.get("files", {}) if isinstance(idx, dict) else {}
    meta_a = files.get(file_id_a)
    meta_b = files.get(file_id_b)
    if not meta_a or not meta_b:
        return {"error": "file_id não encontrado no índice"}

    pages_a = int(meta_a.get("pages", 0) or 0)
    pages_b = int(meta_b.get("pages", 0) or 0)
    max_pages = max(pages_a, pages_b)

    page_diffs: list[dict[str, Any]] = []
    total_add = 0
    total_del = 0
    identical_pages = 0
    modified_pages = 0

    for p in range(1, max_pages + 1):
        try:
            txt_a = read_page_text(file_id_a, p) if p <= pages_a else ""
        except Exception:
            txt_a = ""
        try:
            txt_b = read_page_text(file_id_b, p) if p <= pages_b else ""
        except Exception:
            txt_b = ""

        d = diff_texts(txt_a or "", txt_b or "")
        if d["identical"]:
            identical_pages += 1
            continue
        modified_pages += 1
        total_add += d["additions"]
        total_del += d["deletions"]
        page_diffs.append({
            "page": p,
            "additions": d["additions"],
            "deletions": d["deletions"],
            "unified": d["unified"][:200],  # limita payload
            "truncated": len(d["unified"]) > 200,
        })

    return {
        "file_a": file_id_a,
        "file_b": file_id_b,
        "name_a": meta_a.get("name") or file_id_a,
        "name_b": meta_b.get("name") or file_id_b,
        "pages_a": pages_a,
        "pages_b": pages_b,
        "page_diffs": page_diffs,
        "summary": {
            "added_lines": total_add,
            "removed_lines": total_del,
            "modified_pages": modified_pages,
            "identical_pages": identical_pages,
            "change_ratio": round(
                (total_add + total_del) / max(1, len((meta_a.get("content_hash") or "") + (meta_b.get("content_hash") or ""))),
                3,
            ),
        },
    }


__all__ = ["diff_texts", "diff_documents"]
