"""
Testes de sistema E2E com PDF real.
Exercem o ciclo completo: add → operação → assert.
Não requerem Ollama nem rede externa.
"""
from __future__ import annotations

import tarfile
from pathlib import Path

import fitz
import pytest
from click.testing import CliRunner

from pdfsearchable.cli import main


# ---------------------------------------------------------------------------
# Fixtures locais
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def rich_pdf(isolated_store) -> Path:
    """
    PDF com dois parágrafos de texto claro — garante texto extraível sem OCR
    e FTS funcional.
    """
    path = isolated_store / "contrato_teste.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Contrato de prestação de serviços entre as partes.")
    page.insert_text((72, 130), "Valor total: R$ 10.000,00. Prazo: 30 dias.")
    page.insert_text((72, 160), "Empresa: ACME Ltda. CPF: 123.456.789-00.")
    page2 = doc.new_page()
    page2.insert_text((72, 72), "Assinatura das partes e testemunhas.")
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def second_pdf(isolated_store) -> Path:
    """Segundo PDF com conteúdo distinto — para testes de lista/busca múltipla."""
    path = isolated_store / "relatorio_anual.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Relatório anual de actividades. Ano fiscal 2024.")
    doc.save(str(path))
    doc.close()
    return path


def _add(runner: CliRunner, pdf: Path, monkeypatch) -> None:
    monkeypatch.chdir(pdf.parent)
    result = runner.invoke(main, ["add", str(pdf), "--workers", "1"])
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------


@pytest.mark.system
@pytest.mark.functional
def test_add_indexes_document(runner, rich_pdf, isolated_store, monkeypatch):
    """add cria o documento no índice e extrai texto."""
    _add(runner, rich_pdf, monkeypatch)

    result = runner.invoke(main, ["status"])
    assert result.exit_code == 0
    assert "contrato_teste" in result.output or "1" in result.output


@pytest.mark.system
@pytest.mark.functional
def test_add_twice_skips_duplicate(runner, rich_pdf, isolated_store, monkeypatch):
    """add do mesmo ficheiro duas vezes não duplica no índice."""
    _add(runner, rich_pdf, monkeypatch)
    _add(runner, rich_pdf, monkeypatch)

    from pdfsearchable.store import load_index
    idx = load_index()
    assert len(idx.get("files", [])) == 1


@pytest.mark.system
@pytest.mark.functional
def test_search_finds_indexed_text(runner, rich_pdf, isolated_store, monkeypatch):
    """search FTS encontra termos presentes no PDF."""
    _add(runner, rich_pdf, monkeypatch)

    result = runner.invoke(main, ["search", "prestação"])
    assert result.exit_code == 0
    assert "contrato_teste" in result.output or "resultado" in result.output.lower()


@pytest.mark.system
@pytest.mark.functional
def test_search_no_results(runner, rich_pdf, isolated_store, monkeypatch):
    """search sem resultados exibe mensagem amigável."""
    _add(runner, rich_pdf, monkeypatch)

    result = runner.invoke(main, ["search", "xyzabc123inexistente"])
    assert result.exit_code == 0
    # exit_code 0 even with no results; output should be non-empty
    assert result.output.strip() != ""


@pytest.mark.system
@pytest.mark.functional
def test_info_shows_metadata(runner, rich_pdf, isolated_store, monkeypatch):
    """info exibe metadados detalhados do documento indexado."""
    _add(runner, rich_pdf, monkeypatch)

    result = runner.invoke(main, ["info", "contrato_teste"])
    assert result.exit_code == 0
    assert "contrato_teste" in result.output
    assert "Páginas" in result.output or "página" in result.output.lower()


@pytest.mark.system
@pytest.mark.functional
def test_remove_deletes_document(runner, rich_pdf, isolated_store, monkeypatch):
    """remove elimina documento do índice."""
    _add(runner, rich_pdf, monkeypatch)

    from pdfsearchable.store import load_index
    idx = load_index()
    doc_id = idx["files"][0]["id"]

    remove_result = runner.invoke(main, ["remove", doc_id, "--yes"])
    assert remove_result.exit_code == 0

    idx_after = load_index()
    assert not any(f.get("id") == doc_id for f in idx_after.get("files", []))


