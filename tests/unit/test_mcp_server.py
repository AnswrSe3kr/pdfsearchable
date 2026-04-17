"""Testes unitários para mcp_server.py — tool definitions, tool handlers, protocol helpers."""

import json
import pytest
from unittest.mock import patch, MagicMock

from pdfsearchable.mcp_server import (
    _TOOLS,
    _TOOL_HANDLERS,
    _text,
    _tool_list_documents,
    _tool_search_documents,
    _tool_get_document_text,
)


class TestToolDefinitions:
    def test_tools_is_list(self):
        assert isinstance(_TOOLS, list)
        assert len(_TOOLS) >= 5

    def test_all_tools_have_required_fields(self):
        for tool in _TOOLS:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "description" in tool, f"Tool {tool['name']} missing 'description'"
            assert "inputSchema" in tool, f"Tool {tool['name']} missing 'inputSchema'"

    def test_tool_names(self):
        names = {t["name"] for t in _TOOLS}
        expected = {
            "list_documents",
            "search_documents",
            "get_document_text",
            "ask_document",
            "ask_all_documents",
        }
        assert expected.issubset(names)

    def test_search_has_query_param(self):
        search = next(t for t in _TOOLS if t["name"] == "search_documents")
        props = search["inputSchema"].get("properties", {})
        assert "query" in props

    def test_all_tools_have_handlers(self):
        for tool in _TOOLS:
            assert tool["name"] in _TOOL_HANDLERS, f"Tool {tool['name']} has no handler"


class TestTextHelper:
    def test_text_returns_list(self):
        result = _text("hello")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "hello"


class TestToolListDocuments:
    def test_empty_index(self):
        with patch("pdfsearchable.store.load_index", return_value={"files": []}):
            result = _tool_list_documents({})
            assert isinstance(result, list)
            assert "Nenhum" in result[0]["text"]

    def test_with_documents(self):
        fake_index = {
            "files": [
                {
                    "id": "abc123",
                    "name": "test.pdf",
                    "num_pages": 5,
                    "doc_type": "contrato",
                    "language": "pt-BR",
                    "word_count": 1000,
                    "indexed_at": "2024-01-01T00:00:00Z",
                    "tags": ["fiscal"],
                    "summary": "Resumo do teste",
                    "subject": "Teste",
                },
            ]
        }
        with patch("pdfsearchable.store.load_index", return_value=fake_index):
            result = _tool_list_documents({})
            text = result[0]["text"]
            assert "1 documento(s)" in text
            assert "abc123" in text


class TestToolSearchDocuments:
    def test_empty_query(self):
        result = _tool_search_documents({})
        assert "obrigatório" in result[0]["text"].lower() or "query" in result[0]["text"].lower()

    def test_no_results(self):
        with patch("pdfsearchable.store.fts_search", return_value=[]):
            result = _tool_search_documents({"query": "inexistente"})
            assert "Nenhum" in result[0]["text"]


class TestToolGetDocumentText:
    def test_missing_id(self):
        result = _tool_get_document_text({})
        text = result[0]["text"]
        assert "obrigatório" in text.lower() or "id" in text.lower()

    def test_not_found(self):
        with patch("pdfsearchable.store.load_index", return_value={"files": []}):
            result = _tool_get_document_text({"id": "nonexistent"})
            assert "não encontrado" in result[0]["text"]
