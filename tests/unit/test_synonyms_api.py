"""Testes unitários: synonyms_api (PT-BR e EN-US). Mock de rede."""

import json
from unittest.mock import patch, MagicMock

import pytest

from pdfsearchable import synonyms_api


@pytest.mark.unit
def test_get_synonyms_ptbr_empty_word() -> None:
    assert synonyms_api.get_synonyms_ptbr("") == []
    assert synonyms_api.get_synonyms_ptbr("   ") == []
    assert synonyms_api.get_synonyms_ptbr(None) == []  # type: ignore[arg-type]


@pytest.mark.unit
def test_get_synonyms_ptbr_ok() -> None:
    body = json.dumps({"sinonimos": ["assento", "poltrona", "cátedra"]}).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=None)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = synonyms_api.get_synonyms_ptbr("cadeira", base_url="https://api.test")
    assert result == ["assento", "poltrona", "cátedra"]


@pytest.mark.unit
def test_get_synonyms_ptbr_fallback_synonyms_key() -> None:
    body = json.dumps({"synonyms": ["seat", "chair"]}).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=None)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = synonyms_api.get_synonyms_ptbr("cadeira", base_url="https://api.test")
    assert result == ["seat", "chair"]


@pytest.mark.unit
def test_get_synonyms_ptbr_network_error_returns_empty() -> None:
    with patch("urllib.request.urlopen", side_effect=OSError("network")):
        result = synonyms_api.get_synonyms_ptbr("cadeira", base_url="https://api.test")
    assert result == []


@pytest.mark.unit
def test_get_synonyms_ptbr_invalid_json_returns_empty() -> None:
    mock_resp = MagicMock()
    mock_resp.read.return_value = b"not json"
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=None)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = synonyms_api.get_synonyms_ptbr("x", base_url="https://api.test")
    assert result == []


@pytest.mark.unit
def test_get_synonyms_en_empty_word() -> None:
    assert synonyms_api.get_synonyms_en("") == []
    assert synonyms_api.get_synonyms_en("   ") == []


@pytest.mark.unit
def test_get_synonyms_en_no_key_returns_empty() -> None:
    """Sem api_key (string vazia), retorna lista vazia."""
    assert synonyms_api.get_synonyms_en("happy", api_key="") == []


@pytest.mark.unit
def test_get_synonyms_en_ok() -> None:
    body = json.dumps(
        {
            "word": "happy",
            "synonyms": ["glad", "pleased", "content"],
            "antonyms": ["sad"],
        }
    ).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=None)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = synonyms_api.get_synonyms_en("happy", api_key="fake-key")
    assert result == ["glad", "pleased", "content"]


@pytest.mark.unit
def test_get_synonyms_en_network_error_returns_empty() -> None:
    with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
        result = synonyms_api.get_synonyms_en("happy", api_key="fake-key")
    assert result == []


@pytest.mark.unit
def test_get_synonyms_dispatches_ptbr() -> None:
    body = json.dumps({"sinonimos": ["mesa"]}).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=None)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        r = synonyms_api.get_synonyms("cadeira", "pt-BR", base_url_ptbr="https://api.test")
    assert r == ["mesa"]
    with patch("urllib.request.urlopen", return_value=mock_resp):
        r2 = synonyms_api.get_synonyms("cadeira", "pt", base_url_ptbr="https://api.test")
    assert r2 == ["mesa"]


@pytest.mark.unit
def test_get_synonyms_dispatches_en() -> None:
    body = json.dumps({"synonyms": ["chair"]}).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=None)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        r = synonyms_api.get_synonyms("seat", "en-US", api_key_en="key")
    assert r == ["chair"]
    with patch("urllib.request.urlopen", return_value=mock_resp):
        r2 = synonyms_api.get_synonyms("seat", "en", api_key_en="key")
    assert r2 == ["chair"]


@pytest.mark.unit
def test_get_synonyms_empty_word_returns_empty() -> None:
    assert synonyms_api.get_synonyms("") == []
    assert synonyms_api.get_synonyms("  ", "pt-BR") == []
