"""Testes unitários para exceptions.py — hierarquia de exceções."""
import logging
import pytest

from pdfsearchable.exceptions import (
    PdfSearchableError,
    ValidationError,
    IndexingError,
    StoreError,
    ReportError,
    OcrError,
    ConfigError,
)


class TestHierarchy:
    def test_all_inherit_base(self):
        for cls in (ValidationError, IndexingError, StoreError, ReportError, OcrError, ConfigError):
            assert issubclass(cls, PdfSearchableError)
            assert issubclass(cls, Exception)

    def test_catch_base(self):
        with pytest.raises(PdfSearchableError):
            raise ValidationError("teste")

    def test_catch_specific(self):
        with pytest.raises(StoreError):
            raise StoreError("index corrompido")


class TestAttributes:
    def test_message(self):
        e = ValidationError("ficheiro não encontrado")
        assert e.message == "ficheiro não encontrado"
        assert str(e) == "ficheiro não encontrado"

    def test_details_default_empty(self):
        e = IndexingError("falha")
        assert e.details == {}

    def test_details_custom(self):
        e = StoreError("erro", details={"path": "/tmp/x"})
        assert e.details["path"] == "/tmp/x"

    def test_code_auto(self):
        e = ValidationError("msg")
        assert e.code == "validation_error"

    def test_code_custom(self):
        e = OcrError("msg", code="timeout")
        assert e.code == "timeout"

    def test_repr(self):
        e = ReportError("falha template", code="template_error")
        r = repr(e)
        assert "ReportError" in r
        assert "template_error" in r


class TestLog:
    def test_log_error(self, caplog):
        e = StoreError("índice corrompido", details={"file": "x.json"})
        logger = logging.getLogger("test_exc")
        with caplog.at_level(logging.ERROR, logger="test_exc"):
            e.log(logger, level=logging.ERROR)
        assert "índice corrompido" in caplog.text

    def test_log_warning(self, caplog):
        e = ConfigError("valor inválido")
        logger = logging.getLogger("test_exc_warn")
        with caplog.at_level(logging.WARNING, logger="test_exc_warn"):
            e.log(logger, level=logging.WARNING)
        assert "valor inválido" in caplog.text


class TestChaining:
    def test_from_cause(self):
        try:
            try:
                raise ValueError("json decode")
            except ValueError as cause:
                raise StoreError("índice corrompido") from cause
        except StoreError as e:
            assert e.__cause__ is not None
            assert isinstance(e.__cause__, ValueError)
