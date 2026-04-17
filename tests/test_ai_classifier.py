"""Testes unitários: classificador de tipo de documento (heurísticas e interface)."""

import pytest

from pdfsearchable.ai_classifier import (
    ClassificationResult,
    classify_document,
    _classify_by_heuristics,
    _get_ai_mode,
    KNOWN_TYPES,
)


@pytest.mark.unit
@pytest.mark.white_box
@pytest.mark.functional
@pytest.mark.regression
def test_classification_result() -> None:
    r = ClassificationResult("contrato", "heuristics")
    assert r.label == "contrato"
    assert r.source == "heuristics"


@pytest.mark.unit
@pytest.mark.white_box
@pytest.mark.functional
def test_heuristics_contrato() -> None:
    text = "CONTRATO DE PRESTAÇÃO DE SERVIÇOS. Entre as partes... cláusula 1. Obrigações."
    label, _ = _classify_by_heuristics(text)
    assert label == "contrato"


@pytest.mark.unit
@pytest.mark.functional
def test_heuristics_nota_fiscal() -> None:
    text = "NOTA FISCAL ELETRÔNICA NF-e. CFOP 5102. ICMS. Valor total 1.500,00."
    label, _ = _classify_by_heuristics(text)
    assert label == "nota_fiscal"


@pytest.mark.unit
def test_heuristics_weight_by_position() -> None:
    # Título no início deve pesar mais
    text = "RELATÓRIO ANUAL. Outro texto genérico sem palavras-chave no resto."
    label, _ = _classify_by_heuristics(text)
    assert label == "relatório"


@pytest.mark.unit
def test_heuristics_fallback_email() -> None:
    # Sem palavras de outras categorias (ex.: "reunião" acionaria "ata")
    text = "Assunto: Proposta comercial. From: user@mail.com. To: cliente@empresa.com."
    label, _ = _classify_by_heuristics(text)
    assert label == "e-mail"


@pytest.mark.unit
def test_heuristics_fallback_documento() -> None:
    text = "Texto qualquer sem palavras-chave específicas."
    label, _ = _classify_by_heuristics(text)
    assert label == "documento"


@pytest.mark.unit
def test_heuristics_metadata_hint() -> None:
    # Metadados do PDF (title/subject/keywords) reforçam o tipo (ID4)
    text = "Texto genérico sem palavras-chave fortes."
    meta = {"title": "Contrato de prestação de serviços", "subject": "contrato"}
    label, _ = _classify_by_heuristics(text, metadata_hint=meta)
    assert label == "contrato"


@pytest.mark.unit
def test_classify_document_with_path_hint() -> None:
    # Nome do arquivo deve dar hint
    result = classify_document(
        "Texto genérico.", path=__import__("pathlib").Path("contrato_xyz.pdf")
    )
    assert result.label == "contrato"
    assert result.source == "heuristics"


@pytest.mark.unit
@pytest.mark.smoke
def test_known_types_non_empty() -> None:
    assert len(KNOWN_TYPES) >= 10
    assert "documento" in KNOWN_TYPES
    assert "contrato" in KNOWN_TYPES


@pytest.mark.unit
def test_ai_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDFSEARCHABLE_AI", "heuristics")
    assert _get_ai_mode() == "heuristics"
    monkeypatch.setenv("PDFSEARCHABLE_AI", "openai")
    assert _get_ai_mode() == "openai"
    monkeypatch.setenv("PDFSEARCHABLE_AI", "ollama")
    assert _get_ai_mode() == "ollama"
    monkeypatch.setenv("PDFSEARCHABLE_AI", "auto")
    assert _get_ai_mode() == "auto"
    monkeypatch.delenv("PDFSEARCHABLE_AI", raising=False)
    assert _get_ai_mode() == "auto"
