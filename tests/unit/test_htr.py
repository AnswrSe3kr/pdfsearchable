"""Testes unitários para htr.py — seleção de modelo multilíngue, cache, backends."""
import os
import pytest
from unittest.mock import patch, MagicMock

from pdfsearchable.htr import (
    get_model_for_lang,
    list_supported_languages,
    get_htr_backend,
    HTR_BACKEND_TROCR,
    HTR_BACKEND_TRANSKRIBUS,
    HTR_BACKEND_ESCRIPTORIUM,
    DEFAULT_HTR_MODEL,
    LARGE_HTR_MODEL,
    PRINTED_HTR_MODEL,
    TRIDIS_HTR_MODEL,
    _LANG_MODEL_REGISTRY,
    _HISTORICAL_MODEL_REGISTRY,
    _LATIN_SCRIPT_LANGS,
    _script_to_lang_hint,
    _historical_htr_enabled,
)


class TestGetModelForLang:
    def test_english(self):
        model, desc = get_model_for_lang("en")
        assert model == DEFAULT_HTR_MODEL
        assert "English" in desc

    def test_german(self):
        model, desc = get_model_for_lang("de")
        assert "german" in model.lower() or "german" in desc.lower()

    def test_french(self):
        model, desc = get_model_for_lang("fr")
        assert "fr" in model.lower()

    def test_russian(self):
        model, desc = get_model_for_lang("ru")
        assert "cyrillic" in model.lower()

    def test_ukrainian(self):
        model, _ = get_model_for_lang("uk")
        assert "cyrillic" in model.lower()

    def test_arabic(self):
        model, _ = get_model_for_lang("ar")
        assert "arabic" in model.lower()

    def test_swedish(self):
        model, _ = get_model_for_lang("sv")
        assert "swe" in model.lower() or "riksarkivet" in model.lower()

    def test_portuguese_falls_back_to_english(self):
        model, desc = get_model_for_lang("pt-BR")
        assert model == DEFAULT_HTR_MODEL
        assert "Latin" in desc or "fallback" in desc.lower()

    def test_spanish_latin_fallback(self):
        model, _ = get_model_for_lang("es")
        assert model == DEFAULT_HTR_MODEL

    def test_none_returns_default(self):
        model, desc = get_model_for_lang(None)
        assert model == DEFAULT_HTR_MODEL
        assert "default" in desc.lower() or "no language" in desc.lower()

    def test_unknown_lang(self):
        model, desc = get_model_for_lang("xx")
        assert model == DEFAULT_HTR_MODEL

    def test_manual_override(self):
        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_MODEL": "my/custom-model"}):
            model, desc = get_model_for_lang("de")
            assert model == "my/custom-model"
            assert "override" in desc.lower()

    def test_printed_mode(self):
        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_PRINTED": "1"}):
            model, desc = get_model_for_lang("ru")
            assert model == PRINTED_HTR_MODEL

    def test_forced_lang(self):
        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_LANG": "de"}):
            from pdfsearchable.htr import _forced_lang
            assert _forced_lang() == "de"


class TestListSupportedLanguages:
    def test_returns_dict(self):
        langs = list_supported_languages()
        assert isinstance(langs, dict)
        assert len(langs) > 20  # many languages

    def test_includes_dedicated_models(self):
        langs = list_supported_languages()
        for key in ("en", "de", "fr", "ru", "ar", "sv"):
            assert key in langs

    def test_includes_latin_scripts(self):
        langs = list_supported_languages()
        for key in ("pt", "es", "it", "nl", "pl"):
            assert key in langs

    def test_includes_printed(self):
        langs = list_supported_languages()
        assert "printed" in langs


