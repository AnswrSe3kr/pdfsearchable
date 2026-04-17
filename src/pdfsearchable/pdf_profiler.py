"""
PDF Profiler — classificação estrutural centralizada de PDFs.

Uma única chamada `profile_pdf(path)` retorna um dict com o perfil completo
do documento: tipo dominante, características estruturais, assinaturas,
formulários, camadas, anexos, idioma, perfil por página.

Substitui heurísticas espalhadas pelo código. É usado por indexer.py
para rotear pipelines (OCR padrão vs histórico, HTR, form-aware extraction,
etc.) e por cli.py no comando `doctor` e `profile`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

import fitz  # PyMuPDF

logger = logging.getLogger("pdfsearchable.profiler")

PdfKind = Literal[
    "born_digital",
    "scanned_clean",
    "scanned_noisy",
    "historical_print",
    "historical_manuscript",
    "form",
    "table_heavy",
    "mixed",
    "image_only",
    "password_protected",
    "corrupted",
    "hybrid_ocr",
    "unknown",
]

# Limiares heurísticos (ajustáveis por env futuramente)
_MIN_TEXT_CHARS_DIGITAL = 200  # chars/página para considerar "born digital"
_SCAN_IMAGE_COVERAGE = 0.6  # fração da página coberta por imagens → scan
_HISTORICAL_YEAR_THRESHOLD = 1960  # CreationDate antes disto → candidato histórico
_HISTORICAL_PRODUCERS = (
    "abbyy",
    "archivematica",
    "flexicapture",
    "kofax",
    "internet archive",
    "google books",
)
_FORM_FIELD_MIN = 3  # ≥3 widgets AcroForm → "form"
_TABLE_DRAWING_MIN = 20  # ≥20 drawings/página → tabela/diagrama
_COLUMN_RATIO_BILINGUAL = 0.35  # razão de palavras entre colunas para bilíngue


def profile_pdf(path: str | Path, *, sample_pages: int = 5) -> dict[str, Any]:
    """
    Gera perfil estrutural completo de um PDF.

    Args:
        path: caminho do PDF.
        sample_pages: número máximo de páginas a amostrar para análise por-página.
                      PDFs pequenos são analisados integralmente.

    Returns:
        dict com chaves:
        - kind: PdfKind dominante
        - confidence: 0.0-1.0
        - pages: total de páginas
        - pages_profile: list[dict] por página (limitado por sample_pages)
        - metadata: producer/creator/author/dates
        - features: dict de flags booleanas
        - dominant_lang: código de língua detectado (se content_extractors disponível)
        - errors: list de erros/warnings
    """
    path = Path(path)
    profile: dict[str, Any] = {
        "path": str(path),
        "kind": "unknown",
        "confidence": 0.0,
        "pages": 0,
        "pages_profile": [],
        "metadata": {},
        "features": {
            "has_text": False,
            "has_images": False,
            "has_forms": False,
            "has_xfa": False,
            "has_bookmarks": False,
            "has_attachments": False,
            "has_js": False,
            "has_signatures": False,
            "has_layers": False,
            "has_annotations": False,
            "encrypted": False,
            "is_pdf_a": False,
            "is_pdf_ua": False,
            "multi_column": False,
            "vertical_script": False,
        },
        "dominant_lang": None,
        "errors": [],
    }

    if not path.exists():
        profile["kind"] = "corrupted"
        profile["errors"].append(f"Arquivo não existe: {path}")
        return profile

    try:
        doc = fitz.open(str(path))
    except Exception as e:
        profile["kind"] = "corrupted"
        profile["errors"].append(f"Falha ao abrir: {e}")
        return profile

    try:
        # --- Encrypted/protected ---
        if doc.is_encrypted:
            profile["features"]["encrypted"] = True
            if not doc.authenticate(""):
                profile["kind"] = "password_protected"
                profile["confidence"] = 1.0
                return profile

        profile["pages"] = doc.page_count

        # --- Metadata ---
        md = doc.metadata or {}
        profile["metadata"] = {
            "title": (md.get("title") or "").strip() or None,
            "author": (md.get("author") or "").strip() or None,
            "subject": (md.get("subject") or "").strip() or None,
            "producer": (md.get("producer") or "").strip() or None,
            "creator": (md.get("creator") or "").strip() or None,
            "creation_date": (md.get("creationDate") or "").strip() or None,
            "mod_date": (md.get("modDate") or "").strip() or None,
            "pdf_version": getattr(doc, "pdf_version", lambda: None)() if callable(getattr(doc, "pdf_version", None)) else None,
        }

        # --- Document-level features ---
        try:
            profile["features"]["has_bookmarks"] = bool(doc.get_toc())
        except Exception:
            pass

        try:
            profile["features"]["has_attachments"] = doc.embfile_count() > 0
        except Exception:
            pass

        try:
            # Layers (OCG)
            ocgs = doc.get_ocgs() if hasattr(doc, "get_ocgs") else {}
            profile["features"]["has_layers"] = bool(ocgs)
        except Exception:
            pass

        try:
            # Forms
            widgets = 0
            for pg in doc:
                for _ in pg.widgets() or []:
                    widgets += 1
                    if widgets >= _FORM_FIELD_MIN:
                        break
                if widgets >= _FORM_FIELD_MIN:
                    break
            profile["features"]["has_forms"] = widgets >= _FORM_FIELD_MIN
        except Exception:
            pass

        try:
            # XFA detection via /AcroForm/XFA key
            root = doc.pdf_catalog() if hasattr(doc, "pdf_catalog") else 0
            if root:
                xfa_xref = doc.xref_get_key(root, "AcroForm/XFA")
                profile["features"]["has_xfa"] = bool(xfa_xref and xfa_xref[0] not in ("null", "xref"))
        except Exception:
            pass

        try:
            # JS detection
            root = doc.pdf_catalog() if hasattr(doc, "pdf_catalog") else 0
            if root:
                js_xref = doc.xref_get_key(root, "Names/JavaScript")
                profile["features"]["has_js"] = bool(js_xref and js_xref[0] != "null")
        except Exception:
            pass

        try:
            # Signatures
            sigs = doc.get_sigflags() if hasattr(doc, "get_sigflags") else 0
            profile["features"]["has_signatures"] = bool(sigs & 1) if sigs else False
        except Exception:
            pass

        # PDF/A, PDF/UA via metadata producer
        producer = (profile["metadata"].get("producer") or "").lower()
        if "pdf/a" in producer or "pdfa" in producer:
            profile["features"]["is_pdf_a"] = True
        if "pdf/ua" in producer:
            profile["features"]["is_pdf_ua"] = True

        # --- Per-page sampling ---
        n = doc.page_count
        sample_indices = _sample_indices(n, sample_pages)
        total_text_chars = 0
        total_images = 0
        total_drawings = 0
        total_text_area = 0.0
        total_img_area = 0.0
        total_page_area = 0.0
        annotations_count = 0
        columns_detected = 0

        for idx in sample_indices:
            try:
                page = doc[idx]
            except Exception as e:
                profile["errors"].append(f"Página {idx}: {e}")
                continue

            rect = page.rect
            page_area = max(1.0, rect.width * rect.height)
            total_page_area += page_area

            try:
                text = page.get_text("text") or ""
            except Exception:
                text = ""
            text_len = len(text.strip())
            total_text_chars += text_len

            try:
                images = page.get_images(full=True) or []
            except Exception:
                images = []
            total_images += len(images)

            img_area = 0.0
            try:
                for blk in page.get_text("dict")["blocks"]:
                    if blk.get("type") == 1:  # image block
                        bbox = blk.get("bbox") or (0, 0, 0, 0)
                        img_area += max(0.0, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
                    elif blk.get("type") == 0:  # text block
                        bbox = blk.get("bbox") or (0, 0, 0, 0)
                        total_text_area += max(0.0, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
            except Exception:
                pass
            total_img_area += img_area

            try:
                drawings = page.get_drawings() or []
                total_drawings += len(drawings)
            except Exception:
                pass

            try:
                annots = list(page.annots() or [])
                annotations_count += len(annots)
            except Exception:
                pass

            # Column detection — simples: conta clusters de x-start de blocos de texto
            try:
                x_starts: list[float] = []
                for blk in page.get_text("dict")["blocks"]:
                    if blk.get("type") == 0:
                        bbox = blk.get("bbox") or (0, 0, 0, 0)
                        x_starts.append(bbox[0])
                if len(x_starts) > 4:
                    x_starts.sort()
                    mid = rect.width / 2
                    left = sum(1 for x in x_starts if x < mid * 0.9)
                    right = sum(1 for x in x_starts if x > mid * 1.1)
                    if left >= 2 and right >= 2 and abs(left - right) / max(left + right, 1) < 0.5:
                        columns_detected += 1
            except Exception:
                pass

            profile["pages_profile"].append({
                "index": idx,
                "text_chars": text_len,
                "images": len(images),
                "drawings": len(drawings) if 'drawings' in locals() else 0,
                "img_coverage": round(img_area / page_area, 3),
                "width": round(rect.width, 1),
                "height": round(rect.height, 1),
            })

        sampled = max(1, len(profile["pages_profile"]))
        avg_text_chars = total_text_chars / sampled
        avg_images = total_images / sampled
        avg_drawings = total_drawings / sampled
        img_coverage = total_img_area / max(total_page_area, 1.0)

        profile["features"]["has_text"] = avg_text_chars > 10
        profile["features"]["has_images"] = avg_images > 0
        profile["features"]["has_annotations"] = annotations_count > 0
        profile["features"]["multi_column"] = columns_detected >= sampled * 0.5

        # --- Classificação ---
        kind, confidence = _classify(
            avg_text_chars=avg_text_chars,
            avg_images=avg_images,
            avg_drawings=avg_drawings,
            img_coverage=img_coverage,
            producer=producer,
            metadata=profile["metadata"],
            features=profile["features"],
        )
        profile["kind"] = kind
        profile["confidence"] = confidence

        # --- Detecção de língua via content_extractors (se disponível) ---
        try:
            if total_text_chars > 50:
                sample_text = ""
                for idx in sample_indices[:3]:
                    try:
                        sample_text += doc[idx].get_text("text") or ""
                    except Exception:
                        pass
                    if len(sample_text) > 2000:
                        break
                from pdfsearchable.language import detect_language  # type: ignore
                profile["dominant_lang"] = detect_language(sample_text[:4000])
        except Exception:
            pass

    finally:
        try:
            doc.close()
        except Exception:
            pass

    return profile


def _sample_indices(n: int, sample: int) -> list[int]:
    """Escolhe até `sample` índices distribuídos uniformemente em [0, n)."""
    if n <= 0:
        return []
    if n <= sample:
        return list(range(n))
    step = n / sample
    return sorted({min(n - 1, int(i * step)) for i in range(sample)})


def _classify(
    *,
    avg_text_chars: float,
    avg_images: float,
    avg_drawings: float,
    img_coverage: float,
    producer: str,
    metadata: dict[str, Any],
    features: dict[str, bool],
) -> tuple[PdfKind, float]:
    """Retorna (kind, confidence)."""

    # Forms
    if features.get("has_forms") or features.get("has_xfa"):
        return "form", 0.9

    # Tabela/desenho-pesado
    if avg_drawings > 20 and avg_text_chars > 50:
        return "table_heavy", 0.7

    # Histórico — detecção por produtor/ano + presença forte de imagem
    is_historical_producer = any(h in producer for h in _HISTORICAL_PRODUCERS)
    creation = metadata.get("creation_date") or ""
    is_old = False
    for y in range(1900, _HISTORICAL_YEAR_THRESHOLD):
        if f":{y}" in creation or creation.startswith(f"D:{y}"):
            is_old = True
            break

    if (is_historical_producer or is_old) and img_coverage > 0.4:
        # Sem texto extraível → manuscrito, com texto → impresso
        if avg_text_chars < _MIN_TEXT_CHARS_DIGITAL:
            return "historical_manuscript", 0.65
        return "historical_print", 0.7

    # Image-only (sem texto + alta cobertura de imagem)
    if avg_text_chars < 10 and avg_images > 0 and img_coverage > _SCAN_IMAGE_COVERAGE:
        # Diferenciar scan limpo vs. ruidoso via presença de drawings (ruído)
        if avg_drawings > 5:
            return "scanned_noisy", 0.7
        return "scanned_clean", 0.8

    # Born digital — muito texto, pouca imagem
    if avg_text_chars >= _MIN_TEXT_CHARS_DIGITAL and img_coverage < 0.3:
        return "born_digital", 0.9

    # Híbrido OCR — tem texto E imagens significativas (PDF escaneado + camada OCR)
    if avg_text_chars >= 50 and img_coverage >= 0.4:
        return "hybrid_ocr", 0.75

    # Image-only fallback
    if avg_text_chars < 10 and avg_images > 0:
        return "image_only", 0.6

    # Misturado
    if avg_text_chars > 0:
        return "mixed", 0.5

    return "unknown", 0.3


def recommend_pipeline(profile: dict[str, Any]) -> dict[str, Any]:
    """
    Dado um perfil, recomenda configuração de pipeline de indexação.

    Retorna dict com:
        - needs_ocr: bool
        - ocr_mode: "standard" | "historical" | "none"
        - extract_forms: bool
        - extract_tables: bool
        - extract_attachments: bool
        - use_htr: bool
        - warn_password: bool
        - warn_corrupted: bool
    """
    kind = profile.get("kind", "unknown")
    features = profile.get("features", {})
    rec = {
        "needs_ocr": False,
        "ocr_mode": "none",
        "extract_forms": False,
        "extract_tables": False,
        "extract_attachments": bool(features.get("has_attachments")),
        "use_htr": False,
        "warn_password": False,
        "warn_corrupted": False,
    }

    if kind == "password_protected":
        rec["warn_password"] = True
        return rec
    if kind == "corrupted":
        rec["warn_corrupted"] = True
        return rec

    if kind in ("scanned_clean", "scanned_noisy", "image_only"):
        rec["needs_ocr"] = True
        rec["ocr_mode"] = "standard"
    elif kind in ("historical_print",):
        rec["needs_ocr"] = True
        rec["ocr_mode"] = "historical"
    elif kind == "historical_manuscript":
        rec["needs_ocr"] = True
        rec["ocr_mode"] = "historical"
        rec["use_htr"] = True
    elif kind == "hybrid_ocr":
        rec["needs_ocr"] = False  # já tem camada OCR
    elif kind == "form":
        rec["extract_forms"] = True
    elif kind == "table_heavy":
        rec["extract_tables"] = True

    return rec


__all__ = ["profile_pdf", "recommend_pipeline", "PdfKind"]
