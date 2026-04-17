"""
Testes unitários: TTL cache de load_index em store.py.
Verifica que o cache é invalidado correctamente quando:
  1. O META_FILE muda de path (monkeypatch em testes / cwd change)
  2. save_index é chamado (sempre invalida o TTL)
  3. O TTL expira (simulado via monkeypatch de time.monotonic)
"""

import time

import pytest

import pdfsearchable.store as store_mod
from pdfsearchable.store import load_index, save_index


@pytest.mark.unit
def test_cache_hit_within_ttl(isolated_store, monkeypatch) -> None:
    """Dentro do TTL, load_index deve devolver o cache sem re-ler o ficheiro."""
    # 1ª chamada popula o cache
    idx = load_index()
    assert idx["files"] == []

    # Simular que o tempo ainda está dentro do TTL
    original_ttl = store_mod._INDEX_CACHE_TTL
    monkeypatch.setattr(store_mod, "_INDEX_CACHE_TTL", 60.0)

    # Forçar o tempo de último check para agora (TTL válido)
    monkeypatch.setattr(store_mod, "_index_cache_checked", time.monotonic())
    monkeypatch.setattr(store_mod, "_index_cache_path", store_mod.META_FILE)

    # Corromper o ficheiro em disco para confirmar que o cache é usado
    store_mod.META_FILE.parent.mkdir(parents=True, exist_ok=True)
    store_mod.META_FILE.write_text("INVALID JSON", encoding="utf-8")

    # Deve devolver o cache, não ler o ficheiro corrompido
    idx2 = load_index()
    assert idx2["files"] == []


@pytest.mark.unit
def test_cache_miss_after_ttl_expires(isolated_store, monkeypatch) -> None:
    """Fora do TTL, load_index deve verificar o mtime e re-ler se o ficheiro mudou."""
    # Criar índice com um documento
    save_index({"files": [{"id": "aabbccddeeff0011", "name": "test.pdf"}]})

    # Forçar expiração do TTL: _index_cache_checked muito antigo
    monkeypatch.setattr(store_mod, "_index_cache_checked", 0.0)

    idx = load_index()
    assert len(idx["files"]) == 1
    assert idx["files"][0]["name"] == "test.pdf"


@pytest.mark.unit
def test_save_index_resets_ttl(isolated_store, monkeypatch) -> None:
    """save_index deve repor _index_cache_checked = 0 para forçar re-verificação."""
    # Popular cache com TTL activo
    monkeypatch.setattr(store_mod, "_INDEX_CACHE_TTL", 60.0)
    load_index()

    # Gravar índice novo
    save_index({"files": [{"id": "1122334455667788", "name": "novo.pdf"}]})

    # _index_cache_checked deve ter sido reposto para 0
    assert store_mod._index_cache_checked == 0.0


@pytest.mark.unit
def test_cache_invalidated_on_meta_file_path_change(isolated_store, tmp_path, monkeypatch) -> None:
    """
    Quando META_FILE muda de caminho (ex.: novo isolated_store),
    o TTL não deve ser aplicado — o cache deve ser invalidado.
    """
    # Inserir um documento no índice original
    save_index({"files": [{"id": "ffffffffffffffff", "name": "original.pdf"}]})

    # Simular TTL activo com o path do META_FILE original
    monkeypatch.setattr(store_mod, "_INDEX_CACHE_TTL", 60.0)
    monkeypatch.setattr(store_mod, "_index_cache_checked", time.monotonic())
    monkeypatch.setattr(store_mod, "_index_cache_path", store_mod.META_FILE)

    # Mudar META_FILE para um path diferente (como faz isolated_store)
    new_store = tmp_path / "other_store"
    new_meta = new_store / "index.json"
    new_store.mkdir()
    new_meta.write_text('{"version": 3, "files": []}', encoding="utf-8")

    monkeypatch.setattr(store_mod, "STORE_DIR", new_store)
    monkeypatch.setattr(store_mod, "META_FILE", new_meta)

    # Cache _index_cache_path != novo META_FILE → TTL ignorado → re-lê ficheiro
    idx = load_index()
    # O novo ficheiro tem lista vazia
    assert idx["files"] == []


@pytest.mark.unit
def test_ttl_disabled_when_zero(isolated_store, monkeypatch) -> None:
    """Com _INDEX_CACHE_TTL = 0, cada chamada deve verificar o mtime."""
    monkeypatch.setattr(store_mod, "_INDEX_CACHE_TTL", 0.0)

    save_index({"files": []})
    load_index()  # primeira chamada

    # Modificar o ficheiro directamente (sem save_index)
    raw = store_mod.META_FILE.read_text(encoding="utf-8")
    import json

    data = json.loads(raw)
    data["files"] = [{"id": "0" * 16, "name": "direto.pdf"}]
    store_mod.META_FILE.write_text(json.dumps(data), encoding="utf-8")

    # Com TTL=0, deve re-ler e ver o novo documento
    idx = load_index()
    assert len(idx["files"]) == 1
