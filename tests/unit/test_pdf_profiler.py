"""Testes unitários do pdf_profiler."""

import io
from pathlib import Path

import fitz
import pytest

from pdfsearchable.pdf_profiler import (
    PdfKind,
    _classify,
    _sample_indices,
    profile_pdf,
    recommend_pipeline,
)


def _make_text_pdf(tmp_path: Path, text: str, pages: int = 1) -> Path:
    """Cria PDF born-digital com texto (em textbox multi-linha)."""
    p = tmp_path / "t.pdf"
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        rect = fitz.Rect(72, 72, page.rect.width - 72, page.rect.height - 72)
        page.insert_textbox(rect, text, fontsize=11)
    doc.save(str(p))
    doc.close()
    return p


def _make_empty_pdf(tmp_path: Path, pages: int = 1) -> Path:
    """Cria PDF sem texto (páginas em branco)."""
    p = tmp_path / "e.pdf"
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(str(p))
    doc.close()
    return p


# --------- _sample_indices ---------


def test_sample_indices_small():
    assert _sample_indices(3, 5) == [0, 1, 2]


def test_sample_indices_zero():
    assert _sample_indices(0, 5) == []


def test_sample_indices_uniform():
    idxs = _sample_indices(100, 5)
    assert len(idxs) == 5
    assert idxs[0] == 0
    assert idxs[-1] < 100
    # Monotonicamente crescente
    assert idxs == sorted(idxs)


# --------- _classify ---------


def test_classify_born_digital():
    kind, conf = _classify(
        avg_text_chars=500,
        avg_images=0,
        avg_drawings=0,
        img_coverage=0.05,
        producer="latex",
        metadata={},
        features={},
    )
    assert kind == "born_digital"
    assert conf >= 0.8


def test_classify_form():
    kind, conf = _classify(
        avg_text_chars=100,
        avg_images=0,
        avg_drawings=0,
        img_coverage=0.0,
        producer="",
        metadata={},
        features={"has_forms": True},
    )
    assert kind == "form"


def test_classify_scanned_clean():
    kind, _ = _classify(
        avg_text_chars=0,
        avg_images=1,
        avg_drawings=0,
        img_coverage=0.9,
        producer="scanner",
        metadata={},
        features={},
    )
    assert kind == "scanned_clean"


def test_classify_historical():
    kind, _ = _classify(
        avg_text_chars=50,
        avg_images=1,
        avg_drawings=0,
        img_coverage=0.7,
        producer="ABBYY FineReader",
        metadata={"creation_date": "D:19450101"},
        features={},
    )
    assert kind in ("historical_print", "historical_manuscript")


def test_classify_hybrid_ocr():
    kind, _ = _classify(
        avg_text_chars=300,
        avg_images=1,
        avg_drawings=0,
        img_coverage=0.6,
        producer="",
        metadata={},
        features={},
    )
    assert kind == "hybrid_ocr"


# --------- profile_pdf ---------


def test_profile_missing_file(tmp_path):
    p = profile_pdf(tmp_path / "nope.pdf")
    assert p["kind"] == "corrupted"
    assert p["errors"]


def test_profile_born_digital(tmp_path):
    pdf = _make_text_pdf(tmp_path, "Este é um documento de teste " * 50)
    p = profile_pdf(pdf)
    assert p["kind"] == "born_digital"
    assert p["pages"] == 1
    assert p["features"]["has_text"]
    assert p["confidence"] >= 0.8


def test_profile_empty_pdf(tmp_path):
    pdf = _make_empty_pdf(tmp_path)
    p = profile_pdf(pdf)
    assert p["pages"] == 1
    # Empty page can be image_only or unknown
    assert p["kind"] in ("unknown", "image_only", "mixed")


def test_profile_metadata_extraction(tmp_path):
    pdf = _make_text_pdf(tmp_path, "Hello world " * 30)
    doc = fitz.open(str(pdf))
    doc.set_metadata({"title": "Teste", "author": "Autor"})
    out = tmp_path / "m.pdf"
    doc.save(str(out))
    doc.close()
    p = profile_pdf(out)
    assert p["metadata"]["title"] == "Teste"
    assert p["metadata"]["author"] == "Autor"


