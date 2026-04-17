"""
Processamento de PDF: validação, extração (modos PyMuPDF), senha, hash, normalização.
"""

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

import fitz  # PyMuPDF

# Suprimir mensagens de erro/aviso do MuPDF para o stderr do processo.
# PyMuPDF escreve directamente no stderr (ex.: "non-page object in page tree"),
# o que polui o output do CLI. Os avisos ficam acessíveis via
# fitz.TOOLS.mupdf_warnings() se necessário para diagnóstico.
fitz.TOOLS.mupdf_display_errors(False)
fitz.TOOLS.mupdf_display_warnings(False)

# Suprimir a sugestão de instalar pymupdf_layout (aparece uma vez por processo
# via print() interno do pymupdf, não via MuPDF C). Usa a API oficial do pymupdf.
if hasattr(fitz, "no_recommend_layout"):
    fitz.no_recommend_layout()


def format_pdf_date(raw: str | None) -> str | None:
    """
    Converte data no formato PDF (ex.: D:20240315120000+00'00') para DD/MM/AAAA ou DD/MM/AAAA HH:MM.
    Valida os componentes de data com datetime.strptime para rejeitar datas impossíveis
    (ex.: D:20240230 = 30 de fevereiro, D:20241399 = mês 13).
    Retorna None se raw for vazio ou inválido.
    """
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    # Formato PDF: D:YYYYMMDD ou D:YYYYMMDDHHmmss ou com timezone (+00'00')
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?(?:[+-]\d{2}'\d{2})?", raw)
    if not m:
        return None
    y, mo, d = m.group(1), m.group(2), m.group(3)
    h, mi, _s = m.group(4), m.group(5), m.group(6)
    try:
        # Validar data com datetime.strptime (rejeita datas impossíveis como 30/02 ou mês 13)
        if h and mi:
            datetime.strptime(f"{y}{mo}{d}{h}{mi}", "%Y%m%d%H%M")
            return f"{d}/{mo}/{y} {h}:{mi}"
        datetime.strptime(f"{y}{mo}{d}", "%Y%m%d")
        return f"{d}/{mo}/{y}"
    except (ValueError, Exception):
        return None


ExtractMode = Literal["text", "blocks", "dict"]


def _capture_mupdf_warnings() -> list[str]:
    """
    Captura e limpa a fila de avisos pendentes do MuPDF.
    Retorna lista de strings de aviso (pode estar vazia).
    Deve ser chamada após fitz.open() ou operações de leitura de página.
    """
    raw = fitz.TOOLS.mupdf_warnings(reset=True) or ""
    return [w.strip() for w in raw.splitlines() if w.strip()]


def validate_pdf(path: Path, password: str | None = None) -> tuple[bool, str | None]:
    """
    Verifica se o arquivo é um PDF válido e não está corrompido/protegido.
    Retorna (ok, mensagem_erro). password opcional para PDFs protegidos.
    """
    path = Path(path).resolve()
    if not path.exists():
        return False, "Arquivo não encontrado"
    if path.suffix.lower() != ".pdf":
        return False, "O arquivo deve ser um PDF"
    try:
        doc = fitz.open(path)
        try:
            if doc.is_encrypted:
                if not password:
                    return False, "PDF protegido por senha (use --password ou PDF_PASSWORD)"
                if not doc.authenticate(password):
                    return False, "Senha incorreta"
            # Acesso à primeira página para detectar corrupção
            _ = len(doc)
            return True, None
        finally:
            _capture_mupdf_warnings()  # limpar fila — erros foram logados internamente
            doc.close()
    except fitz.FileDataError as e:
        return False, f"PDF corrompido ou inválido: {e}"
    except Exception as e:
        return False, str(e)


def content_hash(path: Path) -> str:
    """Hash SHA-256 do conteúdo do arquivo (para indexação incremental)."""
    path = Path(path).resolve()
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:32]


def file_size(path: Path) -> int:
    return path.stat().st_size


def normalize_text(text: str) -> str:
    """
    Normaliza texto extraído: espaços múltiplos, quebras de linha excessivas,
    caracteres especiais (hífens Unicode → ASCII).
    """
    if not text:
        return ""
    # Substituir hífens Unicode por hífen ASCII (U+2010 a U+2212)
    text = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]", "-", text)
    # Espaços (incl. non-breaking U+00A0) e quebras
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _detect_columns(page: "fitz.Page") -> int:
    """
    Detecta o número provável de colunas de texto numa página usando a distribuição
    horizontal dos centros de blocos de texto.

    Estratégia:
    - Obtém centros X de todos os blocos de texto com conteúdo significativo
    - Agrupa por proximidade (threshold = 20% da largura da página)
    - Retorna 1 se monotextual, 2+ se multicoluna
    """
    try:
        blocks = page.get_text("blocks")
        page_width = page.rect.width
        if page_width <= 0:
            return 1
        centers: list[float] = []
        for b in blocks:
            if len(b) > 6 and b[6] == 0 and b[4].strip():  # type=0 = texto
                cx = (b[0] + b[2]) / 2
                centers.append(cx)
        if len(centers) < 3:
            return 1
        # Clustering: agrupa centros X próximos (threshold = 20% da largura)
        threshold = page_width * 0.20
        clusters: list[float] = []
        for cx in sorted(centers):
            if not clusters or (cx - clusters[-1]) > threshold:
                clusters.append(cx)
            else:
                # Atualizar média do cluster
                clusters[-1] = (clusters[-1] + cx) / 2
        return max(1, len(clusters))
    except Exception:
        return 1


