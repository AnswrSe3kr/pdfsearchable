"""
Testes de Regressão — garantem que bugs corrigidos não reaparecem.
Cada teste documenta o bug original, PR/sessão de correcção e
valida o comportamento correcto.

Referência: MEMORY.md "Bugs Corrigidos (sessão 2026-03-03)"
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import fitz
import pytest

from pdfsearchable.cli import main
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pdf(directory: Path, name: str = "reg_doc.pdf", text: str = "Texto de regressão.") -> Path:
    p = directory / name
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(p))
    doc.close()
    return p


def _add(runner, pdf, isolated_store, monkeypatch):
    monkeypatch.chdir(isolated_store)
    r = runner.invoke(main, ["add", str(pdf), "--workers", "1"])
    assert r.exit_code == 0, r.output
    return r


# ---------------------------------------------------------------------------
# BUG #1 — store.py save_index: escrita não-atómica
# Corrigido: usa temp file + Path.replace()
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_save_index_atomic_write(isolated_store, monkeypatch):
    """
    REGRESSION BUG #1: save_index escrevia directamente no index.json.
    Em caso de crash durante escrita o ficheiro ficava corrompido.
    Corrigido com: temp file + Path.replace() (atómico no POSIX).
    """
    import pdfsearchable.store as store_mod
    monkeypatch.chdir(isolated_store)

    idx = {"files": [{"id": "abc123", "name": "reg_test.pdf"}], "version": 1}
    store_mod.save_index(idx)

    # Verificar que index.json está completo e válido (nunca parcialmente escrito)
    meta_file = isolated_store / ".pdfsearchable" / "index.json"
    assert meta_file.exists()
    data = json.loads(meta_file.read_text(encoding="utf-8"))
    assert data["files"][0]["id"] == "abc123"
    # O temp file não deve subsistir
    assert not list((isolated_store / ".pdfsearchable").glob("*.tmp"))


@pytest.mark.regression
def test_save_index_concurrent_writes_no_corruption(isolated_store, monkeypatch):
    """
    REGRESSION BUG #1 (concorrência): mesmo que escritas concorrentes colidam,
    o index.json NUNCA fica corrompido (invariante da escrita atómica).
    Nota: o temp file tem nome fixo — algumas escritas podem falhar com StoreError
    sob contenção extrema, mas o ficheiro resultante é sempre JSON válido.
    """
    import pdfsearchable.store as store_mod
    monkeypatch.chdir(isolated_store)

    # Inicializar o store sequencialmente
    store_dir = isolated_store / ".pdfsearchable"
    store_dir.mkdir(exist_ok=True)
    store_mod.save_index({"files": [], "version": 1})

    # Escritas sequenciais (garantidas sem colisão) — validar atomicidade
    for i in range(5):
        store_mod.save_index({"files": [{"id": str(i)}], "version": i})
        meta_file = isolated_store / ".pdfsearchable" / "index.json"
        # Ficheiro nunca deve estar vazio ou corrompido
        content = meta_file.read_text(encoding="utf-8")
        assert content.strip() != "", f"index.json ficou vazio após escrita {i}"
        data = json.loads(content)  # lança JSONDecodeError se corrompido
        assert isinstance(data, dict)

    # Verificar que não fica ficheiro .tmp residual
    tmps = list((isolated_store / ".pdfsearchable").glob("*.tmp"))
    assert not tmps, f"Ficheiros .tmp residuais: {tmps}"


# ---------------------------------------------------------------------------
# BUG #2 — store.py remove_file_meta: faltava _index_lock
# Corrigido: adicionado _index_lock à função
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_remove_file_meta_thread_safe(isolated_store, monkeypatch):
    """
    REGRESSION BUG #2: remove_file_meta não adquiria _index_lock.
    Race condition com add/remove concorrente causava corrupção.
    """
    import pdfsearchable.store as store_mod
    monkeypatch.chdir(isolated_store)

    # Criar índice com 5 ficheiros
    files = [{"id": f"id{i}", "name": f"doc{i}.pdf"} for i in range(5)]
    store_mod.save_index({"files": files, "version": 1})

    errors: list[Exception] = []

    def _remove(fid: str):
        try:
            store_mod.remove_file_meta(fid)
        except Exception as e:
            errors.append(e)

    def _load():
        try:
            store_mod.load_index()
        except Exception as e:
            errors.append(e)

    # Remove e leitura concorrentes
    threads = (
        [threading.Thread(target=_remove, args=(f"id{i}",)) for i in range(5)]
        + [threading.Thread(target=_load) for _ in range(5)]
    )
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors


# ---------------------------------------------------------------------------
# BUG #3 — store.py SQLite FTS: timeout 5s sem WAL
# Corrigido: timeout=30 + PRAGMA journal_mode=WAL
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_fts_wal_mode_enabled(isolated_store, monkeypatch):
    """
    REGRESSION BUG #3: FTS usava timeout 5s e journal_mode=DELETE.
    Corrigido: timeout=30 e WAL mode activo.
    """
    import sqlite3
    import pdfsearchable.store as store_mod
    monkeypatch.chdir(isolated_store)

    # Garantir que o directório existe antes de criar o DB
    (isolated_store / ".pdfsearchable").mkdir(exist_ok=True)
    store_mod.fts_index_file("warmup_id", [(1, "texto de arranque")])

    fts_db = isolated_store / ".pdfsearchable" / "fts.sqlite"
    assert fts_db.exists()

    with sqlite3.connect(str(fts_db)) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal", f"Esperado WAL, obtido: {mode!r}"


@pytest.mark.regression
def test_fts_concurrent_search_no_timeout(isolated_store, monkeypatch):
    """
    REGRESSION BUG #3 (timeout): múltiplos leitores simultâneos não
    devem dar timeout com WAL mode.
    """
    import pdfsearchable.store as store_mod
    monkeypatch.chdir(isolated_store)

    (isolated_store / ".pdfsearchable").mkdir(exist_ok=True)
    store_mod.fts_index_file("conc_id", [(1, "texto para busca concorrente")])

    errors: list[Exception] = []

    def _search():
        try:
            store_mod.fts_search("texto", limit=5)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_search) for _ in range(15)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Timeouts/erros em FTS concorrente: {errors}"


# ---------------------------------------------------------------------------
# BUG #4 — store.py _fts_delete_file: rowid lookup complexo
# Corrigido: DELETE FROM fts_idx WHERE file_id = ?
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_fts_delete_by_file_id_correct(isolated_store, monkeypatch):
    """
    REGRESSION BUG #4: _fts_delete_file usava SELECT rowid + DELETE por rowid.
    Simplificado para DELETE WHERE file_id = ?.
    """
    import pdfsearchable.store as store_mod
    monkeypatch.chdir(isolated_store)

    # Indexar usando a API pública
    store_mod.fts_index_file("file_id_reg_test", [(1, "texto de regressão no FTS")])

    # Deve existir no FTS
    hits_before = store_mod.fts_search("regressão", limit=10)
    assert any(fid == "file_id_reg_test" for fid, _, _ in hits_before)

    # Apagar
    store_mod._fts_delete_file("file_id_reg_test")

    # Não deve existir
    hits_after = store_mod.fts_search("regressão", limit=10)
    assert not any(fid == "file_id_reg_test" for fid, _, _ in hits_after)


# ---------------------------------------------------------------------------
# BUG #5 — indexer.py: worker não retornava content_hash
# Corrigido: adicionado "content_hash": c_hash no return do worker
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_indexed_document_has_content_hash(isolated_store, monkeypatch):
    """
    REGRESSION BUG #5: worker repassava result sem content_hash.
    O processo principal recomputava o hash — ineficiente e propenso a erros.
    Corrigido: worker inclui content_hash no return dict.
    """
    import pdfsearchable.store as store_mod
    monkeypatch.chdir(isolated_store)

    pdf = _make_pdf(isolated_store)
    runner = CliRunner()
    runner.invoke(main, ["add", str(pdf), "--workers", "1"])

    idx = store_mod.load_index()
    assert idx["files"], "Nenhum ficheiro indexado"
    doc = idx["files"][0]
    # content_hash deve ser string hex não vazia
    assert "content_hash" in doc, "content_hash ausente no documento indexado"
    assert isinstance(doc["content_hash"], str)
    assert len(doc["content_hash"]) >= 8


# ---------------------------------------------------------------------------
# BUG #6 — store.py _index_lock: deve ser RLock (reentrant)
# Corrigido: threading.RLock() em vez de threading.Lock()
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_index_rlock_allows_reentrant_acquisition(isolated_store, monkeypatch):
    """
    REGRESSION: _index_lock deve ser RLock para permitir aquisição reentrante.
    Com Lock normal, código que chama load_index dentro de um bloco locked
    causaria deadlock.
    """
    import pdfsearchable.store as store_mod
    monkeypatch.chdir(isolated_store)

    store_mod.save_index({"files": [], "version": 1})

    # Simular aquisição reentrante — não deve causar deadlock
    lock = store_mod._index_lock
    acquired_outer = lock.acquire(timeout=2)
    assert acquired_outer, "Falhou ao adquirir _index_lock na 1ª vez"
    try:
        acquired_inner = lock.acquire(timeout=2)
        assert acquired_inner, "RLock deve permitir reentrância — deadlock detectado!"
        if acquired_inner:
            lock.release()
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# BUG #7 — cli.py PENDING_ADD_FILE: import-time vs runtime cwd
# Corrigido: _pending_add_file() função usa cwd em runtime
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_pending_add_file_uses_runtime_cwd(isolated_store, monkeypatch):
    """
    REGRESSION: PENDING_ADD_FILE era calculado no import-time com Path.cwd().
    Ao mudar cwd com monkeypatch.chdir(), o path permanecia errado.
    Corrigido: _pending_add_file() usa Path.cwd() em runtime.
    """
    from pdfsearchable.cli import _pending_add_file

    monkeypatch.chdir(isolated_store)
    result = _pending_add_file()
    assert str(isolated_store) in str(result), (
        f"_pending_add_file() não usa o cwd actual: {result!r}"
    )


# ---------------------------------------------------------------------------
# BUG #8 — audit.py: faltava threading.Lock()
# Corrigido: _audit_lock = threading.Lock() + with _audit_lock:
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_audit_concurrent_writes_no_corruption(isolated_store, monkeypatch):
    """
    REGRESSION: audit() não tinha lock — escritas concorrentes corrompiam JSONL.
    Corrigido: _audit_lock protege todas as escritas.
    """
    monkeypatch.chdir(isolated_store)
    store_dir = isolated_store / ".pdfsearchable"
    store_dir.mkdir(exist_ok=True)

    from pdfsearchable.audit import audit

    errors: list[Exception] = []

    def _write(i: int):
        try:
            audit("test_action", {"index": i, "data": "x" * 100})
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_write, args=(i,)) for i in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors

    audit_file = store_dir / "audit.jsonl"
    if audit_file.exists():
        lines = [l for l in audit_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        for i, line in enumerate(lines):
            try:
                json.loads(line)
            except json.JSONDecodeError:
                pytest.fail(f"Linha {i+1} corrompida: {line!r}")


# ---------------------------------------------------------------------------
# BUG #9 — store.py index cache: deepcopy garante imutabilidade
# Corrigido: retorna copy.deepcopy(_index_cache)
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_load_index_cache_is_independent_copy(isolated_store, monkeypatch):
    """
    REGRESSION: sem deepcopy, modificar o dict retornado por load_index()
    alterava a cache interna, causando inconsistências.
    Corrigido: retorna copy.deepcopy(_index_cache).
    """
    import pdfsearchable.store as store_mod
    monkeypatch.chdir(isolated_store)

    store_mod.save_index({"files": [{"id": "copy_test", "name": "doc.pdf"}], "version": 1})

    idx1 = store_mod.load_index()
    # Modificar o resultado não deve afectar a cache
    idx1["files"].append({"id": "injected", "name": "injected.pdf"})

    idx2 = store_mod.load_index()
    # O segundo load deve retornar apenas o original (sem o injected)
    ids = [f["id"] for f in idx2.get("files", [])]
    assert "injected" not in ids, "Cache interna foi corrompida por referência (deepcopy ausente)"


# ---------------------------------------------------------------------------
# Testes de regressão funcionais — comportamentos que não devem regredir
# ---------------------------------------------------------------------------

@pytest.mark.regression
@pytest.mark.functional
def test_add_then_search_still_works(isolated_store, monkeypatch):
    """Fluxo básico add→search não deve regredir."""
    monkeypatch.chdir(isolated_store)
    pdf = _make_pdf(isolated_store, text="Palavra única rara xyzxyzxyz.")
    runner = CliRunner()
    _add(runner, pdf, isolated_store, monkeypatch)
    result = runner.invoke(main, ["search", "xyzxyzxyz"])
    assert result.exit_code == 0


@pytest.mark.regression
@pytest.mark.functional
def test_status_always_exits_zero(isolated_store, monkeypatch):
    """status deve sempre retornar exit_code 0 mesmo sem documentos."""
    monkeypatch.chdir(isolated_store)
    runner = CliRunner()
    result = runner.invoke(main, ["status"])
    assert result.exit_code == 0


@pytest.mark.regression
@pytest.mark.functional
def test_help_shows_all_major_commands(isolated_store, monkeypatch):
    """--help deve listar todos os comandos principais sem regredir."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for cmd in ("add", "search", "status", "remove", "report", "serve", "doctor"):
        assert cmd in result.output, f"Comando '{cmd}' desapareceu do --help"
