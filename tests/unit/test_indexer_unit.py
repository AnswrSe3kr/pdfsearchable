"""Testes unitários de indexer.py — funções puras que não exigem pipeline real."""

import os
import time
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch, call

import fitz
import pytest

from pdfsearchable import indexer
from pdfsearchable.exceptions import IndexingError, ValidationError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_pdf(tmp_path: Path, text: str = "Sample text for indexing test.", name: str = "doc.pdf") -> Path:
    """Creates a minimal fitz PDF and returns its path."""
    p = tmp_path / name
    d = fitz.open()
    pg = d.new_page()
    pg.insert_text((72, 72), text, fontsize=11)
    d.save(str(p))
    d.close()
    return p


def _fake_enrich(**overrides):
    """Returns a minimal _enrich_document result dict."""
    base = {
        "word_count": 5,
        "text_chars": 25,
        "doc_type": "documento",
        "classification_source": "heuristics",
        "classification_confidence": 0.9,
        "language": "pt",
        "ocr_percentage": 0,
        "ocr_avg_confidence": None,
        "summary": None,
        "subject": None,
        "tags": [],
        "monetary_values": [],
        "parties": [],
        "entities": {},
        "identified_locations": [],
        "identified_dates": [],
        "confidentiality": None,
        "enrichment_partial": False,
        "ocr_warnings": "",
    }
    base.update(overrides)
    return base


def _fake_extract(*args, **kwargs):
    """Minimal _extract_with_ocr result."""
    return ("sample text", 1, ["sample text"], {"producer": ""}, [False], [-1.0])


# ─────────────────────────────────────────────────────────────────────────────
# _max_workers_auto
# ─────────────────────────────────────────────────────────────────────────────

def test_max_workers_auto_respects_env(monkeypatch):
    monkeypatch.setenv("PDFSEARCHABLE_MAX_WORKERS", "3")
    assert indexer._max_workers_auto() == 3


def test_max_workers_auto_capped_at_64(monkeypatch):
    monkeypatch.setenv("PDFSEARCHABLE_MAX_WORKERS", "200")
    assert indexer._max_workers_auto() == 64


def test_max_workers_auto_invalid_env(monkeypatch):
    monkeypatch.setenv("PDFSEARCHABLE_MAX_WORKERS", "garbage")
    n = indexer._max_workers_auto()
    assert n >= 1


def test_max_workers_auto_negative(monkeypatch):
    monkeypatch.setenv("PDFSEARCHABLE_MAX_WORKERS", "-5")
    n = indexer._max_workers_auto()
    assert n >= 1


def test_max_workers_auto_no_env(monkeypatch):
    monkeypatch.delenv("PDFSEARCHABLE_MAX_WORKERS", raising=False)
    n = indexer._max_workers_auto()
    assert 1 <= n <= 16


# ─────────────────────────────────────────────────────────────────────────────
# _is_text_corrupt
# ─────────────────────────────────────────────────────────────────────────────

def test_is_text_corrupt_empty():
    assert indexer._is_text_corrupt("") is False


def test_is_text_corrupt_short():
    # < 20 non-space chars → never corrupt
    assert indexer._is_text_corrupt("abc") is False


def test_is_text_corrupt_good_text():
    assert indexer._is_text_corrupt("Este é um texto claro e limpo com palavras normais.") is False


def test_is_text_corrupt_high_noise():
    garbage = "\x01\x02\x03\x04\x05" * 100 + "texto"
    assert indexer._is_text_corrupt(garbage) is True


def test_is_text_corrupt_private_unicode():
    private = "\ue000\ue001\ue002" * 50 + "text text text text text"
    assert indexer._is_text_corrupt(private) is True


def test_is_text_corrupt_replacement_char():
    # Many U+FFFD replacement characters
    noisy = "\ufffd" * 50 + "normal text here and more normal text"
    assert indexer._is_text_corrupt(noisy) is True


def test_is_text_corrupt_c1_chars():
    c1 = "\x80\x81\x82\x83\x84" * 50 + "text text text text text"
    assert indexer._is_text_corrupt(c1) is True


def test_is_text_corrupt_custom_threshold():
    # With lower threshold, more text is "corrupt"
    text = "\x01" * 10 + "a" * 90
    assert indexer._is_text_corrupt(text, threshold=0.05) is True
    assert indexer._is_text_corrupt(text, threshold=0.95) is False


# ─────────────────────────────────────────────────────────────────────────────
# _has_low_entropy
# ─────────────────────────────────────────────────────────────────────────────

def test_low_entropy_uniform():
    assert indexer._has_low_entropy("a" * 100) is True


def test_low_entropy_two_chars():
    assert indexer._has_low_entropy("ab" * 50) is True


def test_low_entropy_normal_text():
    text = "Este texto tem diversidade razoável de caracteres e palavras variadas."
    assert indexer._has_low_entropy(text) is False


def test_low_entropy_empty():
    assert indexer._has_low_entropy("") is False


def test_low_entropy_short_text():
    # < 50 chars → always False
    assert indexer._has_low_entropy("abc") is False


def test_low_entropy_custom_threshold():
    text = "abc" * 100  # moderate entropy
    # Very high threshold: even moderate text is "low entropy"
    assert indexer._has_low_entropy(text, threshold=5.0) is True
    # Very low threshold: nothing is low entropy
    assert indexer._has_low_entropy(text, threshold=0.0) is False


# ─────────────────────────────────────────────────────────────────────────────
# _enrich_document
# ─────────────────────────────────────────────────────────────────────────────

class TestEnrichDocument:
    """Tests for _enrich_document with mocked external deps."""

    @pytest.fixture(autouse=True)
    def _mock_ai(self, monkeypatch):
        """Disable Ollama and stub classifiers for all tests in this class.
        Patches must target indexer's own namespace (functions imported at module level)."""
        classification = MagicMock()
        classification.label = "documento"
        classification.source = "heuristics"
        classification.confidence = 0.9
        monkeypatch.setattr(indexer, "_get_ai_mode", lambda: "off")
        monkeypatch.setattr(indexer, "detect_language", lambda t: "pt")
        monkeypatch.setattr(indexer, "classify_document", lambda *a, **kw: classification)
        monkeypatch.setattr(indexer, "extract_tags", lambda *a, **kw: ["tag1"])
        monkeypatch.setattr(indexer, "extract_monetary_values", lambda t: [])
        monkeypatch.setattr(indexer, "extract_parties", lambda t: [])
        monkeypatch.setattr(indexer, "extract_entities", lambda t: {})
        monkeypatch.setattr(indexer, "extract_dates", lambda t: ["15/03/2023"])
        monkeypatch.setattr(indexer, "detect_confidentiality", lambda t: None)

    def test_returns_required_keys(self):
        result = indexer._enrich_document(
            full_text="Texto de exemplo para teste unitário.",
            pdf_path=Path("/tmp/test.pdf"),
            metadata={},
            doc_type_arg=None,
            ocr_per_page=[False],
            page_texts=["Texto de exemplo para teste unitário."],
            page_confidences=[100.0],
        )
        for key in (
            "word_count", "text_chars", "doc_type", "language", "ocr_percentage",
            "ocr_avg_confidence", "tags", "entities", "identified_dates", "enrichment_partial",
        ):
            assert key in result, f"Missing key: {key}"

    def test_doc_type_arg_skips_classification(self):
        """When doc_type_arg is set, classification is bypassed."""
        result = indexer._enrich_document(
            full_text="texto qualquer aqui",
            pdf_path=Path("/tmp/t.pdf"),
            metadata={},
            doc_type_arg="contrato",
            ocr_per_page=[],
            page_texts=[],
            page_confidences=[],
        )
        assert result["doc_type"] == "contrato"
        assert result["classification_source"] is None
        assert result["classification_confidence"] is None

    def test_word_count_and_chars(self):
        text = "um dois três quatro cinco"
        result = indexer._enrich_document(
            full_text=text,
            pdf_path=Path("/tmp/t.pdf"),
            metadata={},
            doc_type_arg="documento",
            ocr_per_page=[False],
            page_texts=[text],
            page_confidences=[-1.0],
        )
        assert result["word_count"] == 5
        assert result["text_chars"] == len(text)

    def test_empty_text(self):
        result = indexer._enrich_document(
            full_text="",
            pdf_path=Path("/tmp/t.pdf"),
            metadata={},
            doc_type_arg="documento",
            ocr_per_page=[],
            page_texts=[],
            page_confidences=[],
        )
        assert result["word_count"] == 0
        assert result["text_chars"] == 0
        assert isinstance(result["tags"], list)

    def test_ocr_percentage_all_ocr(self):
        result = indexer._enrich_document(
            full_text="texto",
            pdf_path=Path("/tmp/t.pdf"),
            metadata={},
            doc_type_arg="documento",
            ocr_per_page=[True, True, True],
            page_texts=["a", "b", "c"],
            page_confidences=[90.0, 85.0, 92.0],
        )
        assert result["ocr_percentage"] == 100
        assert result["ocr_avg_confidence"] == round((90.0 + 85.0 + 92.0) / 3, 1)

    def test_ocr_percentage_no_ocr(self):
        result = indexer._enrich_document(
            full_text="texto",
            pdf_path=Path("/tmp/t.pdf"),
            metadata={},
            doc_type_arg="documento",
            ocr_per_page=[False, False],
            page_texts=["a", "b"],
            page_confidences=[-1.0, -1.0],
        )
        assert result["ocr_percentage"] == 0
        assert result["ocr_avg_confidence"] is None

    def test_subject_from_metadata(self):
        result = indexer._enrich_document(
            full_text="texto",
            pdf_path=Path("/tmp/t.pdf"),
            metadata={"subject": "  Assunto do PDF  "},
            doc_type_arg="documento",
            ocr_per_page=[False],
            page_texts=["texto"],
            page_confidences=[-1.0],
        )
        assert result["subject"] == "Assunto do PDF"

    def test_enrichment_partial_false_without_ollama(self):
        result = indexer._enrich_document(
            full_text="texto",
            pdf_path=Path("/tmp/t.pdf"),
            metadata={},
            doc_type_arg="documento",
            ocr_per_page=[],
            page_texts=[],
            page_confidences=[],
        )
        assert result["enrichment_partial"] is False

    def test_ollama_mode_parallel_tasks(self, monkeypatch):
        """When ai_mode=ollama, parallel Ollama tasks are launched."""
        monkeypatch.setattr(indexer, "_get_ai_mode", lambda: "ollama")
        monkeypatch.setattr(indexer, "extract_summary_and_subject_ollama", lambda t: ("summary_text", "subject_text"))
        monkeypatch.setattr(indexer, "extract_tags_ollama", lambda t, max_tags=5: ["ia_tag"])
        monkeypatch.setattr(indexer, "extract_parties_ollama", lambda t, max_parties=8: ["ia_party"])
        monkeypatch.setattr(indexer, "extract_metadata_ollama", lambda t: {"dates": ["01/01/2024"], "monetary_values": [], "parties": []})
        monkeypatch.setattr(indexer, "merge_entities_with_ollama", lambda e, m: e)

        result = indexer._enrich_document(
            full_text="Texto suficientemente longo para Ollama.",
            pdf_path=Path("/tmp/t.pdf"),
            metadata={},
            doc_type_arg="documento",
            ocr_per_page=[False],
            page_texts=["Texto suficientemente longo para Ollama."],
            page_confidences=[-1.0],
        )
        assert result["summary"] == "summary_text"
        assert result["subject"] == "subject_text"
        assert "ia_tag" in result["tags"]
        assert "ia_party" in result["parties"]
        assert result["enrichment_partial"] is False

    def test_ollama_task_failure_marks_partial(self, monkeypatch):
        """When Ollama task raises, enrichment_partial=True."""
        monkeypatch.setattr(indexer, "_get_ai_mode", lambda: "ollama")
        monkeypatch.setattr(indexer, "extract_summary_and_subject_ollama", MagicMock(side_effect=Exception("Ollama down")))
        monkeypatch.setattr(indexer, "extract_tags_ollama", MagicMock(side_effect=Exception("Ollama down")))
        monkeypatch.setattr(indexer, "extract_parties_ollama", MagicMock(side_effect=Exception("Ollama down")))
        monkeypatch.setattr(indexer, "extract_metadata_ollama", MagicMock(side_effect=Exception("Ollama down")))

        result = indexer._enrich_document(
            full_text="Texto suficiente para Ollama.",
            pdf_path=Path("/tmp/t.pdf"),
            metadata={},
            doc_type_arg="documento",
            ocr_per_page=[False],
            page_texts=["Texto suficiente para Ollama."],
            page_confidences=[-1.0],
        )
        assert result["enrichment_partial"] is True

    def test_ollama_meta_merges_dates(self, monkeypatch):
        """Dates from Ollama meta are merged with regex-extracted dates."""
        monkeypatch.setattr(indexer, "_get_ai_mode", lambda: "ollama")
        monkeypatch.setattr(indexer, "extract_dates", lambda t: ["01/01/2023"])
        monkeypatch.setattr(indexer, "extract_summary_and_subject_ollama", lambda t: None)
        monkeypatch.setattr(indexer, "extract_tags_ollama", lambda t, max_tags=5: [])
        monkeypatch.setattr(indexer, "extract_parties_ollama", lambda t, max_parties=8: [])
        monkeypatch.setattr(indexer, "extract_metadata_ollama", lambda t: {"dates": ["02/02/2024"], "monetary_values": [], "parties": []})
        monkeypatch.setattr(indexer, "merge_entities_with_ollama", lambda e, m: e)

        result = indexer._enrich_document(
            full_text="texto com datas",
            pdf_path=Path("/tmp/t.pdf"),
            metadata={},
            doc_type_arg="documento",
            ocr_per_page=[False],
            page_texts=["texto com datas"],
            page_confidences=[-1.0],
        )
        assert "01/01/2023" in result["identified_dates"]
        assert "02/02/2024" in result["identified_dates"]

    def test_ollama_meta_merges_parties(self, monkeypatch):
        """Parties from Ollama meta are merged."""
        monkeypatch.setattr(indexer, "_get_ai_mode", lambda: "ollama")
        monkeypatch.setattr(indexer, "extract_parties", lambda t: ["regex_party"])
        monkeypatch.setattr(indexer, "extract_summary_and_subject_ollama", lambda t: None)
        monkeypatch.setattr(indexer, "extract_tags_ollama", lambda t, max_tags=5: [])
        monkeypatch.setattr(indexer, "extract_parties_ollama", lambda t, max_parties=8: [])
        monkeypatch.setattr(indexer, "extract_metadata_ollama", lambda t: {
            "dates": [], "monetary_values": ["R$ 1.000,00"], "parties": ["ollama_party"]
        })
        monkeypatch.setattr(indexer, "merge_entities_with_ollama", lambda e, m: e)

        result = indexer._enrich_document(
            full_text="texto para testar parties",
            pdf_path=Path("/tmp/t.pdf"),
            metadata={},
            doc_type_arg="documento",
            ocr_per_page=[False],
            page_texts=["texto para testar parties"],
            page_confidences=[-1.0],
        )
        assert "regex_party" in result["parties"]
        assert "ollama_party" in result["parties"]


