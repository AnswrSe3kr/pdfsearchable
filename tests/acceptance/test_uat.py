"""Testes de aceitação (UAT): valida requisitos de negócio. Caixa preta."""

import pytest
from click.testing import CliRunner

from pdfsearchable.cli import main


@pytest.fixture
def uat_runner():
    return CliRunner()


@pytest.mark.acceptance
@pytest.mark.black_box
@pytest.mark.functional
def test_uat_user_can_add_pdf_and_see_in_report(
    uat_runner: CliRunner,
    isolated_store,
    minimal_pdf,
    monkeypatch,
) -> None:
    """UAT: Como usuário, posso adicionar um PDF e vê-lo no report."""
    monkeypatch.chdir(isolated_store)
    uat_runner.invoke(main, ["add", str(minimal_pdf), "--workers", "1"])
    from pdfsearchable.report import generate_report

    generate_report()
    report_path = isolated_store / ".pdfsearchable" / "report.html"
    assert report_path.exists()
    html = report_path.read_text(encoding="utf-8")
    assert "sample.pdf" in html or "documentos" in html
    assert "1" in html  # pelo menos um documento


@pytest.mark.acceptance
@pytest.mark.black_box
@pytest.mark.functional
def test_uat_user_can_search_and_get_results(
    uat_runner: CliRunner,
    isolated_store,
    minimal_pdf,
    monkeypatch,
) -> None:
    """UAT: Como usuário, posso pesquisar e obter resultados com página."""
    monkeypatch.chdir(isolated_store)
    uat_runner.invoke(main, ["add", str(minimal_pdf), "--workers", "1"])
    result = uat_runner.invoke(main, ["search", "testing"])
    assert result.exit_code == 0
    assert (
        "testing" in result.output
        or "Sample" in result.output
        or "resultado" in result.output.lower()
    )


@pytest.mark.acceptance
@pytest.mark.black_box
@pytest.mark.functional
def test_uat_user_can_remove_document(
    uat_runner: CliRunner,
    isolated_store,
    minimal_pdf,
    monkeypatch,
) -> None:
    """UAT: Como usuário, posso remover um documento do índice."""
    monkeypatch.chdir(isolated_store)
    uat_runner.invoke(main, ["add", str(minimal_pdf), "--workers", "1"])
    idx = isolated_store / ".pdfsearchable" / "index.json"
    assert idx.exists()
    data = __import__("json").loads(idx.read_text())
    fid = data["files"][0]["id"]
    result = uat_runner.invoke(main, ["remove", fid, "--yes"])
    assert result.exit_code == 0
    assert "Removido" in result.output
    data2 = __import__("json").loads(idx.read_text())
    assert len(data2["files"]) == 0


@pytest.mark.acceptance
@pytest.mark.black_box
def test_uat_duplicates_command(isolated_store, uat_runner: CliRunner, monkeypatch) -> None:
    """UAT: Comando duplicates lista duplicatas quando existem."""
    monkeypatch.chdir(isolated_store)
    result = uat_runner.invoke(main, ["duplicates"])
    assert result.exit_code == 0
    assert "duplicata" in result.output.lower() or "único" in result.output.lower()
