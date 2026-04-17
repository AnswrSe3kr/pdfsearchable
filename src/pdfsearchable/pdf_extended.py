"""
Extração estendida de PDF: tabelas, formulários, anotações, metadados XMP,
imagens embutidas e extração de PDFs de portfolios/packages.
Integrado ao indexador para capturar o máximo de dados dos arquivos PDF.

Para PDFs escaneados (imagem), se img2table estiver disponível (extra [tables-ocr]),
tenta extrair tabelas diretamente da imagem renderizada da página.
"""

from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

from pdfsearchable.audit import get_logger as _get_logger

_log = _get_logger("pdfsearchable.pdf_extended")


def _extract_tables_from_page(page: "fitz.Page") -> list[dict[str, Any]]:
    """Extrai tabelas de uma página via find_tables(). Retorna lista de dicts com texto."""
    tables: list[dict[str, Any]] = []
    try:
        finder = page.find_tables()
        for i, tab in enumerate(finder.tables):
            try:
                cells = tab.extract()
                if cells:
                    rows_text = [" | ".join(str(c) for c in row) for row in cells]
                    full_text = "\n".join(rows_text)
                    tables.append(
                        {"page": page.number + 1, "index": i, "cells": cells, "text": full_text}
                    )
            except Exception as _e:
                _log.debug("Falha ao extrair tabela %d da página %d: %s", i, page.number + 1, _e)
                continue
    except Exception as _e:
        _log.debug("find_tables() falhou na página %d: %s", page.number + 1, _e)
    return tables


def _extract_form_fields_from_page(page: "fitz.Page") -> list[dict[str, Any]]:
    """Extrai campos de formulário (AcroForm) preenchidos da página."""
    fields: list[dict[str, Any]] = []
    try:
        widget = page.first_widget()
        while widget is not None:
            try:
                name = getattr(widget, "field_name", None) or ""
                value = getattr(widget, "field_value", None)
                if name or value is not None:
                    fields.append(
                        {"name": str(name), "value": str(value) if value is not None else ""}
                    )
            except Exception as _e:
                _log.debug("Falha ao extrair campo de formulário: %s", _e)
            widget = getattr(widget, "next", None)
    except Exception as _e:
        _log.debug("_extract_form_fields_from_page falhou na página: %s", _e)
    return fields


def _extract_annotations_from_page(page: "fitz.Page") -> list[dict[str, Any]]:
    """
    Extrai anotações/comentários da página com campos enriquecidos:
    - content: texto da anotação (até 1000 chars)
    - type: tipo da anotação (Text, Highlight, Stamp, FreeText, etc.)
    - author: autor/criador da anotação
    - date: data de criação ou modificação (formato DD/MM/AAAA HH:MM)
    - rect: posição [x0, y0, x1, y1] na página
    """
    from pdfsearchable.pdf_processor import format_pdf_date

    anns: list[dict[str, Any]] = []
    try:
        annot = page.first_annot()
        while annot is not None:
            try:
                ann_dict: dict[str, Any] = {}

                # Conteúdo textual
                content = ""
                if hasattr(annot, "get_text") and callable(annot.get_text):
                    try:
                        content = (annot.get_text() or "").strip()
                    except Exception as _e:
                        _log.debug("annot.get_text() falhou: %s", _e)
                if not content and hasattr(annot, "info") and isinstance(annot.info, dict):
                    content = (annot.info.get("content", "") or "").strip()
                if content:
                    ann_dict["content"] = str(content)[:1000]

                # Tipo da anotação (ex.: Text, Highlight, Stamp, FreeText, Square, …)
                if hasattr(annot, "type"):
                    try:
                        ann_type = annot.type
                        if isinstance(ann_type, (list, tuple)) and len(ann_type) > 1:
                            ann_dict["type"] = str(ann_type[1])
                        else:
                            ann_dict["type"] = str(ann_type)
                    except Exception as _e:
                        _log.debug("Falha ao extrair tipo de anotação: %s", _e)

                # Autor e datas (campo info do PDF)
                if hasattr(annot, "info") and isinstance(annot.info, dict):
                    author = (
                        annot.info.get("title") or annot.info.get("author") or ""
                    ).strip()
                    if author:
                        ann_dict["author"] = author[:100]
                    for date_key in ("creationDate", "modDate"):
                        date_raw = (annot.info.get(date_key) or "").strip()
                        if date_raw:
                            formatted = format_pdf_date(date_raw)
                            if formatted:
                                ann_dict["date"] = formatted
                                break

                # Posição na página [x0, y0, x1, y1]
                if hasattr(annot, "rect"):
                    try:
                        r = annot.rect
                        ann_dict["rect"] = [
                            round(r.x0, 1), round(r.y0, 1),
                            round(r.x1, 1), round(r.y1, 1),
                        ]
                    except Exception as _e:
                        _log.debug("Falha ao extrair rect de anotação: %s", _e)

                # Só registar se tiver pelo menos conteúdo ou tipo
                if ann_dict.get("content") or ann_dict.get("type"):
                    anns.append(ann_dict)
            except Exception as _e:
                _log.debug("Falha ao processar anotação na página %d: %s", page.number + 1, _e)
            annot = getattr(annot, "next", None)
    except Exception as _e:
        _log.debug("_extract_annotations_from_page falhou na página %d: %s", page.number + 1, _e)
    return anns


