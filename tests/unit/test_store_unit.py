"""Testes unitários: store — funções puras (_file_id, _content_hash, _migrate_index). Caixa branca."""

import pytest

from pdfsearchable.store import (
    _file_id,
    _content_hash,
    _migrate_index,
    INDEX_VERSION,
)


@pytest.mark.unit
@pytest.mark.white_box
@pytest.mark.functional
def test_file_id_stable() -> None:
    from pathlib import Path

    p = Path("/algum/caminho/arquivo.pdf")
    id1 = _file_id(p)
    id2 = _file_id(p.resolve())
    assert id1 == id2
    assert len(id1) == 16
    assert id1.isalnum()


@pytest.mark.unit
@pytest.mark.white_box
def test_content_hash() -> None:
    h = _content_hash(b"hello")
    assert len(h) == 32
    assert h.isalnum()
    assert _content_hash(b"hello") == _content_hash(b"hello")
    assert _content_hash(b"hello") != _content_hash(b"world")


@pytest.mark.unit
@pytest.mark.white_box
@pytest.mark.functional
@pytest.mark.regression
def test_migrate_index_v1_to_v3() -> None:
    data = {
        "version": 1,
        "files": [
            {"id": "a", "name": "x.pdf", "num_pages": 1, "doc_type": "doc", "word_count": 10}
        ],
    }
    out = _migrate_index(data)
    assert out["version"] == INDEX_VERSION
    f = out["files"][0]
    assert "file_size" in f
    assert "content_hash" in f
    assert "metadata" in f
    assert "pages" in f
    assert "indexed_at" in f
    assert "updated_at" in f
    assert "language" in f
    assert all("has_ocr" in p for p in f.get("pages", []))


@pytest.mark.unit
@pytest.mark.white_box
def test_migrate_index_already_current() -> None:
    data = {"version": INDEX_VERSION, "files": []}
    assert _migrate_index(data) is data