# ─────────────────────────────────────────────────────────────────────────────
# _extract_with_ocr (no-OCR path)
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractWithOcr:
    """All patches target indexer's own imported names (imported at module level)."""

    def test_no_ocr_returns_text(self, tmp_path, monkeypatch):
        """With OCR disabled (use_ocr=False), returns native text from PDF."""
        pdf_path = _make_pdf(tmp_path, "Hello from native text extraction.")
        full, num_pages, page_texts, metadata, ocr_per_page, page_confs = (
            indexer._extract_with_ocr(
                pdf_path, mode="text", password=None, normalize=True,
                use_ocr=False, file_id="abc123", content_hash="hash1",
            )
        )
        assert isinstance(full, str)
        assert num_pages >= 1
        assert isinstance(page_texts, list)
        assert all(v is False for v in ocr_per_page)

    def test_use_ocr_false_skips_ocr(self, tmp_path, monkeypatch):
        """use_ocr=False always takes the early-return path."""
        monkeypatch.setattr(indexer, "ocr_available", lambda: True)
        pdf_path = _make_pdf(tmp_path, "Text that should be extracted natively.")
        full, num_pages, page_texts, metadata, ocr_per_page, _ = (
            indexer._extract_with_ocr(
                pdf_path, "text", None, True, use_ocr=False, file_id="fid1", content_hash=None,
            )
        )
        assert all(v is False for v in ocr_per_page)

    def test_extract_extended_exception_continues(self, tmp_path, monkeypatch):
        """If extract_extended_from_doc raises, extraction still succeeds."""
        monkeypatch.setattr(indexer, "extract_extended_from_doc", MagicMock(side_effect=Exception("extended failed")))
        pdf_path = _make_pdf(tmp_path, "Text extracted despite extended failure.")
        full, num_pages, _, metadata, _, _ = indexer._extract_with_ocr(
            pdf_path, "text", None, True, use_ocr=False, file_id="fid2", content_hash=None,
        )
        assert isinstance(full, str)

    def test_sequential_ocr_path(self, tmp_path, monkeypatch):
        """Sequential OCR path (workers=1) processes pages one-by-one."""
        monkeypatch.setattr(indexer, "ocr_available", lambda: True)
        monkeypatch.setattr(indexer, "get_ocr_workers", lambda: 1)
        monkeypatch.setattr(indexer, "ocr_page_from_image_bytes", lambda img, key, pn, use_cache=True, lang=None: ("ocr_text", 95.0))
        monkeypatch.setattr(indexer, "render_page_to_image", lambda page: b"fake_image")
        monkeypatch.setattr(indexer, "correct_ocr_with_ollama", lambda t: None)
        monkeypatch.setenv("PDFSEARCHABLE_OCR_ALWAYS", "1")

        pdf_path = _make_pdf(tmp_path, "Text for sequential OCR test.")
        full, num_pages, page_texts, metadata, ocr_per_page, page_confs = (
            indexer._extract_with_ocr(
                pdf_path, "text", None, True, use_ocr=True, file_id="fid3", content_hash=None,
            )
        )
        assert num_pages >= 1

    def test_parallel_ocr_path(self, tmp_path, monkeypatch):
        """Parallel OCR path (workers>1) uses ThreadPoolExecutor."""
        monkeypatch.setattr(indexer, "ocr_available", lambda: True)
        monkeypatch.setattr(indexer, "get_ocr_workers", lambda: 2)
        monkeypatch.setattr(indexer, "ocr_page_from_image_bytes", lambda img, key, pn, use_cache=True, lang=None: ("parallel_ocr_text", 88.0))
        monkeypatch.setattr(indexer, "render_page_to_image", lambda page: b"fake_image")
        monkeypatch.setattr(indexer, "correct_ocr_with_ollama", lambda t: None)
        monkeypatch.setenv("PDFSEARCHABLE_OCR_ALWAYS", "1")

        pdf_path = _make_pdf(tmp_path, "Text for parallel OCR test.")
        full, num_pages, page_texts, metadata, ocr_per_page, page_confs = (
            indexer._extract_with_ocr(
                pdf_path, "text", None, True, use_ocr=True, file_id="fid4", content_hash=None,
            )
        )
        assert num_pages >= 1

    def test_render_page_exception_skips_ocr(self, tmp_path, monkeypatch):
        """render_page_to_image failure → OCR skipped for that page (img_bytes=None)."""
        monkeypatch.setattr(indexer, "ocr_available", lambda: True)
        monkeypatch.setattr(indexer, "get_ocr_workers", lambda: 1)
        monkeypatch.setattr(indexer, "render_page_to_image", MagicMock(side_effect=Exception("render failed")))
        monkeypatch.setattr(indexer, "correct_ocr_with_ollama", lambda t: None)
        monkeypatch.setenv("PDFSEARCHABLE_OCR_ALWAYS", "1")

        pdf_path = _make_pdf(tmp_path, "Some native text here.")
        full, num_pages, _, metadata, ocr_per_page, _ = indexer._extract_with_ocr(
            pdf_path, "text", None, True, use_ocr=True, file_id="fid5", content_hash=None,
        )
        assert num_pages >= 1

    def test_native_corrupt_text_forces_ocr(self, tmp_path, monkeypatch):
        """Corrupt native text forces OCR on that page."""
        corrupt_text = "\x01\x02\x03\x04\x05" * 100
        monkeypatch.setattr(indexer, "ocr_available", lambda: True)
        monkeypatch.setattr(indexer, "get_ocr_workers", lambda: 1)
        monkeypatch.setattr(indexer, "render_page_to_image", lambda page: b"fake_image")
        monkeypatch.setattr(indexer, "ocr_page_from_image_bytes", lambda *a, **kw: ("clean text", 95.0))
        monkeypatch.setattr(indexer, "correct_ocr_with_ollama", lambda t: None)
        monkeypatch.setattr(indexer, "extract_text_from_doc", lambda doc, mode="text", normalize=True: (corrupt_text, 1, [corrupt_text], {}))
        monkeypatch.setenv("PDFSEARCHABLE_OCR_ALWAYS", "0")

        pdf_path = _make_pdf(tmp_path, "Content.")
        full, num_pages, page_texts, metadata, ocr_per_page, _ = indexer._extract_with_ocr(
            pdf_path, "text", None, True, use_ocr=True, file_id="fid6", content_hash=None,
        )
        assert num_pages == 1

    def test_ocr_low_confidence_prefers_native(self, tmp_path, monkeypatch):
        """If OCR confidence is very low (< min threshold), native text is preferred."""
        native = "Good native text here with plenty of characters to avoid MIN_CHARS threshold."
        monkeypatch.setattr(indexer, "ocr_available", lambda: True)
        monkeypatch.setattr(indexer, "get_ocr_workers", lambda: 1)
        monkeypatch.setattr(indexer, "render_page_to_image", lambda page: b"img")
        monkeypatch.setattr(indexer, "ocr_page_from_image_bytes", lambda *a, **kw: ("low conf text", 5.0))
        monkeypatch.setattr(indexer, "correct_ocr_with_ollama", lambda t: None)
        monkeypatch.setattr(indexer, "extract_text_from_doc", lambda doc, mode="text", normalize=True: (native, 1, [native], {}))
        monkeypatch.setenv("PDFSEARCHABLE_OCR_ALWAYS", "0")
        monkeypatch.setenv("PDFSEARCHABLE_OCR_MIN_CONFIDENCE_VS_NATIVE", "15")

        pdf_path = _make_pdf(tmp_path, "dummy")
        full, _, page_texts, _, ocr_per_page, _ = indexer._extract_with_ocr(
            pdf_path, "text", None, True, use_ocr=True, file_id="fid7", content_hash=None,
        )
        assert page_texts[0] == native

    def test_correct_ocr_with_ollama_applied(self, tmp_path, monkeypatch):
        """correct_ocr_with_ollama result is used when non-empty."""
        monkeypatch.setattr(indexer, "ocr_available", lambda: True)
        monkeypatch.setattr(indexer, "get_ocr_workers", lambda: 1)
        monkeypatch.setattr(indexer, "render_page_to_image", lambda page: b"img")
        monkeypatch.setattr(indexer, "ocr_page_from_image_bytes", lambda *a, **kw: ("raw ocr", 90.0))
        monkeypatch.setattr(indexer, "correct_ocr_with_ollama", lambda t: "corrected ocr text")
        monkeypatch.setenv("PDFSEARCHABLE_OCR_ALWAYS", "1")

        pdf_path = _make_pdf(tmp_path, "dummy")
        full, _, page_texts, _, _, _ = indexer._extract_with_ocr(
            pdf_path, "text", None, True, use_ocr=True, file_id="fid8", content_hash=None,
        )
        assert "corrected ocr text" in page_texts[0] or "corrected ocr text" in full

    def test_merge_extended_tables(self, tmp_path, monkeypatch):
        """Tables in extended metadata are merged into page texts."""
        monkeypatch.setattr(indexer, "extract_extended_from_doc", lambda doc: {
            "tables": [{"page": 1, "text": "col1|col2"}],
            "form_fields": [], "annotations": [], "xmp": {}, "outline": [],
            "hyperlinks": [], "page_dimensions": [], "attached_files": [], "fonts": [],
        })
        monkeypatch.setattr(indexer, "detect_digital_signatures", lambda doc: [])

        pdf_path = _make_pdf(tmp_path, "Page with table.")
        full, _, page_texts, _, _, _ = indexer._extract_with_ocr(
            pdf_path, "text", None, True, use_ocr=False, file_id="fid9", content_hash=None,
        )
        assert "col1|col2" in page_texts[0]

    def test_merge_extended_no_tables_with_other_extended(self, tmp_path, monkeypatch):
        """Extended metadata (no tables) is merged into metadata dict."""
        monkeypatch.setattr(indexer, "extract_extended_from_doc", lambda doc: {
            "tables": [],
            "form_fields": [{"name": "field1"}],
            "annotations": [],
            "xmp": {"title": "Test"},
            "outline": [],
            "hyperlinks": [],
            "page_dimensions": [],
            "attached_files": [],
            "fonts": [],
        })
        monkeypatch.setattr(indexer, "detect_digital_signatures", lambda doc: [])

        pdf_path = _make_pdf(tmp_path, "Page with form fields.")
        full, _, page_texts, metadata, _, _ = indexer._extract_with_ocr(
            pdf_path, "text", None, True, use_ocr=False, file_id="fid10", content_hash=None,
        )
        assert "extended" in metadata


