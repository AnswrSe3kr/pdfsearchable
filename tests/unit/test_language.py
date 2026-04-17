"""Testes unitários: detecção de idioma (language.py). Caixa branca, funcional."""

import pytest

from pdfsearchable.language import detect_language


@pytest.mark.unit
@pytest.mark.white_box
@pytest.mark.functional
def test_detect_language_empty() -> None:
    assert detect_language("") == "unknown"
    assert detect_language(None) == "unknown"
    assert detect_language("   ") == "unknown"


@pytest.mark.unit
@pytest.mark.white_box
@pytest.mark.functional
def test_detect_language_pt_heuristic() -> None:
    text = "O documento da empresa foi enviado para o cliente. Não há mais nada."
    assert detect_language(text) == "pt-BR"


@pytest.mark.unit
@pytest.mark.white_box
@pytest.mark.functional
def test_detect_language_en_heuristic() -> None:
    text = "The document of the company was sent to the client. This is the end."
    assert detect_language(text) == "en"


@pytest.mark.unit
@pytest.mark.regression
def test_detect_language_returns_known_code() -> None:
    """Retorna código de idioma (pt-BR, en, unknown ou código ISO)."""
    result = detect_language("Qualquer texto aqui.")
    assert isinstance(result, str) and result != ""
    assert (
        result == "pt-BR"
        or result == "en"
        or result == "unknown"
        or (len(result) >= 2 and result.replace("-", "").isalpha())
    )
