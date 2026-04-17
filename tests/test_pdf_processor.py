"""Testes do módulo pdf_processor."""

from pdfsearchable.pdf_processor import (
    content_hash,
    file_size,
    format_pdf_date,
    normalize_text,
    validate_pdf,
)


class TestFormatPdfDate:
    def test_formato_completo(self):
        assert format_pdf_date("D:20240315120000+00'00'") == "15/03/2024 12:00"

    def test_formato_apenas_data(self):
        assert format_pdf_date("D:20240315") == "15/03/2024"

    def test_none_vazio(self):
        assert format_pdf_date(None) is None
        assert format_pdf_date("") is None

    def test_invalido(self):
        assert format_pdf_date("invalid") is None


class TestNormalizeText:
    def test_espacos_multiplos(self):
        assert normalize_text("a   b   c") == "a b c"

    def test_hifen_unicode(self):
        assert normalize_text("a\u2013b\u2014c") == "a-b-c"

    def test_quebras_excessivas(self):
        assert normalize_text("a\n\n\n\nb") == "a\n\nb"

    def test_vazio(self):
        assert normalize_text("") == ""
        assert normalize_text(None) == ""


class TestContentHash:
    def test_consistente(self, tmp_path):
        f = tmp_path / "arquivo.txt"
        f.write_text("conteudo")
        h1 = content_hash(f)
        h2 = content_hash(f)
        assert h1 == h2
        assert len(h1) == 32

    def test_diferente_por_conteudo(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("a")
        f2.write_text("b")
        assert content_hash(f1) != content_hash(f2)


class TestFileSize:
    def test_tamanho(self, tmp_path):
        f = tmp_path / "arquivo.txt"
        f.write_text("12345")
        assert file_size(f) == 5


class TestValidatePdf:
    def test_nao_existe(self, tmp_path):
        ok, msg = validate_pdf(tmp_path / "inexistente.pdf")
        assert not ok
        assert "não encontrado" in msg.lower() or "arquivo" in msg.lower()

    def test_nao_pdf(self, tmp_path):
        f = tmp_path / "arquivo.txt"
        f.write_text("texto")
        ok, msg = validate_pdf(f)
        assert not ok
        assert "pdf" in msg.lower()