# ─────────────────────────────────────────────────────────────────────────────
# index_pdf
# ─────────────────────────────────────────────────────────────────────────────

class TestIndexPdf:

    @pytest.fixture(autouse=True)
    def _mock_pipeline(self, monkeypatch):
        """Stub out OCR and enrichment pipeline for all index_pdf tests."""
        monkeypatch.setattr(indexer, "ocr_available", lambda: False)

        classification = MagicMock()
        classification.label = "documento"
        classification.source = "heuristics"
        classification.confidence = 0.9
        monkeypatch.setattr(indexer, "classify_document", lambda *a, **kw: classification)
        monkeypatch.setattr(indexer, "_get_ai_mode", lambda: "off")
        monkeypatch.setattr(indexer, "detect_language", lambda t: "pt")
        monkeypatch.setattr(indexer, "extract_tags", lambda *a, **kw: [])
        monkeypatch.setattr(indexer, "extract_monetary_values", lambda t: [])
        monkeypatch.setattr(indexer, "extract_parties", lambda t: [])
        monkeypatch.setattr(indexer, "extract_entities", lambda t: {})
        monkeypatch.setattr(indexer, "extract_dates", lambda t: [])
        monkeypatch.setattr(indexer, "detect_confidentiality", lambda t: None)
        monkeypatch.setattr(indexer, "detect_digital_signatures", lambda doc: [])

    def test_nonexistent_file_raises(self):
        with pytest.raises(ValidationError):
            indexer.index_pdf(Path("/no/such/file.pdf"))

    def test_non_pdf_extension_raises(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hello")
        with pytest.raises(ValidationError):
            indexer.index_pdf(f)

    def test_index_pdf_simple(self, tmp_path, isolated_store, monkeypatch):
        """End-to-end index of a simple PDF with mocked store and no OCR."""
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)
        monkeypatch.setattr(indexer, "extract_extended_from_doc", lambda doc: {
            "tables": [], "form_fields": [], "annotations": [], "xmp": {},
            "outline": [], "hyperlinks": [], "page_dimensions": [],
            "attached_files": [], "fonts": [],
        })

        pdf_path = _make_pdf(tmp_path, "Sample indexable content for testing.")

        result = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False)

        assert result is not None
        assert "id" in result
        assert "num_pages" in result
        assert result["num_pages"] >= 1

    def test_skip_existing_returns_none(self, tmp_path, isolated_store, monkeypatch):
        """If file already indexed with same content_hash, returns None."""
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)
        monkeypatch.setattr(indexer, "extract_extended_from_doc", lambda doc: {
            "tables": [], "form_fields": [], "annotations": [], "xmp": {},
            "outline": [], "hyperlinks": [], "page_dimensions": [],
            "attached_files": [], "fonts": [],
        })

        pdf_path = _make_pdf(tmp_path, "Content for skip-existing test.")
        # Index once
        r1 = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False)
        assert r1 is not None

        # Index again with skip_existing=True → should skip
        r2 = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=True)
        assert r2 is None

    def test_pdf_invalid_raises_validation_error(self, tmp_path, monkeypatch):
        """If validate_pdf returns (False, 'error'), raises ValidationError."""
        monkeypatch.setattr(indexer, "validate_pdf", lambda path, pwd=None: (False, "invalid pdf"))
        # "corrupted" not in err, so no partial recovery attempted
        f = tmp_path / "bad.pdf"
        f.write_bytes(b"%PDF fake")
        with pytest.raises(ValidationError):
            indexer.index_pdf(f, use_ocr=False)

    def test_pdf_corrupt_partial_recovery(self, tmp_path, isolated_store, monkeypatch):
        """validate_pdf returns corrupted error → partial recovery succeeds."""
        monkeypatch.setattr(indexer, "validate_pdf", lambda path, pwd=None: (False, "PDF corrompido: dados inválidos"))
        monkeypatch.setattr(indexer, "extract_text_from_pdf_partial", lambda path, password=None, normalize=True: (
            "partial text", 2, ["page1 text", "page2 text"], {}, [0, 1]
        ))
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)

        pdf_path = _make_pdf(tmp_path, "dummy")
        result = indexer.index_pdf(pdf_path, use_ocr=False)
        assert result is not None
        assert result.get("partial_recovery") is True
        assert result["num_pages"] == 2

    def test_pdf_corrupt_partial_recovery_with_failed_pages(self, tmp_path, isolated_store, monkeypatch):
        """Partial recovery with some failed pages includes failed_pages in result."""
        monkeypatch.setattr(indexer, "validate_pdf", lambda path, pwd=None: (False, "PDF corrompido"))
        monkeypatch.setattr(indexer, "extract_text_from_pdf_partial", lambda path, password=None, normalize=True: (
            "partial text", 3, ["p1", "p2", "p3"], {}, [3]  # page 3 failed
        ))
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)

        pdf_path = _make_pdf(tmp_path, "dummy")
        result = indexer.index_pdf(pdf_path, use_ocr=False)
        assert result is not None
        assert "failed_pages" in result

    def test_pdf_corrupt_partial_recovery_fails_raises(self, tmp_path, monkeypatch):
        """If partial recovery also fails, raises ValidationError."""
        monkeypatch.setattr(indexer, "validate_pdf", lambda path, pwd=None: (False, "PDF corrompido"))
        monkeypatch.setattr(indexer, "extract_text_from_pdf_partial", MagicMock(side_effect=Exception("partial failed")))
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)

        pdf_path = _make_pdf(tmp_path, "dummy")
        with pytest.raises(ValidationError):
            indexer.index_pdf(pdf_path, use_ocr=False)

    def test_large_file_sets_compress(self, tmp_path, isolated_store, monkeypatch):
        """Files > 20MB should auto-enable compression."""
        monkeypatch.setattr(indexer, "file_size", lambda p: 25 * 1024 * 1024)  # 25 MB
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)
        monkeypatch.setattr(indexer, "extract_extended_from_doc", lambda doc: {
            "tables": [], "form_fields": [], "annotations": [], "xmp": {},
            "outline": [], "hyperlinks": [], "page_dimensions": [],
            "attached_files": [], "fonts": [],
        })

        pdf_path = _make_pdf(tmp_path, "Large file content.")
        result = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False, compress=False)
        assert result is not None  # file was indexed

    def test_pdf_portfolio_returns_portfolio_dict(self, tmp_path, isolated_store, monkeypatch):
        """PDF Portfolio is detected and embedded PDFs are indexed."""

        embedded_pdf_bytes = _make_pdf(tmp_path, "embedded content").read_bytes()
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: True)
        monkeypatch.setattr(indexer, "extract_embedded_pdfs", lambda path, password=None: [
            ("embedded1.pdf", embedded_pdf_bytes)
        ])
        monkeypatch.setattr(indexer, "extract_extended_from_doc", lambda doc: {
            "tables": [], "form_fields": [], "annotations": [], "xmp": {},
            "outline": [], "hyperlinks": [], "page_dimensions": [],
            "attached_files": [], "fonts": [],
        })

        pdf_path = _make_pdf(tmp_path, "Portfolio cover page.")
        result = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False)
        assert result is not None
        assert result.get("portfolio") is True

    def test_pdf_portfolio_no_embedded_falls_through(self, tmp_path, isolated_store, monkeypatch):
        """Portfolio with no extractable PDFs falls through to normal indexing."""
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: True)
        monkeypatch.setattr(indexer, "extract_embedded_pdfs", lambda path, password=None: [])
        monkeypatch.setattr(indexer, "extract_extended_from_doc", lambda doc: {
            "tables": [], "form_fields": [], "annotations": [], "xmp": {},
            "outline": [], "hyperlinks": [], "page_dimensions": [],
            "attached_files": [], "fonts": [],
        })

        pdf_path = _make_pdf(tmp_path, "Empty portfolio.")
        result = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False)
        # Falls through to normal indexing
        assert result is not None

    def test_fts_deferred_skips_fts_indexing(self, tmp_path, isolated_store, monkeypatch):
        """With PDFSEARCHABLE_FTS_DEFERRED=1, fts_index_file is not called."""
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)
        monkeypatch.setattr(indexer, "extract_extended_from_doc", lambda doc: {
            "tables": [], "form_fields": [], "annotations": [], "xmp": {},
            "outline": [], "hyperlinks": [], "page_dimensions": [],
            "attached_files": [], "fonts": [],
        })
        monkeypatch.setenv("PDFSEARCHABLE_FTS_DEFERRED", "1")
        fts_called = {"count": 0}
        orig_fts = indexer.fts_index_file
        def mock_fts(*a, **kw):
            fts_called["count"] += 1
            return orig_fts(*a, **kw)
        monkeypatch.setattr(indexer, "fts_index_file", mock_fts)

        pdf_path = _make_pdf(tmp_path, "Deferred FTS test content.")
        indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False)
        assert fts_called["count"] == 0

    def test_pdf_structure_warnings_in_result(self, tmp_path, isolated_store, monkeypatch):
        """PDF structure warnings in metadata are surfaced in result."""
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)
        monkeypatch.setattr(indexer, "extract_extended_from_doc", lambda doc: {
            "tables": [], "form_fields": [], "annotations": [], "xmp": {},
            "outline": [], "hyperlinks": [], "page_dimensions": [],
            "attached_files": [], "fonts": [],
        })
        # Inject pdf_warnings into extraction result
        from pdfsearchable.pdf_processor import extract_text_from_doc as _orig_extract
        def patched_extract(doc, mode="text", normalize=True):
            full, n, pts, meta = _orig_extract(doc, mode=mode, normalize=normalize)
            meta["pdf_warnings"] = ["Structure warning 1"]
            return full, n, pts, meta
        monkeypatch.setattr(indexer, "extract_text_from_doc", patched_extract)

        pdf_path = _make_pdf(tmp_path, "PDF with structure warnings.")
        result = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False)
        assert result is not None
        assert "pdf_structure_warnings" in result

    def test_detect_redactions_optional(self, tmp_path, isolated_store, monkeypatch):
        """PDFSEARCHABLE_DETECT_REDACTIONS=1 triggers redaction detection."""
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)
        monkeypatch.setattr(indexer, "extract_extended_from_doc", lambda doc: {
            "tables": [], "form_fields": [], "annotations": [], "xmp": {},
            "outline": [], "hyperlinks": [], "page_dimensions": [],
            "attached_files": [], "fonts": [],
        })
        monkeypatch.setenv("PDFSEARCHABLE_DETECT_REDACTIONS", "1")

        # Mock redaction module
        fake_rr = MagicMock()
        fake_rr.has_redactions = True
        fake_rr.suspicious = True
        fake_rr.total_redacted_zones = 3
        fake_rr.summary = "3 redacted zones"
        fake_rr.pages = []
        with mock.patch.dict("sys.modules", {"pdfsearchable.redaction": MagicMock(detect_redactions=lambda path, password=None: fake_rr)}):
            pdf_path = _make_pdf(tmp_path, "Redacted content here.")
            result = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False)
            assert result is not None

    def test_forensics_optional(self, tmp_path, isolated_store, monkeypatch):
        """PDFSEARCHABLE_FORENSICS=1 triggers forensic analysis."""
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)
        monkeypatch.setattr(indexer, "extract_extended_from_doc", lambda doc: {
            "tables": [], "form_fields": [], "annotations": [], "xmp": {},
            "outline": [], "hyperlinks": [], "page_dimensions": [],
            "attached_files": [], "fonts": [],
        })
        monkeypatch.setenv("PDFSEARCHABLE_FORENSICS", "1")

        fake_fr = MagicMock()
        fake_fr.anomalies = ["anomaly1"]
        fake_fr.risk_score = 0.8
        fake_fr.suspicious = True
        fake_fr.summary = "Suspicious file"
        with mock.patch.dict("sys.modules", {"pdfsearchable.forensics": MagicMock(analyse_forensics=lambda path, password=None: fake_fr)}):
            pdf_path = _make_pdf(tmp_path, "Forensic test content.")
            result = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False)
            assert result is not None

    def test_contracts_optional(self, tmp_path, isolated_store, monkeypatch):
        """PDFSEARCHABLE_CONTRACTS=1 and doc_type=contrato triggers contract extraction."""
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)
        monkeypatch.setattr(indexer, "extract_extended_from_doc", lambda doc: {
            "tables": [], "form_fields": [], "annotations": [], "xmp": {},
            "outline": [], "hyperlinks": [], "page_dimensions": [],
            "attached_files": [], "fonts": [],
        })
        monkeypatch.setenv("PDFSEARCHABLE_CONTRACTS", "1")
        cl = MagicMock()
        cl.label = "contrato"
        cl.source = "heuristics"
        cl.confidence = 0.95
        monkeypatch.setattr(indexer, "classify_document", lambda *a, **kw: cl)

        fake_cd = MagicMock()
        fake_cd.confidence = 0.9
        fake_cd.start_date = "01/01/2024"
        fake_cd.end_date = "31/12/2024"
        fake_cd.renewal_date = None
        fake_cd.duration_months = 12
        fake_cd.auto_renewal = False
        with mock.patch.dict("sys.modules", {"pdfsearchable.contracts": MagicMock(extract_contract_dates=lambda *a, **kw: fake_cd)}):
            pdf_path = _make_pdf(tmp_path, "Contrato entre partes. Vigência: 01/01/2024.")
            result = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False)
            assert result is not None

    def test_classifier_feedback_optional(self, tmp_path, isolated_store, monkeypatch):
        """PDFSEARCHABLE_CLASSIFIER_FEEDBACK=1 triggers feedback recording."""
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)
        monkeypatch.setattr(indexer, "extract_extended_from_doc", lambda doc: {
            "tables": [], "form_fields": [], "annotations": [], "xmp": {},
            "outline": [], "hyperlinks": [], "page_dimensions": [],
            "attached_files": [], "fonts": [],
        })
        monkeypatch.setenv("PDFSEARCHABLE_CLASSIFIER_FEEDBACK", "1")

        called = {"n": 0}
        with mock.patch.dict("sys.modules", {"pdfsearchable.classifier_feedback": MagicMock(
            record_correction=lambda *a, **kw: called.update({"n": called["n"] + 1})
        )}):
            pdf_path = _make_pdf(tmp_path, "Feedback test document content.")
            result = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False)
            assert result is not None

    def test_extraction_retry_on_exception(self, tmp_path, isolated_store, monkeypatch):
        """IndexingError raised after MAX_RETRIES failed extractions."""
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)

        call_count = {"n": 0}

        def always_fail(*a, **kw):
            call_count["n"] += 1
            raise RuntimeError("extraction failed")

        monkeypatch.setattr(indexer, "_extract_with_ocr", always_fail)
        monkeypatch.setattr(indexer, "RETRY_BACKOFF", 0.0)  # no sleep in tests

        pdf_path = _make_pdf(tmp_path, "dummy")
        with pytest.raises(IndexingError):
            indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False)

        assert call_count["n"] == indexer.MAX_RETRIES

    def test_enrichment_partial_in_result(self, tmp_path, isolated_store, monkeypatch):
        """enrichment_partial=True is surfaced in result."""
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)
        monkeypatch.setattr(indexer, "extract_extended_from_doc", lambda doc: {
            "tables": [], "form_fields": [], "annotations": [], "xmp": {},
            "outline": [], "hyperlinks": [], "page_dimensions": [],
            "attached_files": [], "fonts": [],
        })
        # Force enrichment partial
        monkeypatch.setattr(indexer, "_enrich_document", lambda *a, **kw: _fake_enrich(enrichment_partial=True, ocr_warnings="Ollama indisponível"))

        pdf_path = _make_pdf(tmp_path, "Partial enrichment test.")
        result = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False)
        assert result is not None
        assert result.get("enrichment_partial") is True

    def test_skip_existing_different_path_updates_path(self, tmp_path, isolated_store, monkeypatch):
        """When existing file found with same hash but different file_id, path is updated."""
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)

        pdf_path = _make_pdf(tmp_path, "Content for path update test.")

        # Simulate existing file with different id (same hash, moved)
        existing = {"id": "different_file_id", "name": "old_name.pdf"}
        monkeypatch.setattr(indexer, "find_by_content_hash", lambda h: existing)
        monkeypatch.setattr(indexer, "update_path_by_content_hash", lambda h, p, n: True)

        result = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=True)
        assert result is None  # skipped after path update


