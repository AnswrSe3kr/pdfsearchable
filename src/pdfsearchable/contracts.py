"""
Gestão de contratos e prazos.

Detecta automaticamente datas de validade, vigência e renovação em documentos
do tipo "contrato", e gera alertas por e-mail quando prazos se aproximam.
"""
from __future__ import annotations

import logging
import re
import smtplib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

_log = logging.getLogger("pdfsearchable.contracts")

# ---------------------------------------------------------------------------
# Padrões de extracção
# ---------------------------------------------------------------------------

_MONTH_PT: dict[str, int] = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
    "jan": 1, "fev": 2, "mar": 3, "abr": 4,
    "mai": 5, "jun": 6, "jul": 7, "ago": 8,
    "set": 9, "out": 10, "nov": 11, "dez": 12,
}

_RE_VIGENCIA = re.compile(
    r"vig[eê]ncia[:\s]+(?:de\s+)?(\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4})"
    r"\s+(?:a|até|ao|ate)\s+(\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4})",
    re.IGNORECASE,
)
_RE_VALIDADE = re.compile(
    r"v[aá]lid[oa]?\s+(?:até|ate|a partir de)?\s*[:\s]*(\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4})",
    re.IGNORECASE,
)
_RE_VENCIMENTO = re.compile(
    r"vencimento[:\s]+(\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4})",
    re.IGNORECASE,
)
_RE_TERMINO = re.compile(
    r"t[eé]rmino[:\s]+(\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4})",
    re.IGNORECASE,
)
_RE_PRAZO_MESES = re.compile(
    r"prazo[:\s]+(?:de\s+)?(\d+)\s+(m[eê]s(?:es)?|ano(?:s)?|dia(?:s)?)",
    re.IGNORECASE,
)
_RE_DATA_INICIO = re.compile(
    r"(?:data\s+de\s+inicio|início|início\s+da\s+vigência)[:\s]+(\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4})",
    re.IGNORECASE,
)
_RE_RENOVACAO = re.compile(
    r"renova[çc][aã]o\s+autom[aá]tica",
    re.IGNORECASE,
)
_RE_PT_DATE_EXT = re.compile(
    r"(\d{1,2})\s+de\s+(" + "|".join(_MONTH_PT) + r")\s+de\s+(\d{4})",
    re.IGNORECASE,
)
_RE_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_RE_BR_DATE = re.compile(r"(\d{1,2})[\/\.\-](\d{1,2})[\/\.\-](\d{2,4})")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ContractDates:
    """Datas extraídas de um contrato."""
    start_date: str | None = None      # ISO 8601: "YYYY-MM-DD"
    end_date: str | None = None
    renewal_date: str | None = None
    duration_months: int | None = None
    auto_renewal: bool = False
    confidence: float = 0.0


@dataclass
class ContractAlert:
    """Alerta de contrato próximo do vencimento."""
    file_id: str
    name: str
    end_date: str
    days_until_expiry: int
    auto_renewal: bool
    severity: str   # "critical" (<=7d), "warning" (<=30d), "notice" (<=90d), "expired" (<0d)
    doc_type: str = "contrato"


# ---------------------------------------------------------------------------
# Parsing de datas
# ---------------------------------------------------------------------------

def _parse_date(raw: str) -> str | None:
    """
    Normaliza uma string de data para formato ISO "YYYY-MM-DD".
    Suporta DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY, "DD de MÊS de YYYY" e ISO.
    """
    raw = raw.strip()

    # ISO
    m = _RE_ISO_DATE.fullmatch(raw) or _RE_ISO_DATE.search(raw)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _valid_date(y, mo, d):
            return f"{y:04d}-{mo:02d}-{d:02d}"

    # Por extenso
    m2 = _RE_PT_DATE_EXT.search(raw.lower())
    if m2:
        d2, month_name, y2 = int(m2.group(1)), m2.group(2).lower(), int(m2.group(3))
        mo2 = _MONTH_PT.get(month_name, 0)
        if mo2 and _valid_date(y2, mo2, d2):
            return f"{y2:04d}-{mo2:02d}-{d2:02d}"

    # DD/MM/YYYY
    m3 = _RE_BR_DATE.search(raw)
    if m3:
        d3, mo3, y3 = int(m3.group(1)), int(m3.group(2)), int(m3.group(3))
        if y3 < 100:
            y3 += 2000 if y3 < 50 else 1900
        if _valid_date(y3, mo3, d3):
            return f"{y3:04d}-{mo3:02d}-{d3:02d}"

    return None


def _valid_date(y: int, mo: int, d: int) -> bool:
    current_year = datetime.now(timezone.utc).year
    return 1900 <= y <= current_year + 10 and 1 <= mo <= 12 and 1 <= d <= 31