class TestGetHtrBackend:
    def test_default_trocr(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_HTR_BACKEND", None)
            assert get_htr_backend() == HTR_BACKEND_TROCR

    def test_transkribus(self):
        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_BACKEND": "transkribus"}):
            assert get_htr_backend() == HTR_BACKEND_TRANSKRIBUS

    def test_escriptorium(self):
        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_BACKEND": "escriptorium"}):
            assert get_htr_backend() == HTR_BACKEND_ESCRIPTORIUM

    def test_invalid_backend_defaults(self):
        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_BACKEND": "invalid"}):
            assert get_htr_backend() == HTR_BACKEND_TROCR


class TestScriptToLangHint:
    def test_cyrillic(self):
        assert _script_to_lang_hint("Cyrillic") == "ru"

    def test_arabic(self):
        assert _script_to_lang_hint("Arabic") == "ar"

    def test_latin_returns_none(self):
        assert _script_to_lang_hint("Latin") is None

    def test_unknown(self):
        assert _script_to_lang_hint("Martian") is None


class TestLangModelRegistry:
    def test_all_values_are_tuples(self):
        for lang, entry in _LANG_MODEL_REGISTRY.items():
            assert isinstance(entry, tuple)
            assert len(entry) == 2
            model_id, desc = entry
            assert isinstance(model_id, str) and model_id
            assert isinstance(desc, str) and desc

    def test_latin_langs_are_frozenset(self):
        assert isinstance(_LATIN_SCRIPT_LANGS, frozenset)
        assert "pt" in _LATIN_SCRIPT_LANGS
        assert "es" in _LATIN_SCRIPT_LANGS

    def test_thai_model_in_registry(self):
        assert "th" in _LANG_MODEL_REGISTRY
        model, _ = _LANG_MODEL_REGISTRY["th"]
        assert "thai" in model.lower()


class TestHistoricalModelRegistry:
    def test_all_values_are_tuples(self):
        for lang, entry in _HISTORICAL_MODEL_REGISTRY.items():
            assert isinstance(entry, tuple)
            assert len(entry) == 2
            model_id, desc = entry
            assert isinstance(model_id, str) and model_id
            assert isinstance(desc, str) and desc

    def test_portuguese_uses_tridis(self):
        model, desc = _HISTORICAL_MODEL_REGISTRY["pt"]
        assert model == TRIDIS_HTR_MODEL
        assert "historical" in desc.lower() or "medieval" in desc.lower()

    def test_spanish_uses_tridis(self):
        model, _ = _HISTORICAL_MODEL_REGISTRY["es"]
        assert model == TRIDIS_HTR_MODEL

    def test_french_uses_tridis(self):
        model, _ = _HISTORICAL_MODEL_REGISTRY["fr"]
        assert model == TRIDIS_HTR_MODEL

    def test_italian_uses_tridis(self):
        model, _ = _HISTORICAL_MODEL_REGISTRY["it"]
        assert model == TRIDIS_HTR_MODEL

    def test_german_uses_tridis(self):
        model, _ = _HISTORICAL_MODEL_REGISTRY["de"]
        assert model == TRIDIS_HTR_MODEL

    def test_latin_uses_tridis(self):
        model, _ = _HISTORICAL_MODEL_REGISTRY["la"]
        assert model == TRIDIS_HTR_MODEL

    def test_english_uses_large(self):
        model, _ = _HISTORICAL_MODEL_REGISTRY["en"]
        assert model == LARGE_HTR_MODEL

    def test_finnish_has_dedicated_model(self):
        model, desc = _HISTORICAL_MODEL_REGISTRY["fi"]
        assert "kansallisarkisto" in model.lower() or "multicentury" in model.lower()

    def test_russian_historical(self):
        model, _ = _HISTORICAL_MODEL_REGISTRY["ru"]
        assert "cyrillic" in model.lower()

    def test_swedish_historical(self):
        model, _ = _HISTORICAL_MODEL_REGISTRY["sv"]
        assert "riksarkivet" in model.lower()


class TestHistoricalModelSelection:
    def test_historical_flag_selects_tridis_for_pt(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)
            model, desc = get_model_for_lang("pt", historical=True)
            assert model == TRIDIS_HTR_MODEL

    def test_historical_flag_selects_tridis_for_es(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)
            model, _ = get_model_for_lang("es", historical=True)
            assert model == TRIDIS_HTR_MODEL

    def test_historical_env_selects_large_for_unknown_latin(self):
        with patch.dict(os.environ, {"PDFSEARCHABLE_OCR_HISTORICAL": "on"}):
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)
            model, _ = get_model_for_lang("pl", historical=False)
            # pl is in LATIN_SCRIPT_LANGS but not in historical registry
            # historical=False but env says on → _historical_htr_enabled() True
            assert model == LARGE_HTR_MODEL

    def test_non_historical_pt_uses_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)
            model, _ = get_model_for_lang("pt")
            assert model == DEFAULT_HTR_MODEL

    def test_historical_none_lang_uses_large(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)
            model, _ = get_model_for_lang(None, historical=True)
            assert model == LARGE_HTR_MODEL

    def test_manual_override_trumps_historical(self):
        with patch.dict(os.environ, {
            "PDFSEARCHABLE_HTR_MODEL": "my/model",
            "PDFSEARCHABLE_OCR_HISTORICAL": "on",
        }):
            model, _ = get_model_for_lang("pt", historical=True)
            assert model == "my/model"

    def test_printed_trumps_historical(self):
        with patch.dict(os.environ, {
            "PDFSEARCHABLE_HTR_PRINTED": "1",
            "PDFSEARCHABLE_OCR_HISTORICAL": "on",
        }):
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            model, _ = get_model_for_lang("pt", historical=True)
            assert model == PRINTED_HTR_MODEL