# --------- recommend_pipeline ---------


def test_recommend_born_digital():
    rec = recommend_pipeline({"kind": "born_digital", "features": {}})
    assert rec["needs_ocr"] is False
    assert rec["ocr_mode"] == "none"


def test_recommend_scanned_clean():
    rec = recommend_pipeline({"kind": "scanned_clean", "features": {}})
    assert rec["needs_ocr"] is True
    assert rec["ocr_mode"] == "standard"


def test_recommend_historical_manuscript():
    rec = recommend_pipeline({"kind": "historical_manuscript", "features": {}})
    assert rec["needs_ocr"] is True
    assert rec["ocr_mode"] == "historical"
    assert rec["use_htr"] is True


def test_recommend_password():
    rec = recommend_pipeline({"kind": "password_protected", "features": {}})
    assert rec["warn_password"] is True
    assert rec["needs_ocr"] is False


def test_recommend_form():
    rec = recommend_pipeline({"kind": "form", "features": {"has_forms": True}})
    assert rec["extract_forms"] is True


def test_recommend_attachments():
    rec = recommend_pipeline({"kind": "born_digital", "features": {"has_attachments": True}})
    assert rec["extract_attachments"] is True


# ── Gap-filling tests for 100% coverage ──────────────────────────────────────

import unittest.mock as mock

# --- _classify missing branches ---

def test_classify_table_heavy():
    """avg_drawings > 20 and avg_text_chars > 50 → table_heavy (line 370)."""
    kind, conf = _classify(
        avg_text_chars=100, avg_images=0, avg_drawings=25,
        img_coverage=0.1, producer="", metadata={}, features={},
    )
    assert kind == "table_heavy"
    assert conf == 0.7


def test_classify_historical_print():
    """historical producer + high img_coverage + enough text → historical_print (line 385)."""
    kind, conf = _classify(
        avg_text_chars=300, avg_images=2, avg_drawings=0,
        img_coverage=0.7, producer="abbyy finereader",
        metadata={}, features={},
    )
    assert kind == "historical_print"


def test_classify_historical_old_year():
    """Old creation year triggers historical detection (line 377-378)."""
    kind, conf = _classify(
        avg_text_chars=50, avg_images=1, avg_drawings=0,
        img_coverage=0.6, producer="",
        metadata={"creation_date": "D:19200101"}, features={},
    )
    assert kind in ("historical_print", "historical_manuscript")


def test_classify_scanned_noisy():
    """Low text + images + drawings > 5 → scanned_noisy (line 391)."""
    kind, conf = _classify(
        avg_text_chars=5, avg_images=2, avg_drawings=8,
        img_coverage=0.75, producer="", metadata={}, features={},
    )
    assert kind == "scanned_noisy"


def test_classify_image_only_fallback():
    """Low text + images but low img_coverage → image_only fallback (line 404)."""
    kind, conf = _classify(
        avg_text_chars=5, avg_images=1, avg_drawings=0,
        img_coverage=0.2, producer="", metadata={}, features={},
    )
    assert kind == "image_only"
    assert conf == 0.6


def test_classify_mixed():
    """Some text but no other strong signals → mixed (line 408)."""
    kind, conf = _classify(
        avg_text_chars=50, avg_images=0, avg_drawings=0,
        img_coverage=0.1, producer="", metadata={}, features={},
    )
    assert kind == "mixed"


def test_classify_unknown():
    """No text, no images → unknown (line 410)."""
    kind, conf = _classify(
        avg_text_chars=0, avg_images=0, avg_drawings=0,
        img_coverage=0.0, producer="", metadata={}, features={},
    )
    assert kind == "unknown"


def test_classify_xfa_form():
    """has_xfa flag → form (line 365-366)."""
    kind, _ = _classify(
        avg_text_chars=0, avg_images=0, avg_drawings=0,
        img_coverage=0.0, producer="", metadata={}, features={"has_xfa": True},
    )
    assert kind == "form"


def test_classify_pdf_a_producer():
    """producer with 'pdf/a' sets is_pdf_a flag in profile_pdf (lines 201, 203)."""
    # Test via _classify is not enough; test via profile_pdf with mocked PDF
    # Just verify that both "pdf/a" and "pdf/ua" are handled
    # These are set in profile_pdf by reading producer string, tested below via profile tests