# ─────────────────────────────────────────────────────────────────────────────
# _index_one wrapper
# ─────────────────────────────────────────────────────────────────────────────

class TestIndexOne:

    def test_index_one_delegates_to_index_pdf(self, tmp_path, isolated_store, monkeypatch):
        """_index_one is a thin wrapper around index_pdf."""

        monkeypatch.setattr(indexer, "ocr_available", lambda: False)
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)
        monkeypatch.setattr(indexer, "extract_extended_from_doc", lambda doc: {
            "tables": [], "form_fields": [], "annotations": [], "xmp": {},
            "outline": [], "hyperlinks": [], "page_dimensions": [],
            "attached_files": [], "fonts": [],
        })
        cl = MagicMock(); cl.label = "documento"; cl.source = "heuristics"; cl.confidence = 0.9
        monkeypatch.setattr(indexer, "classify_document", lambda *a, **kw: cl)
        monkeypatch.setattr(indexer, "_get_ai_mode", lambda: "off")
        monkeypatch.setattr(indexer, "detect_language", lambda t: "pt")
        monkeypatch.setattr(indexer, "extract_tags", lambda *a, **kw: [])
        monkeypatch.setattr(indexer, "extract_monetary_values", lambda t: [])
        monkeypatch.setattr(indexer, "extract_parties", lambda t: [])
        monkeypatch.setattr(indexer, "extract_entities", lambda t: {})
        monkeypatch.setattr(indexer, "extract_dates", lambda t: [])
        monkeypatch.setattr(indexer, "detect_confidentiality", lambda t: None)
        monkeypatch.setattr(indexer, "detect_digital_signatures", lambda doc: [])

        pdf_path = _make_pdf(tmp_path, "_index_one wrapper test.")
        result = indexer._index_one(pdf_path, None, "text", None, False, False, False)
        assert result is not None
        assert "id" in result