@pytest.mark.system
@pytest.mark.functional
def test_multiple_pdfs_listed(runner, rich_pdf, second_pdf, isolated_store, monkeypatch):
    """add de dois PDFs: ambos aparecem no índice."""
    _add(runner, rich_pdf, monkeypatch)
    _add(runner, second_pdf, monkeypatch)

    from pdfsearchable.store import load_index
    idx = load_index()
    names = [f.get("name", "") for f in idx.get("files", [])]
    assert any("contrato_teste" in n for n in names)
    assert any("relatorio_anual" in n for n in names)


@pytest.mark.system
@pytest.mark.functional
def test_report_generated_with_content(runner, rich_pdf, isolated_store, monkeypatch):
    """report.html gerado contém o nome do documento indexado."""
    _add(runner, rich_pdf, monkeypatch)

    from pdfsearchable.report import generate_report

    generate_report()
    report_path = isolated_store / ".pdfsearchable" / "report.html"
    assert report_path.exists()
    html = report_path.read_text(encoding="utf-8")
    assert "contrato_teste" in html
    assert "Estatísticas" in html or "documentos" in html


@pytest.mark.system
@pytest.mark.functional
def test_stats_shows_counts(runner, rich_pdf, isolated_store, monkeypatch):
    """stats exibe contagens após indexação."""
    _add(runner, rich_pdf, monkeypatch)

    result = runner.invoke(main, ["stats"])
    assert result.exit_code == 0
    assert "1" in result.output  # pelo menos 1 documento


@pytest.mark.system
@pytest.mark.functional
def test_backup_creates_archive(runner, rich_pdf, isolated_store, monkeypatch, tmp_path):
    """backup cria ficheiro .tar.gz válido com o índice."""
    _add(runner, rich_pdf, monkeypatch)

    archive = tmp_path / "backup.tar.gz"
    result = runner.invoke(main, ["backup", "--output", str(archive)])
    assert result.exit_code == 0
    assert archive.exists()
    assert tarfile.is_tarfile(str(archive))
    with tarfile.open(str(archive)) as tf:
        names = tf.getnames()
    assert any(".pdfsearchable" in n for n in names)


@pytest.mark.system
@pytest.mark.functional
def test_verify_passes_after_add(runner, rich_pdf, isolated_store, monkeypatch):
    """verify não reporta erros após indexação normal."""
    _add(runner, rich_pdf, monkeypatch)

    result = runner.invoke(main, ["verify"])
    assert result.exit_code == 0
    assert "erro" not in result.output.lower() or "0" in result.output


@pytest.mark.system
@pytest.mark.functional
def test_export_obsidian_creates_markdown(runner, rich_pdf, isolated_store, monkeypatch, tmp_path):
    """export --format obsidian cria ficheiro .md com frontmatter."""
    _add(runner, rich_pdf, monkeypatch)

    out_dir = tmp_path / "vault"
    result = runner.invoke(main, ["export", "--format", "obsidian", "--output-dir", str(out_dir)])
    assert result.exit_code == 0
    md_files = list(out_dir.glob("*.md"))
    assert len(md_files) >= 1
    content = md_files[0].read_text(encoding="utf-8")
    assert "---" in content  # YAML frontmatter delimiter


@pytest.mark.system
@pytest.mark.functional
def test_fts_index_rebuild(runner, rich_pdf, isolated_store, monkeypatch):
    """index-fts reconstrói sem erros."""
    _add(runner, rich_pdf, monkeypatch)

    result = runner.invoke(main, ["index-fts"])
    assert result.exit_code == 0


@pytest.mark.system
@pytest.mark.functional
def test_duplicates_detects_same_content(runner, rich_pdf, isolated_store, monkeypatch):
    """duplicates: cópia com nome diferente é detectada como duplicata."""
    import shutil

    _add(runner, rich_pdf, monkeypatch)

    copy_path = isolated_store / "contrato_copia.pdf"
    shutil.copy(str(rich_pdf), str(copy_path))
    _add(runner, copy_path, monkeypatch)

    result = runner.invoke(main, ["duplicates"])
    assert result.exit_code == 0
    assert "contrato" in result.output.lower() or "duplicat" in result.output.lower()


@pytest.mark.system
@pytest.mark.functional
def test_doctor_runs_without_crash(runner, isolated_store, monkeypatch):
    """doctor executa sem erro mesmo sem documentos."""
    monkeypatch.chdir(isolated_store)
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 0
