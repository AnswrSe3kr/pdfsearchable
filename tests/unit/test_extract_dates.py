"""
Testes unitários para extract_dates (content_extractors).
Cobre os três formatos suportados: DD/MM/AAAA, ISO e extenso PT/EN.
"""

import pytest

from pdfsearchable.content_extractors import extract_dates


# ── DD/MM/AAAA e variantes ──────────────────────────────────────────────────


@pytest.mark.unit
def test_date_dmy_slash() -> None:
    result = extract_dates("Contrato assinado em 20/03/2024.")
    assert "2024-03-20" in result


@pytest.mark.unit
def test_date_dmy_dash() -> None:
    result = extract_dates("Vencimento: 01-12-2023.")
    assert "2023-12-01" in result


@pytest.mark.unit
def test_date_dmy_dot() -> None:
    result = extract_dates("Emitido em 15.06.2022.")
    assert "2022-06-15" in result


@pytest.mark.unit
def test_date_dmy_single_digit_day_month() -> None:
    result = extract_dates("Data: 5/7/2021.")
    assert "2021-07-05" in result


# ── ISO 8601 ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_date_iso() -> None:
    result = extract_dates("Data limite: 1999-12-31.")
    assert "1999-12-31" in result


@pytest.mark.unit
def test_date_iso_mixed_with_dmy() -> None:
    result = extract_dates("Início: 15/06/2020 e fim: 2021-12-31.")
    assert "2020-06-15" in result
    assert "2021-12-31" in result


# ── Extenso PT ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_date_extenso_pt_de() -> None:
    result = extract_dates("20 de março de 2023 foi aprovado.")
    assert "2023-03-20" in result


@pytest.mark.unit
def test_date_extenso_pt_sem_de() -> None:
    result = extract_dates("Reunião de 5 janeiro 2022.")
    assert "2022-01-05" in result


@pytest.mark.unit
def test_date_extenso_pt_todos_meses() -> None:
    meses = [
        ("janeiro", "01"),
        ("fevereiro", "02"),
        ("março", "03"),
        ("abril", "04"),
        ("maio", "05"),
        ("junho", "06"),
        ("julho", "07"),
        ("agosto", "08"),
        ("setembro", "09"),
        ("outubro", "10"),
        ("novembro", "11"),
        ("dezembro", "12"),
    ]
    for nome, num in meses:
        result = extract_dates(f"10 de {nome} de 2020")
        assert f"2020-{num}-10" in result, f"Falhou para mês: {nome}"


# ── Extenso EN ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_date_extenso_en() -> None:
    result = extract_dates("On 15 January 2022 the agreement was signed.")
    assert "2022-01-15" in result


@pytest.mark.unit
def test_date_extenso_en_march() -> None:
    result = extract_dates("Filed on 3 March 2019.")
    assert "2019-03-03" in result


# ── Deduplicação e limites ───────────────────────────────────────────────────


@pytest.mark.unit
def test_deduplicacao() -> None:
    """A mesma data em formatos diferentes não deve aparecer duas vezes."""
    result = extract_dates("20/03/2024 e 20 de março de 2024 e 2024-03-20.")
    assert result.count("2024-03-20") == 1


@pytest.mark.unit
def test_max_dates_limit() -> None:
    """O limite max_dates deve ser respeitado."""
    text = " ".join(f"01/01/{2000 + i}" for i in range(50))
    result = extract_dates(text, max_dates=10)
    assert len(result) == 10


@pytest.mark.unit
def test_empty_text_returns_empty() -> None:
    assert extract_dates("") == []
    assert extract_dates("   ") == []
    assert extract_dates(None) == []  # type: ignore[arg-type]


# ── Validação de datas absurdas ──────────────────────────────────────────────


@pytest.mark.unit
def test_rejects_impossible_month() -> None:
    """Mês 13 não deve ser aceite."""
    result = extract_dates("Data: 01/13/2023.")
    assert not result


@pytest.mark.unit
def test_rejects_year_out_of_range() -> None:
    """Anos antes de 1800 ou depois de 2100 devem ser rejeitados."""
    result = extract_dates("No ano 1200-01-01 e 2200-12-31.")
    assert not result


@pytest.mark.unit
def test_rejects_impossible_day() -> None:
    """Dia 32 não deve ser aceite."""
    result = extract_dates("Data: 32/01/2023.")
    assert not result


# ── Output normalizado ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_output_normalized_format() -> None:
    """Todas as datas devem estar no formato AAAA-MM-DD."""
    result = extract_dates("15/6/2020, 2021-07-01, 3 de agosto de 2022")
    for d in result:
        parts = d.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 4  # AAAA
        assert len(parts[1]) == 2  # MM
        assert len(parts[2]) == 2  # DD


@pytest.mark.unit
def test_multiple_dates_in_contract() -> None:
    """Simula texto de contrato com várias datas."""
    text = (
        "Contrato celebrado em 01/03/2023, com prazo até 2024-02-29, "
        "renovável em 1 de março de 2025."
    )
    result = extract_dates(text)
    assert "2023-03-01" in result
    # 2024-02-29 é bissexto — não é validado por extract_dates (apenas valida dia 1-31)
    assert "2024-02-29" in result
    assert "2025-03-01" in result
