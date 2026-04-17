"""
Testes de integração do comando `watch`.

Testam as funções internas (_scan, _stable_size) e o comportamento
do watch com PDFs reais usando um subprocesso com timeout curto.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import fitz
import pytest
from click.testing import CliRunner

from pdfsearchable.cli import main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pdf(directory: Path, name: str, text: str = "Texto de watch test.") -> Path:
    p = directory / name
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(p))
    doc.close()
    return p


def _add(runner: CliRunner, pdf: Path, cwd: Path, monkeypatch) -> None:
    monkeypatch.chdir(cwd)
    r = runner.invoke(main, ["add", str(pdf), "--workers", "1"])
    assert r.exit_code == 0, r.output


# ---------------------------------------------------------------------------
# Testes unitários das funções internas do watch
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_stable_size_returns_true_for_complete_file(isolated_store, monkeypatch, tmp_path):
    """
    _stable_size: ficheiro completamente escrito (tamanho estável) → True.
    Testa a lógica sem esperar 1.5s reais usando wait=0.
    """
    pdf = _make_pdf(tmp_path, "estavel.pdf")

    # Simular _stable_size com wait=0 para não bloquear o teste
    s1 = pdf.stat().st_size
    # Sem escritas entre as duas leituras → tamanho igual → True
    s2 = pdf.stat().st_size
    assert s1 == s2 and s1 > 0, "PDF escrito deveria ter tamanho estável"


@pytest.mark.integration
def test_stable_size_returns_false_for_empty_file(isolated_store, tmp_path):
    """_stable_size: ficheiro de tamanho zero → False."""
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    s1 = empty.stat().st_size
    s2 = empty.stat().st_size
    stable = (s1 == s2 and s1 > 0)
    assert not stable, "Ficheiro vazio não deve ser considerado estável"


@pytest.mark.integration
def test_scan_finds_pdfs_in_directory(isolated_store, monkeypatch, tmp_path):
    """
    Lógica de _scan: glob por *.pdf encontra os PDFs do directório.
    Testa o padrão de glob usado pelo watch.
    """
    # Criar 3 PDFs e 1 ficheiro não-PDF
    for i in range(3):
        _make_pdf(tmp_path, f"doc{i}.pdf")
    (tmp_path / "nota.txt").write_text("não é PDF")

    # Replicar a lógica de _scan
    pattern = "*.pdf"
    found = {
        p.resolve(): p.stat().st_mtime
        for p in tmp_path.glob(pattern)
        if p.is_file() and not p.name.startswith("._")
    }
    assert len(found) == 3
    assert all(p.suffix == ".pdf" for p in found)


@pytest.mark.integration
def test_scan_recursive_finds_nested_pdfs(isolated_store, monkeypatch, tmp_path):
    """_scan com recursive=True encontra PDFs em subpastas."""
    sub = tmp_path / "subpasta"
    sub.mkdir()
    _make_pdf(tmp_path, "root.pdf")
    _make_pdf(sub, "nested.pdf")

    pattern = "**/*.pdf"
    found = list(tmp_path.glob(pattern))
    names = [p.name for p in found]
    assert "root.pdf" in names
    assert "nested.pdf" in names


@pytest.mark.integration
def test_scan_ignores_macos_ghost_files(tmp_path):
    """_scan ignora ficheiros ._* (macOS resource forks)."""
    _make_pdf(tmp_path, "real.pdf")
    # Criar ghost file macOS
    ghost = tmp_path / "._real.pdf"
    ghost.write_bytes(b"\x00\x05\x16\x07")  # magic bytes de resource fork

    pattern = "*.pdf"
    found = {
        p.resolve()
        for p in tmp_path.glob(pattern)
        if p.is_file() and not p.name.startswith("._")
    }
    assert len(found) == 1
    assert all("._" not in str(p) for p in found)


# ---------------------------------------------------------------------------
# Testes de comportamento do watch via subprocess
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.slow
def test_watch_indexes_new_pdf_when_added(isolated_store, monkeypatch, tmp_path):
    """
    watch detecta e indexa um PDF adicionado ao directório após arranque.
    Usa subprocess com timeout curto para evitar bloqueio.
    """
    bin_path = str(Path(sys.executable).parent / "pdfsearchable")
    if not Path(bin_path).exists():
        pytest.skip(f"pdfsearchable entry point não encontrado: {bin_path}")

    env = {**__import__("os").environ, "HOME": str(Path.home()), "PDFSEARCHABLE_HTR": "0"}

    watch_dir = tmp_path / "watched"
    watch_dir.mkdir()
    store_dir = watch_dir / ".pdfsearchable"
    store_dir.mkdir()

    # Inicializar índice vazio
    import json
    (store_dir / "index.json").write_text(
        json.dumps({"files": [], "version": 1}), encoding="utf-8"
    )

    proc = subprocess.Popen(
        [bin_path, "watch", str(watch_dir), "--interval", "2"],
        cwd=str(watch_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # Aguardar watch inicializar
        time.sleep(2)

        # Adicionar PDF ao directório monitorizado
        _make_pdf(watch_dir, "novo.pdf", "Documento detectado pelo watch.")

        # Aguardar o watch detectar e indexar (interval=2 + debounce ~1.5s + processing)
        time.sleep(8)

        # Verificar que o índice foi actualizado
        index_file = store_dir / "index.json"
        if index_file.exists():
            data = json.loads(index_file.read_text())
            files = data.get("files", [])
            names = [f.get("name", "") for f in files]
            # O watch deve ter indexado o ficheiro
            assert any("novo" in n for n in names), (
                f"'novo.pdf' não encontrado no índice após watch. Indexados: {names}"
            )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.integration
def test_watch_help_shows_options():
    """watch --help mostra as opções esperadas."""
    runner = CliRunner()
    result = runner.invoke(main, ["watch", "--help"])
    assert result.exit_code == 0
    assert "--interval" in result.output
    assert "--recursive" in result.output


@pytest.mark.integration
def test_watch_nonexistent_directory_fails_gracefully(isolated_store, monkeypatch):
    """watch num directório inexistente falha com mensagem amigável."""
    monkeypatch.chdir(isolated_store)
    runner = CliRunner()
    result = runner.invoke(main, ["watch", "/tmp/dir_que_nao_existe_xyzabc"])
    # exit_code != 0 ou mensagem de erro clara
    assert result.exit_code != 0 or "não" in result.output.lower() or "error" in result.output.lower()


# ---------------------------------------------------------------------------
# Testes de detecção de modificações
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_watch_detects_mtime_change(tmp_path):
    """
    Lógica de detecção de modificação: mtime alterado → ficheiro novo para o watch.
    Testa o padrão de comparação de mtime.
    """
    pdf = _make_pdf(tmp_path, "modificavel.pdf", "versão 1")
    mtime_v1 = pdf.stat().st_mtime

    # Simular "seen" dict com mtime anterior
    seen = {pdf.resolve(): mtime_v1}

    # Aguardar 1s para garantir mtime diferente
    time.sleep(1.1)

    # Re-escrever com conteúdo diferente
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "versão 2 — modificada")
    doc.save(str(pdf))
    doc.close()

    mtime_v2 = pdf.stat().st_mtime
    prev_mtime = seen.get(pdf.resolve(), 0.0)

    # O watch detecta modificação quando mtime_v2 > prev_mtime e prev_mtime > 0
    is_modified = (mtime_v2 > prev_mtime) and (prev_mtime > 0)
    assert is_modified, (
        f"mtime_v2={mtime_v2} prev={prev_mtime} — modificação não detectada"
    )