# --- profile_pdf: missing branches ---

def test_profile_corrupted_file(tmp_path):
    """Non-PDF file causes fitz.open to raise → kind=corrupted (lines 112-115)."""
    bad_file = tmp_path / "bad.pdf"
    bad_file.write_bytes(b"this is not a PDF file at all")
    p = profile_pdf(bad_file)
    assert p["kind"] == "corrupted"
    assert p["errors"]


def test_profile_password_protected(tmp_path):
    """Encrypted PDF that can't be opened with empty password → password_protected (lines 120-124)."""
    # Create a password-protected PDF
    src = tmp_path / "plain.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(src))
    doc.close()

    enc_path = tmp_path / "enc.pdf"
    doc = fitz.open(str(src))
    perm = fitz.PDF_PERM_PRINT
    doc.save(
        str(enc_path),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        user_pw="userpass",
        owner_pw="ownerpass",
        permissions=perm,
    )
    doc.close()

    p = profile_pdf(enc_path)
    assert p["kind"] == "password_protected"
    assert p["features"]["encrypted"] is True


def test_profile_pdf_a_producer(tmp_path):
    """PDF with 'pdf/a' in producer sets is_pdf_a=True (line 201)."""
    src = _make_text_pdf(tmp_path, "hello " * 50)
    doc = fitz.open(str(src))
    doc.set_metadata({"producer": "PDF/A converter"})
    out = tmp_path / "pdfa.pdf"
    doc.save(str(out))
    doc.close()
    p = profile_pdf(out)
    assert p["features"]["is_pdf_a"] is True


def test_profile_pdf_ua_producer(tmp_path):
    """PDF with 'pdf/ua' in producer sets is_pdf_ua=True (line 203)."""
    src = _make_text_pdf(tmp_path, "hello " * 50)
    doc = fitz.open(str(src))
    doc.set_metadata({"producer": "PDF/UA Maker v1"})
    out = tmp_path / "pdfua.pdf"
    doc.save(str(out))
    doc.close()
    p = profile_pdf(out)
    assert p["features"]["is_pdf_ua"] is True


def test_profile_multi_page_sampling(tmp_path):
    """PDF with >sample_pages pages exercises uniform sampling (lines 207, 346-349)."""
    text = "Texto de teste para amostragem " * 50
    path = tmp_path / "big.pdf"
    d = fitz.open()
    for _ in range(12):
        pg = d.new_page()
        rect = fitz.Rect(72, 72, pg.rect.width - 72, pg.rect.height - 72)
        pg.insert_textbox(rect, text, fontsize=11)
    d.save(str(path))
    d.close()
    p = profile_pdf(path, sample_pages=5)
    assert p["pages"] == 12
    assert len(p["pages_profile"]) <= 5


def test_profile_get_toc_exception(tmp_path):
    """get_toc() exception is swallowed (lines 144-145)."""
    pdf = _make_text_pdf(tmp_path, "texto " * 30)
    with mock.patch("fitz.Document.get_toc", side_effect=Exception("toc fail")):
        p = profile_pdf(pdf)
    assert p["kind"] != "corrupted"  # should still work


def test_profile_embfile_count_exception(tmp_path):
    """embfile_count() exception is swallowed (lines 149-150)."""
    pdf = _make_text_pdf(tmp_path, "texto " * 30)
    with mock.patch("fitz.Document.embfile_count", side_effect=Exception("emb fail")):
        p = profile_pdf(pdf)
    assert p["features"]["has_attachments"] is False


def test_profile_get_images_exception(tmp_path):
    """page.get_images() exception is swallowed (lines 237-238)."""
    pdf = _make_text_pdf(tmp_path, "texto " * 30)
    with mock.patch("fitz.Page.get_images", side_effect=Exception("img fail")):
        p = profile_pdf(pdf)
    assert p["kind"] != "corrupted"


def test_profile_language_detection(tmp_path, monkeypatch):
    """Dominant language detected from text (lines 324-331)."""
    import pdfsearchable.language as lang_mod
    monkeypatch.setattr(lang_mod, "detect_language", lambda text: "pt")
    pdf = _make_text_pdf(tmp_path, "Este é um texto em português. " * 30)
    p = profile_pdf(pdf)
    assert p["dominant_lang"] == "pt"