def _duration_to_months(qty: int, unit: str) -> int:
    unit = unit.lower()
    if "ano" in unit:
        return qty * 12
    if "dia" in unit:
        return max(1, qty // 30)
    return qty  # meses


# ---------------------------------------------------------------------------
# Extracção de datas de contrato
# ---------------------------------------------------------------------------

def extract_contract_dates(text: str, filename: str = "") -> ContractDates:
    """
    Extrai datas de contrato do texto usando regex e heurísticas.

    Procura padrões de vigência, validade, vencimento, prazo e renovação automática.
    """
    result = ContractDates()
    found_count = 0

    # Vigência: "vigência de DD/MM/YYYY a DD/MM/YYYY"
    m = _RE_VIGENCIA.search(text)
    if m:
        result.start_date = _parse_date(m.group(1))
        result.end_date = _parse_date(m.group(2))
        if result.start_date or result.end_date:
            found_count += 2

    # Validade / vencimento / término (end_date)
    if not result.end_date:
        for pattern in (_RE_VALIDADE, _RE_VENCIMENTO, _RE_TERMINO):
            m2 = pattern.search(text)
            if m2:
                parsed = _parse_date(m2.group(1))
                if parsed:
                    result.end_date = parsed
                    found_count += 1
                    break

    # Início
    if not result.start_date:
        m3 = _RE_DATA_INICIO.search(text)
        if m3:
            parsed = _parse_date(m3.group(1))
            if parsed:
                result.start_date = parsed
                found_count += 1

    # Prazo em meses/anos/dias
    m4 = _RE_PRAZO_MESES.search(text)
    if m4:
        qty, unit = int(m4.group(1)), m4.group(2)
        result.duration_months = _duration_to_months(qty, unit)
        # Calcular end_date a partir de start_date + prazo
        if result.start_date and not result.end_date:
            try:
                start = date.fromisoformat(result.start_date)
                end_month = start.month + result.duration_months
                end_year = start.year + (end_month - 1) // 12
                end_month = (end_month - 1) % 12 + 1
                result.end_date = f"{end_year:04d}-{end_month:02d}-{start.day:02d}"
                found_count += 1
            except Exception:
                pass

    # Renovação automática
    result.auto_renewal = bool(_RE_RENOVACAO.search(text))

    # Confiança proporcional ao número de campos encontrados
    result.confidence = min(1.0, found_count / 3)

    return result


# ---------------------------------------------------------------------------
# Verificação de contratos a expirar
# ---------------------------------------------------------------------------

def check_expiring_contracts(
    days_ahead: int = 30,
    *,
    include_auto_renewal: bool = True,
) -> list[ContractAlert]:
    """
    Lê o índice e retorna contratos próximos do vencimento.

    Args:
        days_ahead: Janela de alerta em dias.
        include_auto_renewal: Se False, exclui contratos com renovação automática.

    Returns:
        Lista de :class:`ContractAlert` ordenada por urgência (mais urgente primeiro).
    """
    from pdfsearchable.store import load_index  # importação lazy para evitar circular

    try:
        idx = load_index()
    except Exception as exc:
        _log.error("Não foi possível carregar o índice: %s", exc)
        return []

    today = date.today()
    alerts: list[ContractAlert] = []

    for f in idx.get("files", []):
        doc_type = f.get("doc_type") or f.get("type") or ""
        if "contrato" not in doc_type.lower():
            continue

        meta = f.get("metadata") or {}
        contract_data = meta.get("contract_data") or {}
        end_date_str = contract_data.get("end_date")
        auto_renewal = contract_data.get("auto_renewal", False)

        # Tentar datas identificadas como fallback
        if not end_date_str:
            for raw in (f.get("identified_dates") or []):
                parsed = _parse_date(str(raw))
                if parsed:
                    try:
                        candidate = date.fromisoformat(parsed)
                        if candidate > today:
                            end_date_str = parsed
                            break
                    except ValueError:
                        continue

        if not end_date_str:
            continue

        if not include_auto_renewal and auto_renewal:
            continue

        try:
            end_date = date.fromisoformat(end_date_str)
        except ValueError:
            continue

        days_left = (end_date - today).days

        # Incluir expirados e a expirar dentro da janela
        if days_left > days_ahead:
            continue

        if days_left < 0:
            severity = "expired"
        elif days_left <= 7:
            severity = "critical"
        elif days_left <= 30:
            severity = "warning"
        else:
            severity = "notice"

        alerts.append(ContractAlert(
            file_id=f.get("id", ""),
            name=f.get("name", ""),
            end_date=end_date_str,
            days_until_expiry=days_left,
            auto_renewal=auto_renewal,
            severity=severity,
            doc_type=doc_type,
        ))

    alerts.sort(key=lambda a: a.days_until_expiry)
    return alerts


def get_contracts_summary() -> dict[str, Any]:
    """
    Retorna um resumo de todos os contratos indexados.

    Returns:
        Dict com ``total``, ``expired``, ``expiring_30d``, ``expiring_90d``, ``no_date``.
    """
    from pdfsearchable.store import load_index

    try:
        idx = load_index()
    except Exception:
        return {"total": 0, "expired": 0, "expiring_30d": 0, "expiring_90d": 0, "no_date": 0}

    today = date.today()
    total = expired = exp_30 = exp_90 = no_date = 0

    for f in idx.get("files", []):
        doc_type = f.get("doc_type") or f.get("type") or ""
        if "contrato" not in doc_type.lower():
            continue
        total += 1
        meta = f.get("metadata") or {}
        contract_data = meta.get("contract_data") or {}
        end_str = contract_data.get("end_date")
        if not end_str:
            no_date += 1
            continue
        try:
            end = date.fromisoformat(end_str)
            days_left = (end - today).days
            if days_left < 0:
                expired += 1
            elif days_left <= 30:
                exp_30 += 1
            elif days_left <= 90:
                exp_90 += 1
        except ValueError:
            no_date += 1

    return {
        "total": total,
        "expired": expired,
        "expiring_30d": exp_30,
        "expiring_90d": exp_90,
        "no_date": no_date,
    }


# ---------------------------------------------------------------------------
# Envio de alertas por e-mail
# ---------------------------------------------------------------------------

def send_expiry_alerts(
    alerts: list[ContractAlert],
    recipients: list[str],
    *,
    smtp_host: str = "localhost",
    smtp_port: int = 587,
    smtp_user: str = "",
    smtp_pass: str = "",
    from_addr: str = "pdfsearchable@localhost",
) -> tuple[int, list[str]]:
    """
    Envia alertas de vencimento por e-mail via SMTP.

    Args:
        alerts: Lista de alertas a enviar.
        recipients: Lista de endereços de e-mail destinatários.
        smtp_host: Servidor SMTP.
        smtp_port: Porta SMTP (587 para TLS, 465 para SSL, 25 para plain).
        smtp_user: Utilizador SMTP (vazio = sem autenticação).
        smtp_pass: Palavra-passe SMTP.
        from_addr: Endereço de origem.

    Returns:
        ``(sucessos, lista_de_erros)``
    """
    if not alerts or not recipients:
        return 0, []

    _SEVERITY_LABEL = {
        "expired": "🔴 EXPIRADO",
        "critical": "🔴 CRÍTICO",
        "warning": "🟡 AVISO",
        "notice": "🟢 INFORMAÇÃO",
    }
    _SEVERITY_COLOR = {
        "expired": "#dc2626",
        "critical": "#dc2626",
        "warning": "#d97706",
        "notice": "#059669",
    }

    rows_html = ""
    for a in alerts:
        color = _SEVERITY_COLOR.get(a.severity, "#6b7280")
        label = _SEVERITY_LABEL.get(a.severity, a.severity)
        days_text = (
            f"Expirado há {abs(a.days_until_expiry)} dia(s)"
            if a.days_until_expiry < 0
            else f"Expira em {a.days_until_expiry} dia(s)"
        )
        renewal = " · Renovação automática" if a.auto_renewal else ""
        rows_html += (
            f"<tr><td style='padding:8px;border-bottom:1px solid #e5e7eb'>{a.name}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb'>{a.end_date}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;color:{color}'>"
            f"{label} — {days_text}{renewal}</td></tr>"
        )

    html_body = f"""
    <html><body style='font-family:-apple-system,sans-serif;color:#111'>
    <h2 style='color:#1e3a5f'>⚠️ pdfsearchable — Contratos a expirar</h2>
    <p>{len(alerts)} contrato(s) requerem atenção:</p>
    <table style='border-collapse:collapse;width:100%;margin-top:16px'>
    <thead><tr style='background:#f3f4f6'>
      <th style='padding:8px;text-align:left'>Documento</th>
      <th style='padding:8px;text-align:left'>Data fim</th>
      <th style='padding:8px;text-align:left'>Estado</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
    </table>
    <p style='margin-top:16px;color:#6b7280;font-size:12px'>
      Gerado por pdfsearchable · Execute <code>pdfsearchable contracts</code> para mais detalhes.
    </p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[pdfsearchable] {len(alerts)} contrato(s) a expirar"
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    errors: list[str] = []
    successes = 0

    try:
        if smtp_port == 465:
            import ssl
            ctx = ssl.create_default_context()
            server: smtplib.SMTP = smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port)
            if smtp_port == 587:
                server.starttls()
        if smtp_user:
            server.login(smtp_user, smtp_pass)
        server.sendmail(from_addr, recipients, msg.as_string())
        server.quit()
        successes = len(recipients)
        _log.info("Alertas enviados para %d destinatário(s)", successes)
    except Exception as exc:
        msg_err = str(exc)
        _log.error("Falha ao enviar alertas SMTP: %s", msg_err)
        errors.append(msg_err)

    return successes, errors