def _extract_images_from_page(page: "fitz.Page") -> list[dict[str, Any]]:
    """
    Extrai metadados das imagens embutidas na página:
    - width, height: dimensões em pixels
    - colorspace: espaço de cor (RGB, CMYK, Grayscale, etc.)
    - bpc: bits por componente
    - bbox: posição [x0, y0, x1, y1] na página (coordenadas PDF)
    Não extrai pixels — apenas metadados para indexação.
    """
    images: list[dict[str, Any]] = []
    try:
        for img_info in page.get_image_info(xrefs=False):
            w = img_info.get("width")
            h = img_info.get("height")
            if not w or not h:
                continue
            entry: dict[str, Any] = {"width": int(w), "height": int(h)}
            cs = img_info.get("colorspace")
            if cs:
                entry["colorspace"] = str(cs)
            bpc = img_info.get("bpc")
            if bpc:
                entry["bpc"] = int(bpc)
            bbox = img_info.get("bbox")
            if bbox:
                entry["bbox"] = [round(float(v), 1) for v in bbox]
            images.append(entry)
    except Exception as _e:
        _log.debug("_extract_images_from_page falhou na página %d: %s", page.number + 1, _e)
    return images


def _extract_xmp_metadata(doc: "fitz.Document") -> dict[str, Any]:
    """Extrai metadados XMP adicionais quando disponíveis.

    Parseia o XML XMP e extrai campos comuns: creator, rights, description,
    subject (keywords), publisher, contributor, software, título alternativo.
    """
    xmp: dict[str, Any] = {}
    try:
        if hasattr(doc, "get_xml_metadata") and callable(doc.get_xml_metadata):
            xml_str = doc.get_xml_metadata()
            if xml_str and len(xml_str) < 200000:
                xmp["xml_length"] = len(xml_str)
                xmp["has_xmp"] = True
                # Parse XMP (best-effort, sem dependências externas)
                import re as _re
                def _first(pattern: str) -> str | None:
                    m = _re.search(pattern, xml_str, _re.IGNORECASE | _re.DOTALL)
                    return m.group(1).strip() if m else None
                for key, pat in (
                    ("dc_title", r"<dc:title>.*?<rdf:li[^>]*>(.*?)</rdf:li>"),
                    ("dc_creator", r"<dc:creator>.*?<rdf:li[^>]*>(.*?)</rdf:li>"),
                    ("dc_description", r"<dc:description>.*?<rdf:li[^>]*>(.*?)</rdf:li>"),
                    ("dc_publisher", r"<dc:publisher>.*?<rdf:li[^>]*>(.*?)</rdf:li>"),
                    ("dc_contributor", r"<dc:contributor>.*?<rdf:li[^>]*>(.*?)</rdf:li>"),
                    ("dc_rights", r"<dc:rights>.*?<rdf:li[^>]*>(.*?)</rdf:li>"),
                    ("xmp_creator_tool", r"<xmp:CreatorTool[^>]*>([^<]+)</xmp:CreatorTool>"),
                    ("xmp_create_date", r"<xmp:CreateDate[^>]*>([^<]+)</xmp:CreateDate>"),
                    ("xmp_modify_date", r"<xmp:ModifyDate[^>]*>([^<]+)</xmp:ModifyDate>"),
                    ("xmp_metadata_date", r"<xmp:MetadataDate[^>]*>([^<]+)</xmp:MetadataDate>"),
                    ("pdf_producer", r"<pdf:Producer[^>]*>([^<]+)</pdf:Producer>"),
                    ("pdf_keywords", r"<pdf:Keywords[^>]*>([^<]+)</pdf:Keywords>"),
                    ("pdfx_source", r"<pdfx:Source[^>]*>([^<]+)</pdfx:Source>"),
                ):
                    v = _first(pat)
                    if v:
                        # Limpa entities HTML comuns
                        v = v.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                        if len(v) < 500:
                            xmp[key] = v
                # dc:subject (array de keywords)
                subjects = _re.findall(
                    r"<dc:subject>.*?<rdf:li[^>]*>(.*?)</rdf:li>",
                    xml_str,
                    _re.IGNORECASE | _re.DOTALL,
                )
                if subjects:
                    xmp["dc_subject"] = [s.strip() for s in subjects if s.strip()][:20]
    except Exception as _e:
        _log.debug("_extract_xmp_metadata falhou: %s", _e)
    return xmp