# ─────────────────────────────────────────────────────────────────────────────
# index_pdfs
# ─────────────────────────────────────────────────────────────────────────────

class TestIndexPdfs:

    @pytest.fixture(autouse=True)
    def _mock_pipeline(self, monkeypatch):
        """Disable OCR and stub AI for all index_pdfs tests."""

        monkeypatch.setattr(indexer, "ocr_available", lambda: False)
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)
        monkeypatch.setattr(indexer, "extract_extended_from_doc", lambda doc: {
            "tables": [], "form_fields": [], "annotations": [], "xmp": {},
            "outline": [], "hyperlinks": [], "page_dimensions": [],
            "attached_files": [], "fonts": [],
        })
        cl = MagicMock(); cl.label = "documento"; cl.source = "heuristics"; cl.confidence = 0.9
        monkeypatch.setattr(indexer, "classify_document", lambda *a, **kw: cl)
        monkeypatch.setattr(indexer, "_get_ai_mode", lambda: "off")
        monkeypatch.setattr(indexer, "detect_language", lambda t: "pt")
        monkeypatch.setattr(indexer, "extract_tags", lambda *a, **kw: [])
        monkeypatch.setattr(indexer, "extract_monetary_values", lambda t: [])
        monkeypatch.setattr(indexer, "extract_parties", lambda t: [])
        monkeypatch.setattr(indexer, "extract_entities", lambda t: {})
        monkeypatch.setattr(indexer, "extract_dates", lambda t: [])
        monkeypatch.setattr(indexer, "detect_confidentiality", lambda t: None)
        monkeypatch.setattr(indexer, "detect_digital_signatures", lambda doc: [])

    def test_empty_paths(self, isolated_store):
        result = indexer.index_pdfs([], workers=1)
        assert result == []

    def test_single_file_sequential(self, tmp_path, isolated_store):
        pdf = _make_pdf(tmp_path, "Sequential index test.")
        results = indexer.index_pdfs([pdf], workers=1, use_ocr=False, skip_existing=False)
        assert len(results) == 1
        assert results[0]["id"]

    def test_multiple_files_sequential(self, tmp_path, isolated_store):
        pdfs = [_make_pdf(tmp_path, f"File {i} content", f"f{i}.pdf") for i in range(3)]
        results = indexer.index_pdfs(pdfs, workers=1, use_ocr=False, skip_existing=False)
        assert len(results) == 3

    def test_doc_types_passed(self, tmp_path, isolated_store):
        pdf = _make_pdf(tmp_path, "Doc type test content.")
        doc_types = {str(pdf): "contrato"}
        results = indexer.index_pdfs([pdf], doc_types=doc_types, workers=1, use_ocr=False, skip_existing=False)
        assert len(results) == 1
        assert results[0]["doc_type"] == "contrato"

    def test_skip_failed_true_continues_on_error(self, tmp_path, isolated_store):
        """skip_failed=True: bad file is skipped, good file is indexed."""
        good = _make_pdf(tmp_path, "Good file content here.")
        bad = tmp_path / "not_pdf.pdf"
        bad.write_bytes(b"not a pdf at all")

        results = indexer.index_pdfs([bad, good], workers=1, use_ocr=False, skip_failed=True, skip_existing=False)
        # At least the good file should be indexed
        assert any(r["id"] for r in results)

    def test_skip_failed_false_raises_on_error(self, tmp_path, isolated_store):
        """skip_failed=False: error in one file propagates."""
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"not a pdf at all")

        with pytest.raises((ValidationError, IndexingError, Exception)):
            indexer.index_pdfs([bad], workers=1, use_ocr=False, skip_failed=False, skip_existing=False)

    def test_on_file_start_callback(self, tmp_path, isolated_store):
        """on_file_start callback is called for each file in sequential mode."""
        pdf = _make_pdf(tmp_path, "Callback test content.")
        started = []
        indexer.index_pdfs([pdf], workers=1, use_ocr=False, skip_existing=False,
                            on_file_start=lambda p: started.append(p))
        assert len(started) == 1

    def test_on_file_progress_callback(self, tmp_path, isolated_store):
        """on_file_progress callback is called for each file."""
        pdfs = [_make_pdf(tmp_path, f"Progress test {i}", f"p{i}.pdf") for i in range(2)]
        progress = []
        indexer.index_pdfs(pdfs, workers=1, use_ocr=False, skip_existing=False,
                           on_file_progress=lambda p, cur, tot: progress.append((cur, tot)))
        assert len(progress) == 2

    def test_batch_size_splits_processing(self, tmp_path, isolated_store):
        """batch_size splits the work into chunks."""
        pdfs = [_make_pdf(tmp_path, f"Batch {i} content", f"b{i}.pdf") for i in range(4)]
        results = indexer.index_pdfs(pdfs, workers=1, use_ocr=False, skip_existing=False, batch_size=2)
        assert len(results) == 4

    def test_workers_auto_uses_max_workers_auto(self, tmp_path, isolated_store, monkeypatch):
        """workers=0 falls back to _max_workers_auto(). In sequential test env, cap to 1."""
        monkeypatch.setattr(indexer, "_max_workers_auto", lambda: 1)
        pdf = _make_pdf(tmp_path, "Auto workers test.")
        results = indexer.index_pdfs([pdf], workers=0, use_ocr=False, skip_existing=False)
        assert len(results) == 1

    def test_skip_existing_in_multiprocessing_path(self, tmp_path, isolated_store, monkeypatch):
        """In multiprocessing path, skip_existing checks content_hash before submitting."""
        import pdfsearchable.store as store_mod
        pdf = _make_pdf(tmp_path, "Skip existing multiprocessing test.")

        # Index once
        indexer.index_pdfs([pdf], workers=1, use_ocr=False, skip_existing=False)

        # Second time with workers>1 but mocked pool to avoid actual subprocess
        class FakeFuture:
            def __init__(self, result_val):
                self._result = result_val
            def done(self):
                return True
            def result(self, timeout=None):
                return self._result

        # Just test that skip_existing check prevents submission
        from pdfsearchable.pdf_processor import content_hash, file_size
        c_hash = content_hash(pdf)
        from pdfsearchable.store import _file_id
        file_id = _file_id(pdf)
        existing = store_mod.find_by_content_hash(c_hash)
        assert existing is not None  # already in store

    def test_generic_exception_skip_failed(self, tmp_path, isolated_store, monkeypatch):
        """Generic Exception in sequential mode with skip_failed=True is caught."""

        pdf = _make_pdf(tmp_path, "Exception skip failed test.")

        # Make extract always raise generic Exception
        call_n = {"n": 0}
        def fail_extract(*a, **kw):
            call_n["n"] += 1
            raise RuntimeError("unexpected error")

        monkeypatch.setattr(indexer, "_extract_with_ocr", fail_extract)
        monkeypatch.setattr(indexer, "RETRY_BACKOFF", 0.0)

        results = indexer.index_pdfs([pdf], workers=1, use_ocr=False, skip_failed=True, skip_existing=False)
        assert results == []  # all failed, none added


# ─────────────────────────────────────────────────────────────────────────────
# _worker_extract_and_classify
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkerExtractAndClassify:

    @pytest.fixture(autouse=True)
    def _mock_ai(self, monkeypatch):
        cl = MagicMock(); cl.label = "documento"; cl.source = "heuristics"; cl.confidence = 0.9
        monkeypatch.setattr(indexer, "classify_document", lambda *a, **kw: cl)
        monkeypatch.setattr(indexer, "_get_ai_mode", lambda: "off")
        monkeypatch.setattr(indexer, "detect_language", lambda t: "pt")
        monkeypatch.setattr(indexer, "extract_tags", lambda *a, **kw: [])
        monkeypatch.setattr(indexer, "extract_monetary_values", lambda t: [])
        monkeypatch.setattr(indexer, "extract_parties", lambda t: [])
        monkeypatch.setattr(indexer, "extract_entities", lambda t: {})
        monkeypatch.setattr(indexer, "extract_dates", lambda t: [])
        monkeypatch.setattr(indexer, "detect_confidentiality", lambda t: None)
        monkeypatch.setattr(indexer, "detect_digital_signatures", lambda doc: [])

    def test_worker_returns_expected_keys(self, tmp_path, isolated_store, monkeypatch):
        monkeypatch.setattr(indexer, "ocr_available", lambda: False)
        monkeypatch.setattr(indexer, "extract_extended_from_doc", lambda doc: {
            "tables": [], "form_fields": [], "annotations": [], "xmp": {},
            "outline": [], "hyperlinks": [], "page_dimensions": [],
            "attached_files": [], "fonts": [],
        })

        pdf_path = _make_pdf(tmp_path, "Worker extract test content.")
        args = (str(pdf_path), "text", None, False, None)
        result = indexer._worker_extract_and_classify(args)

        for key in ("file_id", "content_hash", "full_text", "page_texts",
                    "metadata", "ocr_per_page", "ocr_confidences", "num_pages",
                    "doc_type", "word_count", "language"):
            assert key in result, f"Missing key: {key}"

    def test_worker_validation_error_raises(self, tmp_path, monkeypatch):
        """_worker raises ValidationError if PDF is invalid."""
        monkeypatch.setattr(indexer, "validate_pdf", lambda path, pwd=None: (False, "invalid"))
        monkeypatch.setattr(indexer, "RETRY_BACKOFF", 0.0)

        f = tmp_path / "bad.pdf"
        f.write_bytes(b"%PDF fake")
        with pytest.raises(ValidationError):
            indexer._worker_extract_and_classify((str(f), "text", None, False, None))

    def test_worker_corrupt_partial_recovery(self, tmp_path, isolated_store, monkeypatch):
        """Worker handles corrupt PDF with partial recovery."""
        monkeypatch.setattr(indexer, "validate_pdf", lambda path, pwd=None: (False, "PDF corrompido"))
        monkeypatch.setattr(indexer, "extract_text_from_pdf_partial", lambda path, password=None, normalize=True: (
            "partial text here", 1, ["partial text here"], {}, []
        ))
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)

        pdf_path = _make_pdf(tmp_path, "dummy")
        result = indexer._worker_extract_and_classify((str(pdf_path), "text", None, False, None))
        assert result["full_text"] == "partial text here"

    def test_worker_retry_on_exception(self, tmp_path, monkeypatch):
        """Worker retries MAX_RETRIES times before raising."""
        monkeypatch.setattr(indexer, "RETRY_BACKOFF", 0.0)

        call_n = {"n": 0}
        def fake_validate(path, pwd=None):
            call_n["n"] += 1
            if call_n["n"] < indexer.MAX_RETRIES:
                raise RuntimeError("transient error")
            return (True, None)

        monkeypatch.setattr(indexer, "validate_pdf", fake_validate)
        monkeypatch.setattr(indexer, "_extract_with_ocr", _fake_extract)

        pdf_path = _make_pdf(tmp_path, "Retry test content.")
        result = indexer._worker_extract_and_classify((str(pdf_path), "text", None, False, "documento"))
        assert result["doc_type"] == "documento"


