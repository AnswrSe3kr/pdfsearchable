"""Testes do módulo content_extractors."""

from pdfsearchable.content_extractors import (
    extract_entities,
    extract_monetary_values,
    extract_parties,
    extract_tags,
)


class TestExtractTags:
    def test_por_doc_type(self):
        tags = extract_tags("contrato", "texto sobre contrato de prestação de serviços")
        assert "contrato" in tags
        assert "serviço" in tags

    def test_documento_generico_excluido(self):
        tags = extract_tags("documento", "texto qualquer")
        assert "documento" not in tags


class TestExtractParties:
    def test_parte_a_b(self, sample_text):
        parties = extract_parties(sample_text)
        assert any("João" in p for p in parties) or len(parties) > 0

    def test_outorgante(self):
        text = "Outorgante: Maria Santos, CPF 111.222.333-44"
        parties = extract_parties(text)
        assert len(parties) >= 1


class TestExtractMonetaryValues:
    def test_brl(self):
        vals = extract_monetary_values("Valor R$ 1.500,00 e R$ 2.000")
        assert len(vals) >= 1
        assert any(v["currency"] == "BRL" for v in vals)

    def test_usd(self):
        vals = extract_monetary_values("Total $ 100.00 USD")
        assert len(vals) >= 1
        assert any(v["currency"] == "USD" for v in vals)


class TestExtractEntities:
    def test_emails(self, sample_text):
        ent = extract_entities(sample_text)
        assert "joao@email.com" in ent["emails"] or "suporte@empresa.com.br" in ent["emails"]

    def test_cpf(self, sample_text):
        ent = extract_entities(sample_text)
        assert any("111.444.777" in c or "11144477735" in c for c in ent["cpfs"])

    def test_cnpj(self, sample_text):
        ent = extract_entities(sample_text)
        assert len(ent["cnpjs"]) >= 1