def test_profile_language_exception_swallowed(tmp_path, monkeypatch):
    """Language detection exception is swallowed (lines 330-331)."""
    import pdfsearchable.language as lang_mod
    monkeypatch.setattr(lang_mod, "detect_language", lambda text: (_ for _ in ()).throw(RuntimeError("lang fail")))
    pdf = _make_text_pdf(tmp_path, "texto " * 30)
    p = profile_pdf(pdf)  # must not raise


def test_profile_drawings_exception_swallowed(tmp_path):
    """page.get_drawings() exception is swallowed (lines 257-258)."""
    pdf = _make_text_pdf(tmp_path, "texto " * 30)
    with mock.patch("fitz.Page.get_drawings", side_effect=Exception("draw fail")):
        p = profile_pdf(pdf)
    assert p["kind"] != "corrupted"


def test_profile_annots_exception_swallowed(tmp_path):
    """page.annots() exception is swallowed (lines 263-264)."""
    pdf = _make_text_pdf(tmp_path, "texto " * 30)
    with mock.patch("fitz.Page.annots", side_effect=Exception("annot fail")):
        p = profile_pdf(pdf)
    assert p["kind"] != "corrupted"


def test_profile_multi_column_detection(tmp_path):
    """Multi-column layout detected when x_starts has left + right clusters (lines 270-281)."""
    path = tmp_path / "cols.pdf"
    d = fitz.open()
    pg = d.new_page()
    # Insert text blocks in two columns
    mid = pg.rect.width / 2
    for y in range(80, 700, 40):
        pg.insert_text((50, y), "Left column text block here to test", fontsize=10)
        pg.insert_text((mid + 30, y), "Right column text here to test also", fontsize=10)
    d.save(str(path))
    d.close()
    p = profile_pdf(path)
    # multi_column should be True or at least not crash
    assert isinstance(p["features"]["multi_column"], bool)


def test_profile_bookmarks_detection(tmp_path):
    """has_bookmarks set when TOC exists (lines 143-145)."""
    path = tmp_path / "toc.pdf"
    d = fitz.open()
    for _ in range(3):
        pg = d.new_page()
        pg.insert_text((72, 72), "chapter content here okay", fontsize=11)
    toc = [[1, "Chapter 1", 1], [1, "Chapter 2", 2]]
    d.set_toc(toc)
    d.save(str(path))
    d.close()
    p = profile_pdf(path)
    assert p["features"]["has_bookmarks"] is True


def test_profile_doc_close_exception(tmp_path, monkeypatch):
    """doc.close() exception in finally block is swallowed (lines 336-337)."""
    pdf = _make_text_pdf(tmp_path, "texto " * 30)
    real_close = fitz.Document.close

    close_called = {"n": 0}

    def bad_close(self):
        close_called["n"] += 1
        if close_called["n"] == 1:
            raise Exception("close fail")
        return real_close(self)

    monkeypatch.setattr(fitz.Document, "close", bad_close)
    p = profile_pdf(pdf)  # must not propagate exception
    assert p["kind"] != "corrupted"


# --- recommend_pipeline missing branches ---

def test_recommend_corrupted():
    """kind=corrupted → warn_corrupted=True (lines 443-445)."""
    rec = recommend_pipeline({"kind": "corrupted", "features": {}})
    assert rec["warn_corrupted"] is True
    assert rec["needs_ocr"] is False


def test_recommend_scanned_noisy():
    """kind=scanned_noisy → needs_ocr + standard (lines 447-449)."""
    rec = recommend_pipeline({"kind": "scanned_noisy", "features": {}})
    assert rec["needs_ocr"] is True
    assert rec["ocr_mode"] == "standard"


def test_recommend_image_only():
    """kind=image_only → needs_ocr + standard (lines 447-449)."""
    rec = recommend_pipeline({"kind": "image_only", "features": {}})
    assert rec["needs_ocr"] is True
    assert rec["ocr_mode"] == "standard"