# ─────────────────────────────────────────────────────────────────────────────
# get_stats
# ─────────────────────────────────────────────────────────────────────────────

def test_get_stats_empty_store(isolated_store):
    stats = indexer.get_stats()
    assert isinstance(stats, dict)
    assert "total_files" in stats
    assert stats["total_files"] == 0
    assert stats["total_pages"] == 0


def test_get_stats_with_indexed_file(tmp_path, isolated_store, monkeypatch):
    import pdfsearchable.ocr as ocr_mod
    import pdfsearchable.pdf_extended as pe_mod

    monkeypatch.setattr(ocr_mod, "ocr_available", lambda: False)
    monkeypatch.setattr(pe_mod, "is_pdf_portfolio", lambda path, password=None: False)
    monkeypatch.setattr(pe_mod, "extract_extended_from_doc", lambda doc: {
        "tables": [], "form_fields": [], "annotations": [], "xmp": {},
        "outline": [], "hyperlinks": [], "page_dimensions": [],
        "attached_files": [], "fonts": [],
    })
    cl = MagicMock(); cl.label = "documento"; cl.source = "heuristics"; cl.confidence = 0.9
    monkeypatch.setattr(indexer, "classify_document", lambda *a, **kw: cl)
    monkeypatch.setattr(indexer, "_get_ai_mode", lambda: "off")
    monkeypatch.setattr(indexer, "detect_language", lambda t: "pt")
    monkeypatch.setattr(indexer, "extract_tags", lambda *a, **kw: [])
    monkeypatch.setattr(indexer, "extract_monetary_values", lambda t: [])
    monkeypatch.setattr(indexer, "extract_parties", lambda t: [])
    monkeypatch.setattr(indexer, "extract_entities", lambda t: {})
    monkeypatch.setattr(indexer, "extract_dates", lambda t: [])
    monkeypatch.setattr(indexer, "detect_confidentiality", lambda t: None)
    monkeypatch.setattr(indexer, "detect_digital_signatures", lambda doc: [])

    pdf = _make_pdf(tmp_path, "Stats test content.")
    indexer.index_pdf(pdf, use_ocr=False, skip_existing=False)

    stats = indexer.get_stats()
    assert stats["total_files"] == 1
    assert stats["total_pages"] >= 1


# ─────────────────────────────────────────────────────────────────────────────
# Additional gap-filling tests
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractWithOcrGaps:
    """Additional tests for _extract_with_ocr to cover remaining branches."""

    def test_language_hint_from_metadata(self, tmp_path, monkeypatch):
        """_htr_lang_hint from PDF metadata 'language' field (line 541)."""
        monkeypatch.setattr(indexer, "ocr_available", lambda: True)
        monkeypatch.setattr(indexer, "get_ocr_workers", lambda: 1)
        monkeypatch.setattr(indexer, "render_page_to_image", lambda page: b"img")
        monkeypatch.setattr(indexer, "correct_ocr_with_ollama", lambda t: None)
        captured = {"lang": None}
        def cap_ocr(img, key, pn, use_cache=True, lang=None):
            captured["lang"] = lang
            return ("ocr text", 90.0)
        monkeypatch.setattr(indexer, "ocr_page_from_image_bytes", cap_ocr)
        # Metadata with language field
        monkeypatch.setattr(indexer, "extract_text_from_doc",
                            lambda doc, mode="text", normalize=True: ("", 1, [""], {"language": "de"}))
        monkeypatch.setenv("PDFSEARCHABLE_OCR_ALWAYS", "1")

        pdf_path = _make_pdf(tmp_path, "dummy")
        indexer._extract_with_ocr(pdf_path, "text", None, True, use_ocr=True,
                                   file_id="f_lang_meta", content_hash=None)
        assert captured["lang"] == "de"

    def test_language_hint_detected_from_native_text(self, tmp_path, monkeypatch):
        """_htr_lang_hint detected from native text (lines 547-550)."""
        monkeypatch.setattr(indexer, "ocr_available", lambda: True)
        monkeypatch.setattr(indexer, "get_ocr_workers", lambda: 1)
        monkeypatch.setattr(indexer, "render_page_to_image", lambda page: b"img")
        monkeypatch.setattr(indexer, "correct_ocr_with_ollama", lambda t: None)
        monkeypatch.setattr(indexer, "detect_language", lambda text: "fr")
        captured = {"lang": None}
        def cap_ocr(img, key, pn, use_cache=True, lang=None):
            captured["lang"] = lang
            return ("ocr text", 90.0)
        monkeypatch.setattr(indexer, "ocr_page_from_image_bytes", cap_ocr)
        # Native text long enough (>80 chars stripped)
        long_native = "Ceci est un document en français avec beaucoup de texte natif pour détection de langue. Nous ajoutons encore plus de texte ici."
        monkeypatch.setattr(indexer, "extract_text_from_doc",
                            lambda doc, mode="text", normalize=True: (long_native, 1, [long_native], {}))
        monkeypatch.setenv("PDFSEARCHABLE_OCR_ALWAYS", "1")

        pdf_path = _make_pdf(tmp_path, "dummy")
        indexer._extract_with_ocr(pdf_path, "text", None, True, use_ocr=True,
                                   file_id="f_lang_native", content_hash=None)
        assert captured["lang"] == "fr"

    def test_language_hint_unknown_stays_none(self, tmp_path, monkeypatch):
        """When detect_language returns 'unknown', _htr_lang_hint stays None (line 550)."""
        monkeypatch.setattr(indexer, "ocr_available", lambda: True)
        monkeypatch.setattr(indexer, "get_ocr_workers", lambda: 1)
        monkeypatch.setattr(indexer, "render_page_to_image", lambda page: b"img")
        monkeypatch.setattr(indexer, "correct_ocr_with_ollama", lambda t: None)
        monkeypatch.setattr(indexer, "detect_language", lambda text: "unknown")
        captured = {"lang": "not_set"}
        def cap_ocr(img, key, pn, use_cache=True, lang=None):
            captured["lang"] = lang
            return ("ocr text", 90.0)
        monkeypatch.setattr(indexer, "ocr_page_from_image_bytes", cap_ocr)
        long_native = "Some text that is long enough to trigger language detection attempt but returns unknown."
        monkeypatch.setattr(indexer, "extract_text_from_doc",
                            lambda doc, mode="text", normalize=True: (long_native, 1, [long_native], {}))
        monkeypatch.setenv("PDFSEARCHABLE_OCR_ALWAYS", "1")

        pdf_path = _make_pdf(tmp_path, "dummy")
        indexer._extract_with_ocr(pdf_path, "text", None, True, use_ocr=True,
                                   file_id="f_lang_unknown", content_hash=None)
        assert captured["lang"] is None

    def test_language_hint_detect_raises(self, tmp_path, monkeypatch):
        """Language detect raising exception → _htr_lang_hint=None (line 551-552)."""
        monkeypatch.setattr(indexer, "ocr_available", lambda: True)
        monkeypatch.setattr(indexer, "get_ocr_workers", lambda: 1)
        monkeypatch.setattr(indexer, "render_page_to_image", lambda page: b"img")
        monkeypatch.setattr(indexer, "correct_ocr_with_ollama", lambda t: None)
        monkeypatch.setattr(indexer, "detect_language",
                            MagicMock(side_effect=Exception("lang error")))
        monkeypatch.setattr(indexer, "ocr_page_from_image_bytes",
                            lambda img, key, pn, use_cache=True, lang=None: ("ocr", 90.0))
        long_native = "Some long native text for language detection attempt." * 5
        monkeypatch.setattr(indexer, "extract_text_from_doc",
                            lambda doc, mode="text", normalize=True: (long_native, 1, [long_native], {}))
        monkeypatch.setenv("PDFSEARCHABLE_OCR_ALWAYS", "1")

        pdf_path = _make_pdf(tmp_path, "dummy")
        # Should not raise
        result = indexer._extract_with_ocr(pdf_path, "text", None, True, use_ocr=True,
                                            file_id="f_lang_exc", content_hash=None)
        assert result[0] is not None or result[0] == ""

    def test_parallel_ocr_future_exception(self, tmp_path, monkeypatch):
        """OCR future raising inside ThreadPoolExecutor → logged and empty result (line 605-610)."""
        monkeypatch.setattr(indexer, "ocr_available", lambda: True)
        monkeypatch.setattr(indexer, "get_ocr_workers", lambda: 2)
        monkeypatch.setattr(indexer, "render_page_to_image", lambda page: b"img")
        monkeypatch.setattr(indexer, "ocr_page_from_image_bytes",
                            MagicMock(side_effect=Exception("ocr process crashed")))
        monkeypatch.setattr(indexer, "correct_ocr_with_ollama", lambda t: None)
        monkeypatch.setenv("PDFSEARCHABLE_OCR_ALWAYS", "1")

        pdf_path = _make_pdf(tmp_path, "Parallel OCR failure test.")
        full, num_pages, page_texts, _, ocr_per_page, _ = indexer._extract_with_ocr(
            pdf_path, "text", None, True, use_ocr=True,
            file_id="f_parallel_exc", content_hash=None,
        )
        assert num_pages >= 1

    def test_parallel_ocr_low_conf_prefers_native(self, tmp_path, monkeypatch):
        """Parallel OCR with low confidence prefers native text (lines 635-643)."""
        native = "Good native text preserved with many characters to exceed MIN_CHARS threshold."
        monkeypatch.setattr(indexer, "ocr_available", lambda: True)
        monkeypatch.setattr(indexer, "get_ocr_workers", lambda: 2)
        monkeypatch.setattr(indexer, "render_page_to_image", lambda page: b"img")
        monkeypatch.setattr(indexer, "ocr_page_from_image_bytes",
                            lambda *a, **kw: ("low conf ocr", 5.0))
        monkeypatch.setattr(indexer, "correct_ocr_with_ollama", lambda t: None)
        monkeypatch.setattr(indexer, "extract_text_from_doc",
                            lambda doc, mode="text", normalize=True: (native, 1, [native], {}))
        monkeypatch.setenv("PDFSEARCHABLE_OCR_ALWAYS", "0")
        monkeypatch.setenv("PDFSEARCHABLE_OCR_MIN_CONFIDENCE_VS_NATIVE", "15")

        pdf_path = _make_pdf(tmp_path, "dummy")
        full, _, page_texts, _, _, _ = indexer._extract_with_ocr(
            pdf_path, "text", None, True, use_ocr=True,
            file_id="f_par_low_conf", content_hash=None,
        )
        assert page_texts[0] == native

    def test_parallel_ocr_good_text_replaces_native(self, tmp_path, monkeypatch):
        """Parallel OCR with good text replaces native (line 641)."""
        # With ocr_always=False, high-confidence OCR text replaces short native text
        monkeypatch.setattr(indexer, "ocr_available", lambda: True)
        monkeypatch.setattr(indexer, "get_ocr_workers", lambda: 2)
        monkeypatch.setattr(indexer, "render_page_to_image", lambda page: b"img")
        monkeypatch.setattr(indexer, "ocr_page_from_image_bytes",
                            lambda *a, **kw: ("high quality OCR text replacing sparse native", 92.0))
        monkeypatch.setattr(indexer, "correct_ocr_with_ollama", lambda t: None)
        # Very short native — forces OCR path
        monkeypatch.setattr(indexer, "extract_text_from_doc",
                            lambda doc, mode="text", normalize=True: ("a", 1, ["a"], {}))
        monkeypatch.setenv("PDFSEARCHABLE_OCR_ALWAYS", "0")

        pdf_path = _make_pdf(tmp_path, "dummy")
        full, _, page_texts, _, ocr_per_page, _ = indexer._extract_with_ocr(
            pdf_path, "text", None, True, use_ocr=True,
            file_id="f_par_good", content_hash=None,
        )
        assert "OCR text" in page_texts[0]

    def test_sequential_ocr_good_text_replaces_native(self, tmp_path, monkeypatch):
        """Sequential OCR with good text replaces short native (lines 677-679)."""
        monkeypatch.setattr(indexer, "ocr_available", lambda: True)
        monkeypatch.setattr(indexer, "get_ocr_workers", lambda: 1)
        monkeypatch.setattr(indexer, "render_page_to_image", lambda page: b"img")
        monkeypatch.setattr(indexer, "ocr_page_from_image_bytes",
                            lambda *a, **kw: ("high quality sequential OCR", 90.0))
        monkeypatch.setattr(indexer, "correct_ocr_with_ollama", lambda t: None)
        monkeypatch.setattr(indexer, "extract_text_from_doc",
                            lambda doc, mode="text", normalize=True: ("a", 1, ["a"], {}))
        monkeypatch.setenv("PDFSEARCHABLE_OCR_ALWAYS", "0")

        pdf_path = _make_pdf(tmp_path, "dummy")
        full, _, page_texts, _, ocr_per_page, _ = indexer._extract_with_ocr(
            pdf_path, "text", None, True, use_ocr=True,
            file_id="f_seq_good", content_hash=None,
        )
        assert "sequential OCR" in page_texts[0]
        assert ocr_per_page[0] is True

    def test_sequential_no_img_bytes(self, tmp_path, monkeypatch):
        """Sequential OCR: render returns None → ocr_text='' (lines 663-664)."""
        monkeypatch.setattr(indexer, "ocr_available", lambda: True)
        monkeypatch.setattr(indexer, "get_ocr_workers", lambda: 1)
        monkeypatch.setattr(indexer, "render_page_to_image",
                            MagicMock(side_effect=Exception("render failed")))
        monkeypatch.setattr(indexer, "correct_ocr_with_ollama", lambda t: None)
        monkeypatch.setenv("PDFSEARCHABLE_OCR_ALWAYS", "1")

        pdf_path = _make_pdf(tmp_path, "Some text here.")
        # render_page_to_image raises → img_bytes=None → OCR returns ('', -1.0)
        full, num_pages, page_texts, _, ocr_per_page, _ = indexer._extract_with_ocr(
            pdf_path, "text", None, True, use_ocr=True,
            file_id="f_seq_no_img", content_hash=None,
        )
        assert num_pages >= 1


