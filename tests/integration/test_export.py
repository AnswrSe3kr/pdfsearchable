"""
Testes de integração: módulo export.py.
Verifica que cada formato (json, jsonl, csv, markdown) produz output correcto.
"""

import csv
import json

import pytest

from pdfsearchable.store import add_file_meta, save_file_text
from pdfsearchable.export import (
    export_json,
    export_jsonl,
    export_csv,
    export_markdown,
    export,
)


@pytest.fixture
def store_with_two_docs(isolated_store):
    """Índice com dois documentos + texto para testes de exportação."""
    add_file_meta(
        "aaaa000000000001",
        "/docs/contrato.pdf",
        3,
        doc_type="contrato",
        language="pt-BR",
        word_count=500,
        summary="Contrato de prestação de serviços.",
        tags=["contrato", "serviços"],
    )
    save_file_text(
        "aaaa000000000001",
        "Texto do contrato página 1\n\nTexto da página 2",
        page_texts=[(1, "Texto do contrato página 1"), (2, "Texto da página 2")],
    )
    add_file_meta(
        "bbbb000000000002",
        "/docs/relatorio.pdf",
        10,
        doc_type="relatório",
        language="pt-BR",
        word_count=2000,
        tags=["relatório", "anual"],
        identified_dates=["2024-01-15", "2024-12-31"],
    )
    save_file_text(
        "bbbb000000000002",
        "Relatório anual 2024",
        page_texts=[(1, "Relatório anual 2024")],
    )
    return isolated_store


@pytest.mark.integration
def test_export_json(store_with_two_docs, tmp_path) -> None:
    out = tmp_path / "index.json"
    n = export_json(out)

    assert n == 2
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "files" in data
    assert len(data["files"]) == 2
    names = {f["name"] for f in data["files"]}
    assert "contrato.pdf" in names
    assert "relatorio.pdf" in names


@pytest.mark.integration
def test_export_jsonl_with_text(store_with_two_docs, tmp_path) -> None:
    out = tmp_path / "export.jsonl"
    n = export_jsonl(out, include_text=True)

    assert n == 2
    lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2
    # Todos têm os campos obrigatórios
    for line in lines:
        assert "id" in line
        assert "name" in line
        assert "text" in line
        assert "doc_type" in line
    # O contrato tem o texto
    contrato = next(l for l in lines if l["name"] == "contrato.pdf")
    assert "contrato" in contrato["text"].lower()


@pytest.mark.integration
def test_export_jsonl_without_text(store_with_two_docs, tmp_path) -> None:
    out = tmp_path / "meta.jsonl"
    export_jsonl(out, include_text=False)

    lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    for line in lines:
        assert "text" not in line


@pytest.mark.integration
def test_export_jsonl_includes_dates(store_with_two_docs, tmp_path) -> None:
    out = tmp_path / "export.jsonl"
    export_jsonl(out)

    lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    relatorio = next(l for l in lines if l["name"] == "relatorio.pdf")
    assert "identified_dates" in relatorio
    assert "2024-01-15" in relatorio["identified_dates"]


@pytest.mark.integration
def test_export_csv(store_with_two_docs, tmp_path) -> None:
    out = tmp_path / "meta.csv"
    n = export_csv(out)

    assert n == 2
    with open(out, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 2
    names = {r["name"] for r in rows}
    assert "contrato.pdf" in names
    assert "relatorio.pdf" in names
    # Tags como string separada por "; "
    contrato_row = next(r for r in rows if r["name"] == "contrato.pdf")
    assert "contrato" in contrato_row["tags"]


@pytest.mark.integration
def test_export_markdown(store_with_two_docs, tmp_path) -> None:
    out_dir = tmp_path / "md_output"
    n = export_markdown(out_dir)

    assert n == 2
    assert out_dir.is_dir()
    md_files = list(out_dir.glob("*.md"))
    assert len(md_files) == 2

    # Verificar conteúdo de um ficheiro Markdown
    contrato_md = next((f for f in md_files if "contrato" in f.name.lower()), None)
    assert contrato_md is not None
    content = contrato_md.read_text(encoding="utf-8")
    assert "# contrato.pdf" in content
    assert "**Tipo:** contrato" in content
    assert "Texto do contrato" in content


@pytest.mark.integration
def test_export_markdown_includes_dates(store_with_two_docs, tmp_path) -> None:
    out_dir = tmp_path / "md_output"
    export_markdown(out_dir)

    relatorio_md = next((f for f in out_dir.glob("*.md") if "relatorio" in f.name.lower()), None)
    assert relatorio_md is not None
    content = relatorio_md.read_text(encoding="utf-8")
    assert "2024-01-15" in content


@pytest.mark.integration
def test_export_unified_entry_point(store_with_two_docs, tmp_path) -> None:
    """A função export() unificada deve delegar para o handler correcto."""
    out = tmp_path / "unified.jsonl"
    n = export("jsonl", out)
    assert n == 2
    assert out.exists()


@pytest.mark.integration
def test_export_unknown_format_raises(store_with_two_docs, tmp_path) -> None:
    with pytest.raises(ValueError, match="Formato desconhecido"):
        export("xml", tmp_path / "out.xml")


@pytest.mark.integration
def test_export_empty_index(isolated_store, tmp_path) -> None:
    """Exportar um índice vazio deve produzir ficheiro válido com 0 documentos."""
    out = tmp_path / "vazio.jsonl"
    n = export_jsonl(out)
    assert n == 0
    assert out.read_text(encoding="utf-8").strip() == ""
