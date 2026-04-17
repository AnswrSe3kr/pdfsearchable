"""Testes unitários: locations (referências a locais, merge com IA)."""

import pytest

from pdfsearchable.locations import (
    get_location_refs,
    merge_location_refs_with_ia,
    _find_location_info,
    _normalize_name_for_match,
)


@pytest.mark.unit
def test_get_location_refs_finds_brasil_and_sao_paulo() -> None:
    docs = [("f1", "Documento sobre Brasil. Escritório em São Paulo.")]
    refs = get_location_refs(docs)
    names = [r["name"] for r in refs]
    assert "Brasil" in names
    assert "São Paulo" in names


@pytest.mark.unit
def test_merge_location_refs_with_ia_adds_ia_only() -> None:
    base = get_location_refs([("f1", "Texto sobre Brasil.")])
    merged = merge_location_refs_with_ia(
        base,
        [("f1", "Texto sobre Brasil e Nordeste.")],
        ["Nordeste"],
    )
    names = [r["name"] for r in merged]
    assert "Brasil" in names
    assert "Nordeste" in names
    nordeste = next(r for r in merged if r["name"] == "Nordeste")
    assert nordeste.get("lat") is not None
    assert nordeste.get("kind") == "região"


@pytest.mark.unit
def test_normalize_name_for_match() -> None:
    assert _normalize_name_for_match("São Paulo") == "são paulo"
    assert _normalize_name_for_match("  Brasil  ") == "brasil"


@pytest.mark.unit
def test_find_location_info() -> None:
    info = _find_location_info("Brasil")
    assert info is not None
    assert info["kind"] == "país"
    assert info.get("lat") is not None