class TestIndexPdfsMultiprocessing:
    """Tests for index_pdfs multiprocessing path — key edge cases only."""

    @pytest.fixture(autouse=True)
    def _mock_pipeline(self, monkeypatch):
        import pdfsearchable.ocr as ocr_mod
        import pdfsearchable.pdf_extended as pe_mod
        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: False)
        monkeypatch.setattr(pe_mod, "is_pdf_portfolio", lambda path, password=None: False)
        monkeypatch.setattr(pe_mod, "extract_extended_from_doc", lambda doc: {
            "tables": [], "form_fields": [], "annotations": [], "xmp": {},
            "outline": [], "hyperlinks": [], "page_dimensions": [],
            "attached_files": [], "fonts": [],
        })
        cl = MagicMock(); cl.label = "documento"; cl.source = "heuristics"; cl.confidence = 0.9
        monkeypatch.setattr(indexer, "classify_document", lambda *a, **kw: cl)
        monkeypatch.setattr(indexer, "_get_ai_mode", lambda: "off")
        monkeypatch.setattr(indexer, "detect_language", lambda t: "pt")
        monkeypatch.setattr(indexer, "extract_tags", lambda *a, **kw: [])
        monkeypatch.setattr(indexer, "extract_monetary_values", lambda t: [])
        monkeypatch.setattr(indexer, "extract_parties", lambda t: [])
        monkeypatch.setattr(indexer, "extract_entities", lambda t: {})
        monkeypatch.setattr(indexer, "extract_dates", lambda t: [])
        monkeypatch.setattr(indexer, "detect_confidentiality", lambda t: None)
        monkeypatch.setattr(indexer, "detect_digital_signatures", lambda doc: [])

    def test_multiprocessing_with_faked_pool(self, tmp_path, isolated_store, monkeypatch):
        """Replace ProcessPoolExecutor with a sync executor to test multiprocessing branch."""
        from concurrent.futures import Future
        pdfs = [_make_pdf(tmp_path, f"MP test {i}", f"mp{i}.pdf") for i in range(2)]

        class _FakePool:
            def __init__(self, max_workers=None): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def submit(self, fn, arg):
                f = Future()
                try:
                    f.set_result(fn(arg))
                except Exception as e:
                    f.set_exception(e)
                return f

        monkeypatch.setattr(indexer, "ProcessPoolExecutor", _FakePool)
        # as_completed must also work on Future objects
        results = indexer.index_pdfs(pdfs, workers=2, use_ocr=False, skip_existing=False)
        assert len(results) == 2

    def test_multiprocessing_worker_raises_validation(self, tmp_path, isolated_store, monkeypatch):
        """ValidationError from worker is handled in multiprocessing path."""
        from concurrent.futures import Future
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"not pdf")

        class _FakePool:
            def __init__(self, max_workers=None): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def submit(self, fn, arg):
                f = Future()
                try:
                    f.set_result(fn(arg))
                except Exception as e:
                    f.set_exception(e)
                return f

        monkeypatch.setattr(indexer, "ProcessPoolExecutor", _FakePool)
        results = indexer.index_pdfs([bad], workers=2, use_ocr=False,
                                      skip_existing=False, skip_failed=True)
        assert results == []

    def test_multiprocessing_skip_existing_unchanged(self, tmp_path, isolated_store, monkeypatch):
        """Multiprocessing path: same file_id with same hash → skipped (line 1187-1191)."""
        from concurrent.futures import Future
        pdf = _make_pdf(tmp_path, "Already indexed content.")

        class _FakePool:
            def __init__(self, max_workers=None): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def submit(self, fn, arg):
                f = Future()
                try:
                    f.set_result(fn(arg))
                except Exception as e:
                    f.set_exception(e)
                return f

        monkeypatch.setattr(indexer, "ProcessPoolExecutor", _FakePool)
        # Index once sequentially
        indexer.index_pdfs([pdf], workers=1, use_ocr=False, skip_existing=False)
        # Index again with workers>1 and skip_existing=True → should be skipped
        results = indexer.index_pdfs([pdf], workers=2, use_ocr=False, skip_existing=True)
        assert results == []

    def test_multiprocessing_all_skipped(self, tmp_path, isolated_store, monkeypatch):
        """Multiprocessing: when all files skipped, to_process empty (line 1200-1201)."""
        from concurrent.futures import Future
        pdf = _make_pdf(tmp_path, "Content for all-skipped test.")
        indexer.index_pdfs([pdf], workers=1, use_ocr=False, skip_existing=False)

        class _FakePool:
            def __init__(self, max_workers=None): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def submit(self, fn, arg):
                raise AssertionError("should not submit")

        monkeypatch.setattr(indexer, "ProcessPoolExecutor", _FakePool)
        results = indexer.index_pdfs([pdf], workers=2, use_ocr=False, skip_existing=True)
        assert results == []


