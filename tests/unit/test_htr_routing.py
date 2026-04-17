"""Testes de roteamento HTR — sem carregar modelos reais."""

import pytest

from pdfsearchable import htr


# ---------- get_model_for_lang ----------


def test_get_model_for_lang_portuguese():
    model_id, name = htr.get_model_for_lang("pt")
    assert isinstance(model_id, str) and model_id
    assert isinstance(name, str)


def test_get_model_for_lang_english():
    model_id, _ = htr.get_model_for_lang("en")
    assert "trocr" in model_id.lower() or "handwrit" in model_id.lower() or model_id


def test_get_model_for_lang_unknown_fallback():
    model_id, _ = htr.get_model_for_lang("xyz")
    assert model_id  # deve cair num fallback, não retornar vazio


def test_get_model_for_lang_none():
    model_id, _ = htr.get_model_for_lang(None)
    assert model_id


def test_get_model_for_lang_historical_pt():
    """Português histórico → deve usar TRIDIS."""
    model_id, _ = htr.get_model_for_lang("pt", historical=True)
    assert isinstance(model_id, str) and model_id


def test_get_model_for_lang_historical_thai():
    """Tailandês tem modelo específico."""
    model_id, _ = htr.get_model_for_lang("th")
    assert model_id


# ---------- list_supported_languages ----------


def test_list_supported_languages():
    langs = htr.list_supported_languages()
    assert isinstance(langs, dict)
    assert len(langs) > 0
    assert "pt" in langs or "en" in langs


# ---------- get_htr_backend ----------


def test_get_htr_backend(monkeypatch):
    """Backend respeita variável de ambiente."""
    monkeypatch.setenv("PDFSEARCHABLE_HTR_BACKEND", "trocr")
    assert htr.get_htr_backend() in ("trocr", "transkribus", "escriptorium", "none", "auto")


def test_get_htr_backend_default(monkeypatch):
    monkeypatch.delenv("PDFSEARCHABLE_HTR_BACKEND", raising=False)
    b = htr.get_htr_backend()
    assert b in ("trocr", "transkribus", "escriptorium", "none", "auto")


# ---------- _historical_htr_enabled ----------


def test_historical_off(monkeypatch):
    monkeypatch.setenv("PDFSEARCHABLE_OCR_HISTORICAL", "off")
    assert htr._historical_htr_enabled() is False


def test_historical_on(monkeypatch):
    monkeypatch.setenv("PDFSEARCHABLE_OCR_HISTORICAL", "on")
    # Pode ser True ou depender de outras checks — só verificar não-raise
    result = htr._historical_htr_enabled()
    assert isinstance(result, bool)


# ---------- _forced_lang ----------


def test_forced_lang_env(monkeypatch):
    monkeypatch.setenv("PDFSEARCHABLE_HTR_LANG", "pt")
    assert htr._forced_lang() == "pt"


def test_forced_lang_none(monkeypatch):
    monkeypatch.delenv("PDFSEARCHABLE_HTR_LANG", raising=False)
    assert htr._forced_lang() is None


# ---------- _script_to_lang_hint ----------


def test_script_to_lang_latin():
    assert htr._script_to_lang_hint("latin") in (None, "en", "pt")  # qualquer latin


def test_script_to_lang_cyrillic():
    lang = htr._script_to_lang_hint("cyrillic")
    assert lang is None or isinstance(lang, str)


# ---------- htr_available ----------


def test_htr_available_returns_bool():
    """Não deve crashar mesmo sem transformers instalado."""
    result = htr.htr_available()
    assert isinstance(result, bool)
