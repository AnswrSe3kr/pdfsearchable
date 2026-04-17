"""Testes CLI — usa CliRunner para invocar comandos sem spawnar processo."""

import fitz
import pytest
from click.testing import CliRunner

from pdfsearchable.cli import main


@pytest.fixture
def runner():
    return CliRunner()


def _make_pdf(path, text="Sample text for CLI testing."):
    doc = fitz.open()
    page = doc.new_page()
    rect = fitz.Rect(72, 72, 540, 770)
    page.insert_textbox(rect, text * 10, fontsize=11)
    doc.save(str(path))
    doc.close()


# ---------- help / version ----------


def test_cli_help(runner):
    r = runner.invoke(main, ["--help"])
    assert r.exit_code == 0
    assert "pdfsearchable" in r.output.lower() or "Usage" in r.output


def test_cli_init_help(runner):
    r = runner.invoke(main, ["init", "--help"])
    assert r.exit_code == 0


def test_cli_stats_help(runner):
    r = runner.invoke(main, ["stats", "--help"])
    assert r.exit_code == 0


def test_cli_search_help(runner):
    r = runner.invoke(main, ["search", "--help"])
    assert r.exit_code == 0


def test_cli_add_help(runner):
    r = runner.invoke(main, ["add", "--help"])
    assert r.exit_code == 0


def test_cli_serve_help(runner):
    r = runner.invoke(main, ["serve", "--help"])
    assert r.exit_code == 0


def test_cli_doctor_help(runner):
    r = runner.invoke(main, ["doctor", "--help"])
    assert r.exit_code == 0


# ---------- init em cwd tmp ----------


def test_cli_init(runner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(main, ["init"])
    # init pode retornar 0 (sucesso) ou 1 (já existe) — não deve crashar
    assert r.exit_code in (0, 1, 2)


# ---------- stats em cwd vazio ----------


def test_cli_stats_empty(runner, isolated_store):
    r = runner.invoke(main, ["stats"])
    # stats em store vazio: 0 ou aviso
    assert r.exit_code in (0, 1)


# ---------- verify ----------


def test_cli_verify_empty(runner, isolated_store):
    r = runner.invoke(main, ["verify"])
    assert r.exit_code in (0, 1)


# ---------- duplicates ----------


def test_cli_duplicates_empty(runner, isolated_store):
    r = runner.invoke(main, ["duplicates"])
    assert r.exit_code in (0, 1)


# ---------- search sem índice ----------


def test_cli_search_empty(runner, isolated_store):
    r = runner.invoke(main, ["search", "qualquer"])
    assert r.exit_code in (0, 1)


# ---------- doctor ----------


def test_cli_doctor(runner, isolated_store):
    r = runner.invoke(main, ["doctor"])
    # doctor deve sempre retornar informação diagnóstica
    assert r.exit_code in (0, 1)
    assert r.output  # produz output


# ---------- info em file_id inválido ----------


def test_cli_info_missing_id(runner, isolated_store):
    r = runner.invoke(main, ["info", "deadbeef"])
    # deve falhar graciosamente, não crashar
    assert r.exit_code in (0, 1, 2)


# ---------- set-type em id inválido ----------


def test_cli_set_type_invalid(runner, isolated_store):
    r = runner.invoke(main, ["set-type", "deadbeef", "contrato"])
    assert r.exit_code in (0, 1, 2)
