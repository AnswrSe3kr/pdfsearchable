"""
Detecção de redacções e zonas ocultas em PDFs via PyMuPDF.

Detecta quatro categorias de ocultação de conteúdo:
- Rectângulos negros (cobertura física de texto com formas preenchidas a preto).
- Texto invisível (cor branca ou flag de invisibilidade sobre fundo claro/escuro).
- Anotações /Redact (anotações PDF do tipo redacção, tipo 12 na spec PDF).
- Camadas OCG ocultas (Optional Content Groups com estado "off").
"""

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

_log = logging.getLogger("pdfsearchable.redaction")

# Limiar de área mínima para considerar um rectângulo como redacção (px²).
# Valores menores são provavelmente artefactos de rendering, não redacções.
_MIN_RECT_AREA_PX2 = 500

# Componentes RGB próximos de preto para detectar rectângulos de cobertura.
# Tolerância de 0.05 por canal (0–1) para lidar com conversões de espaço de cor.
_BLACK_TOLERANCE = 0.05


# ---------------------------------------------------------------------------
# Dataclass de resultado
# ---------------------------------------------------------------------------


@dataclass
class RedactionReport:
    """
    Resultado da análise de redacções de um PDF.

    Atributos:
        file_id: identificador do documento (sha256[:16] do caminho absoluto).
        pdf_path: caminho absoluto do PDF como string.
        pages: lista de dicts por página com contagens de cada técnica detectada.
            Cada dict: {"page": int, "black_rects": int,
                        "invisible_text_chars": int, "redact_annots": int,
                        "ocg_hidden_layers": list[str]}
        total_redacted_zones: soma total de zonas detectadas em todo o documento.
        has_redactions: True se total_redacted_zones > 0.
        suspicious: True se texto invisível ou anotações /Redact forem detectados
            (indicadores de redacção intencional versus cobertura física acidental).
        summary: frase legível resumindo os resultados.
    """

    file_id: str
    pdf_path: str
    pages: list[dict] = field(default_factory=list)
    total_redacted_zones: int = 0
    has_redactions: bool = False
    suspicious: bool = False
    summary: str = ""


# ---------------------------------------------------------------------------
# Funções auxiliares privadas
# ---------------------------------------------------------------------------


def _compute_file_id(pdf_path: Path) -> str:
    """Calcula file_id como sha256(caminho_absoluto)[:16] hex — igual ao padrão do projecto."""
    return hashlib.sha256(str(pdf_path.resolve()).encode()).hexdigest()[:16]


def _is_near_black(color: tuple | None) -> bool:
    """
    Verifica se uma cor RGB (0–1 por canal) é próxima de preto.
    Aceita tuplos de 1 elemento (greyscale), 3 (RGB) ou 4 (CMYK/RGBA).
    Retorna False para None ou formatos desconhecidos.
    """
    if color is None:
        return False
    try:
        if len(color) == 1:
            # Greyscale: 0 = preto
            return color[0] <= _BLACK_TOLERANCE
        if len(color) >= 3:
            r, g, b = color[0], color[1], color[2]
            return r <= _BLACK_TOLERANCE and g <= _BLACK_TOLERANCE and b <= _BLACK_TOLERANCE
    except (TypeError, IndexError):
        pass
    return False


def _detect_black_rects(page: fitz.Page) -> int:
    """
    Conta rectângulos/paths preenchidos a preto com área >= _MIN_RECT_AREA_PX2.
    Usa page.get_drawings() que devolve primitivas vectoriais da página.
    """
    count = 0
    try:
        drawings = page.get_drawings()
    except Exception as exc:
        _log.debug("_detect_black_rects: get_drawings falhou na página %d: %s", page.number + 1, exc)
        return 0

    for item in drawings:
        fill = item.get("fill")
        if not _is_near_black(fill):
            continue
        rect = item.get("rect")
        if rect is None:
            continue
        try:
            # fitz.Rect.get_area() devolve área em unidades PDF (pt²); aproximação px² suficiente
            area = abs((rect[2] - rect[0]) * (rect[3] - rect[1]))
            if area >= _MIN_RECT_AREA_PX2:
                count += 1
        except (TypeError, IndexError):
            continue

    return count