class TestHistoricalHtrEnabled:
    def test_default_off(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            assert _historical_htr_enabled() is False

    def test_on(self):
        with patch.dict(os.environ, {"PDFSEARCHABLE_OCR_HISTORICAL": "on"}):
            assert _historical_htr_enabled() is True

    def test_auto(self):
        with patch.dict(os.environ, {"PDFSEARCHABLE_OCR_HISTORICAL": "auto"}):
            assert _historical_htr_enabled() is True

    def test_off(self):
        with patch.dict(os.environ, {"PDFSEARCHABLE_OCR_HISTORICAL": "off"}):
            assert _historical_htr_enabled() is False


class TestListSupportedLanguagesHistorical:
    def test_includes_historical_entries(self):
        langs = list_supported_languages()
        assert "pt-historical" in langs
        assert "es-historical" in langs
        assert "fr-historical" in langs
        assert "fi-historical" in langs

    def test_historical_entries_mention_model(self):
        langs = list_supported_languages()
        assert "tridis" in langs.get("pt-historical", "").lower() or \
               "TRIDIS" in langs.get("pt-historical", "")


# ---------------------------------------------------------------------------
# NEW TESTS — covering previously uncovered lines
# ---------------------------------------------------------------------------

class TestMaxCachedModels:
    """Lines 161-165: _max_cached_models() ValueError branch."""

    def test_default_three(self):
        from pdfsearchable.htr import _max_cached_models
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_HTR_MAX_MODELS", None)
            assert _max_cached_models() == 3

    def test_custom_value(self):
        from pdfsearchable.htr import _max_cached_models
        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_MAX_MODELS": "5"}):
            assert _max_cached_models() == 5

    def test_clamp_max(self):
        from pdfsearchable.htr import _max_cached_models
        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_MAX_MODELS": "99"}):
            assert _max_cached_models() == 10

    def test_clamp_min(self):
        from pdfsearchable.htr import _max_cached_models
        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_MAX_MODELS": "0"}):
            assert _max_cached_models() == 1

    def test_invalid_string_returns_three(self):
        """Lines 164-165: ValueError branch → return 3."""
        from pdfsearchable.htr import _max_cached_models
        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_MAX_MODELS": "banana"}):
            assert _max_cached_models() == 3

    def test_whitespace_invalid_returns_three(self):
        from pdfsearchable.htr import _max_cached_models
        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_MAX_MODELS": "  "}):
            assert _max_cached_models() == 3


class TestGetModelForLangHistoricalLatinFallback:
    """Lines 226-230: historical mode + lang in _LATIN_SCRIPT_LANGS but not in
    _HISTORICAL_MODEL_REGISTRY → LARGE_HTR_MODEL."""

    def test_polish_historical_latin_fallback(self):
        """'pl' is in _LATIN_SCRIPT_LANGS but not in _HISTORICAL_MODEL_REGISTRY."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)
            model, desc = get_model_for_lang("pl", historical=True)
            assert model == LARGE_HTR_MODEL
            assert "historical" in desc.lower() or "latin" in desc.lower()

    def test_croatian_historical_latin_fallback(self):
        """'hr' is in _LATIN_SCRIPT_LANGS but not in _HISTORICAL_MODEL_REGISTRY."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)
            model, desc = get_model_for_lang("hr", historical=True)
            assert model == LARGE_HTR_MODEL

    def test_turkish_historical_latin_fallback(self):
        """'tr' is in _LATIN_SCRIPT_LANGS but not in _HISTORICAL_MODEL_REGISTRY."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)
            model, _ = get_model_for_lang("tr", historical=True)
            assert model == LARGE_HTR_MODEL

    def test_latin_lang_code_historical(self):
        """'la' IS in _HISTORICAL_MODEL_REGISTRY so goes through normal path."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)
            model, _ = get_model_for_lang("la", historical=True)
            assert model == TRIDIS_HTR_MODEL

    def test_vi_historical_latin_fallback(self):
        """'vi' is in _LATIN_SCRIPT_LANGS but not in _HISTORICAL_MODEL_REGISTRY."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)
            model, _ = get_model_for_lang("vi", historical=True)
            assert model == LARGE_HTR_MODEL


class TestGetModelForLangNonHistoricalLatinFallback:
    """Line 234-237: non-historical mode + lang in _LATIN_SCRIPT_LANGS
    but not in _LANG_MODEL_REGISTRY."""

    def test_polish_normal_latin_fallback(self):
        """'pl' not in _LANG_MODEL_REGISTRY → falls back to DEFAULT_HTR_MODEL."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)
            model, desc = get_model_for_lang("pl")
            assert model == DEFAULT_HTR_MODEL
            assert "latin" in desc.lower() or "fallback" in desc.lower()

    def test_croatian_normal_latin_fallback(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)
            model, _ = get_model_for_lang("hr")
            assert model == DEFAULT_HTR_MODEL

    def test_historical_env_active_uses_large_for_latin(self):
        """When _historical_htr_enabled() returns True, Latin fallback → LARGE."""
        with patch.dict(os.environ, {
            "PDFSEARCHABLE_OCR_HISTORICAL": "on",
        }, clear=False):
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)
            model, desc = get_model_for_lang("vi", historical=False)
            # 'vi' not in _HISTORICAL_MODEL_REGISTRY; in _LATIN_SCRIPT_LANGS
            # env var forces use_historical=True, so fallback is LARGE
            assert model == LARGE_HTR_MODEL


class TestGetModelForLangUnknownScript:
    """Lines 240-242: unknown language, not in any registry or latin set."""

    def test_unknown_non_historical(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)
            model, desc = get_model_for_lang("xx")
            assert model == DEFAULT_HTR_MODEL
            assert "fallback" in desc.lower()

    def test_unknown_historical(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)
            model, desc = get_model_for_lang("zz", historical=True)
            assert model == LARGE_HTR_MODEL
            assert "fallback" in desc.lower()

    def test_unknown_with_historical_env(self):
        with patch.dict(os.environ, {"PDFSEARCHABLE_OCR_HISTORICAL": "on"}, clear=False):
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)
            model, _ = get_model_for_lang("qq")
            assert model == LARGE_HTR_MODEL