def _extract_outline(doc: "fitz.Document", *, max_entries: int = 500) -> list[dict[str, Any]]:
    """Extrai bookmarks/outline (TOC) do PDF.

    Retorna lista de {level, title, page}. PyMuPDF retorna [level, title, page, ...].
    """
    out: list[dict[str, Any]] = []
    try:
        toc = doc.get_toc(simple=True) or []
        for entry in toc[:max_entries]:
            if not entry or len(entry) < 3:
                continue
            level, title, page = entry[0], entry[1], entry[2]
            if not isinstance(title, str):
                continue
            out.append(
                {
                    "level": int(level) if isinstance(level, (int, float)) else 1,
                    "title": title.strip()[:300],
                    "page": int(page) if isinstance(page, (int, float)) and page > 0 else 0,
                }
            )
    except Exception as _e:
        _log.debug("_extract_outline falhou: %s", _e)
    return out


def _extract_hyperlinks_from_page(page: "fitz.Page") -> list[dict[str, Any]]:
    """Extrai hiperlinks (URIs e GoTo internos) de uma página."""
    links: list[dict[str, Any]] = []
    try:
        for link in page.get_links() or []:
            kind = link.get("kind")
            entry: dict[str, Any] = {"page": page.number + 1}
            if kind == fitz.LINK_URI or link.get("uri"):
                entry["type"] = "uri"
                uri = link.get("uri") or ""
                if uri and len(uri) < 2000:
                    entry["uri"] = uri
                    links.append(entry)
            elif kind == fitz.LINK_GOTO:
                entry["type"] = "goto"
                tgt = link.get("page")
                if isinstance(tgt, int) and tgt >= 0:
                    entry["target_page"] = tgt + 1
                    links.append(entry)
    except Exception as _e:
        _log.debug("_extract_hyperlinks_from_page falhou na página %d: %s", page.number + 1, _e)
    return links


