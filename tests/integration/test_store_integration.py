"""Testes de integração: store (load/save index, add_file_meta, get_duplicate_groups, FTS)."""

import pytest

from pdfsearchable.store import (
    load_index,
    save_index,
    add_file_meta,
    get_file_meta,
    get_duplicate_groups,
    remove_file_meta,
    save_file_text,
    load_file_text,
    fts_index_file,
    fts_search,
    INDEX_VERSION,
)


@pytest.mark.integration
@pytest.mark.gray_box
@pytest.mark.functional
def test_store_load_save_index(isolated_store) -> None:
    idx = load_index()
    assert idx["version"] == INDEX_VERSION
    assert "files" in idx
    idx["files"].append({"id": "0000000000000001", "name": "a.pdf"})
    save_index(idx)
    idx2 = load_index()
    assert len(idx2["files"]) == 1
    assert idx2["files"][0]["id"] == "0000000000000001"


@pytest.mark.integration
@pytest.mark.functional
def test_store_add_file_meta_and_get(isolated_store) -> None:
    add_file_meta(
        "0000000000000002",
        "/path/to/doc.pdf",
        num_pages=3,
        doc_type="contrato",
        word_count=100,
        file_size=5000,
        content_hash="abc123",
        pages=[{"n": 1, "char_count": 50, "has_ocr": False}],
        indexed_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        language="pt-BR",
    )
    meta = get_file_meta("0000000000000002")
    assert meta is not None
    assert meta["name"] == "doc.pdf"
    assert meta["num_pages"] == 3
    assert meta["doc_type"] == "contrato"
    assert meta["language"] == "pt-BR"


@pytest.mark.integration
@pytest.mark.functional
def test_store_duplicate_groups(isolated_store) -> None:
    add_file_meta("000000000000000a", "/a/1.pdf", 1, content_hash="same", file_size=100)
    add_file_meta("000000000000000b", "/b/2.pdf", 1, content_hash="same", file_size=100)
    add_file_meta("000000000000000c", "/c/3.pdf", 1, content_hash="other", file_size=100)
    groups = get_duplicate_groups()
    assert len(groups) == 1
    assert len(groups[0]) == 2
    ids = {f["id"] for f in groups[0]}
    assert ids == {"000000000000000a", "000000000000000b"}


@pytest.mark.integration
@pytest.mark.functional
def test_store_save_and_load_file_text(isolated_store) -> None:
    add_file_meta("0000000000000003", "/x.pdf", 1)
    save_file_text("0000000000000003", "Hello world", page_texts=[(1, "Hello world")])
    assert load_file_text("0000000000000003") == "Hello world"


@pytest.mark.integration
@pytest.mark.functional
def test_store_fts_index_and_search(isolated_store) -> None:
    fts_index_file("0000000000000004", [(1, "Primeira página com termo único xyz123.")])
    fts_index_file("0000000000000005", [(1, "Outra página sem o termo.")])
    hits = fts_search("xyz123", limit=10)
    assert len(hits) >= 1
    assert any(h[0] == "0000000000000004" for h in hits)


@pytest.mark.integration
@pytest.mark.regression
def test_store_remove_file_meta(isolated_store) -> None:
    add_file_meta("0000000000000006", "/r.pdf", 1)
    assert get_file_meta("0000000000000006") is not None
    remove_file_meta("0000000000000006")
    assert get_file_meta("0000000000000006") is None
