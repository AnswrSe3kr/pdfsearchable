"""Testes unitários do mcp_server (sem stdio real)."""

import json

import pytest

from pdfsearchable import mcp_server as mcp


# ---------- _text helper ----------


def test_text_helper():
    out = mcp._text("hello")
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0].get("type") == "text"
    assert out[0].get("text") == "hello"


# ---------- _tool_list_documents em store vazio ----------


def test_list_documents_empty(isolated_store):
    result = mcp._tool_list_documents({})
    assert isinstance(result, list)
    # Deve retornar formato content — lista com 1 item text
    assert len(result) >= 1
    assert result[0].get("type") == "text"


# ---------- _tool_search_documents sem store ----------


def test_search_documents_no_query(isolated_store):
    result = mcp._tool_search_documents({})
    assert isinstance(result, list)
    assert result[0].get("type") == "text"


def test_search_documents_empty_store(isolated_store):
    result = mcp._tool_search_documents({"query": "nada"})
    assert isinstance(result, list)


# ---------- _tool_get_document_text com file_id inválido ----------


def test_get_document_text_missing_id(isolated_store):
    result = mcp._tool_get_document_text({})
    assert isinstance(result, list)
    assert result[0].get("type") == "text"


def test_get_document_text_unknown_id(isolated_store):
    result = mcp._tool_get_document_text({"file_id": "deadbeef12345678"})
    assert isinstance(result, list)
    # Deve retornar erro graciosamente
    assert any("erro" in str(c.get("text", "")).lower() or
               "não" in str(c.get("text", "")).lower() or
               "not" in str(c.get("text", "")).lower()
               for c in result)


# ---------- _tool_index_document com path inexistente ----------


def test_index_document_missing_path(isolated_store):
    result = mcp._tool_index_document({"path": "/nao/existe.pdf"})
    assert isinstance(result, list)
    assert result[0].get("type") == "text"


def test_index_document_no_path(isolated_store):
    result = mcp._tool_index_document({})
    assert isinstance(result, list)
    assert result[0].get("type") == "text"