def _detect_invisible_text(page: fitz.Page) -> int:
    """
    Conta caracteres de texto invisível na página.

    Critérios:
    - Cor do texto == 16777215 (0xFFFFFF branco em inteiro BGR PyMuPDF).
    - Cor do texto == 0 (preto) com bit de rendering mode 3 (invisible) em flags.
    - Rendering mode 3 (invisible) via campo 'color_space' / 'colorspace' == 3
      (PDF text render mode 3 = invisible, independente da cor).

    Usa rawdict para acesso a spans individuais e seus atributos de cor.
    """
    count = 0
    try:
        raw = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    except Exception as exc:
        _log.debug("_detect_invisible_text: get_text falhou na página %d: %s", page.number + 1, exc)
        return 0

    for block in raw.get("blocks", []):
        if block.get("type") != 0:  # type 0 = texto
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                # Cor do texto em PyMuPDF rawdict: inteiro sRGB 0xRRGGBB
                color = span.get("color", -1)
                # Branco (0xFFFFFF = 16777215) sobre qualquer fundo
                if color == 16777215:
                    text = span.get("text", "")
                    count += len(text)
                    continue
                # Rendering mode 3 = invisible (PDF spec Table 106)
                # PyMuPDF expõe como campo 'flags' bit 0x4 ou como campo separado
                flags = span.get("flags", 0)
                # Bit 6 (0x40) em flags de span PyMuPDF indica modo de rendering invisible
                # Texto com cor 0 (preto) e flags indicando invisibilidade
                if color == 0 and (flags & 0x40):
                    text = span.get("text", "")
                    count += len(text)

    return count


def _detect_redact_annots(page: fitz.Page) -> int:
    """
    Conta anotações do tipo /Redact (tipo 12 na especificação PDF ISO 32000).
    Estas anotações são colocadas por ferramentas de redacção (Acrobat, etc.)
    e podem ainda não ter sido "aplicadas" (flatten).
    """
    count = 0
    try:
        annot = page.first_annot
        while annot:
            # annot.type é tuplo (int, str); int 12 = /Redact segundo PDF spec
            if annot.type[0] == 12:
                count += 1
            annot = annot.next
    except Exception as exc:
        _log.debug("_detect_redact_annots: erro na página %d: %s", page.number + 1, exc)
    return count


def _detect_ocg_hidden_layers(doc: fitz.Document) -> list[str]:
    """
    Detecta camadas OCG (Optional Content Groups) com estado desligado ("off").
    Retorna lista de nomes das camadas ocultas.

    Usa doc.get_layers() que devolve lista de dicts com chaves 'name' e 'on'.
    doc.get_layer_config() devolve a configuração activa do documento.
    """
    hidden: list[str] = []
    try:
        layers = doc.get_layers()
        if not layers:
            return []
        for layer in layers:
            name = layer.get("name", "")
            on = layer.get("on", True)
            if not on and name:
                hidden.append(name)
    except Exception as exc:
        _log.debug("_detect_ocg_hidden_layers: erro ao ler camadas OCG: %s", exc)
    return hidden


