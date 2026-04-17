"""Testes unitários: content_extractors (valores monetários, entidades, etc.)."""

import pytest

from pdfsearchable.content_extractors import extract_monetary_values, extract_entities


@pytest.mark.unit
def test_monetary_brl_not_identified_as_usd() -> None:
    """R$ 12.522,50 no texto deve ser apenas BRL, nunca USD (remuneração em Real)."""
    text = "REMUNERAÇÃO: R$ 12.522,50. Outros valores R$ 250,00."
    out = extract_monetary_values(text)
    usd_with_same_number = [
        x for x in out if x["currency"] == "USD" and "12.522,50" in (x.get("value_str") or "")
    ]
    assert len(usd_with_same_number) == 0
    brl_vals = [x for x in out if x["currency"] == "BRL"]
    assert any("12.522,50" in (x.get("value_str") or "") for x in brl_vals)


@pytest.mark.unit
def test_monetary_brl_and_usd_distinct() -> None:
    """Texto com R$ e com USD deve retornar BRL e USD nos trechos corretos."""
    text = "Salário R$ 5.000,00. Pagamento USD 100,00."
    out = extract_monetary_values(text)
    brl = [x for x in out if x["currency"] == "BRL"]
    usd = [x for x in out if x["currency"] == "USD"]
    assert any("5.000" in (x.get("value_str") or "") for x in brl)
    assert any("100" in (x.get("value_str") or "") for x in usd)


@pytest.mark.unit
def test_extract_entities() -> None:
    """
    E-mails, CPFs, CNPJs e IPs são extraídos para exibição no document-view.
    CPF 111.444.777-35 e CNPJ 11.222.333/0001-81 têm dígitos verificadores válidos
    (validação implementada para reduzir falsos positivos).
    """
    # CPF 111.444.777-35: válido (dígitos verificadores 3 e 5 corretos)
    # CNPJ 11.222.333/0001-81: válido (dígitos verificadores 8 e 1 corretos)
    text = (
        "Contato: joao@empresa.com.br. "
        "CPF 111.444.777-35. "
        "CNPJ 11.222.333/0001-81. "
        "Servidor 192.168.1.1."
    )
    out = extract_entities(text)
    assert out["emails"] == ["joao@empresa.com.br"]
    assert "111.444.777-35" in out["cpfs"]
    assert "11.222.333/0001-81" in out["cnpjs"]
    assert "192.168.1.1" in out["ips"]


@pytest.mark.unit
def test_extract_entities_invalid_cpf_cnpj_rejected() -> None:
    """CPF e CNPJ com dígitos verificadores inválidos devem ser rejeitados (evitar falsos positivos)."""
    # 123.456.789-00: CPF com dígitos verificadores errados → deve ser rejeitado
    # 12.345.678/0001-90: CNPJ com dígitos verificadores errados → deve ser rejeitado
    text = "CPF 123.456.789-00. CNPJ 12.345.678/0001-90."
    out = extract_entities(text)
    assert "123.456.789-00" not in out["cpfs"]
    assert "12.345.678/0001-90" not in out["cnpjs"]
