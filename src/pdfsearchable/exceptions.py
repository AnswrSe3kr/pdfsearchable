"""
Exceções do pdfsearchable para tratamento de erros consistente e auditoria.

Hierarquia:
  PdfSearchableError (base)
    ├─ ValidationError  : arquivo não encontrado, não-PDF, inválido, corrompido, senha.
    ├─ IndexingError    : falha na indexação (extração, OCR, IA, gravação no store).
    ├─ StoreError       : falha no índice (index.json), FTS ou leitura/gravação.
    └─ ReportError      : falha na geração do report HTML.

Regras de uso:
  - Ao levantar: usar `raise XxxError("msg user-friendly", details={...}) from cause`
    para preservar a causa original (__cause__) no traceback.
  - Ao capturar em CLI/serve: mostrar `e.message` ao utilizador via Rich console
    e registar `e.details` + traceback com `logger.exception(...)`.
  - Erros inesperados (Exception genérica): logger.exception + mensagem genérica
    com indicação do arquivo de log.

Exemplo:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StoreError(
            "Índice corrompido — execute 'pdfsearchable doctor'.",
            details={"file": str(path), "error": str(exc)},
        ) from exc
"""

from __future__ import annotations

import logging
from typing import Any


class PdfSearchableError(Exception):
    """
    Erro base. Atributos:
      message (str)  — texto user-friendly para mostrar no terminal.
      details (dict) — contexto técnico para logs (path, exception, etc.).
      code    (str)  — código curto para programmatic handling (ex.: 'store_read').
    """

    def __init__(
        self,
        message: str,
        details: dict[str, Any] | None = None,
        code: str = "",
    ):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.code = code or type(self).__name__.lower().replace("error", "_error")

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.message!r}, code={self.code!r})"

    def log(self, logger: logging.Logger, level: int = logging.ERROR) -> None:
        """Regista o erro com contexto completo. Usa logger.exception se level=ERROR."""
        if level >= logging.ERROR:
            logger.exception(
                "%s [%s]: %s | details=%s",
                type(self).__name__,
                self.code,
                self.message,
                self.details,
            )
        else:
            logger.log(
                level,
                "%s [%s]: %s | details=%s",
                type(self).__name__,
                self.code,
                self.message,
                self.details,
            )


class ValidationError(PdfSearchableError):
    """
    Arquivo não encontrado, não é PDF, inválido, corrompido ou protegido por senha.
    Usar em pdf_processor.validate_pdf e indexer antes de processar.

    Exemplos de code:
      'not_found'         — arquivo não existe
      'not_pdf'           — extensão ou magic bytes errados
      'corrupted'         — PDF não parseável
      'password_required' — PDF encriptado sem senha
      'empty'             — PDF sem páginas
    """


class IndexingError(PdfSearchableError):
    """
    Falha durante a indexação: extração de texto, OCR, classificação por IA,
    gravação no store. Usar no indexer ao envolver falhas por documento.

    Exemplos de code:
      'extraction_failed' — PyMuPDF não conseguiu extrair
      'ocr_failed'        — Tesseract devolveu erro
      'store_write'       — falha ao gravar texto extraído
    """


class StoreError(PdfSearchableError):
    """
    Falha no armazenamento: index.json, FTS SQLite ou leitura/gravação de arquivos.
    Usar em store.py (load_index, save_index, load_file_text, fts_search, etc.).

    Exemplos de code:
      'index_read'  — falha ao ler index.json
      'index_write' — falha ao gravar index.json
      'fts_search'  — FTS query falhou
      'text_read'   — falha ao ler texto de página
    """


class ReportError(PdfSearchableError):
    """
    Falha na geração do report HTML.
    Usar em report.generate_report (invocado apenas pelo serve ao arrancar).

    Exemplos de code:
      'template_error'  — Jinja2 falhou ao renderizar
      'write_failed'    — falha ao gravar report.html
    """


class OcrError(PdfSearchableError):
    """
    Falha específica de OCR — separada de IndexingError para fácil filtragem nos logs.
    Usar em ocr.py quando Tesseract/HTR falha de forma não-recuperável.

    Exemplos de code:
      'tesseract_not_found' — Tesseract não instalado
      'timeout'             — OCR demorou além do limite
      'rendering_failed'    — falha ao renderizar página para imagem
    """


class ConfigError(PdfSearchableError):
    """
    Configuração inválida ou variável de ambiente com valor fora do esperado.
    Usar em config.py e ao validar env vars no arranque.

    Exemplos de code:
      'invalid_value'   — valor não parseable ou fora do intervalo
      'missing_required' — variável obrigatória não definida
    """
