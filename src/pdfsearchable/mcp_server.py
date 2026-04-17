"""
Servidor MCP (Model Context Protocol) via stdin/stdout para pdfsearchable.

Expõe 5 tools para clientes MCP (Claude Desktop, Cursor, Zed, Windsurf, etc.):
  - list_documents      : lista todos os PDFs indexados com metadados
  - search_documents    : full-text search (FTS5) com trechos
  - get_document_text   : texto completo de um documento por ID
  - ask_document        : RAG numa pergunta sobre um único documento (Ollama)
  - ask_all_documents   : RAG multi-documento com FTS para pré-selecção

Protocolo: JSON-RPC 2.0 sobre stdin/stdout (MCP stdio transport).
Não tem dependências externas — apenas stdlib + pdfsearchable.

Configurar no claude_desktop_config.json:
  {
    "mcpServers": {
      "pdfsearchable": {
        "command": "pdfsearchable",
        "args": ["mcp"],
        "cwd": "/caminho/para/o/projeto"
      }
    }
  }
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from pdfsearchable.audit import get_logger as _get_logger

_log = _get_logger("pdfsearchable.mcp")

# ---------------------------------------------------------------------------
# Tool definitions (esquema MCP)
# ---------------------------------------------------------------------------

_TOOLS: list[dict] = [
    {
        "name": "list_documents",
        "description": (
            "Lista todos os documentos PDF indexados com metadados: nome, tipo, idioma, "
            "número de páginas, palavras, data de indexação, tags e sumário."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_documents",
        "description": (
            "Pesquisa full-text (FTS5) nos documentos PDF indexados. "
            "Suporta operadores: AND, OR, NOT, aspas para frases exactas. "
            "Retorna trechos com os termos em destaque, ID do documento e número de página."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": 'Termos de pesquisa, ex.: "rescisão contratual" OR prazo',
                },
                "limit": {
                    "type": "integer",
                    "description": "Número máximo de resultados (padrão: 20, máx.: 50)",
                    "default": 20,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_document_text",
        "description": (
            "Devolve o texto completo extraído de um documento PDF pelo seu ID. "
            "Útil para análise detalhada, extracção de cláusulas, ou contexto para perguntas."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "ID do documento (16 caracteres hex, obtido de list_documents ou search_documents)",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Limite de caracteres do texto (padrão: 50 000)",
                    "default": 50000,
                },
            },
            "required": ["id"],
        },
    },
    {
        "name": "ask_document",
        "description": (
            "Faz uma pergunta sobre um documento específico usando RAG via Ollama. "
            "Requer Ollama em execução e PDFSEARCHABLE_AI=ollama. "
            "Ideal para perguntas precisas sobre um contrato, relatório ou documento."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "ID do documento"},
                "question": {"type": "string", "description": "Pergunta em linguagem natural"},
            },
            "required": ["id", "question"],
        },
    },
    {
        "name": "ask_all_documents",
        "description": (
            "Pesquisa os documentos mais relevantes para a pergunta (via FTS) e responde "
            "com RAG multi-documento via Ollama. "
            "Ideal para perguntas transversais a toda a colecção de PDFs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Pergunta em linguagem natural"},
                "max_docs": {
                    "type": "integer",
                    "description": "Máximo de documentos a consultar (padrão: 5, máx.: 10)",
                    "default": 5,
                },
                "chars_per_doc": {
                    "type": "integer",
                    "description": "Caracteres por documento no contexto (padrão: 6000)",
                    "default": 6000,
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "get_redaction_report",
        "description": (
            "Devolve o relatório de detecção de redacções (zonas negras / censura visual) "
            "para um documento. Requer que a indexação tenha sido feita com "
            "PDFSEARCHABLE_DETECT_REDACTIONS=1. Retorna número de zonas por página, "
            "totais e avaliação de suspeita."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_id_or_name": {
                    "type": "string",
                    "description": "ID (16 chars) ou nome parcial do documento.",
                },
            },
            "required": ["file_id_or_name"],
        },
    },
    {
        "name": "get_forensics_summary",
        "description": (
            "Devolve o resumo de análise forense (anomalias estruturais no PDF: "
            "producer vs criador, assinaturas inválidas, fontes embutidas, "
            "datas de modificação, etc.). Requer indexação com PDFSEARCHABLE_FORENSICS=1."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_id_or_name": {
                    "type": "string",
                    "description": "ID (16 chars) ou nome parcial do documento.",
                },
            },
            "required": ["file_id_or_name"],
        },
    },
    {
        "name": "index_document",
        "description": (
            "Indexa um arquivo PDF pelo caminho absoluto. "
            "Extrai texto, aplica OCR se necessário, classifica o tipo de documento e "
            "adiciona ao índice pesquisável. "
            "Use quando o utilizador arrasta um arquivo ou partilha um caminho para indexar."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Caminho absoluto para o arquivo PDF a indexar",
                },
                "doc_type": {
                    "type": "string",
                    "description": "Tipo de documento opcional (ex.: contrato, relatório). "
                    "Se omitido, o tipo é detectado automaticamente.",
                },
            },
            "required": ["path"],
        },
    },
]

# ---------------------------------------------------------------------------
# Transport helpers
# ---------------------------------------------------------------------------


def _write(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _respond(msg_id: Any, result: Any) -> None:
    _write({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error(msg_id: Any, code: int, message: str) -> None:
    _write({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def _text(content: str) -> list[dict]:
    return [{"type": "text", "text": content}]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _tool_list_documents(_args: dict) -> list[dict]:
    from pdfsearchable.store import load_index

    idx = load_index()
    files = idx.get("files", [])
    if not files:
        return _text("Nenhum documento indexado. Use `pdfsearchable add` para adicionar PDFs.")
    rows = []
    for f in files:
        rows.append(
            {
                "id": f.get("id", ""),
                "name": f.get("name", ""),
                "doc_type": f.get("doc_type") or "documento",
                "language": f.get("language") or "",
                "num_pages": f.get("num_pages") or 0,
                "word_count": f.get("word_count") or 0,
                "indexed_at": (f.get("indexed_at") or "")[:10],
                "tags": f.get("tags") or [],
                "summary": (f.get("summary") or "")[:200],
                "subject": f.get("subject") or "",
            }
        )
    return _text(
        f"{len(rows)} documento(s) indexado(s):\n\n"
        + json.dumps(rows, ensure_ascii=False, indent=2)
    )


def _tool_search_documents(args: dict) -> list[dict]:
    from pdfsearchable.store import load_index, fts_search

    query = str(args.get("query", "")).strip()
    if not query:
        return _text("Erro: parâmetro 'query' é obrigatório.")
    limit = min(50, max(1, int(args.get("limit", 20))))

    results = fts_search(query, limit=limit)
    if not results:
        return _text(f"Nenhum resultado para a pesquisa '{query}'.")

    idx = load_index()
    id_to_name = {f.get("id", ""): f.get("name", "") for f in idx.get("files", [])}
    rows = [
        {
            "file_id": fid,
            "name": id_to_name.get(fid, fid),
            "page": page_num,
            "snippet": snippet,
        }
        for fid, page_num, snippet in results
    ]
    return _text(
        f"{len(rows)} resultado(s) para '{query}':\n\n"
        + json.dumps(rows, ensure_ascii=False, indent=2)
    )


def _tool_get_document_text(args: dict) -> list[dict]:
    from pdfsearchable.store import load_index, load_file_text

    file_id = str(args.get("id", "")).strip()
    if not file_id:
        return _text("Erro: parâmetro 'id' é obrigatório.")
    max_chars = min(200_000, max(1_000, int(args.get("max_chars", 50_000))))

    idx = load_index()
    meta = next((f for f in idx.get("files", []) if f.get("id") == file_id), None)
    if not meta:
        return _text(f"Documento com ID '{file_id}' não encontrado no índice.")

    text = load_file_text(file_id)
    if not text or not text.strip():
        return _text(f"Documento '{meta.get('name', file_id)}' não tem texto extraído.")

    truncated = ""
    if len(text) > max_chars:
        truncated = f"\n\n[Texto truncado: {max_chars:,} de {len(text):,} caracteres]"
        text = text[:max_chars]

    header = (
        f"Documento: {meta.get('name', file_id)}\n"
        f"Tipo: {meta.get('doc_type') or '—'}  |  "
        f"Páginas: {meta.get('num_pages') or 0}  |  "
        f"Idioma: {meta.get('language') or '—'}\n"
        f"{'─' * 60}\n\n"
    )
    return _text(header + text + truncated)


def _tool_ask_document(args: dict) -> list[dict]:
    from pdfsearchable.store import load_index, load_file_text
    from pdfsearchable.content_extractors import ask_document_ollama

    file_id = str(args.get("id", "")).strip()
    question = str(args.get("question", "")).strip()
    if not file_id or not question:
        return _text("Erro: 'id' e 'question' são obrigatórios.")

    idx = load_index()
    meta = next((f for f in idx.get("files", []) if f.get("id") == file_id), None)
    if not meta:
        return _text(f"Documento '{file_id}' não encontrado.")

    text = load_file_text(file_id)
    if not text or not text.strip():
        return _text(f"Documento '{meta.get('name', file_id)}' sem texto extraído.")

    answer = ask_document_ollama(text, question)
    if not answer:
        return _text(
            "Ollama não devolveu resposta. Verifique se está em execução "
            "e se PDFSEARCHABLE_AI=ollama está configurado."
        )
    return _text(f"[{meta.get('name', file_id)}]\n\n{answer}")


def _tool_ask_all_documents(args: dict) -> list[dict]:
    from pdfsearchable.store import load_index, load_file_text, fts_search
    from pdfsearchable.content_extractors import ask_document_ollama

    question = str(args.get("question", "")).strip()
    if not question:
        return _text("Erro: 'question' é obrigatório.")
    max_docs = min(10, max(1, int(args.get("max_docs", 5))))
    chars_per_doc = min(20_000, max(1_000, int(args.get("chars_per_doc", 6_000))))

    # FTS para pré-seleccionar documentos relevantes
    fts_hits = fts_search(question, limit=max_docs * 4)
    seen_ids: list[str] = []
    for fid, _, _ in fts_hits:
        if fid not in seen_ids:
            seen_ids.append(fid)
        if len(seen_ids) >= max_docs:
            break

    # Fallback: usar todos os documentos até max_docs
    if not seen_ids:
        idx = load_index()
        seen_ids = [f.get("id", "") for f in idx.get("files", [])[:max_docs] if f.get("id")]

    if not seen_ids:
        return _text("Nenhum documento no índice.")

    idx = load_index()
    id_to_meta = {f.get("id", ""): f for f in idx.get("files", [])}

    context_parts: list[str] = []
    for fid in seen_ids:
        m = id_to_meta.get(fid, {})
        text = load_file_text(fid)
        if text and text.strip():
            snippet = text[:chars_per_doc]
            context_parts.append(
                f"=== {m.get('name', fid)} ({m.get('doc_type', '—')}) ===\n{snippet}"
            )

    if not context_parts:
        return _text("Documentos seleccionados não têm texto extraído.")

    context = "\n\n".join(context_parts)
    answer = ask_document_ollama(context, question)
    if not answer:
        return _text(
            "Ollama não devolveu resposta. "
            "Certifique-se que está em execução com PDFSEARCHABLE_AI=ollama."
        )

    sources = [id_to_meta.get(fid, {}).get("name", fid) for fid in seen_ids]
    footer = f"\n\n---\nFontes consultadas ({len(sources)}): {', '.join(sources)}"
    return _text(answer + footer)


def _tool_index_document(args: dict) -> list[dict]:
    from pathlib import Path as _Path

    from pdfsearchable.exceptions import IndexingError, ValidationError
    from pdfsearchable.indexer import index_pdfs

    raw_path = str(args.get("path", "")).strip()
    if not raw_path:
        return _text("Erro: parâmetro 'path' é obrigatório.")
    pdf_path = _Path(raw_path).expanduser().resolve()
    if not pdf_path.exists():
        return _text(f"Arquivo não encontrado: '{pdf_path}'")
    if pdf_path.suffix.lower() != ".pdf":
        return _text(f"O arquivo '{pdf_path.name}' não é um PDF.")

    doc_type = str(args.get("doc_type", "")).strip() or None
    doc_types = {str(pdf_path): doc_type} if doc_type else {}

    try:
        results = index_pdfs(
            [pdf_path],
            use_ocr=True,
            skip_existing=True,
            doc_types=doc_types,
        )
    except ValidationError as e:
        return _text(f"Validação falhou: {e.message}")
    except IndexingError as e:
        return _text(f"Indexação falhou: {e.message}")
    except Exception as e:
        _log.exception("index_document MCP tool falhou para %s", pdf_path)
        return _text(f"Erro inesperado ao indexar '{pdf_path.name}': {e}")

    if not results:
        return _text(
            f"'{pdf_path.name}' já estava indexado (sem alterações). "
            "Use search_documents para pesquisar."
        )
    r = results[0]
    return _text(
        f"✅ '{pdf_path.name}' indexado com sucesso.\n"
        f"  ID: {r.get('file_id', '—')}\n"
        f"  Páginas: {r.get('num_pages', 0)}\n"
        f"  Palavras: {r.get('word_count', 0)}\n"
        f"  Tipo: {r.get('doc_type', '—')}\n"
        f"  Idioma: {r.get('language', '—')}\n"
        "Use search_documents ou get_document_text para consultar o conteúdo."
    )


def _find_doc(needle: str) -> dict | None:
    """Encontra um doc por ID exacto ou nome parcial (case-insensitive)."""
    from pdfsearchable.store import load_index

    idx = load_index()
    files = idx.get("files", [])
    n = (needle or "").strip().lower()
    match = next((f for f in files if f.get("id", "") == needle), None)
    if match is None:
        match = next((f for f in files if n in (f.get("name") or "").lower()), None)
    return match


def _tool_get_redaction_report(args: dict) -> list[dict]:
    needle = str(args.get("file_id_or_name", "")).strip()
    if not needle:
        return _text("Parâmetro 'file_id_or_name' é obrigatório.")
    match = _find_doc(needle)
    if not match:
        return _text(f"Documento não encontrado: {needle}")
    rr = (match.get("metadata") or {}).get("redaction_report")
    if not rr:
        return _text(
            f"Sem relatório de redacções para '{match.get('name')}'. "
            "Indexe com PDFSEARCHABLE_DETECT_REDACTIONS=1 para activar."
        )
    return _text(
        f"Redacções em '{match.get('name')}':\n\n" + json.dumps(rr, ensure_ascii=False, indent=2)
    )


def _tool_get_forensics_summary(args: dict) -> list[dict]:
    needle = str(args.get("file_id_or_name", "")).strip()
    if not needle:
        return _text("Parâmetro 'file_id_or_name' é obrigatório.")
    match = _find_doc(needle)
    if not match:
        return _text(f"Documento não encontrado: {needle}")
    fr = (match.get("metadata") or {}).get("forensics")
    if not fr:
        return _text(
            f"Sem relatório forense para '{match.get('name')}'. "
            "Indexe com PDFSEARCHABLE_FORENSICS=1 para activar."
        )
    return _text(
        f"Análise forense de '{match.get('name')}':\n\n"
        + json.dumps(fr, ensure_ascii=False, indent=2)
    )


_TOOL_HANDLERS = {
    "list_documents": _tool_list_documents,
    "search_documents": _tool_search_documents,
    "get_document_text": _tool_get_document_text,
    "ask_document": _tool_ask_document,
    "ask_all_documents": _tool_ask_all_documents,
    "index_document": _tool_index_document,
    "get_redaction_report": _tool_get_redaction_report,
    "get_forensics_summary": _tool_get_forensics_summary,
}

# ---------------------------------------------------------------------------
# Main server loop
# ---------------------------------------------------------------------------

try:
    from pdfsearchable import __version__ as _pkg_version
except Exception as _e:
    _log.debug("Falha ao importar __version__: %s", _e)
    _pkg_version = "0.4.0"
_SERVER_INFO = {"name": "pdfsearchable", "version": _pkg_version}
_PROTOCOL_VERSION = "2024-11-05"


def run_stdio_server() -> None:
    """
    Loop principal do servidor MCP.
    Lê mensagens JSON-RPC do stdin, processa, escreve respostas no stdout.
    Logs vão para stderr para não contaminar o canal MCP.
    """
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # Aplicar config do projecto (lê .pdfsearchable/config.* se existir)
    try:
        from pdfsearchable.config import apply_config_to_env

        apply_config_to_env()
    except Exception as _e:
        _log.debug("Falha ao aplicar config: %s", _e)

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as exc:
            _log.warning("JSON inválido: %s", exc)
            continue

        method = msg.get("method", "")
        msg_id = msg.get("id")  # None para notificações

        try:
            if method == "initialize":
                _respond(
                    msg_id,
                    {
                        "protocolVersion": _PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": _SERVER_INFO,
                    },
                )

            elif method == "initialized":
                pass  # notificação — sem resposta

            elif method == "tools/list":
                _respond(msg_id, {"tools": _TOOLS})

            elif method == "tools/call":
                params = msg.get("params", {})
                tool_name = params.get("name", "")
                tool_args = params.get("arguments") or {}
                handler = _TOOL_HANDLERS.get(tool_name)
                if handler is None:
                    _error(msg_id, -32601, f"Tool desconhecida: '{tool_name}'")
                else:
                    content = handler(tool_args)
                    _respond(msg_id, {"content": content})

            elif method == "ping":
                _respond(msg_id, {})

            elif msg_id is not None:
                _error(msg_id, -32601, f"Método não suportado: '{method}'")

        except Exception as exc:
            _log.exception("Erro ao processar mensagem MCP (method=%s)", method)
            if msg_id is not None:
                _error(msg_id, -32603, f"Erro interno: {exc}")