def _extract_page_text_multicolumn(page: "fitz.Page", *, normalize: bool = True) -> str:
    """
    Extrai texto de uma página com layout multicoluna, reordenando blocos da esquerda
    para a direita coluna a coluna, e de cima para baixo dentro de cada coluna.

    Algoritmo:
    1. Obtém blocos de texto com coordenadas
    2. Detecta colunas por clustering de posições X
    3. Atribui cada bloco à coluna mais próxima
    4. Ordena: coluna da esquerda primeiro, depois y0 (topo para baixo)
    5. Fallback para extração simples se menos de 2 colunas detectadas
    """
    try:
        blocks = page.get_text("blocks")
        text_blocks = [
            (b[0], b[1], b[2], b[3], b[4])
            for b in blocks
            if len(b) > 6 and b[6] == 0 and b[4].strip()
        ]
        if not text_blocks:
            return ""

        page_width = page.rect.width
        if page_width <= 0 or len(text_blocks) < 3:
            result = "\n".join(b[4] for b in sorted(text_blocks, key=lambda b: (b[1], b[0])))
            return normalize_text(result) if normalize else result

        # Detectar colunas por clustering dos centros X
        threshold = page_width * 0.20
        x_centers = sorted((b[0] + b[2]) / 2 for b in text_blocks)
        col_centers: list[float] = []
        for xc in x_centers:
            if not col_centers or (xc - col_centers[-1]) > threshold:
                col_centers.append(xc)
            else:
                col_centers[-1] = (col_centers[-1] + xc) / 2

        if len(col_centers) < 2:
            # Uma única coluna: ordem natural (y, x)
            result = "\n".join(b[4] for b in sorted(text_blocks, key=lambda b: (b[1], b[0])))
            return normalize_text(result) if normalize else result

        def _col_idx(x0: float, x1: float) -> int:
            """Retorna o índice da coluna mais próxima do centro do bloco."""
            cx = (x0 + x1) / 2
            return min(range(len(col_centers)), key=lambda i: abs(cx - col_centers[i]))

        # Ordenar por (coluna, y0) para respeitar a ordem de leitura
        sorted_blocks = sorted(text_blocks, key=lambda b: (_col_idx(b[0], b[2]), b[1]))
        result = "\n".join(b[4] for b in sorted_blocks)
        return normalize_text(result) if normalize else result
    except Exception:
        # Fallback seguro
        t = page.get_text()
        return normalize_text(t) if normalize else t


def extract_text_from_doc(
    doc: "fitz.Document",
    *,
    mode: ExtractMode = "text",
    normalize: bool = True,
) -> tuple[str, int, list[str], dict]:
    """
    Extrai texto de um documento PyMuPDF já aberto.
    Retorna (texto_completo, num_pages, lista_texto_por_página, metadata_dict).
    Usado para evitar abrir o mesmo PDF duas vezes (reduz lock blocking do MuPDF).

    Avisos de estrutura do PDF (ex.: "non-page object in page tree") são capturados
    via fitz.TOOLS.mupdf_warnings() e registados em metadata["pdf_warnings"] para
    que o indexador possa surfaçá-los ao utilizador em vez de os ignorar silenciosamente.
    """
    num_pages = len(doc)
    page_texts: list[str] = []
    pdf_warnings: list[str] = []
    for i in range(num_pages):
        page = doc[i]
        if mode == "text":
            # Detecção automática de multicolunas: se >=2 colunas detectadas,
            # reordenar blocos por coluna para preservar ordem de leitura correta.
            if _detect_columns(page) >= 2:
                t = _extract_page_text_multicolumn(page, normalize=False)
            else:
                t = page.get_text()
        elif mode == "blocks":
            blocks = page.get_text("blocks")
            t = "\n".join(b[4] for b in blocks)
        else:  # dict
            d = page.get_text("dict")
            parts = []
            for block in d.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        parts.append(span.get("text", ""))
            t = "\n".join(parts)
        # Capturar avisos MuPDF gerados durante a extracção desta página
        page_warns = _capture_mupdf_warnings()
        for w in page_warns:
            if w not in pdf_warnings:
                pdf_warnings.append(w)
        if normalize:
            t = normalize_text(t)
        page_texts.append(t)
    full = "\n\n".join(page_texts)
    if normalize:
        full = normalize_text(full)
    meta = doc.metadata or {}
    metadata = {
        "title": (meta.get("title") or "").strip() or None,
        "author": (meta.get("author") or "").strip() or None,
        "subject": (meta.get("subject") or "").strip() or None,
        "creation_date": format_pdf_date(meta.get("creationDate"))
        or (meta.get("creationDate") or "").strip()
        or None,
        "mod_date": format_pdf_date(meta.get("modDate"))
        or (meta.get("modDate") or "").strip()
        or None,
        "producer": (meta.get("producer") or "").strip() or None,
        "creator": (meta.get("creator") or "").strip() or None,
        "keywords": (meta.get("keywords") or "").strip() or None,
    }
    # Incluir avisos de estrutura do PDF nos metadados para rastreabilidade
    if pdf_warnings:
        metadata["pdf_warnings"] = pdf_warnings
    return full, num_pages, page_texts, metadata


