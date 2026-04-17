"""Testes do módulo search."""

from pdfsearchable.search import (
    CNPJ_PATTERN,
    CPF_PATTERN,
    EMAIL_PATTERN,
    IPV4_PATTERN,
    search_term_in_text,
    search_with_masks,
)


class TestMasks:
    def test_cpf_formatado(self):
        assert CPF_PATTERN.search("CPF 123.456.789-00")
        assert CPF_PATTERN.search("12345678900")

    def test_email(self):
        assert EMAIL_PATTERN.search("contato@empresa.com.br")

    def test_ipv4(self):
        assert IPV4_PATTERN.search("192.168.1.1")

    def test_cnpj(self):
        assert CNPJ_PATTERN.search("12.345.678/0001-90")


class TestSearchTermInText:
    def test_busca_texto_simples(self):
        hits = search_term_in_text("contrato", "documento sobre contrato de trabalho")
        assert len(hits) >= 1

    def test_case_insensitive(self):
        hits = search_term_in_text("Contrato", "CONTRATO de prestação")
        assert len(hits) >= 1


class TestSearchWithMasks:
    def test_busca_termo_literal(self):
        hits = list(search_with_masks("contrato", "documento sobre contrato"))
        assert len(hits) >= 1
        assert hits[0][0] == "term"

    def test_busca_cpf_com_mascara(self):
        hits = list(search_with_masks("cpf", "CPF 123.456.789-00 do titular"))
        assert len(hits) >= 1