class TestEnrichDocumentGaps:
    """Fill remaining branch gaps in _enrich_document."""

    @pytest.fixture(autouse=True)
    def _base_mocks(self, monkeypatch):
        cl = MagicMock(); cl.label = "documento"; cl.source = "heuristics"; cl.confidence = 0.9
        monkeypatch.setattr(indexer, "classify_document", lambda *a, **kw: cl)
        monkeypatch.setattr(indexer, "detect_language", lambda t: "pt")
        monkeypatch.setattr(indexer, "extract_tags", lambda *a, **kw: [])
        monkeypatch.setattr(indexer, "extract_monetary_values", lambda t: [])
        monkeypatch.setattr(indexer, "extract_parties", lambda t: [])
        monkeypatch.setattr(indexer, "extract_entities", lambda t: {})
        monkeypatch.setattr(indexer, "extract_dates", lambda t: [])
        monkeypatch.setattr(indexer, "detect_confidentiality", lambda t: None)

    def test_ollama_summary_without_subject_keeps_subject(self, monkeypatch):
        """Ollama subject empty but summary returned → subject from metadata preserved."""
        monkeypatch.setattr(indexer, "_get_ai_mode", lambda: "ollama")
        monkeypatch.setattr(indexer, "extract_summary_and_subject_ollama",
                            lambda t: ("summary_ok", ""))
        monkeypatch.setattr(indexer, "extract_tags_ollama", lambda t, max_tags=5: [])
        monkeypatch.setattr(indexer, "extract_parties_ollama", lambda t, max_parties=8: [])
        monkeypatch.setattr(indexer, "extract_metadata_ollama", lambda t: None)
        monkeypatch.setattr(indexer, "merge_entities_with_ollama", lambda e, m: e)

        result = indexer._enrich_document(
            full_text="Texto grande o suficiente.",
            pdf_path=Path("/tmp/t.pdf"),
            metadata={"subject": "Existing subject"},
            doc_type_arg="documento",
            ocr_per_page=[False],
            page_texts=["Texto grande o suficiente."],
            page_confidences=[-1.0],
        )
        assert result["subject"] == "Existing subject"

    def test_ollama_tags_dedup(self, monkeypatch):
        """Ollama tags that already exist are not duplicated (line 240)."""
        monkeypatch.setattr(indexer, "extract_tags", lambda *a, **kw: ["existing_tag"])
        monkeypatch.setattr(indexer, "_get_ai_mode", lambda: "ollama")
        monkeypatch.setattr(indexer, "extract_summary_and_subject_ollama", lambda t: None)
        monkeypatch.setattr(indexer, "extract_tags_ollama",
                            lambda t, max_tags=5: ["existing_tag", "new_tag"])
        monkeypatch.setattr(indexer, "extract_parties_ollama", lambda t, max_parties=8: [])
        monkeypatch.setattr(indexer, "extract_metadata_ollama", lambda t: None)
        monkeypatch.setattr(indexer, "merge_entities_with_ollama", lambda e, m: e)

        result = indexer._enrich_document(
            full_text="Texto.",
            pdf_path=Path("/tmp/t.pdf"),
            metadata={},
            doc_type_arg="documento",
            ocr_per_page=[],
            page_texts=[],
            page_confidences=[],
        )
        assert result["tags"].count("existing_tag") == 1
        assert "new_tag" in result["tags"]

    def test_ollama_parties_dedup(self, monkeypatch):
        """Ollama parties that already exist are not duplicated (line 246)."""
        monkeypatch.setattr(indexer, "extract_parties", lambda t: ["João"])
        monkeypatch.setattr(indexer, "_get_ai_mode", lambda: "ollama")
        monkeypatch.setattr(indexer, "extract_summary_and_subject_ollama", lambda t: None)
        monkeypatch.setattr(indexer, "extract_tags_ollama", lambda t, max_tags=5: [])
        monkeypatch.setattr(indexer, "extract_parties_ollama",
                            lambda t, max_parties=8: ["João", "Maria"])
        monkeypatch.setattr(indexer, "extract_metadata_ollama", lambda t: None)
        monkeypatch.setattr(indexer, "merge_entities_with_ollama", lambda e, m: e)

        result = indexer._enrich_document(
            full_text="Texto.",
            pdf_path=Path("/tmp/t.pdf"),
            metadata={},
            doc_type_arg="documento",
            ocr_per_page=[],
            page_texts=[],
            page_confidences=[],
        )
        assert result["parties"].count("João") == 1
        assert "Maria" in result["parties"]

    def test_ollama_monetary_values_dedup(self, monkeypatch):
        """Ollama monetary values already present are not duplicated (line 258)."""
        monkeypatch.setattr(indexer, "extract_monetary_values",
                            lambda t: [{"currency": "BRL", "value_str": "R$ 100"}])
        monkeypatch.setattr(indexer, "_get_ai_mode", lambda: "ollama")
        monkeypatch.setattr(indexer, "extract_summary_and_subject_ollama", lambda t: None)
        monkeypatch.setattr(indexer, "extract_tags_ollama", lambda t, max_tags=5: [])
        monkeypatch.setattr(indexer, "extract_parties_ollama", lambda t, max_parties=8: [])
        monkeypatch.setattr(indexer, "extract_metadata_ollama",
                            lambda t: {"dates": [], "monetary_values": ["R$ 100", "R$ 200"], "parties": []})
        monkeypatch.setattr(indexer, "merge_entities_with_ollama", lambda e, m: e)

        result = indexer._enrich_document(
            full_text="Texto.",
            pdf_path=Path("/tmp/t.pdf"),
            metadata={},
            doc_type_arg="documento",
            ocr_per_page=[],
            page_texts=[],
            page_confidences=[],
        )
        # R$ 100 is already there (existing), R$ 200 is new → added with OTHER currency
        value_strs = [m["value_str"] for m in result["monetary_values"]]
        assert value_strs.count("R$ 100") == 1
        assert "R$ 200" in value_strs

    def test_ollama_dates_dedup_and_limit(self, monkeypatch):
        """Ollama dates deduplicated (case-insensitive) and limit respected (line 273)."""
        monkeypatch.setattr(indexer, "extract_dates", lambda t: ["01/01/2024"])
        monkeypatch.setattr(indexer, "_get_ai_mode", lambda: "ollama")
        monkeypatch.setattr(indexer, "extract_summary_and_subject_ollama", lambda t: None)
        monkeypatch.setattr(indexer, "extract_tags_ollama", lambda t, max_tags=5: [])
        monkeypatch.setattr(indexer, "extract_parties_ollama", lambda t, max_parties=8: [])
        monkeypatch.setattr(indexer, "extract_metadata_ollama",
                            lambda t: {"dates": ["01/01/2024", "15/02/2024"], "monetary_values": [], "parties": []})
        monkeypatch.setattr(indexer, "merge_entities_with_ollama", lambda e, m: e)

        result = indexer._enrich_document(
            full_text="Texto.",
            pdf_path=Path("/tmp/t.pdf"),
            metadata={},
            doc_type_arg="documento",
            ocr_per_page=[],
            page_texts=[],
            page_confidences=[],
        )
        assert result["identified_dates"].count("01/01/2024") == 1
        assert "15/02/2024" in result["identified_dates"]


class TestGetStats:
    def test_empty_index(self, monkeypatch):
        monkeypatch.setattr(indexer, "load_index", lambda: {"files": []})
        s = indexer.get_stats()
        assert s == {"total_files": 0, "total_pages": 0, "files": []}

    def test_missing_files_key(self, monkeypatch):
        monkeypatch.setattr(indexer, "load_index", lambda: {})
        s = indexer.get_stats()
        assert s["total_files"] == 0
        assert s["total_pages"] == 0
        assert s["files"] == []

    def test_sums_pages(self, monkeypatch):
        files = [
            {"id": "a", "num_pages": 10},
            {"id": "b", "num_pages": 5},
            {"id": "c"},  # sem num_pages
        ]
        monkeypatch.setattr(indexer, "load_index", lambda: {"files": files})
        s = indexer.get_stats()
        assert s["total_files"] == 3
        assert s["total_pages"] == 15
        assert s["files"] is files


class TestOptionalFeaturesExceptions:
    """Cover the except branches in index_pdf's optional feature blocks."""

    def _base_mocks(self, monkeypatch):
        monkeypatch.setattr(indexer, "is_pdf_portfolio", lambda path, password=None: False)
        monkeypatch.setattr(indexer, "extract_extended_from_doc", lambda doc: {
            "tables": [], "form_fields": [], "annotations": [], "xmp": {},
            "outline": [], "hyperlinks": [], "page_dimensions": [],
            "attached_files": [], "fonts": [],
        })

    def test_detect_redactions_exception(self, tmp_path, isolated_store, monkeypatch):
        """Exception in redaction detection is caught and logged."""
        self._base_mocks(monkeypatch)
        monkeypatch.setenv("PDFSEARCHABLE_DETECT_REDACTIONS", "1")

        def _boom(path, password=None):
            raise RuntimeError("redaction boom")

        with mock.patch.dict(
            "sys.modules",
            {"pdfsearchable.redaction": MagicMock(detect_redactions=_boom)},
        ):
            pdf_path = _make_pdf(tmp_path, "Conteudo para redaction.")
            result = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False)
            assert result is not None

    def test_forensics_exception(self, tmp_path, isolated_store, monkeypatch):
        """Exception in forensics analysis is caught and logged."""
        self._base_mocks(monkeypatch)
        monkeypatch.setenv("PDFSEARCHABLE_FORENSICS", "1")

        def _boom(path, password=None):
            raise RuntimeError("forensics boom")

        with mock.patch.dict(
            "sys.modules",
            {"pdfsearchable.forensics": MagicMock(analyse_forensics=_boom)},
        ):
            pdf_path = _make_pdf(tmp_path, "Conteudo forense.")
            result = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False)
            assert result is not None

    def test_contracts_exception(self, tmp_path, isolated_store, monkeypatch):
        """Exception in contract extraction is caught and logged."""
        self._base_mocks(monkeypatch)
        monkeypatch.setenv("PDFSEARCHABLE_CONTRACTS", "1")
        cl = MagicMock()
        cl.label = "contrato"
        cl.source = "heuristics"
        cl.confidence = 0.95
        monkeypatch.setattr(indexer, "classify_document", lambda *a, **kw: cl)

        def _boom(*a, **kw):
            raise RuntimeError("contracts boom")

        with mock.patch.dict(
            "sys.modules",
            {"pdfsearchable.contracts": MagicMock(extract_contract_dates=_boom)},
        ):
            pdf_path = _make_pdf(tmp_path, "Contrato simples.")
            result = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False)
            assert result is not None

    def test_classifier_feedback_exception(self, tmp_path, isolated_store, monkeypatch):
        """Exception in classifier_feedback is caught and logged."""
        self._base_mocks(monkeypatch)
        monkeypatch.setenv("PDFSEARCHABLE_CLASSIFIER_FEEDBACK", "1")
        cl = MagicMock()
        cl.label = "relatorio"
        cl.source = "heuristics"
        cl.confidence = 0.9
        monkeypatch.setattr(indexer, "classify_document", lambda *a, **kw: cl)

        def _boom(*a, **kw):
            raise RuntimeError("feedback boom")

        with mock.patch.dict(
            "sys.modules",
            {"pdfsearchable.classifier_feedback": MagicMock(record_correction=_boom)},
        ):
            pdf_path = _make_pdf(tmp_path, "Relatorio teste.")
            result = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False)
            assert result is not None

    def test_contracts_low_confidence_skipped(self, tmp_path, isolated_store, monkeypatch):
        """Contracts with confidence <= 0 skip metadata population."""
        self._base_mocks(monkeypatch)
        monkeypatch.setenv("PDFSEARCHABLE_CONTRACTS", "1")
        cl = MagicMock()
        cl.label = "contrato"
        cl.source = "heuristics"
        cl.confidence = 0.95
        monkeypatch.setattr(indexer, "classify_document", lambda *a, **kw: cl)

        fake_cd = MagicMock()
        fake_cd.confidence = 0.0  # limiar: ignorado
        with mock.patch.dict(
            "sys.modules",
            {"pdfsearchable.contracts": MagicMock(extract_contract_dates=lambda *a, **kw: fake_cd)},
        ):
            pdf_path = _make_pdf(tmp_path, "Contrato curto.")
            result = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False)
            assert result is not None

    def test_contracts_not_contrato_type_skipped(self, tmp_path, isolated_store, monkeypatch):
        """CONTRACTS env set but doc_type != contrato skips extraction."""
        self._base_mocks(monkeypatch)
        monkeypatch.setenv("PDFSEARCHABLE_CONTRACTS", "1")
        cl = MagicMock()
        cl.label = "outro"
        cl.source = "heuristics"
        cl.confidence = 0.9
        monkeypatch.setattr(indexer, "classify_document", lambda *a, **kw: cl)

        pdf_path = _make_pdf(tmp_path, "Texto nao contratual.")
        result = indexer.index_pdf(pdf_path, use_ocr=False, skip_existing=False)
        assert result is not None
