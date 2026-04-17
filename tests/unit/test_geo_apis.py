"""Testes unitários para geo_apis.py — ViaCEP, IP-API, extração de CEPs."""

import os
import json
import pytest
from unittest.mock import patch, MagicMock

from pdfsearchable.geo_apis import (
    extract_ceps_from_text,
    fetch_via_cep,
    fetch_ip_api,
    is_via_cep_enabled,
    is_ip_api_enabled,
    _is_safe_http_url,
    _urlopen_json,
    CEP_PATTERN,
)


class TestExtractCeps:
    def test_basic(self):
        text = "CEP: 01001-000 e 70040-020"
        ceps = extract_ceps_from_text(text)
        assert "01001000" in ceps
        assert "70040020" in ceps

    def test_without_hyphen(self):
        text = "cep 01001000"
        ceps = extract_ceps_from_text(text)
        assert "01001000" in ceps

    def test_dedup(self):
        text = "01001-000 01001000 01001-000"
        ceps = extract_ceps_from_text(text)
        assert len(ceps) == 1

    def test_empty(self):
        assert extract_ceps_from_text("") == []
        assert extract_ceps_from_text("sem ceps aqui") == []

    def test_no_false_positive_from_long_number(self):
        text = "123456789012"  # 12 digits, not a CEP
        ceps = extract_ceps_from_text(text)
        assert len(ceps) == 0


class TestFetchViaCep:
    def test_invalid_cep_length(self):
        assert fetch_via_cep("123") is None

    def test_valid_cep_mock(self):
        fake_response = {"cep": "01001-000", "localidade": "São Paulo", "uf": "SP"}
        with patch("pdfsearchable.geo_apis._urlopen_json", return_value=fake_response):
            result = fetch_via_cep("01001-000")
            assert result["localidade"] == "São Paulo"

    def test_cep_with_error(self):
        with patch("pdfsearchable.geo_apis._urlopen_json", return_value={"erro": True}):
            assert fetch_via_cep("00000000") is None


class TestFetchIpApi:
    def test_empty_ip(self):
        assert fetch_ip_api("") is None
        assert fetch_ip_api(None) is None

    def test_valid_ip_mock(self):
        fake = {
            "status": "success",
            "country": "Brazil",
            "city": "São Paulo",
            "lat": -23.5,
            "lon": -46.6,
        }
        with patch("pdfsearchable.geo_apis._urlopen_json", return_value=fake):
            result = fetch_ip_api("8.8.8.8")
            assert result["country"] == "Brazil"

    def test_failed_lookup(self):
        with patch("pdfsearchable.geo_apis._urlopen_json", return_value={"status": "fail"}):
            assert fetch_ip_api("999.999.999.999") is None


class TestIsSafeUrl:
    def test_https(self):
        assert _is_safe_http_url("https://example.com") is True

    def test_http(self):
        assert _is_safe_http_url("http://localhost:8080") is True

    def test_ftp(self):
        assert _is_safe_http_url("ftp://server") is False

    def test_empty(self):
        assert _is_safe_http_url("") is False
        assert _is_safe_http_url(None) is False


class TestEnableFlags:
    def test_via_cep_default_enabled(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_VIA_CEP", None)
            assert is_via_cep_enabled() is True

    def test_via_cep_disabled(self):
        with patch.dict(os.environ, {"PDFSEARCHABLE_VIA_CEP": "0"}):
            assert is_via_cep_enabled() is False

    def test_ip_api_default_enabled(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_IP_API", None)
            assert is_ip_api_enabled() is True

    def test_ip_api_disabled(self):
        with patch.dict(os.environ, {"PDFSEARCHABLE_IP_API": "no"}):
            assert is_ip_api_enabled() is False


class TestUrlopenJson:
    def test_unsafe_url(self):
        assert _urlopen_json("ftp://bad") is None

    def test_network_error(self):
        with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
            assert _urlopen_json("http://localhost:99999/bad") is None
