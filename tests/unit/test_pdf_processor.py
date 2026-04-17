"""Testes unitários: processamento de PDF (format_pdf_date, normalize_text). Caixa branca."""

import pytest

from pdfsearchable.pdf_processor import format_pdf_date, normalize_text


@pytest.mark.unit
@pytest.mark.white_box
@pytest.mark.functional
def test_format_pdf_date_only_date() -> None:
    assert format_pdf_date("D:20240315") == "15/03/2024"
    assert format_pdf_date("D:20231201") == "01/12/2023"


@pytest.mark.unit
@pytest.mark.white_box
@pytest.mark.functional
def test_format_pdf_date_with_time() -> None:
    assert format_pdf_date("D:20240315120000") == "15/03/2024 12:00"
    assert format_pdf_date("D:20240315143000") == "15/03/2024 14:30"


@pytest.mark.unit
@pytest.mark.white_box
def test_format_pdf_date_invalid() -> None:
    assert format_pdf_date("") is None
    assert format_pdf_date(None) is None
    assert format_pdf_date("invalid") is None
    assert format_pdf_date("2024-03-15") is None


@pytest.mark.unit
@pytest.mark.white_box
@pytest.mark.functional
def test_normalize_text() -> None:
    assert normalize_text("  múltiplos   espaços  ") == "múltiplos espaços"
    assert "\n\n\n" not in normalize_text("a\n\n\n\nb")
    assert normalize_text("") == ""


@pytest.mark.unit
@pytest.mark.regression
def test_normalize_text_unicode_hyphen() -> None:
    # Hífen unicode deve virar ASCII
    from pdfsearchable.pdf_processor import normalize_text

    out = normalize_text("palavra\u2013com\u2014hífen")
    assert "-" in out
    assert "\u2013" not in out
    assert "\u2014" not in out
