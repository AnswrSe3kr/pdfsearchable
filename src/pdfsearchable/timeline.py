"""
Linha do tempo automática de documentos.

Constrói uma cronologia ordenada a partir das datas extraídas dos PDFs indexados,
com suporte a agrupamento por ano/mês e estatísticas temporais.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger("pdfsearchable.timeline")

# Meses em português (PT-BR e PT-EU)
_MONTH_NAMES: dict[str, int] = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
    "jan": 1, "fev": 2, "mar": 3, "abr": 4,
    "mai": 5, "jun": 6, "jul": 7, "ago": 8,
    "set": 9, "out": 10, "nov": 11, "dez": 12,
}

_RE_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_RE_PDF_DATE = re.compile(r"D:(\d{4})(\d{2})(\d{2})")
_RE_BR_DATE = re.compile(r"(\d{1,2})[/\.\-](\d{1,2})[/\.\-](\d{2,4})")
_RE_PT_DATE = re.compile(
    r"(\d{1,2})\s+de\s+(" + "|".join(_MONTH_NAMES) + r")\s+de\s+(\d{4})",
    re.IGNORECASE,
)

_CURRENT_YEAR = datetime.now(timezone.utc).year


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TimelineEntry:
    """Uma entrada na linha do tempo — representa um documento e a sua data principal."""

    year: int
    month: int | None          # 1–12 ou None
    day: int | None            # 1–31 ou None
    date_iso: str              # "YYYY-MM-DD", "YYYY-MM" ou "YYYY"
    name: str
    file_id: str
    doc_type: str
    all_dates: list[str] = field(default_factory=list)   # todas as datas ISO do documento
    source: str = "indexed_at"                            # origem da data principal
    confidence: float = 0.5


# ---------------------------------------------------------------------------
# Parsing de datas
# ---------------------------------------------------------------------------

def _parse_pdf_date(raw: str) -> tuple[int, int, int] | None:
    """Parseia formato de data PDF: ``D:YYYYMMDDHHmmSS[OHH'mm']``."""
    m = _RE_PDF_DATE.match(raw.strip())
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1900 <= y <= _CURRENT_YEAR + 1 and 1 <= mo <= 12 and 1 <= d <= 31:
            return y, mo, d
    return None


def _parse_iso_date(raw: str) -> tuple[int, int, int] | None:
    """Parseia ISO 8601: ``YYYY-MM-DD``."""
    m = _RE_ISO_DATE.search(raw)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1900 <= y <= _CURRENT_YEAR + 1 and 1 <= mo <= 12 and 1 <= d <= 31:
            return y, mo, d
    return None


def _parse_br_date(raw: str) -> tuple[int, int, int] | None:
    """Parseia formato brasileiro: ``DD/MM/YYYY`` ou ``DD/MM/YY``."""
    m = _RE_BR_DATE.search(raw)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000 if y < 50 else 1900
        if 1900 <= y <= _CURRENT_YEAR + 1 and 1 <= mo <= 12 and 1 <= d <= 31:
            return y, mo, d
    return None


def _parse_pt_date(raw: str) -> tuple[int, int, int] | None:
    """Parseia formato por extenso: ``15 de janeiro de 2024``."""
    m = _RE_PT_DATE.search(raw.lower())
    if m:
        d = int(m.group(1))
        mo = _MONTH_NAMES.get(m.group(2).lower(), 0)
        y = int(m.group(3))
        if mo and 1900 <= y <= _CURRENT_YEAR + 1 and 1 <= d <= 31:
            return y, mo, d
    return None


def _to_iso(y: int, mo: int | None = None, d: int | None = None) -> str:
    """Formata componentes de data em string ISO."""
    if mo and d:
        return f"{y:04d}-{mo:02d}-{d:02d}"
    if mo:
        return f"{y:04d}-{mo:02d}"
    return f"{y:04d}"


def _parse_any_date(raw: str) -> tuple[int, int, int] | None:
    """Tenta todos os parsers por ordem de especificidade."""
    for parser in (_parse_pdf_date, _parse_iso_date, _parse_pt_date, _parse_br_date):
        result = parser(raw)
        if result:
            return result
    return None


def _normalise_dates(raw_dates: list[str]) -> list[str]:
    """Normaliza lista de datas brutas para strings ISO, removendo inválidas."""
    out: list[str] = []
    for raw in raw_dates:
        parsed = _parse_any_date(str(raw))
        if parsed:
            iso = _to_iso(*parsed)
            if iso not in out:
                out.append(iso)
    return out


# ---------------------------------------------------------------------------
# Construção da linha do tempo
# ---------------------------------------------------------------------------

def _extract_best_date(
    f: dict[str, Any],
) -> tuple[int, int | None, int | None, str, str, float] | None:
    """
    Extrai a data mais confiável de um arquivo do índice.

    Retorna ``(year, month, day, date_iso, source, confidence)`` ou ``None``.
    """
    meta: dict[str, Any] = f.get("metadata") or {}

    # P1 — metadados do PDF (mais confiável)
    for key, src in (
        ("creationDate", "metadata_creation"),
        ("modDate", "metadata_modification"),
    ):
        raw = meta.get(key) or ""
        if raw:
            parsed = _parse_pdf_date(raw)
            if parsed:
                y, mo, d = parsed
                return y, mo, d, _to_iso(y, mo, d), src, 1.0

    # P2 — datas identificadas no conteúdo
    content_dates = f.get("identified_dates") or meta.get("identified_dates") or []
    for raw in content_dates[:5]:
        parsed = _parse_any_date(str(raw))
        if parsed:
            y, mo, d = parsed
            return y, mo, d, _to_iso(y, mo, d), "content_date", 0.8

    # P3 — data de indexação (fallback)
    indexed_at = f.get("indexed_at") or ""
    if indexed_at:
        parsed = _parse_iso_date(indexed_at)
        if parsed:
            y, mo, d = parsed
            return y, mo, d, _to_iso(y, mo, d), "indexed_at", 0.5

    return None


def build_timeline(files: list[dict[str, Any]]) -> list[TimelineEntry]:
    """
    Constrói a linha do tempo a partir da lista de arquivos do índice.

    Ordena por data ASC (mais antigo primeiro). Um arquivo = uma entrada.
    Arquivos sem data detectável são omitidos.
    """
    entries: list[TimelineEntry] = []
    seen_ids: set[str] = set()

    for f in files:
        file_id: str = f.get("id", "")
        if not file_id or file_id in seen_ids:
            continue
        seen_ids.add(file_id)

        result = _extract_best_date(f)
        if result is None:
            continue

        y, mo, d, date_iso, source, confidence = result

        # Usar identified_dates para all_dates
        all_dates = _normalise_dates(f.get("identified_dates") or [])

        entries.append(TimelineEntry(
            year=y,
            month=mo,
            day=d,
            date_iso=date_iso,
            name=f.get("name", file_id),
            file_id=file_id,
            doc_type=f.get("doc_type") or f.get("type") or "documento",
            all_dates=all_dates,
            source=source,
            confidence=confidence,
        ))

    entries.sort(key=lambda e: e.date_iso)
    return entries


def group_by_year(entries: list[TimelineEntry]) -> dict[int, list[TimelineEntry]]:
    """Agrupa entradas por ano, chaves ordenadas ASC."""
    groups: dict[int, list[TimelineEntry]] = {}
    for e in entries:
        groups.setdefault(e.year, []).append(e)
    return dict(sorted(groups.items()))


def group_by_year_month(
    entries: list[TimelineEntry],
) -> dict[int, dict[int, list[TimelineEntry]]]:
    """Agrupa entradas por ano → mês (mês 0 = sem mês conhecido)."""
    groups: dict[int, dict[int, list[TimelineEntry]]] = {}
    for e in entries:
        year_group = groups.setdefault(e.year, {})
        month_key = e.month or 0
        year_group.setdefault(month_key, []).append(e)
    return dict(sorted(groups.items()))


def get_date_range(
    entries: list[TimelineEntry],
) -> tuple[str | None, str | None]:
    """Retorna ``(data_mais_antiga, data_mais_recente)`` em ISO, ou ``None`` se vazio."""
    if not entries:
        return None, None
    sorted_entries = sorted(entries, key=lambda e: e.date_iso)
    return sorted_entries[0].date_iso, sorted_entries[-1].date_iso


def timeline_stats(entries: list[TimelineEntry]) -> dict[str, Any]:
    """
    Retorna estatísticas da linha do tempo.

    Keys: ``total``, ``with_precise_date``, ``span_years``, ``docs_per_year``.
    """
    if not entries:
        return {"total": 0, "with_precise_date": 0, "span_years": 0, "docs_per_year": {}}

    precise = sum(1 for e in entries if e.month is not None and e.day is not None)
    years = [e.year for e in entries]
    span = max(years) - min(years) if years else 0
    docs_per_year: dict[int, int] = {}
    for e in entries:
        docs_per_year[e.year] = docs_per_year.get(e.year, 0) + 1

    oldest, newest = get_date_range(entries)
    return {
        "total": len(entries),
        "with_precise_date": precise,
        "span_years": span,
        "oldest": oldest,
        "newest": newest,
        "docs_per_year": dict(sorted(docs_per_year.items())),
    }
