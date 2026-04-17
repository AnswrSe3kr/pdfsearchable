"""
Análise forense de PDFs: detecção de anomalias e sinais de adulteração.

Expõe `analyse_forensics(pdf_path, *, password)` que retorna um `ForensicsReport`
com lista de anomalias ponderadas, pontuação de risco e indicador de suspeita.
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Silenciar stderr do fitz antes de qualquer importação que o acione.
# Equivalente ao padrão em pdf_processor.py.
import fitz  # PyMuPDF

fitz.TOOLS.mupdf_display_errors(False)
fitz.TOOLS.mupdf_display_warnings(False)
if hasattr(fitz, "no_recommend_layout"):
    fitz.no_recommend_layout()

_log = logging.getLogger("pdfsearchable.forensics")

# ---------------------------------------------------------------------------
# Pesos por severidade e limiar de suspeita
# ---------------------------------------------------------------------------
_SEVERITY_WEIGHT: dict[str, int] = {"low": 5, "medium": 20, "high": 40}
_RISK_CAP = 100
_SUSPICIOUS_THRESHOLD = 40

# ---------------------------------------------------------------------------
# Pares Producer/Creator considerados inconsistentes
# (Creator → lista de Producers suspeitos quando combinados com esse Creator)
# ---------------------------------------------------------------------------
_SUSPECT_PAIRS: list[tuple[str, str]] = [
    # Documento criado no Word mas reprocessado por Ghostscript
    ("microsoft word", "ghostscript"),
    ("microsoft word", "gpl ghostscript"),
    ("microsoft excel", "ghostscript"),
    ("microsoft excel", "gpl ghostscript"),
    ("libreoffice", "ghostscript"),
    ("libreoffice", "gpl ghostscript"),
    # Criado por ferramenta da Adobe mas Producer de terceiros
    ("acrobat distiller", "ghostscript"),
    # Criado pelo Word mas Producer é o iTextSharp (frequentemente usado em adulterações)
    ("microsoft word", "itext"),
    ("microsoft word", "itextsharp"),
    # Criado por scanner mas Producer indica editor de texto
    ("scan", "microsoft"),
    ("scanner", "microsoft"),
]

# Substrings de nomes de fontes suspeitas em documentos declarados como escaneados
_SUSPECT_FONT_SUBSTRINGS = ("arial", "times new roman", "helvetica", "calibri", "verdana")

# Ratio xref_length / file_size_kb acima do qual se emite aviso LOW
_XREF_RATIO_THRESHOLD = 50  # xrefs por KB


# ---------------------------------------------------------------------------
# Dataclass de resultado
# ---------------------------------------------------------------------------

@dataclass
class ForensicsReport:
    """Resultado da análise forense de um PDF."""

    file_id: str
    """Identificador estável: sha256(caminho_absoluto)[:16]."""

    pdf_path: str
    """Caminho absoluto do arquivo analisado."""

    anomalies: list[dict] = field(default_factory=list)
    """Lista de anomalias detectadas. Cada entrada tem 'type', 'severity' e 'detail'."""

    risk_score: int = 0
    """Pontuação de risco 0-100 (soma ponderada: low=5, medium=20, high=40, máx 100)."""

    suspicious: bool = False
    """True se risk_score >= 40."""

    summary: str = ""
    """Frase curta descritiva do resultado, ex.: '2 anomalias detectadas (risco médio)'."""


# ---------------------------------------------------------------------------
# Helpers de parsing de datas PDF
# ---------------------------------------------------------------------------

# Formato PDF: D:YYYYMMDDHHmmSS seguido de Z ou +HH'mm ou -HH'mm (opcional)
_PDF_DATE_RE = re.compile(
    r"D:(\d{4})(\d{2})(\d{2})"      # YYYYMMDD (obrigatório)
    r"(?:(\d{2})(\d{2})(\d{2}))?"   # HHmmSS (opcional)
    r"(Z|[+-]\d{2}'\d{2})?",        # timezone (opcional)
)


def _parse_pdf_date(raw: str | None) -> datetime | None:
    """
    Converte uma data no formato PDF (D:YYYYMMDDHHmmSSOHH'mm) para datetime UTC.
    Retorna None se o valor estiver ausente ou mal formado.
    """
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    m = _PDF_DATE_RE.match(raw)
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hour = int(m.group(4) or 0)
    minute = int(m.group(5) or 0)
    second = int(m.group(6) or 0)
    tz_str = m.group(7)

    # Determinar offset UTC
    offset = timedelta(0)
    if tz_str and tz_str != "Z":
        sign = 1 if tz_str[0] == "+" else -1
        parts = tz_str[1:].split("'")
        try:
            tz_h = int(parts[0])
            tz_m = int(parts[1]) if len(parts) > 1 else 0
            offset = timedelta(hours=tz_h, minutes=tz_m) * sign
        except (ValueError, IndexError):
            offset = timedelta(0)

    tz_info = timezone(offset)
    try:
        dt = datetime(year, month, day, hour, minute, second, tzinfo=tz_info)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _has_timezone(raw: str | None) -> bool:
    """Retorna True se a data PDF contiver informação de timezone (Z, +HH'mm ou -HH'mm)."""
    if not raw or not isinstance(raw, str):
        return False
    m = _PDF_DATE_RE.match(raw.strip())
    if not m:
        return False
    return bool(m.group(7))


# ---------------------------------------------------------------------------
# Helpers de parsing XMP
# ---------------------------------------------------------------------------

_XMP_CREATE_DATE_RE = re.compile(
    r"<xmp:CreateDate>\s*([^<]+)\s*</xmp:CreateDate>", re.IGNORECASE
)
_XMP_MODIFY_DATE_RE = re.compile(
    r"<xmp:ModifyDate>\s*([^<]+)\s*</xmp:ModifyDate>", re.IGNORECASE
)
# Formato ISO 8601: 2024-03-15T12:00:00+00:00 ou 2024-03-15T12:00:00Z
_ISO8601_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})"
    r"(?:T(\d{2}):(\d{2}):(\d{2}))?"
    r"(Z|[+-]\d{2}:\d{2})?",
)


def _parse_xmp_date(raw: str | None) -> datetime | None:
    """
    Converte uma data ISO 8601 do XMP para datetime UTC.
    Retorna None se ausente ou mal formada.
    """
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    m = _ISO8601_RE.match(raw)
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hour = int(m.group(4) or 0)
    minute = int(m.group(5) or 0)
    second = int(m.group(6) or 0)
    tz_str = m.group(7)

    offset = timedelta(0)
    if tz_str and tz_str != "Z":
        sign = 1 if tz_str[0] == "+" else -1
        parts = tz_str[1:].split(":")
        try:
            tz_h = int(parts[0])
            tz_m = int(parts[1]) if len(parts) > 1 else 0
            offset = timedelta(hours=tz_h, minutes=tz_m) * sign
        except (ValueError, IndexError):
            offset = timedelta(0)

    tz_info = timezone(offset)
    try:
        dt = datetime(year, month, day, hour, minute, second, tzinfo=tz_info)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _extract_xmp_create_date(xml_str: str | None) -> datetime | None:
    """Extrai xmp:CreateDate do XML de metadados XMP e converte para datetime UTC."""
    if not xml_str:
        return None
    m = _XMP_CREATE_DATE_RE.search(xml_str)
    if not m:
        return None
    return _parse_xmp_date(m.group(1))


# ---------------------------------------------------------------------------
# Verificações individuais
# ---------------------------------------------------------------------------

def _check_creation_after_modification(
    meta: dict[str, str],
) -> list[dict]:
    """Detecta creationDate posterior a modDate (sinal de adulteração de metadados)."""
    anomalies: list[dict] = []
    creation_raw = meta.get("creationDate", "")
    mod_raw = meta.get("modDate", "")
    creation_dt = _parse_pdf_date(creation_raw)
    mod_dt = _parse_pdf_date(mod_raw)
    if creation_dt and mod_dt and creation_dt > mod_dt:
        diff = creation_dt - mod_dt
        anomalies.append({
            "type": "creation_after_modification",
            "severity": "high",
            "detail": (
                f"Data de criação ({creation_raw}) é posterior à data de modificação "
                f"({mod_raw}) em {int(diff.total_seconds() // 3600)}h. "
                "Indica possível adulteração dos metadados."
            ),
        })
    return anomalies


def _check_producer_creator_inconsistency(
    meta: dict[str, str],
) -> list[dict]:
    """Detecta combinações Creator/Producer historicamente associadas a reprocessamento suspeito."""
    anomalies: list[dict] = []
    creator = (meta.get("creator") or "").lower()
    producer = (meta.get("producer") or "").lower()
    if not creator or not producer:
        return anomalies
    for c_substr, p_substr in _SUSPECT_PAIRS:
        if c_substr in creator and p_substr in producer:
            anomalies.append({
                "type": "producer_creator_inconsistency",
                "severity": "medium",
                "detail": (
                    f"Creator '{meta.get('creator')}' e Producer '{meta.get('producer')}' "
                    "formam uma combinação suspeita, frequentemente associada a conversão "
                    "não documentada ou adulteração do PDF."
                ),
            })
            break  # uma única anomalia deste tipo por documento
    return anomalies


def _check_xmp_vs_docinfo(
    meta: dict[str, str],
    xmp_xml: str | None,
) -> list[dict]:
    """
    Compara xmp:CreateDate (XMP) com metadata['creationDate'] (DocInfo).
    Diferença > 24h → anomalia HIGH (datas inconsistentes entre camadas de metadados).
    """
    anomalies: list[dict] = []
    if not xmp_xml:
        return anomalies
    xmp_dt = _extract_xmp_create_date(xmp_xml)
    docinfo_dt = _parse_pdf_date(meta.get("creationDate", ""))
    if xmp_dt and docinfo_dt:
        diff = abs((xmp_dt - docinfo_dt).total_seconds())
        if diff > 86400:  # 24h em segundos
            anomalies.append({
                "type": "xmp_docinfo_date_conflict",
                "severity": "high",
                "detail": (
                    f"xmp:CreateDate ({xmp_dt.isoformat()}) e DocInfo creationDate "
                    f"({meta.get('creationDate')}) diferem em "
                    f"{int(diff // 3600)}h. Indício de metadados manipulados."
                ),
            })
    return anomalies


def _check_timestamps_without_timezone(
    meta: dict[str, str],
) -> list[dict]:
    """Emite aviso LOW quando datas PDF não contêm informação de timezone."""
    anomalies: list[dict] = []
    fields_to_check = [
        ("creationDate", "Data de criação"),
        ("modDate", "Data de modificação"),
    ]
    missing: list[str] = []
    for key, label in fields_to_check:
        raw = meta.get(key, "")
        if raw and not _has_timezone(raw):
            missing.append(label)
    if missing:
        anomalies.append({
            "type": "timestamps_without_timezone",
            "severity": "low",
            "detail": (
                f"{', '.join(missing)} sem timezone definido. "
                "PDFs gerados por ferramentas legítimas normalmente incluem timezone."
            ),
        })
    return anomalies


def _check_old_pdf_version(
    doc: "fitz.Document",
    meta: dict[str, str],
) -> list[dict]:
    """
    Verifica se a versão PDF é anormalmente antiga (< 1.4) para um documento
    cujos metadados indicam criação posterior a 2010.
    """
    anomalies: list[dict] = []
    try:
        version_str = doc.pdf_version()  # ex.: "1.3"
    except Exception:
        return anomalies

    try:
        version_num = float(version_str)
    except (ValueError, TypeError):
        return anomalies

    if version_num >= 1.4:
        return anomalies

    # Verificar se a data de criação é posterior a 2010
    creation_raw = meta.get("creationDate", "")
    creation_dt = _parse_pdf_date(creation_raw)
    if creation_dt and creation_dt.year > 2010:
        anomalies.append({
            "type": "pdf_version_anachronism",
            "severity": "medium",
            "detail": (
                f"Versão PDF {version_str} é anterior ao PDF 1.4, mas os metadados "
                f"indicam criação em {creation_dt.year}. "
                "Versões antigas com datas recentes podem indicar downgrade malicioso."
            ),
        })
    return anomalies


def _check_fonts_in_scanned_document(
    doc: "fitz.Document",
    meta: dict[str, str],
) -> list[dict]:
    """
    Verifica presença de fontes com nomes de texto vetorial num documento
    declarado como escaneado (Producer contém 'scan').
    """
    anomalies: list[dict] = []
    producer = (meta.get("producer") or "").lower()
    if "scan" not in producer:
        return anomalies

    suspect_fonts: list[str] = []
    try:
        for page_idx in range(min(len(doc), 10)):  # verificar até 10 páginas
            for font in doc.get_page_fonts(page_idx):
                # font = (xref, ext, type, basefont, name, encoding, referencer)
                font_name = ""
                if len(font) > 3:
                    font_name = (font[3] or "").lower()
                if not font_name and len(font) > 4:
                    font_name = (font[4] or "").lower()
                for suspect in _SUSPECT_FONT_SUBSTRINGS:
                    if suspect in font_name and font_name not in suspect_fonts:
                        suspect_fonts.append(font_name)
                        break
    except Exception as exc:
        _log.debug("_check_fonts_in_scanned_document: erro ao ler fontes: %s", exc)

    if suspect_fonts:
        anomalies.append({
            "type": "text_fonts_in_scanned_document",
            "severity": "medium",
            "detail": (
                f"Documento declarado como escaneado (Producer: '{meta.get('producer')}') "
                f"contém fontes de texto vetorial: {', '.join(suspect_fonts[:5])}. "
                "Pode indicar inserção de texto após digitalização."
            ),
        })
    return anomalies


def _check_xref_ratio(
    doc: "fitz.Document",
    pdf_path: Path,
) -> list[dict]:
    """
    Verifica se o número de entradas xref é desproporcionalmente alto para o
    tamanho do arquivo, indicando possivelmente muitas revisões/actualizações.
    """
    anomalies: list[dict] = []
    try:
        xref_len = doc.xref_length()
    except Exception:
        return anomalies

    try:
        file_size_bytes = pdf_path.stat().st_size
    except OSError:
        return anomalies

    if file_size_bytes <= 0:
        return anomalies

    file_size_kb = file_size_bytes / 1024.0
    ratio = xref_len / file_size_kb if file_size_kb > 0 else 0

    if ratio > _XREF_RATIO_THRESHOLD:
        anomalies.append({
            "type": "high_xref_ratio",
            "severity": "low",
            "detail": (
                f"Ratio xref/tamanho ({xref_len} entradas / {file_size_kb:.1f} KB = "
                f"{ratio:.1f}) acima do limiar ({_XREF_RATIO_THRESHOLD}). "
                "Pode indicar número elevado de revisões incrementais."
            ),
        })
    return anomalies


def _check_empty_author_with_producer(
    meta: dict[str, str],
) -> list[dict]:
    """
    Campo Author vazio mas Producer preenchido pode indicar geração automatizada
    sem metadados de autoria (risco baixo, mas relevante em contexto forense).
    """
    anomalies: list[dict] = []
    author = (meta.get("author") or "").strip()
    producer = (meta.get("producer") or "").strip()
    if not author and producer:
        anomalies.append({
            "type": "empty_author_with_producer",
            "severity": "low",
            "detail": (
                f"Campo Author está vazio, mas Producer está preenchido ('{producer}'). "
                "Documentos gerados automaticamente frequentemente omitem o autor."
            ),
        })
    return anomalies


# ---------------------------------------------------------------------------
# Cálculo da pontuação e sumário
# ---------------------------------------------------------------------------

def _compute_risk_score(anomalies: list[dict]) -> int:
    """Soma ponderada das anomalias com cap em _RISK_CAP."""
    total = sum(_SEVERITY_WEIGHT.get(a.get("severity", "low"), 5) for a in anomalies)
    return min(total, _RISK_CAP)


def _build_summary(anomalies: list[dict], risk_score: int) -> str:
    """Gera frase curta descritiva do resultado da análise."""
    n = len(anomalies)
    if n == 0:
        return "Nenhuma anomalia detectada."
    if risk_score >= 80:
        nivel = "risco elevado"
    elif risk_score >= 40:
        nivel = "risco médio"
    else:
        nivel = "risco baixo"
    plural = "anomalia" if n == 1 else "anomalias"
    return f"{n} {plural} detectada{'s' if n != 1 else ''} ({nivel})"


# ---------------------------------------------------------------------------
# Função pública principal
# ---------------------------------------------------------------------------

def analyse_forensics(
    pdf_path: Path,
    *,
    password: str | None = None,
) -> ForensicsReport:
    """
    Analisa um PDF à procura de anomalias e sinais de adulteração.

    Parâmetros:
        pdf_path: Caminho para o arquivo PDF a analisar.
        password: Senha do PDF, se aplicável.

    Retorna:
        ForensicsReport com lista de anomalias, pontuação de risco e sumário.
        Em caso de falha ao abrir o PDF, retorna relatório vazio com risk_score=0.
    """
    pdf_path = Path(pdf_path).resolve()
    file_id = hashlib.sha256(str(pdf_path).encode()).hexdigest()[:16]

    empty_report = ForensicsReport(
        file_id=file_id,
        pdf_path=str(pdf_path),
        anomalies=[],
        risk_score=0,
        suspicious=False,
        summary="Não foi possível abrir o PDF para análise.",
    )

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        _log.warning("analyse_forensics: falha ao abrir '%s': %s", pdf_path, exc)
        return empty_report

    try:
        if doc.is_encrypted:
            if password:
                authenticated = doc.authenticate(password)
                if not authenticated:
                    _log.warning(
                        "analyse_forensics: senha incorrecta para '%s'", pdf_path
                    )
                    return empty_report
            else:
                _log.warning(
                    "analyse_forensics: PDF cifrado sem senha fornecida: '%s'", pdf_path
                )
                return empty_report

        meta: dict[str, str] = doc.metadata or {}
        xmp_xml: str | None = None
        try:
            xmp_xml = doc.get_xml_metadata() or None
        except Exception as exc:
            _log.debug("analyse_forensics: falha ao ler XMP de '%s': %s", pdf_path, exc)

        all_anomalies: list[dict] = []

        # 1. Data criação > data modificação
        all_anomalies.extend(_check_creation_after_modification(meta))

        # 2. Producer/Creator inconsistentes
        all_anomalies.extend(_check_producer_creator_inconsistency(meta))

        # 3. XMP vs DocInfo conflict
        all_anomalies.extend(_check_xmp_vs_docinfo(meta, xmp_xml))

        # 4. Timestamps sem timezone
        all_anomalies.extend(_check_timestamps_without_timezone(meta))

        # 5. Versão PDF anormalmente antiga
        all_anomalies.extend(_check_old_pdf_version(doc, meta))

        # 6. Fontes de texto em documento declarado como escaneado
        all_anomalies.extend(_check_fonts_in_scanned_document(doc, meta))

        # 7. Ratio xref/tamanho alto
        all_anomalies.extend(_check_xref_ratio(doc, pdf_path))

        # 8. Author vazio com Producer preenchido
        all_anomalies.extend(_check_empty_author_with_producer(meta))

        risk_score = _compute_risk_score(all_anomalies)
        suspicious = risk_score >= _SUSPICIOUS_THRESHOLD
        summary = _build_summary(all_anomalies, risk_score)

        _log.debug(
            "analyse_forensics: '%s' → %d anomalia(s), score=%d, suspicious=%s",
            pdf_path.name, len(all_anomalies), risk_score, suspicious,
        )

        return ForensicsReport(
            file_id=file_id,
            pdf_path=str(pdf_path),
            anomalies=all_anomalies,
            risk_score=risk_score,
            suspicious=suspicious,
            summary=summary,
        )

    except Exception as exc:
        _log.error(
            "analyse_forensics: erro inesperado ao analisar '%s': %s", pdf_path, exc
        )
        return empty_report
    finally:
        try:
            doc.close()
        except Exception:
            pass