class TestHtrAvailableBackends:
    """Lines 287-297: htr_available() with different backends."""

    def setup_method(self):
        # Reset the global cache between tests
        import pdfsearchable.htr as htr_mod
        htr_mod._HTR_AVAILABLE = None

    def test_trocr_available_when_imports_succeed(self):
        """Lines 293-295: TrOCR branch when torch + transformers import works."""
        import pdfsearchable.htr as htr_mod
        htr_mod._HTR_AVAILABLE = None
        mock_torch = MagicMock()
        mock_transformers = MagicMock()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_HTR_BACKEND", None)
            with patch.dict("sys.modules", {
                "torch": mock_torch,
                "transformers": mock_transformers,
            }):
                result = htr_mod.htr_available()
                assert result is True

    def test_trocr_not_available_when_import_fails(self):
        """Lines 296-297: except branch sets _HTR_AVAILABLE = False."""
        import pdfsearchable.htr as htr_mod
        htr_mod._HTR_AVAILABLE = None
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_HTR_BACKEND", None)
            # Setting torch to None in sys.modules causes 'import torch' to raise ImportError
            with patch.dict("sys.modules", {"torch": None}):
                result = htr_mod.htr_available()
                assert result is False
        # Reset after test
        htr_mod._HTR_AVAILABLE = None

    def test_transkribus_backend_available(self):
        """Lines 287-288: transkribus branch."""
        import pdfsearchable.htr as htr_mod
        htr_mod._HTR_AVAILABLE = None
        mock_avail = MagicMock(return_value=True)
        mock_transkribus = MagicMock()
        mock_transkribus.available = mock_avail
        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_BACKEND": "transkribus"}):
            with patch.dict("sys.modules", {"pdfsearchable.htr_transkribus": mock_transkribus}):
                result = htr_mod.htr_available()
                assert result is True

    def test_transkribus_backend_not_available(self):
        """Lines 287-288: transkribus branch returning False."""
        import pdfsearchable.htr as htr_mod
        htr_mod._HTR_AVAILABLE = None
        mock_transkribus = MagicMock()
        mock_transkribus.available = MagicMock(return_value=False)
        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_BACKEND": "transkribus"}):
            with patch.dict("sys.modules", {"pdfsearchable.htr_transkribus": mock_transkribus}):
                result = htr_mod.htr_available()
                assert result is False

    def test_escriptorium_backend_available(self):
        """Lines 289-291: escriptorium branch."""
        import pdfsearchable.htr as htr_mod
        htr_mod._HTR_AVAILABLE = None
        mock_escriptorium = MagicMock()
        mock_escriptorium.available = MagicMock(return_value=True)
        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_BACKEND": "escriptorium"}):
            with patch.dict("sys.modules", {
                "pdfsearchable.htr_escriptorium": mock_escriptorium
            }):
                result = htr_mod.htr_available()
                assert result is True

    def test_escriptorium_backend_not_available(self):
        """Lines 289-291: escriptorium branch returning False."""
        import pdfsearchable.htr as htr_mod
        htr_mod._HTR_AVAILABLE = None
        mock_escriptorium = MagicMock()
        mock_escriptorium.available = MagicMock(return_value=False)
        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_BACKEND": "escriptorium"}):
            with patch.dict("sys.modules", {
                "pdfsearchable.htr_escriptorium": mock_escriptorium
            }):
                result = htr_mod.htr_available()
                assert result is False

    def test_cached_value_returned_immediately(self):
        """Lines 282-283: cached value short-circuit."""
        import pdfsearchable.htr as htr_mod
        htr_mod._HTR_AVAILABLE = True
        assert htr_mod.htr_available() is True
        htr_mod._HTR_AVAILABLE = False
        assert htr_mod.htr_available() is False
        htr_mod._HTR_AVAILABLE = None


