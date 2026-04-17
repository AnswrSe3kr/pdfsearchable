"""Testes unitários para config.py — carregamento de config, validação e aplicação."""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _reset_config():
    """Reset config cache between tests."""
    import pdfsearchable.config as cfg

    cfg._loaded = None
    yield
    cfg._loaded = None


class TestLoadJson:
    def test_load_json_valid(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"PDFSEARCHABLE_AI": "ollama", "port": 9000}))
        with patch("pdfsearchable.config.CONFIG_JSON", cfg_file):
            from pdfsearchable.config import _load_json

            data = _load_json()
            assert data["PDFSEARCHABLE_AI"] == "ollama"
            assert data["port"] == 9000

    def test_load_json_missing(self, tmp_path):
        cfg_file = tmp_path / "missing.json"
        with patch("pdfsearchable.config.CONFIG_JSON", cfg_file):
            from pdfsearchable.config import _load_json

            assert _load_json() is None

    def test_load_json_invalid(self, tmp_path):
        cfg_file = tmp_path / "bad.json"
        cfg_file.write_text("{invalid json")
        with patch("pdfsearchable.config.CONFIG_JSON", cfg_file):
            from pdfsearchable.config import _load_json

            assert _load_json() is None


class TestLoadFromPath:
    def test_json_with_pdfsearchable_section(self, tmp_path):
        cfg_file = tmp_path / "custom.json"
        cfg_file.write_text(json.dumps({"pdfsearchable": {"ai": "ollama", "port": 8080}}))
        from pdfsearchable.config import _load_from_path

        result = _load_from_path(cfg_file)
        assert result.get("PDFSEARCHABLE_AI") == "ollama"
        assert result.get("PDFSEARCHABLE_PORT") == "8080"

    def test_json_flat_keys(self, tmp_path):
        cfg_file = tmp_path / "flat.json"
        cfg_file.write_text(json.dumps({"OCR_DPI": 300}))
        from pdfsearchable.config import _load_from_path

        result = _load_from_path(cfg_file)
        assert "PDFSEARCHABLE_OCR_DPI" in result

    def test_bool_values(self, tmp_path):
        cfg_file = tmp_path / "bools.json"
        cfg_file.write_text(json.dumps({"pdfsearchable": {"ocr_always": True, "htr": False}}))
        from pdfsearchable.config import _load_from_path

        result = _load_from_path(cfg_file)
        assert result.get("PDFSEARCHABLE_OCR_ALWAYS") == "1"
        assert result.get("PDFSEARCHABLE_HTR") == "0"

    def test_unknown_extension(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("key: value")
        from pdfsearchable.config import _load_from_path

        assert _load_from_path(cfg_file) == {}


class TestGetConfigValue:
    def test_env_takes_precedence(self):
        from pdfsearchable.config import get_config_value

        with patch.dict(os.environ, {"MY_KEY": "from_env"}):
            assert get_config_value("MY_KEY", "default") == "from_env"

    def test_fallback_to_default(self):
        from pdfsearchable.config import get_config_value

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NONEXISTENT_KEY_XYZ", None)
            assert get_config_value("NONEXISTENT_KEY_XYZ", "fallback") == "fallback"


class TestGetSearchSynonyms:
    def test_from_env_json(self):
        from pdfsearchable.config import get_search_synonyms

        with patch.dict(os.environ, {"PDFSEARCHABLE_SEARCH_SYNONYMS": '{"cpf": "documento"}'}):
            result = get_search_synonyms()
            assert result == {"cpf": "documento"}

    def test_invalid_env_json(self):
        from pdfsearchable.config import get_search_synonyms

        with patch.dict(os.environ, {"PDFSEARCHABLE_SEARCH_SYNONYMS": "not json"}):
            result = get_search_synonyms()
            assert result == {}

    def test_empty(self):
        from pdfsearchable.config import get_search_synonyms

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_SEARCH_SYNONYMS", None)
            result = get_search_synonyms()
            assert isinstance(result, dict)


class TestApplyConfigToEnv:
    def test_does_not_override_existing(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"PDFSEARCHABLE_AI": "ollama"}))
        import pdfsearchable.config as cfg

        with patch.dict(
            os.environ, {"PDFSEARCHABLE_AI": "off", "PDFSEARCHABLE_CONFIG_FILE": str(cfg_file)}
        ):
            cfg._loaded = None
            cfg.apply_config_to_env()
            assert os.environ["PDFSEARCHABLE_AI"] == "off"  # not overridden


class TestValidateConfigEnv:
    def test_valid_int(self):
        from pdfsearchable.config import validate_config_env

        with patch.dict(os.environ, {"PDFSEARCHABLE_PORT": "8000"}):
            warnings = validate_config_env()
            assert not any("PORT" in w for w in warnings)

    def test_out_of_range(self):
        from pdfsearchable.config import validate_config_env

        with patch.dict(os.environ, {"PDFSEARCHABLE_PORT": "99999"}):
            warnings = validate_config_env()
            assert any("PORT" in w for w in warnings)

    def test_not_a_number(self):
        from pdfsearchable.config import validate_config_env

        with patch.dict(os.environ, {"PDFSEARCHABLE_PORT": "abc"}):
            warnings = validate_config_env()
            assert any("PORT" in w for w in warnings)

    def test_invalid_url(self):
        from pdfsearchable.config import validate_config_env

        with patch.dict(os.environ, {"PDFSEARCHABLE_WEBHOOK_URL": "ftp://bad"}):
            warnings = validate_config_env()
            assert any("WEBHOOK" in w for w in warnings)

    def test_valid_url(self):
        from pdfsearchable.config import validate_config_env

        with patch.dict(os.environ, {"PDFSEARCHABLE_WEBHOOK_URL": "https://example.com/hook"}):
            warnings = validate_config_env()
            assert not any("WEBHOOK" in w for w in warnings)