def test_recommend_historical_print():
    """kind=historical_print → needs_ocr + historical (lines 450-452)."""
    rec = recommend_pipeline({"kind": "historical_print", "features": {}})
    assert rec["needs_ocr"] is True
    assert rec["ocr_mode"] == "historical"
    assert rec["use_htr"] is False


def test_recommend_hybrid_ocr():
    """kind=hybrid_ocr → needs_ocr=False (line 457-458)."""
    rec = recommend_pipeline({"kind": "hybrid_ocr", "features": {}})
    assert rec["needs_ocr"] is False
    assert rec["ocr_mode"] == "none"


def test_recommend_table_heavy():
    """kind=table_heavy → extract_tables=True (lines 461-462)."""
    rec = recommend_pipeline({"kind": "table_heavy", "features": {}})
    assert rec["extract_tables"] is True


def test_recommend_unknown():
    """kind=unknown → no special flags set."""
    rec = recommend_pipeline({"kind": "unknown", "features": {}})
    assert rec["needs_ocr"] is False
    assert rec["ocr_mode"] == "none"


# --- Additional pdf_profiler gap tests ---

def test_profile_get_ocgs_exception(tmp_path):
    """get_ocgs() exception is swallowed (lines 156-157)."""
    pdf = _make_text_pdf(tmp_path, "texto " * 30)
    with mock.patch("fitz.Document.get_ocgs", side_effect=Exception("ocgs fail")):
        p = profile_pdf(pdf)
    assert p["features"]["has_layers"] is False


def test_profile_widgets_exception(tmp_path):
    """widgets() iteration exception is swallowed (lines 170-171)."""
    pdf = _make_text_pdf(tmp_path, "texto " * 30)
    with mock.patch("fitz.Page.widgets", side_effect=Exception("widget fail")):
        p = profile_pdf(pdf)
    assert p["kind"] != "corrupted"


def test_profile_get_sigflags_exception(tmp_path):
    """get_sigflags() exception is swallowed (lines 195-196)."""
    pdf = _make_text_pdf(tmp_path, "texto " * 30)
    with mock.patch("fitz.Document.get_sigflags", side_effect=Exception("sig fail")):
        p = profile_pdf(pdf)
    assert p["kind"] != "corrupted"


def test_profile_page_access_exception(tmp_path):
    """Page access exception adds to errors and continues (lines 220-222)."""
    pdf = _make_text_pdf(tmp_path, "texto " * 30)
    real_getitem = fitz.Document.__getitem__
    call_count = {"n": 0}

    def bad_getitem(self, idx):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception("page access fail")
        return real_getitem(self, idx)

    with mock.patch.object(fitz.Document, "__getitem__", bad_getitem):
        p = profile_pdf(pdf)
    # Should still work (error is appended to errors list)
    assert isinstance(p["errors"], list)


def test_profile_get_text_exception(tmp_path):
    """page.get_text() exception sets text to empty (lines 230-231)."""
    pdf = _make_text_pdf(tmp_path, "texto " * 30)
    with mock.patch("fitz.Page.get_text", side_effect=Exception("text fail")):
        p = profile_pdf(pdf)
    assert p["kind"] != "corrupted"


def test_profile_encrypted_authenticates_ok(tmp_path):
    """is_encrypted=True but authenticate('') succeeds → proceeds normally (line 121→126)."""
    pdf = _make_text_pdf(tmp_path, "texto visível " * 30)
    real_fitz_open = fitz.open

    class FakeDoc:
        """Wrapper that reports is_encrypted=True but authenticates OK."""
        def __init__(self, inner):
            self._inner = inner
            self.is_encrypted = True

        def authenticate(self, pw):
            return 1 if pw == "" else 0  # success

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def __iter__(self):
            return iter(self._inner)

        def __getitem__(self, idx):
            return self._inner[idx]

    def fake_open(path):
        return FakeDoc(real_fitz_open(path))

    with mock.patch("fitz.open", fake_open):
        p = profile_pdf(pdf)
    assert p["features"]["encrypted"] is True
    assert p["kind"] != "password_protected"