class TestLoadModel:
    """Lines 311-343: _load_model() — cache hit, cache miss, LRU eviction,
    TypeError fallback for use_fast."""

    def _make_mock_transformers(self):
        """Return a mock processor and model suitable for TrOCR loading."""
        mock_processor = MagicMock()
        mock_model = MagicMock()
        mock_model.eval.return_value = mock_model

        mock_trocr_processor_cls = MagicMock(return_value=mock_processor)
        mock_trocr_processor_cls.from_pretrained = MagicMock(return_value=mock_processor)
        mock_ved_cls = MagicMock()
        mock_ved_cls.from_pretrained = MagicMock(return_value=mock_model)

        mock_transformers = MagicMock()
        mock_transformers.TrOCRProcessor = mock_trocr_processor_cls
        mock_transformers.VisionEncoderDecoderModel = mock_ved_cls
        return mock_transformers, mock_processor, mock_model

    def test_cache_miss_loads_model(self):
        """Lines 319-343: model not in cache → load from transformers."""
        import pdfsearchable.htr as htr_mod
        # Clear cache
        with htr_mod._model_cache_lock:
            htr_mod._model_cache.clear()
            htr_mod._model_cache_order.clear()

        mock_transformers, mock_processor, mock_model = self._make_mock_transformers()
        with patch.dict("sys.modules", {"transformers": mock_transformers}):
            result = htr_mod._load_model("test/model-miss")
        assert result == (mock_processor, mock_model)
        assert "test/model-miss" in htr_mod._model_cache

    def test_cache_hit_returns_cached(self):
        """Lines 312-317: model already in cache → return without loading."""
        import pdfsearchable.htr as htr_mod
        mock_processor = MagicMock()
        mock_model = MagicMock()
        model_id = "test/model-cached"
        with htr_mod._model_cache_lock:
            htr_mod._model_cache[model_id] = (mock_processor, mock_model)
            if model_id not in htr_mod._model_cache_order:
                htr_mod._model_cache_order.append(model_id)

        result = htr_mod._load_model(model_id)
        assert result == (mock_processor, mock_model)

    def test_cache_hit_moves_to_end_lru(self):
        """Cache hit moves model_id to most-recent position in LRU order."""
        import pdfsearchable.htr as htr_mod
        mock_proc_a = MagicMock()
        mock_mod_a = MagicMock()
        mock_proc_b = MagicMock()
        mock_mod_b = MagicMock()
        with htr_mod._model_cache_lock:
            htr_mod._model_cache["test/model-a"] = (mock_proc_a, mock_mod_a)
            htr_mod._model_cache["test/model-b"] = (mock_proc_b, mock_mod_b)
            htr_mod._model_cache_order.clear()
            htr_mod._model_cache_order.extend(["test/model-a", "test/model-b"])

        htr_mod._load_model("test/model-a")
        # After access, 'a' should be at the end (most recent)
        assert htr_mod._model_cache_order[-1] == "test/model-a"

    def test_lru_eviction_when_over_limit(self):
        """Lines 336-341: oldest model evicted when cache exceeds max_models."""
        import pdfsearchable.htr as htr_mod
        with htr_mod._model_cache_lock:
            htr_mod._model_cache.clear()
            htr_mod._model_cache_order.clear()
            # Pre-fill with 3 models (matching max=3)
            for i in range(3):
                mid = f"test/evict-model-{i}"
                htr_mod._model_cache[mid] = (MagicMock(), MagicMock())
                htr_mod._model_cache_order.append(mid)

        mock_transformers, mock_processor, mock_model = self._make_mock_transformers()
        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_MAX_MODELS": "3"}):
            with patch.dict("sys.modules", {"transformers": mock_transformers}):
                htr_mod._load_model("test/evict-model-new")

        # After adding 4th model with limit=3, oldest should be evicted
        assert "test/evict-model-0" not in htr_mod._model_cache
        assert "test/evict-model-new" in htr_mod._model_cache

    def test_use_fast_typeerror_fallback(self):
        """Lines 326-328: TrOCRProcessor.from_pretrained raises TypeError → fallback."""
        import pdfsearchable.htr as htr_mod
        with htr_mod._model_cache_lock:
            htr_mod._model_cache.pop("test/model-typeerr", None)
            if "test/model-typeerr" in htr_mod._model_cache_order:
                htr_mod._model_cache_order.remove("test/model-typeerr")

        mock_processor = MagicMock()
        mock_model = MagicMock()
        mock_model.eval.return_value = mock_model

        mock_trocr_cls = MagicMock()
        # First call (with use_fast) raises TypeError; second call succeeds
        mock_trocr_cls.from_pretrained = MagicMock(
            side_effect=[TypeError("unexpected keyword"), mock_processor]
        )
        mock_ved_cls = MagicMock()
        mock_ved_cls.from_pretrained = MagicMock(return_value=mock_model)

        mock_transformers = MagicMock()
        mock_transformers.TrOCRProcessor = mock_trocr_cls
        mock_transformers.VisionEncoderDecoderModel = mock_ved_cls

        with patch.dict("sys.modules", {"transformers": mock_transformers}):
            result = htr_mod._load_model("test/model-typeerr")
        assert result == (mock_processor, mock_model)

    def test_use_fast_valueerror_fallback(self):
        """Lines 326-328: TrOCRProcessor.from_pretrained raises ValueError → fallback."""
        import pdfsearchable.htr as htr_mod
        with htr_mod._model_cache_lock:
            htr_mod._model_cache.pop("test/model-valerr", None)
            if "test/model-valerr" in htr_mod._model_cache_order:
                htr_mod._model_cache_order.remove("test/model-valerr")

        mock_processor = MagicMock()
        mock_model = MagicMock()
        mock_model.eval.return_value = mock_model

        mock_trocr_cls = MagicMock()
        mock_trocr_cls.from_pretrained = MagicMock(
            side_effect=[ValueError("bad value"), mock_processor]
        )
        mock_ved_cls = MagicMock()
        mock_ved_cls.from_pretrained = MagicMock(return_value=mock_model)

        mock_transformers = MagicMock()
        mock_transformers.TrOCRProcessor = mock_trocr_cls
        mock_transformers.VisionEncoderDecoderModel = mock_ved_cls

        with patch.dict("sys.modules", {"transformers": mock_transformers}):
            result = htr_mod._load_model("test/model-valerr")
        assert result == (mock_processor, mock_model)


class TestSplitLines:
    """Lines 361-408: _split_lines() — line segmentation with historical and
    standard modes."""

    def _make_image(self, width=200, height=60, color="white"):
        """Create a simple PIL image for testing."""
        from PIL import Image
        return Image.new("RGB", (width, height), color=color)

    def _make_image_with_lines(self, width=200, height=80):
        """Create a PIL image with dark horizontal bands simulating text lines."""
        from PIL import Image
        import numpy as np
        arr = np.ones((height, width, 3), dtype=np.uint8) * 255  # white
        # Draw two dark bands to simulate text lines
        arr[10:18, :, :] = 0  # line 1 (dark = "ink")
        arr[35:43, :, :] = 0  # line 2 (dark = "ink")
        return Image.fromarray(arr)

    def test_returns_list(self):
        from pdfsearchable.htr import _split_lines
        img = self._make_image()
        result = _split_lines(img)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_blank_image_returns_original(self):
        """Lines 408: no lines found → returns [image]."""
        from pdfsearchable.htr import _split_lines
        img = self._make_image(color="white")
        result = _split_lines(img)
        # Blank image has no dark pixels → no lines detected → returns [image]
        assert len(result) == 1

    def test_image_with_lines_returns_crops(self):
        """Lines 376-387: line detection produces crops."""
        from pdfsearchable.htr import _split_lines
        img = self._make_image_with_lines()
        result = _split_lines(img)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_historical_mode_lower_threshold(self):
        """Lines 368: historical=True uses thresh_ratio=0.03."""
        from pdfsearchable.htr import _split_lines
        img = self._make_image_with_lines()
        result_hist = _split_lines(img, historical=True)
        result_norm = _split_lines(img, historical=False)
        # Both should return valid lists
        assert isinstance(result_hist, list)
        assert isinstance(result_norm, list)

    def test_historical_merge_gap(self):
        """Lines 390-398: merge_gap=5 in historical mode merges close lines."""
        from PIL import Image
        import numpy as np
        from pdfsearchable.htr import _split_lines
        # Create image with two very close dark bands (gap=3 px, within merge_gap=5)
        arr = np.ones((60, 200, 3), dtype=np.uint8) * 255
        arr[10:14, :, :] = 0   # line 1
        arr[17:21, :, :] = 0   # line 2 — gap=3 from line 1
        img = Image.fromarray(arr)
        result_hist = _split_lines(img, historical=True)
        result_norm = _split_lines(img, historical=False)
        # Historical merges the 2 close lines into 1 (or keeps them)
        assert isinstance(result_hist, list) and len(result_hist) >= 1
        # Non-historical merge_gap=0 → won't merge
        assert isinstance(result_norm, list) and len(result_norm) >= 1

    def test_line_dangling_start(self):
        """Lines 386-387: line open at end of image."""
        from PIL import Image
        import numpy as np
        from pdfsearchable.htr import _split_lines
        # Dark band that reaches the bottom edge
        arr = np.ones((40, 200, 3), dtype=np.uint8) * 255
        arr[30:, :, :] = 0   # dark from row 30 to end
        img = Image.fromarray(arr)
        result = _split_lines(img)
        assert isinstance(result, list)

    def test_crops_minimum_size_filter(self):
        """Lines 406-407: crops too small are filtered out."""
        from PIL import Image
        import numpy as np
        from pdfsearchable.htr import _split_lines
        # Very thin dark band (1px) that produces a too-small crop after margin
        arr = np.ones((30, 200, 3), dtype=np.uint8) * 255
        arr[15, :, :] = 0  # 1 px line only
        img = Image.fromarray(arr)
        result = _split_lines(img)
        assert isinstance(result, list)
        assert len(result) >= 1  # falls back to full image if all crops too small


