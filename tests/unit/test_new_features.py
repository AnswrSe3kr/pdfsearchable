"""
Testes unitários para as 5 novas funcionalidades:
1. Validação CPF/CNPJ (_validate_cpf, _validate_cnpj)
2. Detecção multicolunas (_detect_columns) — mock de página
3. Extração parcial de PDF corrompido (extract_text_from_pdf_partial)
4. Função _is_blank_page no OCR
5. Anotações enriquecidas (_extract_annotations_from_page)
"""

import pytest


# ---------------------------------------------------------------------------
# 1. Validação CPF
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateCpf:
    def test_cpf_valido(self) -> None:
        from pdfsearchable.content_extractors import _validate_cpf

        # CPF válido: 111.444.777-35
        assert _validate_cpf("111.444.777-35") is True
        assert _validate_cpf("11144477735") is True

    def test_cpf_invalido_digitos_errados(self) -> None:
        from pdfsearchable.content_extractors import _validate_cpf

        assert _validate_cpf("123.456.789-00") is False
        assert _validate_cpf("111.111.111-11") is False  # todos iguais

    def test_cpf_todos_iguais_rejeitado(self) -> None:
        from pdfsearchable.content_extractors import _validate_cpf

        for d in "0123456789":
            assert _validate_cpf(d * 11) is False

    def test_cpf_formato_errado(self) -> None:
        from pdfsearchable.content_extractors import _validate_cpf

        assert _validate_cpf("") is False
        assert _validate_cpf("123") is False
        assert _validate_cpf("abcdefghijk") is False


# ---------------------------------------------------------------------------
# 2. Validação CNPJ
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateCnpj:
    def test_cnpj_valido(self) -> None:
        from pdfsearchable.content_extractors import _validate_cnpj

        # CNPJ válido: 11.222.333/0001-81
        assert _validate_cnpj("11.222.333/0001-81") is True
        assert _validate_cnpj("11222333000181") is True

    def test_cnpj_invalido_digitos_errados(self) -> None:
        from pdfsearchable.content_extractors import _validate_cnpj

        assert _validate_cnpj("12.345.678/0001-90") is False

    def test_cnpj_todos_iguais_rejeitado(self) -> None:
        from pdfsearchable.content_extractors import _validate_cnpj

        assert _validate_cnpj("00000000000000") is False
        assert _validate_cnpj("11111111111111") is False

    def test_cnpj_alfanumerico_aceito(self) -> None:
        from pdfsearchable.content_extractors import _validate_cnpj

        # CNPJ alfanumérico (2026+) — sem algoritmo público definido → aceitar
        assert _validate_cnpj("AB.CDE.FGH/0001-90") is True


# ---------------------------------------------------------------------------
# 3. Regex BRL exige dígito após R$
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegexBrl:
    def test_brl_com_valor_extraido(self) -> None:
        from pdfsearchable.content_extractors import extract_monetary_values

        text = "Valor: R$ 1.500,00"
        out = extract_monetary_values(text)
        assert any(v["currency"] == "BRL" for v in out)

    def test_brl_sem_valor_nao_extraido(self) -> None:
        from pdfsearchable.content_extractors import extract_monetary_values

        # "R$ " sem número não deve ser capturado
        text = "Símbolo R$ sozinho"
        out = extract_monetary_values(text)
        # Se capturar, o value_str não pode ser só "R$ "
        for v in out:
            assert v["currency"] != "BRL" or any(c.isdigit() for c in (v.get("value_str") or ""))


# ---------------------------------------------------------------------------
# 4. Detecção de página em branco (OCR)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBlankPageDetection:
    def test_imagem_branca_detectada(self) -> None:
        """Imagem 100% branca deve ser detectada como blank."""
        pytest.importorskip("PIL")
        pytest.importorskip("numpy")
        from PIL import Image
        from pdfsearchable.ocr import _is_blank_page

        img = Image.new("L", (100, 100), color=255)  # branco puro
        assert _is_blank_page(img) is True

    def test_imagem_com_texto_nao_detectada(self) -> None:
        """Imagem com pixels escuros não deve ser blank."""
        pytest.importorskip("PIL")
        pytest.importorskip("numpy")
        from PIL import Image, ImageDraw
        from pdfsearchable.ocr import _is_blank_page

        img = Image.new("L", (100, 100), color=255)
        draw = ImageDraw.Draw(img)
        draw.rectangle([10, 10, 90, 90], fill=0)  # bloco preto = texto
        assert _is_blank_page(img) is False

    def test_sem_numpy_retorna_false(self, monkeypatch) -> None:
        """Sem numpy, _is_blank_page deve retornar False (comportamento seguro)."""
        import pdfsearchable.ocr as ocr_mod

        monkeypatch.setattr(ocr_mod, "_numpy_available", lambda: False)
        pytest.importorskip("PIL")
        from PIL import Image

        img = Image.new("L", (10, 10), color=255)
        assert ocr_mod._is_blank_page(img) is False