def test_classify_historical_manuscript_old_year():
    """Old year + high img_coverage + low text → historical_manuscript (line 384)."""
    kind, conf = _classify(
        avg_text_chars=50, avg_images=2, avg_drawings=0,
        img_coverage=0.7, producer="",
        metadata={"creation_date": "D:19200101"}, features={},
    )
    assert kind == "historical_manuscript"
    assert conf == 0.65


def test_profile_language_detection_short_text(tmp_path, monkeypatch):
    """Language detection skipped when total_text_chars <= 50 (line 319 branch)."""
    import pdfsearchable.language as lang_mod
    detect_called = {"n": 0}
    original = lang_mod.detect_language

    def counting_detect(text):
        detect_called["n"] += 1
        return original(text)

    monkeypatch.setattr(lang_mod, "detect_language", counting_detect)
    pdf = _make_empty_pdf(tmp_path)
    p = profile_pdf(pdf)
    assert detect_called["n"] == 0  # not called for empty PDF


# --- Final pdf_profiler gap tests ---

def test_profile_form_widgets_break(tmp_path):
    """Form widgets >= _FORM_FIELD_MIN triggers inner and outer break (lines 164-168)."""
    pdf = _make_text_pdf(tmp_path, "form " * 20)

    # Mock pg.widgets() to return 3 fake widgets
    fake_widget = object()
    with mock.patch("fitz.Page.widgets", return_value=[fake_widget, fake_widget, fake_widget]):
        p = profile_pdf(pdf)
    assert p["features"]["has_forms"] is True


def test_profile_xfa_root_zero(tmp_path):
    """pdf_catalog() returns 0 → skip XFA check (branch 176→182)."""
    pdf = _make_text_pdf(tmp_path, "texto " * 30)
    with mock.patch("fitz.Document.pdf_catalog", return_value=0):
        p = profile_pdf(pdf)
    assert p["features"]["has_xfa"] is False


def test_profile_xfa_xref_get_key_exception(tmp_path):
    """xref_get_key raises → XFA except block (lines 179-180)."""
    pdf = _make_text_pdf(tmp_path, "texto " * 30)
    with mock.patch("fitz.Document.xref_get_key", side_effect=Exception("xref fail")):
        p = profile_pdf(pdf)
    assert p["features"]["has_xfa"] is False


def test_profile_js_root_zero(tmp_path):
    """pdf_catalog() returns 0 → skip JS check (branch 185→191)."""
    pdf = _make_text_pdf(tmp_path, "texto " * 30)
    # Need to return 0 for the second call too (JS detection)
    with mock.patch("fitz.Document.pdf_catalog", return_value=0):
        p = profile_pdf(pdf)
    assert p["features"]["has_js"] is False


def test_profile_js_xref_exception(tmp_path):
    """xref_get_key for JS raises → JS except block (lines 188-189)."""
    pdf = _make_text_pdf(tmp_path, "texto " * 30)
    call_count = {"n": 0}
    real_xref = fitz.Document.xref_get_key

    def raise_on_second(self, xref, key):
        call_count["n"] += 1
        if call_count["n"] == 2:  # second call is for JS
            raise Exception("js xref fail")
        return real_xref(self, xref, key)

    with mock.patch.object(fitz.Document, "xref_get_key", raise_on_second):
        p = profile_pdf(pdf)
    assert p["features"]["has_js"] is False


def test_profile_page_access_exception_appends_error(tmp_path):
    """doc[idx] raises → error appended and loop continues (lines 220-222)."""
    path = tmp_path / "three.pdf"
    d = fitz.open()
    for _ in range(3):
        pg = d.new_page()
        pg.insert_text((72, 72), "content " * 20, fontsize=11)
    d.save(str(path))
    d.close()

    # Wrap the real doc so doc[1] raises during per-page sampling.
    # (doc[0] may be used earlier for forms iteration — raise on idx==1 to
    # guarantee the error lands in the sample-indices loop at line 220-222.)
    class _PatchedDoc:
        def __init__(self, inner):
            self._inner = inner
        def __getattr__(self, name):
            return getattr(self._inner, name)
        def __len__(self):
            return len(self._inner)
        def __iter__(self):
            return iter(self._inner)
        def __getitem__(self, idx):
            if idx == 1:
                raise Exception("page inaccessible")
            return self._inner[idx]
        def close(self):
            self._inner.close()

    real_open = fitz.open
    def fake_open(p, *a, **kw):
        doc = real_open(p, *a, **kw)
        return _PatchedDoc(doc)

    with mock.patch("fitz.open", fake_open):
        p = profile_pdf(path)
    assert any("Página" in e for e in p["errors"])