def _extract_page_dimensions(doc: "fitz.Document", *, max_pages: int = 2000) -> list[dict[str, Any]]:
    """Extrai dimensões (width, height, rotation) de cada página em pontos PDF.

    Útil para detecção de orientação mista, documentos grandes (A0/A1) ou
    anomalias de layout.
    """
    dims: list[dict[str, Any]] = []
    try:
        for i in range(min(len(doc), max_pages)):
            page = doc[i]
            rect = page.rect
            dims.append(
                {
                    "page": i + 1,
                    "width": round(rect.width, 1),
                    "height": round(rect.height, 1),
                    "rotation": int(page.rotation or 0),
                }
            )
    except Exception as _e:
        _log.debug("_extract_page_dimensions falhou: %s", _e)
    return dims


def _extract_attached_files(doc: "fitz.Document") -> list[dict[str, Any]]:
    """Lista metadados de arquivos anexados (EmbeddedFiles), sem conteúdo binário."""
    attached: list[dict[str, Any]] = []
    try:
        count = doc.embfile_count() if hasattr(doc, "embfile_count") else 0
        for i in range(count):
            try:
                info = doc.embfile_info(i)
                if info:
                    attached.append(
                        {
                            "name": str(info.get("filename", ""))[:300],
                            "size": int(info.get("size", 0)),
                            "description": str(info.get("desc", ""))[:300] if info.get("desc") else "",
                            "creation_date": str(info.get("creationDate", ""))[:40],
                            "mod_date": str(info.get("modDate", ""))[:40],
                        }
                    )
            except Exception as _e:
                _log.debug("embfile_info(%d) falhou: %s", i, _e)
                continue
    except Exception as _e:
        _log.debug("_extract_attached_files falhou: %s", _e)
    return attached


def _extract_fonts(doc: "fitz.Document", *, max_fonts: int = 50) -> list[dict[str, Any]]:
    """Lista fontes únicas usadas no documento (útil para forense/análise tipográfica)."""
    seen: set[tuple] = set()
    fonts: list[dict[str, Any]] = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            for f in page.get_fonts(full=False) or []:
                # f = (xref, ext, type, basefont, name, encoding)
                if len(f) < 5:
                    continue
                key = (f[3] or "", f[2] or "")
                if key in seen:
                    continue
                seen.add(key)
                fonts.append(
                    {
                        "basefont": str(f[3] or "")[:100],
                        "type": str(f[2] or "")[:40],
                        "encoding": str(f[5] or "")[:40] if len(f) > 5 else "",
                    }
                )
                if len(fonts) >= max_fonts:
                    return fonts
    except Exception as _e:
        _log.debug("_extract_fonts falhou: %s", _e)
    return fonts


# ── Tabelas em PDFs escaneados (img2table) ────────────────────────────────────


def _img2table_available() -> bool:
    """Verifica se img2table está disponível (extra [tables-ocr])."""
    try:
        import img2table  # noqa: F401

        return True
    except ImportError:
        return False


def _extract_tables_from_image_page(image_bytes: bytes, page_num: int) -> list[dict[str, Any]]:
    """
    Extrai tabelas de uma página escaneada (imagem) usando img2table.
    Usa Tesseract como OCR interno para reconhecer o conteúdo das células.
    Requer extra [tables-ocr]: pip install -e ".[tables-ocr]"
    Usa apenas o primeiro idioma de PDFSEARCHABLE_OCR_LANG para compatibilidade.
    Retorna lista de dicts com "page", "index" e "text".
    """
    if not _img2table_available():
        return []
    try:
        import io
        from img2table.ocr import TesseractOCR
        from img2table.document import Image as Img2Image
        from pdfsearchable.ocr import get_ocr_lang

        # img2table aceita apenas um idioma; usa o primeiro configurado
        lang = get_ocr_lang().split("+")[0]
        ocr = TesseractOCR(n_threads=1, lang=lang)
        doc = Img2Image(src=io.BytesIO(image_bytes))
        extracted = doc.extract_tables(ocr=ocr, implicit_rows=True, borderless_tables=False)
        result: list[dict[str, Any]] = []
        for table in extracted or []:
            try:
                df = getattr(table, "df", None)
                if df is None or df.empty:
                    continue
                rows: list[str] = []
                for _, row in df.iterrows():
                    cells = [str(v).strip() for v in row.values if str(v).strip()]
                    if cells:
                        rows.append(" | ".join(cells))
                text = "\n".join(rows)
                if text.strip():
                    result.append({"page": page_num, "index": len(result), "text": text})
            except Exception as _e:
                _log.debug("Falha ao processar tabela img2table na página %d: %s", page_num, _e)
                continue
        return result
    except Exception as _e:
        _log.debug("_extract_tables_from_image_page falhou na página %d: %s", page_num, _e)
        return []