# ---------------------------------------------------------------------------
# 5. Detecção de multicolunas (pdf_processor)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDetectColumns:
    def _make_mock_page(self, blocks: list[tuple]):
        """Cria um mock simples de fitz.Page para testes de _detect_columns."""

        class MockRect:
            width = 595.0  # A4

        class MockPage:
            rect = MockRect()

            def __init__(self, block_data):
                self._blocks = block_data

            def get_text(self, mode="text"):
                if mode == "blocks":
                    return self._blocks
                return ""

        return MockPage(blocks)

    def test_uma_coluna(self) -> None:
        from pdfsearchable.pdf_processor import _detect_columns

        # Todos os blocos centrados em ~298 (centro da página A4)
        blocks = [
            (200, 10, 400, 30, "texto um", 0, 0),
            (200, 40, 400, 60, "texto dois", 0, 0),
            (200, 70, 400, 90, "texto três", 0, 0),
        ]
        page = self._make_mock_page(blocks)
        assert _detect_columns(page) == 1

    def test_duas_colunas(self) -> None:
        from pdfsearchable.pdf_processor import _detect_columns

        # Coluna esquerda ~x=75–275, coluna direita ~x=320–520
        blocks = [
            (75, 10, 275, 30, "col esquerda 1", 0, 0),
            (75, 40, 275, 60, "col esquerda 2", 0, 0),
            (75, 70, 275, 90, "col esquerda 3", 0, 0),
            (320, 10, 520, 30, "col direita 1", 0, 0),
            (320, 40, 520, 60, "col direita 2", 0, 0),
            (320, 70, 520, 90, "col direita 3", 0, 0),
        ]
        page = self._make_mock_page(blocks)
        assert _detect_columns(page) >= 2


# ---------------------------------------------------------------------------
# 6. extract_text_from_pdf_partial — assinatura e contrato
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractPdfPartial:
    def test_assinatura_retorna_5_valores(self, tmp_path) -> None:
        """extract_text_from_pdf_partial deve retornar uma 5-tupla."""
        pytest.importorskip("fitz")
        import fitz
        from pdfsearchable.pdf_processor import extract_text_from_pdf_partial

        # Criar PDF mínimo válido
        pdf_path = tmp_path / "test.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), "Texto de teste para recuperação parcial.")
        doc.save(str(pdf_path))
        doc.close()

        result = extract_text_from_pdf_partial(pdf_path)
        assert len(result) == 5
        full_text, num_pages, page_texts, metadata, failed_pages = result
        assert isinstance(full_text, str)
        assert isinstance(num_pages, int)
        assert isinstance(page_texts, list)
        assert isinstance(metadata, dict)
        assert isinstance(failed_pages, list)
        assert num_pages == 1
        assert failed_pages == []
        assert "Texto de teste" in full_text


# ---------------------------------------------------------------------------
# 7. extract_embedded_pdfs — PDF sem embutidos retorna lista vazia
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractEmbeddedPdfs:
    def test_pdf_sem_embutidos(self, tmp_path) -> None:
        """PDF normal sem anexos deve retornar lista vazia."""
        pytest.importorskip("fitz")
        import fitz
        from pdfsearchable.pdf_extended import extract_embedded_pdfs

        pdf_path = tmp_path / "normal.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        assert extract_embedded_pdfs(pdf_path) == []

    def test_is_pdf_portfolio_falso_em_pdf_normal(self, tmp_path) -> None:
        pytest.importorskip("fitz")
        import fitz
        from pdfsearchable.pdf_extended import is_pdf_portfolio

        pdf_path = tmp_path / "simples.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        assert is_pdf_portfolio(pdf_path) is False
