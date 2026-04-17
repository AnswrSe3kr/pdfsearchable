"""
Extracção de tabelas de PDFs para CSV/JSON.

Backends:
- PyMuPDF (sempre activo): page.find_tables() → ExtractedTable
- img2table (opcional, guarded): fallback para páginas sem tabelas nativas

Requer PyMuPDF (fitz). img2table é opcional: pip install -e ".[tables-ocr]"
"""

import csv
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import fitz  # PyMuPDF

# Suprimir mensagens de erro/aviso do MuPDF para o stderr do processo
# (ver pdf_processor.py como referência).
fitz.TOOLS.mupdf_display_errors(False)

_log = logging.getLogger("pdfsearchable.table_extractor")

# Guard de importação para img2table (extra opcional [tables-ocr])
try:
    from img2table.document import PDF as Img2TablePDF  # noqa: F401
    from img2table.ocr import TesseractOCR  # noqa: F401

    _IMG2TABLE_AVAILABLE = True
except ImportError:
    _IMG2TABLE_AVAILABLE = False

# Controla se o aviso de indisponibilidade do img2table já foi emitido
# (para não repetir em cada página).
_img2table_warned: bool = False


@dataclass
class ExtractedTable:
    """Representa uma tabela extraída de um PDF."""

    page: int
    """Número de página (1-based)."""

    table_index: int
    """Índice da tabela na página (0-based)."""

    headers: list[str]
    """Linha de cabeçalho da tabela."""

    rows: list[list[str]]
    """Linhas de dados (sem o cabeçalho)."""

    bbox: tuple[float, float, float, float]
    """Bounding box (x0, y0, x1, y1) em pontos PDF."""

    confidence: float
    """Confiança da extracção: 1.0 para PyMuPDF nativo; <1.0 para img2table."""

    source: str
    """Backend utilizado: "pymupdf" | "img2table"."""


def _cell_str(value: object) -> str:
    """Converte um valor de célula para string, tratando None como string vazia."""
    if value is None:
        return ""
    return str(value).strip()


def _tables_from_page_pymupdf(page: "fitz.Page") -> list[ExtractedTable]:
    """
    Extrai tabelas de uma página usando PyMuPDF page.find_tables().

    Os cabeçalhos são retirados da primeira linha quando table.header.external == True;
    caso contrário, gera cabeçalhos genéricos "Col1", "Col2", ...
    """
    tables: list[ExtractedTable] = []
    try:
        finder = page.find_tables()
    except Exception as exc:
        _log.debug("find_tables() falhou na página %d: %s", page.number + 1, exc)
        return tables

    for idx, tab in enumerate(finder.tables):
        try:
            cells = tab.extract()
        except Exception as exc:
            _log.debug(
                "Falha ao extrair células da tabela %d, página %d: %s",
                idx,
                page.number + 1,
                exc,
            )
            continue

        if not cells:
            continue

        # Determinar cabeçalhos
        try:
            has_external_header = getattr(tab.header, "external", False)
        except Exception:
            has_external_header = False

        if has_external_header and len(cells) > 0:
            headers = [_cell_str(c) for c in cells[0]]
            data_rows = cells[1:]
        else:
            # Cabeçalhos genéricos baseados no número de colunas da primeira linha
            ncols = len(cells[0]) if cells else 0
            headers = [f"Col{i + 1}" for i in range(ncols)]
            data_rows = cells

        rows = [[_cell_str(c) for c in row] for row in data_rows]

        # Bounding box da tabela
        try:
            r = tab.bbox
            bbox: tuple[float, float, float, float] = (
                float(r[0]),
                float(r[1]),
                float(r[2]),
                float(r[3]),
            )
        except Exception:
            bbox = (0.0, 0.0, 0.0, 0.0)

        tables.append(
            ExtractedTable(
                page=page.number + 1,
                table_index=idx,
                headers=headers,
                rows=rows,
                bbox=bbox,
                confidence=1.0,
                source="pymupdf",
            )
        )

    return tables


