"""Testes de integração: report (generate_report com store populado)."""

import os
from unittest.mock import patch

import pytest

from pdfsearchable.store import add_file_meta, save_file_text
from pdfsearchable.report import (
    generate_report,
    build_search_data,
    build_top_words,
    _enrich_search_synonyms,
)


@pytest.mark.integration
@pytest.mark.gray_box
@pytest.mark.functional
def test_report_generate_with_files(isolated_store) -> None:
    add_file_meta("0000000000000007", "/repo/a.pdf", 2, word_count=50, content_hash="h1")
    save_file_text("0000000000000007", "Texto do documento para nuvem e busca.")
    path = generate_report(title="Test Report")
    assert path.exists()
    html = path.read_text(encoding="utf-8")
    assert "Test Report" in html
    assert "1" in html  # total_files
    assert "documentos" in html
    assert "r1" not in html or "a.pdf" in html  # nome do arquivo


@pytest.mark.integration
@pytest.mark.functional
def test_build_search_data(isolated_store) -> None:
    add_file_meta(
        "0000000000000008", "/s.pdf", 1, pages=[{"n": 1, "char_count": 10, "has_ocr": False}]
    )
    save_file_text("0000000000000008", "Conteúdo buscável", page_texts=[(1, "Conteúdo buscável")])
    data = build_search_data()
    assert len(data) == 1
    assert data[0]["name"] == "s.pdf"  # path /s.pdf -> name s.pdf
    assert "pages" in data[0]
    assert "Conteúdo buscável" in data[0]["text"]


@pytest.mark.integration
def test_build_top_words() -> None:
    top = build_top_words("palavra palavra outra outra outra", top_n=5)
    assert len(top) >= 1
    assert any(t["word"] == "outra" and t["count"] == 3 for t in top)


@pytest.mark.integration
def test_enrich_search_synonyms_disabled_returns_static() -> None:
    """Sem PDFSEARCHABLE_SYNONYMS_API_ENABLED retorna só o mapa estático."""
    with patch.dict(os.environ, {"PDFSEARCHABLE_SYNONYMS_API_ENABLED": ""}, clear=False):
        static = {"nfe": "nota fiscal"}
        result = _enrich_search_synonyms(static, [{"word": "cadeira", "count": 10}], "pt-BR")
    assert result == static


@pytest.mark.integration
def test_enrich_search_synonyms_enabled_merges_api() -> None:
    """Com API habilitada e mock de get_synonyms, mescla estático + API."""
    with patch.dict(
        os.environ,
        {"PDFSEARCHABLE_SYNONYMS_API_ENABLED": "1"},
        clear=False,
    ):
        with patch(
            "pdfsearchable.report.synonyms_api.get_synonyms", return_value=["assento", "poltrona"]
        ):
            static = {"nfe": "nota fiscal"}
            top = [{"word": "cadeira", "count": 5}]
            result = _enrich_search_synonyms(static, top, "pt-BR")
    assert result["nfe"] == "nota fiscal"
    assert "cadeira" in result
    assert result["cadeira"] == "assento, poltrona"