def extract_extended_pdf_data(
    pdf_path: Path,
    *,
    password: str | None = None,
    include_tables: bool = True,
    include_forms: bool = True,
    include_annotations: bool = True,
    include_xmp: bool = True,
) -> dict[str, Any]:
    """
    Extrai dados estendidos do PDF: tabelas, formulários, anotações e XMP.
    Retorna dict com chaves: tables, form_fields, annotations, xmp.
    Abre e fecha o documento internamente; para evitar segunda abertura (lock MuPDF),
    use extract_extended_from_doc(doc) quando já tiver o documento aberto.
    """
    pdf_path = Path(pdf_path).resolve()
    doc = fitz.open(pdf_path)
    try:
        if doc.is_encrypted and password:
            doc.authenticate(password)
        return extract_extended_from_doc(
            doc,
            include_tables=include_tables,
            include_forms=include_forms,
            include_annotations=include_annotations,
            include_xmp=include_xmp,
        )
    finally:
        doc.close()


def extract_extended_from_doc(
    doc: "fitz.Document",
    *,
    include_tables: bool = True,
    include_forms: bool = True,
    include_annotations: bool = True,
    include_xmp: bool = True,
    include_images: bool = True,
    include_outline: bool = True,
    include_hyperlinks: bool = True,
    include_page_dims: bool = True,
    include_attached: bool = True,
    include_fonts: bool = True,
    max_annotations: int = 200,
    max_images: int = 100,
    max_hyperlinks: int = 500,
) -> dict[str, Any]:
    """
    Extrai dados estendidos de um documento PyMuPDF já aberto.
    Retorna dict com chaves: tables, form_fields, annotations, xmp, images.

    Parâmetros:
    - max_annotations: limite total de anotações (padrão 200, antes era 30)
    - max_images: limite total de metadados de imagens (padrão 100)
    - include_images: extrair metadados de imagens embutidas (True por padrão)

    Usado pelo indexador para evitar abrir o mesmo PDF duas vezes (reduz lock blocking).
    """
    result: dict[str, Any] = {
        "tables": [],
        "form_fields": [],
        "annotations": [],
        "images": [],
        "xmp": {},
        "outline": [],
        "hyperlinks": [],
        "page_dimensions": [],
        "attached_files": [],
        "fonts": [],
    }
    for i in range(len(doc)):
        page = doc[i]
        if include_tables:
            native_tables = _extract_tables_from_page(page)
            result["tables"].extend(native_tables)
            if not native_tables and _img2table_available():
                native_text = page.get_text().strip()
                if len(native_text) < 100:
                    try:
                        from pdfsearchable.ocr import render_page_to_image

                        img_bytes = render_page_to_image(page)
                        image_tables = _extract_tables_from_image_page(img_bytes, i + 1)
                        result["tables"].extend(image_tables)
                    except Exception as _e:
                        _log.debug("Falha ao extrair tabelas via imagem na página %d: %s", i + 1, _e)
        if include_forms:
            result["form_fields"].extend(_extract_form_fields_from_page(page))
        if include_annotations and len(result["annotations"]) < max_annotations:
            page_anns = _extract_annotations_from_page(page)
            remaining = max_annotations - len(result["annotations"])
            result["annotations"].extend(page_anns[:remaining])
        if include_images and len(result["images"]) < max_images:
            page_imgs = _extract_images_from_page(page)
            remaining = max_images - len(result["images"])
            result["images"].extend(page_imgs[:remaining])
        if include_hyperlinks and len(result["hyperlinks"]) < max_hyperlinks:
            page_links = _extract_hyperlinks_from_page(page)
            remaining = max_hyperlinks - len(result["hyperlinks"])
            result["hyperlinks"].extend(page_links[:remaining])
    if include_xmp:
        result["xmp"] = _extract_xmp_metadata(doc)
    if include_outline:
        result["outline"] = _extract_outline(doc)
    if include_page_dims:
        result["page_dimensions"] = _extract_page_dimensions(doc)
    if include_attached:
        result["attached_files"] = _extract_attached_files(doc)
    if include_fonts:
        result["fonts"] = _extract_fonts(doc)
    return result