def _tables_from_page_img2table(
    page: "fitz.Page",
    page_number_1based: int,
    start_table_index: int,
) -> list[ExtractedTable]:
    """
    Extrai tabelas de uma página usando img2table como fallback.

    Renderiza a página como imagem PNG e passa ao img2table com TesseractOCR.
    Retorna lista de ExtractedTable com confidence < 1.0 e source="img2table".

    Deve ser chamado apenas quando _IMG2TABLE_AVAILABLE == True.
    """
    global _img2table_warned

    if not _IMG2TABLE_AVAILABLE:
        if not _img2table_warned:
            _log.warning(
                "img2table não está disponível. Instale com: pip install -e '.[tables-ocr]'"
            )
            _img2table_warned = True
        return []

    tables: list[ExtractedTable] = []
    try:
        import io
        from img2table.document import Image as Img2Image
        from img2table.ocr import TesseractOCR as _TesseractOCR

        # Renderizar página como imagem PNG a 150 DPI (balanço velocidade/qualidade)
        mat = fitz.Matrix(150 / 72, 150 / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")

        try:
            from pdfsearchable.ocr import get_ocr_lang

            lang = get_ocr_lang().split("+")[0]
        except Exception:
            lang = "por"

        ocr = _TesseractOCR(n_threads=1, lang=lang)
        doc_img = Img2Image(src=io.BytesIO(img_bytes))
        extracted = doc_img.extract_tables(ocr=ocr, implicit_rows=True, borderless_tables=False)

        for rel_idx, tab in enumerate(extracted or []):
            try:
                df = getattr(tab, "df", None)
                if df is None or df.empty:
                    continue

                col_values = list(df.columns)
                headers = [str(c).strip() for c in col_values]

                rows: list[list[str]] = []
                for _, row_data in df.iterrows():
                    rows.append([str(v).strip() for v in row_data.values])

                # img2table não expõe bbox em coordenadas PDF de forma fiável;
                # usar bbox da tabela em pixels se disponível, senão (0,0,0,0)
                try:
                    tb = tab.bbox  # type: ignore[attr-defined]
                    bbox: tuple[float, float, float, float] = (
                        float(tb.x1),
                        float(tb.y1),
                        float(tb.x2),
                        float(tb.y2),
                    )
                except Exception:
                    bbox = (0.0, 0.0, 0.0, 0.0)

                tables.append(
                    ExtractedTable(
                        page=page_number_1based,
                        table_index=start_table_index + rel_idx,
                        headers=headers,
                        rows=rows,
                        bbox=bbox,
                        confidence=0.7,
                        source="img2table",
                    )
                )
            except Exception as exc:
                _log.debug(
                    "Falha ao converter tabela img2table %d, página %d: %s",
                    rel_idx,
                    page_number_1based,
                    exc,
                )
    except Exception as exc:
        _log.debug("img2table falhou na página %d: %s", page_number_1based, exc)

    return tables


def extract_tables(
    pdf_path: Path,
    *,
    password: str | None = None,
    pages: list[int] | None = None,
    use_img2table: bool = False,
    min_rows: int = 2,
    min_cols: int = 2,
) -> list[ExtractedTable]:
    """
    Extrai tabelas de um PDF.

    Usa PyMuPDF como backend primário (sempre activo). Se use_img2table=True,
    tenta img2table como fallback nas páginas onde PyMuPDF não encontrou tabelas.

    Parâmetros
    ----------
    pdf_path:       Caminho para o arquivo PDF.
    password:       Senha de desencriptação (opcional).
    pages:          Lista de números de página a processar (1-based). None = todas.
    use_img2table:  Activar img2table como fallback (requer extra [tables-ocr]).
    min_rows:       Número mínimo de linhas de dados para incluir a tabela.
    min_cols:       Número mínimo de colunas para incluir a tabela.

    Retorna
    -------
    Lista de ExtractedTable ordenada por página e índice.
    """
    global _img2table_warned

    pdf_path = Path(pdf_path).resolve()

    # Aviso antecipado se use_img2table=True mas backend não disponível
    if use_img2table and not _IMG2TABLE_AVAILABLE and not _img2table_warned:
        _log.warning(
            "use_img2table=True mas img2table não está instalado. "
            "Instale com: pip install -e '.[tables-ocr]'"
        )
        _img2table_warned = True

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        _log.error("Não foi possível abrir o PDF %s: %s", pdf_path, exc)
        return []

    try:
        if doc.is_encrypted:
            if password:
                if not doc.authenticate(password):
                    _log.error("Senha incorrecta para o PDF %s", pdf_path)
                    return []
            else:
                _log.error("PDF %s está encriptado mas nenhuma senha foi fornecida.", pdf_path)
                return []

        total_pages = len(doc)

        # Determinar páginas a processar (converter de 1-based para 0-based)
        if pages is not None:
            page_indices = [p - 1 for p in pages if 1 <= p <= total_pages]
        else:
            page_indices = list(range(total_pages))

        all_tables: list[ExtractedTable] = []

        for page_idx in page_indices:
            page = doc[page_idx]
            page_num = page_idx + 1  # 1-based

            # Backend primário: PyMuPDF
            pymupdf_tables = _tables_from_page_pymupdf(page)

            # Filtrar por min_rows e min_cols
            pymupdf_tables = [
                t for t in pymupdf_tables if len(t.rows) >= min_rows and len(t.headers) >= min_cols
            ]
            all_tables.extend(pymupdf_tables)

            # Backend de fallback: img2table (apenas se PyMuPDF não encontrou tabelas)
            if use_img2table and not pymupdf_tables:
                img2_tables = _tables_from_page_img2table(
                    page,
                    page_number_1based=page_num,
                    start_table_index=0,
                )
                img2_tables = [
                    t for t in img2_tables if len(t.rows) >= min_rows and len(t.headers) >= min_cols
                ]
                all_tables.extend(img2_tables)

        return all_tables

    finally:
        doc.close()


def tables_to_csv(
    tables: list[ExtractedTable],
    output_dir: Path,
    stem: str,
) -> list[Path]:
    """
    Grava cada tabela como arquivo CSV em output_dir.

    Convenção de nome: <stem>_pNN_tMM.csv  (NN = página com zero-padding, MM = índice).
    Encoding: UTF-8 com BOM (utf-8-sig) para compatibilidade com Microsoft Excel.

    Retorna lista dos paths criados.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []

    for table in tables:
        filename = f"{stem}_p{table.page:02d}_t{table.table_index:02d}.csv"
        out_path = output_dir / filename

        try:
            with open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(table.headers)
                writer.writerows(table.rows)
            created.append(out_path)
            _log.debug("CSV gravado: %s (%d linhas)", out_path.name, len(table.rows))
        except OSError as exc:
            _log.error("Falha ao gravar CSV %s: %s", out_path, exc)

    return created


def tables_to_json(
    tables: list[ExtractedTable],
    output_dir: Path,
    stem: str,
) -> Path:
    """
    Grava todas as tabelas como um único arquivo JSON em output_dir.

    Nome do arquivo: <stem>_tables.json
    Formato:
      {
        "version": 1,
        "pdf_path": "<stem>",
        "tables": [...]
      }

    Retorna o path do arquivo criado.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    out_path = output_dir / f"{stem}_tables.json"

    payload: dict = {
        "version": 1,
        "pdf_path": stem,
        "tables": [],
    }

    for table in tables:
        d = asdict(table)
        # bbox é tuple → converter para lista para serialização JSON standard
        d["bbox"] = list(d["bbox"])
        payload["tables"].append(d)

    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        _log.debug("JSON de tabelas gravado: %s (%d tabelas)", out_path.name, len(tables))
    except OSError as exc:
        _log.error("Falha ao gravar JSON de tabelas %s: %s", out_path, exc)

    return out_path