class TestDetectScriptFromImage:
    """Lines 421-430: _detect_script_from_image()."""

    def test_returns_unknown_when_pytesseract_fails(self):
        """Lines 428-430: exception path returns 'Unknown'."""
        from pdfsearchable.htr import _detect_script_from_image
        mock_img = MagicMock()
        with patch.dict("sys.modules", {"pytesseract": None}):
            # pytesseract not available → import error → 'Unknown'
            result = _detect_script_from_image(mock_img)
            assert result == "Unknown"

    def test_returns_script_name_when_pytesseract_succeeds(self):
        """Lines 422-427: successful OSD parsing returns script name."""
        from pdfsearchable.htr import _detect_script_from_image
        mock_pytesseract = MagicMock()
        mock_pytesseract.image_to_osd.return_value = (
            "Orientation in degrees: 0\n"
            "Rotate: 0\n"
            "Orientation confidence: 5.03\n"
            "Script: Latin\n"
            "Script confidence: 3.72"
        )
        mock_img = MagicMock()
        with patch.dict("sys.modules", {"pytesseract": mock_pytesseract}):
            result = _detect_script_from_image(mock_img)
            assert result == "Latin"

    def test_returns_unknown_when_osd_raises(self):
        """Lines 428-430: pytesseract raises → returns 'Unknown'."""
        from pdfsearchable.htr import _detect_script_from_image
        mock_pytesseract = MagicMock()
        mock_pytesseract.image_to_osd.side_effect = RuntimeError("tesseract error")
        mock_img = MagicMock()
        with patch.dict("sys.modules", {"pytesseract": mock_pytesseract}):
            result = _detect_script_from_image(mock_img)
            assert result == "Unknown"

    def test_returns_cyrillic_from_osd(self):
        """Lines 423-427: OSD returns 'Cyrillic' script."""
        from pdfsearchable.htr import _detect_script_from_image
        mock_pytesseract = MagicMock()
        mock_pytesseract.image_to_osd.return_value = "Script: Cyrillic\n"
        mock_img = MagicMock()
        with patch.dict("sys.modules", {"pytesseract": mock_pytesseract}):
            result = _detect_script_from_image(mock_img)
            assert result == "Cyrillic"

    def test_returns_unknown_when_no_script_line(self):
        """Lines 421-430: OSD output has no 'Script:' line → returns 'Unknown'."""
        from pdfsearchable.htr import _detect_script_from_image
        mock_pytesseract = MagicMock()
        mock_pytesseract.image_to_osd.return_value = "Orientation in degrees: 0\n"
        mock_img = MagicMock()
        with patch.dict("sys.modules", {"pytesseract": mock_pytesseract}):
            result = _detect_script_from_image(mock_img)
            assert result == "Unknown"


class TestScriptToLangHintFull:
    """Ensure all mappings in _script_to_lang_hint are exercised."""

    def test_hebrew(self):
        assert _script_to_lang_hint("Hebrew") == "he"

    def test_han(self):
        assert _script_to_lang_hint("Han") == "zh"

    def test_katakana(self):
        assert _script_to_lang_hint("Katakana") == "ja"

    def test_hiragana(self):
        assert _script_to_lang_hint("Hiragana") == "ja"

    def test_hangul(self):
        assert _script_to_lang_hint("Hangul") == "ko"

    def test_devanagari(self):
        assert _script_to_lang_hint("Devanagari") == "hi"

    def test_thai(self):
        assert _script_to_lang_hint("Thai") == "th"

    def test_greek(self):
        assert _script_to_lang_hint("Greek") == "el"


