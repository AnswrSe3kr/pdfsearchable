"""Testes unitários para htr_transkribus.py e htr_escriptorium.py — verificação de disponibilidade e parsing."""

import os
import pytest
from unittest.mock import patch


class TestTranskribusAvailability:
    def test_not_available_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            from pdfsearchable.htr_transkribus import available

            assert available() is False

    def test_not_available_missing_model(self):
        with patch.dict(
            os.environ,
            {
                "PDFSEARCHABLE_TRANSKRIBUS_USER": "user@test.com",
                "PDFSEARCHABLE_TRANSKRIBUS_PW": "password",
            },
            clear=True,
        ):
            from pdfsearchable.htr_transkribus import available

            assert available() is False

    def test_available_with_all_env(self):
        with patch.dict(
            os.environ,
            {
                "PDFSEARCHABLE_TRANSKRIBUS_USER": "user@test.com",
                "PDFSEARCHABLE_TRANSKRIBUS_PW": "password",
                "PDFSEARCHABLE_TRANSKRIBUS_MODEL_ID": "39995",
            },
        ):
            from pdfsearchable.htr_transkribus import available

            assert available() is True


class TestTranskribusPageXml:
    def test_parse_page_xml_with_namespace(self):
        from pdfsearchable.htr_transkribus import _parse_page_xml

        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <PcGts xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15">
          <Page>
            <TextRegion>
              <TextLine>
                <TextEquiv><Unicode>Primeira linha</Unicode></TextEquiv>
              </TextLine>
              <TextLine>
                <TextEquiv><Unicode>Segunda linha</Unicode></TextEquiv>
              </TextLine>
            </TextRegion>
          </Page>
        </PcGts>"""
        result = _parse_page_xml(xml)
        assert "Primeira linha" in result
        assert "Segunda linha" in result

    def test_parse_page_xml_empty(self):
        from pdfsearchable.htr_transkribus import _parse_page_xml

        xml = b"""<?xml version="1.0"?><PcGts></PcGts>"""
        result = _parse_page_xml(xml)
        assert result == ""

    def test_parse_page_xml_invalid(self):
        from pdfsearchable.htr_transkribus import _parse_page_xml

        result = _parse_page_xml(b"not xml at all")
        assert result == ""


class TestTranskribusConfig:
    def test_model_id_valid(self):
        from pdfsearchable.htr_transkribus import _model_id

        with patch.dict(os.environ, {"PDFSEARCHABLE_TRANSKRIBUS_MODEL_ID": "39995"}):
            assert _model_id() == 39995

    def test_model_id_empty(self):
        from pdfsearchable.htr_transkribus import _model_id

        with patch.dict(os.environ, {"PDFSEARCHABLE_TRANSKRIBUS_MODEL_ID": ""}):
            assert _model_id() is None

    def test_model_id_invalid(self):
        from pdfsearchable.htr_transkribus import _model_id

        with patch.dict(os.environ, {"PDFSEARCHABLE_TRANSKRIBUS_MODEL_ID": "abc"}):
            assert _model_id() is None

    def test_cleanup_enabled_default(self):
        from pdfsearchable.htr_transkribus import _cleanup_enabled

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_TRANSKRIBUS_CLEANUP", None)
            assert _cleanup_enabled() is True

    def test_cleanup_disabled(self):
        from pdfsearchable.htr_transkribus import _cleanup_enabled

        with patch.dict(os.environ, {"PDFSEARCHABLE_TRANSKRIBUS_CLEANUP": "0"}):
            assert _cleanup_enabled() is False


class TestEscriptoriumAvailability:
    def test_not_available_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            from pdfsearchable.htr_escriptorium import available

            assert available() is False

    def test_available_with_all_env(self):
        with patch.dict(
            os.environ,
            {
                "PDFSEARCHABLE_ESCRIPTORIUM_URL": "https://escriptorium.example.org",
                "PDFSEARCHABLE_ESCRIPTORIUM_TOKEN": "abc123",
                "PDFSEARCHABLE_ESCRIPTORIUM_MODEL": "42",
            },
        ):
            from pdfsearchable.htr_escriptorium import available

            assert available() is True

    def test_not_available_missing_token(self):
        with patch.dict(
            os.environ,
            {
                "PDFSEARCHABLE_ESCRIPTORIUM_URL": "https://example.org",
                "PDFSEARCHABLE_ESCRIPTORIUM_MODEL": "42",
            },
            clear=True,
        ):
            from pdfsearchable.htr_escriptorium import available

            assert available() is False


class TestEscriptoriumConfig:
    def test_cleanup_enabled_default(self):
        from pdfsearchable.htr_escriptorium import _cleanup_enabled

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_ESCRIPTORIUM_CLEANUP", None)
            assert _cleanup_enabled() is True

    def test_cleanup_disabled(self):
        from pdfsearchable.htr_escriptorium import _cleanup_enabled

        with patch.dict(os.environ, {"PDFSEARCHABLE_ESCRIPTORIUM_CLEANUP": "false"}):
            assert _cleanup_enabled() is False

    def test_htr_timeout_default(self):
        from pdfsearchable.htr_escriptorium import _htr_timeout

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_HTR_TIMEOUT", None)
            assert _htr_timeout() == 120

    def test_htr_timeout_custom(self):
        from pdfsearchable.htr_escriptorium import _htr_timeout

        with patch.dict(os.environ, {"PDFSEARCHABLE_HTR_TIMEOUT": "60"}):
            assert _htr_timeout() == 60