def _build_summary(
    pages: list[dict],
    total_zones: int,
    suspicious: bool,
) -> str:
    """
    Constrói uma frase legível resumindo os resultados da análise.
    Ex.: "3 zonas negras em 2 páginas; texto invisível detectado"
    """
    if total_zones == 0:
        return "sem redacções detectadas"

    parts: list[str] = []

    total_black = sum(p["black_rects"] for p in pages)
    pages_with_black = sum(1 for p in pages if p["black_rects"] > 0)
    if total_black > 0:
        plural_zonas = "zona negra" if total_black == 1 else "zonas negras"
        plural_pags = "página" if pages_with_black == 1 else "páginas"
        parts.append(f"{total_black} {plural_zonas} em {pages_with_black} {plural_pags}")

    total_invis = sum(p["invisible_text_chars"] for p in pages)
    if total_invis > 0:
        parts.append("texto invisível detectado")

    total_annots = sum(p["redact_annots"] for p in pages)
    if total_annots > 0:
        plural = "anotação /Redact" if total_annots == 1 else "anotações /Redact"
        parts.append(f"{total_annots} {plural}")

    all_hidden: list[str] = []
    for p in pages:
        all_hidden.extend(p.get("ocg_hidden_layers", []))
    unique_hidden = list(dict.fromkeys(all_hidden))  # preservar ordem, remover duplicados
    if unique_hidden:
        nomes = ", ".join(f'"{n}"' for n in unique_hidden[:3])
        sufixo = f" (+{len(unique_hidden) - 3} mais)" if len(unique_hidden) > 3 else ""
        parts.append(f"camada(s) OCG oculta(s): {nomes}{sufixo}")

    return "; ".join(parts) if parts else "sem redacções detectadas"


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def detect_redactions(
    pdf_path: Path,
    *,
    password: str | None = None,
) -> RedactionReport:
    """
    Analisa um PDF em busca de redacções e zonas ocultas.

    Técnicas de detecção:
    - Rectângulos negros com área >= 500 px² (cobertura física).
    - Texto invisível (cor branca ou rendering mode invisible).
    - Anotações /Redact (tipo 12, PDF spec ISO 32000).
    - Camadas OCG com estado "off" (Optional Content Groups ocultos).

    Parâmetros:
        pdf_path: caminho para o arquivo PDF.
        password: senha para PDFs protegidos (opcional).

    Retorna:
        RedactionReport com resultados da análise. Em caso de erro ao abrir
        o PDF, retorna report com has_redactions=False e summary="erro ao analisar PDF".
    """
    pdf_path = Path(pdf_path).resolve()
    file_id = _compute_file_id(pdf_path)

    # Suprimir mensagens MuPDF para stderr — padrão do projecto (ver pdf_processor.py)
    fitz.TOOLS.mupdf_display_errors(False)

    # Tentar abrir o PDF
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        _log.warning("detect_redactions: não foi possível abrir '%s': %s", pdf_path, exc)
        return RedactionReport(
            file_id=file_id,
            pdf_path=str(pdf_path),
            pages=[],
            total_redacted_zones=0,
            has_redactions=False,
            suspicious=False,
            summary="erro ao analisar PDF",
        )

    try:
        # Autenticar se necessário
        if doc.is_encrypted:
            if not password:
                _log.warning(
                    "detect_redactions: PDF '%s' protegido por senha — sem password fornecida.",
                    pdf_path,
                )
                return RedactionReport(
                    file_id=file_id,
                    pdf_path=str(pdf_path),
                    pages=[],
                    total_redacted_zones=0,
                    has_redactions=False,
                    suspicious=False,
                    summary="erro ao analisar PDF",
                )
            if not doc.authenticate(password):
                _log.warning("detect_redactions: senha incorrecta para '%s'.", pdf_path)
                return RedactionReport(
                    file_id=file_id,
                    pdf_path=str(pdf_path),
                    pages=[],
                    total_redacted_zones=0,
                    has_redactions=False,
                    suspicious=False,
                    summary="erro ao analisar PDF",
                )

        # Detectar camadas OCG ocultas (ao nível do documento, não por página)
        ocg_hidden = _detect_ocg_hidden_layers(doc)

        pages_data: list[dict] = []
        total_invis_chars = 0
        total_redact_annots = 0

        for page_idx in range(len(doc)):
            try:
                page = doc[page_idx]
            except Exception as exc:
                _log.debug(
                    "detect_redactions: erro ao aceder página %d de '%s': %s",
                    page_idx + 1,
                    pdf_path,
                    exc,
                )
                pages_data.append(
                    {
                        "page": page_idx + 1,
                        "black_rects": 0,
                        "invisible_text_chars": 0,
                        "redact_annots": 0,
                        "ocg_hidden_layers": [],
                    }
                )
                continue

            black_rects = _detect_black_rects(page)
            invis_chars = _detect_invisible_text(page)
            redact_annots = _detect_redact_annots(page)

            total_invis_chars += invis_chars
            total_redact_annots += redact_annots

            pages_data.append(
                {
                    "page": page_idx + 1,
                    "black_rects": black_rects,
                    "invisible_text_chars": invis_chars,
                    "redact_annots": redact_annots,
                    # Camadas OCG são ao nível do documento; associar a cada página
                    # para conformidade com o schema do dataclass.
                    "ocg_hidden_layers": list(ocg_hidden),
                }
            )

        # Calcular totais
        total_black = sum(p["black_rects"] for p in pages_data)
        total_zones = total_black + total_invis_chars + total_redact_annots + len(ocg_hidden)
        has_redactions = total_zones > 0
        # Suspeito = texto invisível ou anotações /Redact (indicadores intencionais)
        suspicious = total_invis_chars > 0 or total_redact_annots > 0

        summary = _build_summary(pages_data, total_zones, suspicious)

        _log.info(
            "detect_redactions: '%s' — zonas=%d, suspeito=%s",
            pdf_path.name,
            total_zones,
            suspicious,
        )

        return RedactionReport(
            file_id=file_id,
            pdf_path=str(pdf_path),
            pages=pages_data,
            total_redacted_zones=total_zones,
            has_redactions=has_redactions,
            suspicious=suspicious,
            summary=summary,
        )

    except Exception as exc:
        _log.error(
            "detect_redactions: erro inesperado ao analisar '%s': %s",
            pdf_path,
            exc,
            exc_info=True,
        )
        return RedactionReport(
            file_id=file_id,
            pdf_path=str(pdf_path),
            pages=[],
            total_redacted_zones=0,
            has_redactions=False,
            suspicious=False,
            summary="erro ao analisar PDF",
        )
    finally:
        try:
            doc.close()
        except Exception:
            pass