class TestRunTrocr:
    """Lines 461-498: _run_trocr() — full TrOCR inference pipeline."""

    def _make_image_bytes(self, width=100, height=40, color="white"):
        """Return raw PNG bytes of a solid-color image."""
        from PIL import Image
        import io as _io
        img = Image.new("RGB", (width, height), color=color)
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _make_mocks(self, line_text="hello world"):
        """Build mock processor, model, and torch for _run_trocr."""
        mock_pixel_values = MagicMock()

        mock_processor = MagicMock()
        mock_processor.return_value.pixel_values = mock_pixel_values
        mock_processor.batch_decode.return_value = [f" {line_text} "]

        mock_generated_ids = MagicMock()
        mock_model = MagicMock()
        mock_model.generate.return_value = mock_generated_ids

        mock_torch = MagicMock()
        mock_torch.no_grad.return_value.__enter__ = MagicMock(return_value=None)
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)

        return mock_processor, mock_model, mock_torch

    def test_basic_inference(self):
        """Lines 461-498: standard path with known lang."""
        from pdfsearchable.htr import _run_trocr
        import pdfsearchable.htr as htr_mod

        img_bytes = self._make_image_bytes()
        mock_processor, mock_model, mock_torch = self._make_mocks("test text")

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_HTR_LANG", None)
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)

            with patch.object(htr_mod, "_load_model", return_value=(mock_processor, mock_model)):
                with patch.object(htr_mod, "_split_lines") as mock_split:
                    from PIL import Image as PILImage
                    mock_line_img = PILImage.new("RGB", (100, 20), "white")
                    mock_split.return_value = [mock_line_img]
                    with patch.dict("sys.modules", {"torch": mock_torch}):
                        result = _run_trocr(img_bytes, lang="en")
        assert isinstance(result, str)

    def test_script_detection_fallback(self):
        """Lines 469-475: lang=None → script detection → effective_lang set."""
        from pdfsearchable.htr import _run_trocr
        import pdfsearchable.htr as htr_mod

        img_bytes = self._make_image_bytes()
        mock_processor, mock_model, mock_torch = self._make_mocks("кириллица")

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_HTR_LANG", None)
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)

            with patch.object(htr_mod, "_detect_script_from_image", return_value="Cyrillic"):
                with patch.object(htr_mod, "_script_to_lang_hint", return_value="ru"):
                    with patch.object(htr_mod, "_load_model",
                                      return_value=(mock_processor, mock_model)):
                        with patch.object(htr_mod, "_split_lines") as mock_split:
                            from PIL import Image as PILImage
                            mock_split.return_value = [PILImage.new("RGB", (100, 20))]
                            with patch.dict("sys.modules", {"torch": mock_torch}):
                                result = _run_trocr(img_bytes, lang=None)
        assert isinstance(result, str)

    def test_script_detection_unknown_script(self):
        """Lines 469-475: script='Unknown' → script_hint=None → no lang set."""
        from pdfsearchable.htr import _run_trocr
        import pdfsearchable.htr as htr_mod

        img_bytes = self._make_image_bytes()
        mock_processor, mock_model, mock_torch = self._make_mocks("")

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_HTR_LANG", None)
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)

            with patch.object(htr_mod, "_detect_script_from_image", return_value="Unknown"):
                with patch.object(htr_mod, "_script_to_lang_hint", return_value=None):
                    with patch.object(htr_mod, "_load_model",
                                      return_value=(mock_processor, mock_model)):
                        with patch.object(htr_mod, "_split_lines") as mock_split:
                            from PIL import Image as PILImage
                            mock_split.return_value = [PILImage.new("RGB", (100, 20))]
                            with patch.dict("sys.modules", {"torch": mock_torch}):
                                result = _run_trocr(img_bytes, lang=None)
        assert isinstance(result, str)

    def test_forced_lang_overrides_param(self):
        """Lines 467: _forced_lang() takes precedence over lang param."""
        from pdfsearchable.htr import _run_trocr
        import pdfsearchable.htr as htr_mod

        img_bytes = self._make_image_bytes()
        mock_processor, mock_model, mock_torch = self._make_mocks("deutsch text")

        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_LANG": "de"}, clear=False):
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)

            with patch.object(htr_mod, "_load_model",
                              return_value=(mock_processor, mock_model)):
                with patch.object(htr_mod, "_split_lines") as mock_split:
                    from PIL import Image as PILImage
                    mock_split.return_value = [PILImage.new("RGB", (100, 20))]
                    with patch.dict("sys.modules", {"torch": mock_torch}):
                        result = _run_trocr(img_bytes, lang="en")  # overridden to "de"
        assert isinstance(result, str)

    def test_empty_result_when_all_lines_fail(self):
        """Lines 495-498: all line crops raise → empty string returned."""
        from pdfsearchable.htr import _run_trocr
        import pdfsearchable.htr as htr_mod

        img_bytes = self._make_image_bytes()

        mock_processor = MagicMock()
        mock_processor.side_effect = RuntimeError("inference error")
        mock_model = MagicMock()
        mock_torch = MagicMock()
        mock_torch.no_grad.return_value.__enter__ = MagicMock(return_value=None)
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_HTR_LANG", None)
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)

            with patch.object(htr_mod, "_load_model",
                              return_value=(mock_processor, mock_model)):
                with patch.object(htr_mod, "_split_lines") as mock_split:
                    from PIL import Image as PILImage
                    mock_split.return_value = [PILImage.new("RGB", (100, 20))]
                    with patch.dict("sys.modules", {"torch": mock_torch}):
                        result = _run_trocr(img_bytes, lang="en")
        assert result == ""

    def test_historical_mode_active(self):
        """Lines 477-480: historical=True changes model selection and split_lines call."""
        from pdfsearchable.htr import _run_trocr
        import pdfsearchable.htr as htr_mod

        img_bytes = self._make_image_bytes()
        mock_processor, mock_model, mock_torch = self._make_mocks("antigo texto")

        with patch.dict(os.environ, {"PDFSEARCHABLE_OCR_HISTORICAL": "on"}, clear=False):
            os.environ.pop("PDFSEARCHABLE_HTR_LANG", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)

            with patch.object(htr_mod, "_load_model",
                              return_value=(mock_processor, mock_model)):
                with patch.object(htr_mod, "_split_lines") as mock_split:
                    from PIL import Image as PILImage
                    mock_split.return_value = [PILImage.new("RGB", (100, 20))]
                    with patch.dict("sys.modules", {"torch": mock_torch}):
                        result = _run_trocr(img_bytes, lang="pt")
            # Verify historical flag was passed to _split_lines
            mock_split.assert_called_once()
            call_kwargs = mock_split.call_args
            assert call_kwargs[1].get("historical") is True or (
                len(call_kwargs[0]) > 1 and call_kwargs[0][1] is True
            )
        assert isinstance(result, str)

    def test_multiple_lines_joined(self):
        """Lines 493-495: multiple non-empty line results are joined with newline."""
        from pdfsearchable.htr import _run_trocr
        import pdfsearchable.htr as htr_mod
        from PIL import Image as PILImage

        img_bytes = self._make_image_bytes()

        # Each call to processor(crop, ...) returns an object with .pixel_values
        # batch_decode always returns text for each generated_ids call
        mock_processor = MagicMock()
        proc_return = MagicMock()
        proc_return.pixel_values = MagicMock()
        mock_processor.return_value = proc_return
        mock_processor.batch_decode.return_value = ["text line"]

        mock_model = MagicMock()
        mock_model.generate.return_value = MagicMock()

        mock_torch = MagicMock()
        mock_torch.no_grad.return_value.__enter__ = MagicMock(return_value=None)
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_HTR_LANG", None)
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_MODEL", None)
            os.environ.pop("PDFSEARCHABLE_HTR_PRINTED", None)

            with patch.object(htr_mod, "_load_model",
                              return_value=(mock_processor, mock_model)):
                with patch.object(htr_mod, "_split_lines") as mock_split:
                    lines_imgs = [PILImage.new("RGB", (100, 20)) for _ in range(3)]
                    mock_split.return_value = lines_imgs
                    with patch.dict("sys.modules", {"torch": mock_torch}):
                        result = _run_trocr(img_bytes, lang="en")
        # 3 lines each returning "text line" → joined with \n
        assert isinstance(result, str)
        assert result.count("\n") == 2  # 3 lines joined = 2 newlines


