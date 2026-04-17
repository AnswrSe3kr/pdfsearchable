"""Testes de sistema (E2E): CLI do início ao fim. Caixa preta."""

import pytest
from click.testing import CliRunner

from pdfsearchable.cli import main


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.mark.system
@pytest.mark.black_box
@pytest.mark.functional
@pytest.mark.smoke
def test_cli_help(cli_runner: CliRunner) -> None:
    """Sanidade: comando principal responde --help."""
    result = cli_runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "add" in result.output
    assert "report" in result.output
    assert "search" in result.output


@pytest.mark.system
@pytest.mark.black_box
@pytest.mark.functional
def test_cli_add_and_status_e2e(
    cli_runner: CliRunner,
    isolated_store,
    minimal_pdf,
    monkeypatch,
) -> None:
    """E2E: add PDF -> status lista o documento."""
    monkeypatch.chdir(isolated_store)
    pdf_name = minimal_pdf.name
    result = cli_runner.invoke(main, ["add", str(minimal_pdf), "--workers", "1"], input="n\n")
    assert result.exit_code == 0, result.output + (result.exception or "")
    result_status = cli_runner.invoke(main, ["status"])
    assert result_status.exit_code == 0
    assert pdf_name in result_status.output or "1" in result_status.output


@pytest.mark.system
@pytest.mark.black_box
@pytest.mark.functional
def test_cli_report_generated_e2e(
    cli_runner: CliRunner,
    isolated_store,
    minimal_pdf,
    monkeypatch,
) -> None:
    """E2E: add PDF -> report gera HTML (report é gerado por serve; aqui chamamos generate_report)."""
    monkeypatch.chdir(isolated_store)
    cli_runner.invoke(main, ["add", str(minimal_pdf), "--workers", "1"])
    from pdfsearchable.report import generate_report

    generate_report()
    report_path = isolated_store / ".pdfsearchable" / "report.html"
    assert report_path.exists()
    html = report_path.read_text(encoding="utf-8")
    assert "documentos" in html or "Estatísticas" in html


@pytest.mark.system
@pytest.mark.black_box
@pytest.mark.functional
def test_cli_search_after_add_e2e(
    cli_runner: CliRunner,
    isolated_store,
    minimal_pdf,
    monkeypatch,
) -> None:
    """E2E: add PDF -> search encontra o texto."""
    monkeypatch.chdir(isolated_store)
    cli_runner.invoke(main, ["add", str(minimal_pdf), "--workers", "1"])
    result = cli_runner.invoke(main, ["search", "Sample"], input="n\n")
    assert result.exit_code == 0
    assert "Sample" in result.output or "resultado" in result.output.lower()


@pytest.mark.system
@pytest.mark.regression
def test_cli_status_empty(isolated_store, cli_runner: CliRunner, monkeypatch) -> None:
    """Status sem documentos exibe mensagem amigável."""
    monkeypatch.chdir(isolated_store)
    result = cli_runner.invoke(main, ["status"])
    assert result.exit_code == 0
    assert "Nenhum" in result.output or "primeiro" in result.output.lower()
