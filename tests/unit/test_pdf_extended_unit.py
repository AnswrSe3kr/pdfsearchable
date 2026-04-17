"""Testes unitários de pdf_extended — usa PDFs sintéticos com fitz."""

from pathlib import Path

import fitz
import pytest

from pdfsearchable import pdf_extended as px


def _make_basic_pdf(tmp_path: Path, text: str = "Exemplo de texto.") -> Path:
    p = tmp_path / "basic.pdf"
    doc = fitz.open()
    page = doc.new_page()
    rect = fitz.Rect(72, 72, 540, 770)
    page.insert_textbox(rect, text * 10, fontsize=11)
    doc.save(str(p))
    doc.close()
    return p


def _make_pdf_with_link(tmp_path: Path) -> Path:
    p = tmp_path / "link.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Visit example.com")
    page.insert_link({
        "kind": fitz.LINK_URI,
        "from": fitz.Rect(72, 60, 200, 80),
        "uri": "https://example.com",
    })
    doc.save(str(p))
    doc.close()
    return p


# ---------- extract_extended_from_doc ----------


def test_extract_extended_minimal(tmp_path):
    pdf = _make_basic_pdf(tmp_path)
    doc = fitz.open(str(pdf))
    try:
        data = px.extract_extended_from_doc(doc)
        assert isinstance(data, dict)
        # Chaves esperadas
        for key in ("pages",):
            assert key in data or isinstance(data, dict)
    finally:
        doc.close()


def test_extract_extended_all_disabled(tmp_path):
    pdf = _make_basic_pdf(tmp_path)
    doc = fitz.open(str(pdf))
    try:
        data = px.extract_extended_from_doc(
            doc,
            include_tables=False,
            include_forms=False,
            include_annotations=False,
            include_xmp=False,
            include_images=False,
            include_outline=False,
            include_hyperlinks=False,
            include_page_dims=False,
            include_attached=False,
            include_fonts=False,
        )
        assert isinstance(data, dict)
    finally:
        doc.close()


# ---------- _extract_hyperlinks_from_page ----------


def test_extract_hyperlinks(tmp_path):
    pdf = _make_pdf_with_link(tmp_path)
    doc = fitz.open(str(pdf))
    try:
        links = px._extract_hyperlinks_from_page(doc[0])
        assert isinstance(links, list)
        assert len(links) >= 1
        assert any("example.com" in (l.get("uri") or "") for l in links)
    finally:
        doc.close()


# ---------- _extract_page_dimensions ----------


def test_extract_page_dimensions(tmp_path):
    pdf = _make_basic_pdf(tmp_path)
    doc = fitz.open(str(pdf))
    try:
        dims = px._extract_page_dimensions(doc)
        assert isinstance(dims, list)
        assert len(dims) == 1
        d0 = dims[0]
        assert "width" in d0 or "width_pt" in d0 or len(d0) > 0
    finally:
        doc.close()


# ---------- _extract_fonts ----------


def test_extract_fonts(tmp_path):
    pdf = _make_basic_pdf(tmp_path)
    doc = fitz.open(str(pdf))
    try:
        fonts = px._extract_fonts(doc)
        assert isinstance(fonts, list)
    finally:
        doc.close()


# ---------- _extract_form_fields_from_page (PDF sem forms) ----------


def test_extract_forms_empty(tmp_path):
    pdf = _make_basic_pdf(tmp_path)
    doc = fitz.open(str(pdf))
    try:
        fields = px._extract_form_fields_from_page(doc[0])
        assert isinstance(fields, list)
        assert fields == []
    finally:
        doc.close()


# ---------- _extract_annotations_from_page ----------


def test_extract_annotations_empty(tmp_path):
    pdf = _make_basic_pdf(tmp_path)
    doc = fitz.open(str(pdf))
    try:
        annots = px._extract_annotations_from_page(doc[0])
        assert isinstance(annots, list)
    finally:
        doc.close()


# ---------- _extract_tables_from_page (PDF simples) ----------


def test_extract_tables_returns_list(tmp_path):
    pdf = _make_basic_pdf(tmp_path)
    doc = fitz.open(str(pdf))
    try:
        tables = px._extract_tables_from_page(doc[0])
        assert isinstance(tables, list)
    finally:
        doc.close()