def extract_embedded_pdfs(
    pdf_path: Path,
    *,
    password: str | None = None,
) -> list[tuple[str, bytes]]:
    """
    Extrai PDFs embutidos de um PDF Portfolio/Package (attachments via EmbeddedFiles).
    Retorna lista de (nome_arquivo, bytes_do_pdf).
    Retorna lista vazia se o PDF não contiver arquivos embutidos ou se nenhum for PDF.

    Suporte:
    - PDF portfolios (PDF 1.7+ com /Collection no catálogo)
    - PDF com anexos via EmbeddedFiles
    - Identifica PDFs pelo magic bytes %PDF- ou pela extensão .pdf
    """
    pdf_path = Path(pdf_path).resolve()
    results: list[tuple[str, bytes]] = []
    try:
        doc = fitz.open(pdf_path)
        try:
            if doc.is_encrypted and password:
                doc.authenticate(password)
            count = 0
            try:
                count = doc.embfile_count()
            except Exception as _e:
                _log.debug("embfile_count() falhou: %s", _e)
                return results
            if count <= 0:
                return results
            for i in range(count):
                try:
                    info = doc.embfile_info(i)
                    name = (
                        info.get("filename")
                        or info.get("ufilename")
                        or f"embedded_{i + 1}.pdf"
                    )
                    data = doc.embfile_get(i)
                    if not data:
                        continue
                    # Verificar se é PDF: magic bytes %PDF- ou extensão .pdf
                    is_pdf = data[:5] == b"%PDF-" or str(name).lower().endswith(".pdf")
                    if not is_pdf:
                        continue
                    if not str(name).lower().endswith(".pdf"):
                        name = f"{name}.pdf"
                    results.append((str(name), data))
                    _log.debug(
                        "PDF embutido extraído: %s (%d bytes)", name, len(data)
                    )
                except Exception as _e:
                    _log.debug("Falha ao extrair embedded file %d: %s", i, _e)
        finally:
            doc.close()
    except Exception as _e:
        _log.debug("extract_embedded_pdfs falhou em %s: %s", pdf_path.name, _e)
    return results


def is_pdf_portfolio(pdf_path: Path, *, password: str | None = None) -> bool:
    """
    Verifica se o PDF é um portfolio/package (contém arquivos embutidos).
    Retorna True se o documento tiver 1 ou mais arquivos embutidos.
    """
    pdf_path = Path(pdf_path).resolve()
    try:
        doc = fitz.open(pdf_path)
        try:
            if doc.is_encrypted and password:
                doc.authenticate(password)
            return doc.embfile_count() > 0
        finally:
            doc.close()
    except Exception as _e:
        _log.debug("is_pdf_portfolio falhou em %s: %s", pdf_path.name, _e)
        return False


def tables_to_searchable_text(tables: list[dict[str, Any]]) -> str:
    """Converte tabelas extraídas em texto pesquisável para indexação."""
    parts: list[str] = []
    for t in tables:
        text = t.get("text") or ""
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts) if parts else ""