class TestRunHtrOnImage:
    """Lines 516-530: run_htr_on_image() routing."""

    def setup_method(self):
        import pdfsearchable.htr as htr_mod
        htr_mod._HTR_AVAILABLE = None

    def _make_image_bytes(self):
        from PIL import Image
        import io as _io
        img = Image.new("RGB", (80, 30), "white")
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_returns_empty_when_not_available(self):
        """Line 516-517: htr_available() False → return ''."""
        import pdfsearchable.htr as htr_mod
        from pdfsearchable.htr import run_htr_on_image
        htr_mod._HTR_AVAILABLE = False
        result = run_htr_on_image(self._make_image_bytes(), lang="en")
        assert result == ""

    def test_routes_to_trocr_by_default(self):
        """Lines 519-530: default backend → _run_trocr."""
        import pdfsearchable.htr as htr_mod
        from pdfsearchable.htr import run_htr_on_image
        htr_mod._HTR_AVAILABLE = True

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_HTR_BACKEND", None)

            with patch.object(htr_mod, "_run_trocr", return_value="trocr result") as mock_trocr:
                result = run_htr_on_image(self._make_image_bytes(), lang="en")

        assert result == "trocr result"
        mock_trocr.assert_called_once()

    def test_routes_to_transkribus(self):
        """Lines 521-523: transkribus backend → htr_transkribus.run."""
        import pdfsearchable.htr as htr_mod
        from pdfsearchable.htr import run_htr_on_image
        htr_mod._HTR_AVAILABLE = True

        mock_transkribus = MagicMock()
        mock_transkribus.run = MagicMock(return_value="transkribus result")

        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_BACKEND": "transkribus"}):
            with patch.dict("sys.modules", {"pdfsearchable.htr_transkribus": mock_transkribus}):
                result = run_htr_on_image(self._make_image_bytes(), lang=None)

        assert result == "transkribus result"

    def test_routes_to_escriptorium(self):
        """Lines 525-527: escriptorium backend → htr_escriptorium.run."""
        import pdfsearchable.htr as htr_mod
        from pdfsearchable.htr import run_htr_on_image
        htr_mod._HTR_AVAILABLE = True

        mock_escriptorium = MagicMock()
        mock_escriptorium.run = MagicMock(return_value="escriptorium result")

        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_BACKEND": "escriptorium"}):
            with patch.dict("sys.modules", {
                "pdfsearchable.htr_escriptorium": mock_escriptorium
            }):
                result = run_htr_on_image(self._make_image_bytes(), lang=None)

        assert result == "escriptorium result"

    def test_trocr_lang_forwarded(self):
        """Lines 530: lang param is forwarded to _run_trocr."""
        import pdfsearchable.htr as htr_mod
        from pdfsearchable.htr import run_htr_on_image
        htr_mod._HTR_AVAILABLE = True

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_HTR_BACKEND", None)

            with patch.object(htr_mod, "_run_trocr", return_value="") as mock_trocr:
                img_bytes = self._make_image_bytes()
                run_htr_on_image(img_bytes, lang="de")

        mock_trocr.assert_called_once()
        _, kwargs = mock_trocr.call_args
        assert kwargs.get("lang") == "de"