def extract_text_from_pdf(
    pdf_path: Path,
    *,
    mode: ExtractMode = "text",
    password: str | None = None,
    normalize: bool = True,
) -> tuple[str, int, list[str], dict]:
    """
    Extrai texto do PDF.
    Retorna (texto_completo, num_pages, lista_texto_por_página, metadata_dict).
    mode: "text" (padrão), "blocks" (blocos em ordem), "dict" (estrutura detalhada).
    metadata_dict: title, author, creation_date, etc.
    """
    pdf_path = Path(pdf_path).resolve()
    doc = fitz.open(pdf_path)
    try:
        if doc.is_encrypted:
            if not password:
                raise ValueError("PDF protegido por senha — forneça --password ou PDF_PASSWORD.")
            auth_result = doc.authenticate(password)
            if not auth_result:
                raise ValueError("Senha incorreta ou PDF não pôde ser desencriptado.")
        return extract_text_from_doc(doc, mode=mode, normalize=normalize)
    finally:
        doc.close()


def extract_text_from_pdf_partial(
    pdf_path: Path,
    *,
    mode: ExtractMode = "text",
    password: str | None = None,
    normalize: bool = True,
) -> tuple[str, int, list[str], dict, list[int]]:
    """
    Versão tolerante a falhas de extract_text_from_pdf.
    Extrai texto página a página, ignorando páginas com erro em vez de abortar.
    Útil para PDFs parcialmente corrompidos (páginas truncadas, fontes inválidas, etc.).

    Retorna (texto_completo, num_pages, textos_por_página, metadata, páginas_com_erro).
    - páginas_com_erro: lista de números de página (1-based) que falharam.
    - Se todas as páginas falharem, retorna texto vazio mas não lança exceção.
    """
    pdf_path = Path(pdf_path).resolve()
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        raise ValueError(f"Não foi possível abrir o PDF: {e}") from e

    failed_pages: list[int] = []
    try:
        if doc.is_encrypted and password:
            try:
                doc.authenticate(password)
            except Exception:
                pass

        # Tentar obter número de páginas — PDFs muito corrompidos podem falhar aqui
        try:
            num_pages = len(doc)
        except Exception:
            num_pages = 0

        # Metadados (melhor esforço)
        meta: dict = {}
        try:
            meta = doc.metadata or {}
        except Exception:
            pass

        page_texts: list[str] = []
        for i in range(num_pages):
            try:
                page = doc[i]
                if mode == "text":
                    t = page.get_text()
                elif mode == "blocks":
                    blocks = page.get_text("blocks")
                    t = "\n".join(b[4] for b in blocks if len(b) > 4)
                else:  # dict
                    d = page.get_text("dict")
                    parts: list[str] = []
                    for block in d.get("blocks", []):
                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                parts.append(span.get("text", ""))
                    t = "\n".join(parts)
                if normalize:
                    t = normalize_text(t)
                page_texts.append(t)
            except Exception:
                failed_pages.append(i + 1)
                page_texts.append("")  # placeholder para manter índices

        metadata = {
            "title": (meta.get("title") or "").strip() or None,
            "author": (meta.get("author") or "").strip() or None,
            "subject": (meta.get("subject") or "").strip() or None,
            "creation_date": format_pdf_date(meta.get("creationDate"))
            or (meta.get("creationDate") or "").strip()
            or None,
            "mod_date": format_pdf_date(meta.get("modDate"))
            or (meta.get("modDate") or "").strip()
            or None,
            "producer": (meta.get("producer") or "").strip() or None,
            "creator": (meta.get("creator") or "").strip() or None,
            "keywords": (meta.get("keywords") or "").strip() or None,
        }

        valid_texts = [t for t in page_texts if t.strip()]
        full = "\n\n".join(valid_texts)
        if normalize and full:
            full = normalize_text(full)

        return full, num_pages, page_texts, metadata, failed_pages
    finally:
        doc.close()