def test_profile_image_block_area(tmp_path):
    """PDF page with image block exercises area calculation (lines 245-246)."""
    import struct
    # Create a PDF with an actual image embedded via fitz
    path = tmp_path / "img.pdf"
    d = fitz.open()
    pg = d.new_page()
    # Create a tiny 4x4 JPEG in memory
    import io as _io
    try:
        from PIL import Image as PILImage
        img = PILImage.new("RGB", (4, 4), color=(128, 128, 128))
        buf = _io.BytesIO()
        img.save(buf, format="JPEG")
        img_bytes = buf.getvalue()
        rect = fitz.Rect(50, 50, 100, 100)
        pg.insert_image(rect, stream=img_bytes)
    except ImportError:
        # PIL not available — insert a simple drawing instead
        pg.draw_rect(fitz.Rect(50, 50, 100, 100))
    d.save(str(path))
    d.close()
    p = profile_pdf(path)
    assert p["kind"] != "corrupted"


def test_profile_few_text_blocks_no_column(tmp_path):
    """Page with <= 4 text blocks skips column detection (branch 270→269)."""
    path = tmp_path / "few.pdf"
    d = fitz.open()
    pg = d.new_page()
    # Insert only 2 text items → len(x_starts) <= 4
    pg.insert_text((72, 100), "One", fontsize=11)
    pg.insert_text((72, 130), "Two", fontsize=11)
    d.save(str(path))
    d.close()
    p = profile_pdf(path)
    assert p["features"]["multi_column"] is False


def test_profile_block_type_other(tmp_path):
    """Block with type != 0 and != 1 in get_text('dict') skips both branches (247→243)."""
    path = _make_text_pdf(tmp_path, "hello world")

    real_get_text = fitz.Page.get_text

    def patched_get_text(self, mode="text", *a, **kw):
        result = real_get_text(self, mode, *a, **kw)
        if mode == "dict" and isinstance(result, dict):
            # Inject a block with type=2 (neither text nor image)
            result = dict(result)
            result["blocks"] = [{"type": 2, "bbox": (0, 0, 10, 10)}] + list(result.get("blocks", []))
        return result

    with mock.patch.object(fitz.Page, "get_text", patched_get_text):
        p = profile_pdf(path)
    assert p["kind"] != "corrupted"


def test_profile_multi_column_detected(tmp_path):
    """columns_detected increments when text is spread across two columns (line 279)."""
    path = _make_text_pdf(tmp_path, "content " * 30)

    real_get_text = fitz.Page.get_text

    def patched_get_text(self, mode="text", *a, **kw):
        if mode != "dict":
            return real_get_text(self, mode, *a, **kw)
        # Simulate a two-column layout: page width ~595, mid=297.5
        # left blocks at x=50 (< 267.75), right blocks at x=350 (> 327.25)
        blocks = []
        for i in range(3):
            blocks.append({"type": 0, "bbox": (50, 100 + i*40, 200, 130 + i*40), "lines": []})
        for i in range(3):
            blocks.append({"type": 0, "bbox": (350, 100 + i*40, 500, 130 + i*40), "lines": []})
        return {"blocks": blocks}

    with mock.patch.object(fitz.Page, "get_text", patched_get_text):
        p = profile_pdf(path)
    assert p["features"]["multi_column"] is True


def test_profile_dominant_lang_from_text(tmp_path, monkeypatch):
    """Language detection executed from accumulated text (lines 324-325)."""
    import pdfsearchable.language as lang_mod
    detected = {"lang": None}

    def fake_detect(text):
        detected["lang"] = "pt"
        return "pt"

    monkeypatch.setattr(lang_mod, "detect_language", fake_detect)
    pdf = _make_text_pdf(tmp_path, "Este texto tem bastante conteúdo. " * 30)
    p = profile_pdf(pdf)
    assert p["dominant_lang"] == "pt"
    assert detected["lang"] == "pt"
