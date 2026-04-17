"""
Backend HTR — Transkribus Cloud API.

Ideal para manuscritos históricos (séc. XIV–XX) em várias línguas, incluindo português
antigo, latim, alemão e hebraico. Requer conta em https://app.transkribus.eu e um
modelo HTR compatível com o acervo.

Workflow:
  1. Login → sessão com cookie JSESSIONID
  2. Garantir colecção (usa PDFSEARCHABLE_TRANSKRIBUS_COL_ID ou cria temporária)
  3. Inicializar upload → uploadId
  4. Enviar imagem (multipart PUT) → docId
  5. Submeter job HTR com modelo configurado → jobId
  6. Aguardar conclusão (polling)
  7. Obter transcript (PAGE XML) → extrair linhas de texto
  8. Limpar documento temporário (opcional)

Variáveis de ambiente:
  PDFSEARCHABLE_TRANSKRIBUS_USER       — e-mail da conta Transkribus (obrigatório)
  PDFSEARCHABLE_TRANSKRIBUS_PW         — senha da conta (obrigatório)
  PDFSEARCHABLE_TRANSKRIBUS_MODEL_ID   — ID do modelo HTR (inteiro; obrigatório)
                                         Exemplos de modelos públicos:
                                           39995 — Portuguese Handwriting (séc. XIX-XX)
                                           48152 — Generic Handwriting (multilíngue)
                                           13442 — Handwritten Text Recognition (inglês)
  PDFSEARCHABLE_TRANSKRIBUS_COL_ID     — ID da colecção (opcional; cria temp se omitido)
  PDFSEARCHABLE_TRANSKRIBUS_BASE_URL   — URL base API (padrão: https://transkribus.eu/TrpServer/rest)
  PDFSEARCHABLE_TRANSKRIBUS_CLEANUP    — 0 para manter documentos temporários (padrão: 1 = limpa)
  PDFSEARCHABLE_HTR_TIMEOUT            — timeout de polling em segundos (padrão: 120)
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from typing import Any

from pdfsearchable.audit import get_logger as _get_logger
from pdfsearchable.exceptions import OcrError

_log = _get_logger("pdfsearchable.htr.transkribus")

_DEFAULT_BASE_URL = "https://transkribus.eu/TrpServer/rest"
# Namespace padrão PAGE XML (READ Coop / Transkribus)
_PAGE_NS = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"

# Estado da sessão (singleton por processo)
_session_opener: urllib.request.OpenerDirector | None = None
_session_col_id: int | None = None


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

def _base_url() -> str:
    return (os.environ.get("PDFSEARCHABLE_TRANSKRIBUS_BASE_URL") or _DEFAULT_BASE_URL).rstrip("/")


def _htr_timeout() -> int:
    try:
        return max(10, int(os.environ.get("PDFSEARCHABLE_HTR_TIMEOUT") or "120"))
    except ValueError:
        return 120


def _model_id() -> int | None:
    raw = os.environ.get("PDFSEARCHABLE_TRANSKRIBUS_MODEL_ID", "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _cleanup_enabled() -> bool:
    raw = os.environ.get("PDFSEARCHABLE_TRANSKRIBUS_CLEANUP", "1").strip().lower()
    return raw not in ("0", "false", "no")


# ---------------------------------------------------------------------------
# Verificação de disponibilidade
# ---------------------------------------------------------------------------

def available() -> bool:
    """True se credenciais Transkribus e modelo HTR estiverem configurados."""
    user = os.environ.get("PDFSEARCHABLE_TRANSKRIBUS_USER", "").strip()
    pw = os.environ.get("PDFSEARCHABLE_TRANSKRIBUS_PW", "").strip()
    return bool(user and pw and _model_id() is not None)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _make_opener() -> urllib.request.OpenerDirector:
    """Cria opener urllib com gestão automática de cookies (sessão Transkribus)."""
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _api_json(
    opener: urllib.request.OpenerDirector,
    method: str,
    url: str,
    data: bytes | None = None,
    content_type: str = "application/json",
    timeout: int = 30,
) -> Any:
    """Chamada à API Transkribus — retorna JSON parsed. Levanta OcrError em falha HTTP."""
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", content_type)
    try:
        with opener.open(req, timeout=timeout) as resp:  # nosec B310
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        snippet = e.read()[:300].decode(errors="replace")
        raise OcrError(
            f"Transkribus API erro {e.code} em {url}: {snippet}",
            {"url": url, "status": e.code},
        ) from e
    except urllib.error.URLError as e:
        raise OcrError(
            f"Transkribus inacessível ({url}): {e.reason}",
            {"url": url},
        ) from e


def _build_multipart(boundary: str, files: dict[str, tuple[str, bytes, str]]) -> bytes:
    """Constrói corpo multipart/form-data. files: {campo: (filename, bytes, content_type)}"""
    body = b""
    for field_name, (filename, data, ctype) in files.items():
        body += f"--{boundary}\r\n".encode()
        body += (
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n"
        ).encode()
        body += data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

def _login() -> urllib.request.OpenerDirector:
    """Login no Transkribus. Retorna opener com cookie JSESSIONID activo."""
    user = os.environ.get("PDFSEARCHABLE_TRANSKRIBUS_USER", "").strip()
    pw = os.environ.get("PDFSEARCHABLE_TRANSKRIBUS_PW", "").strip()
    base = _base_url()
    opener = _make_opener()
    form = urllib.parse.urlencode({"user": user, "pw": pw}).encode()
    req = urllib.request.Request(
        f"{base}/auth/login",
        data=form,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with opener.open(req, timeout=30) as resp:  # nosec B310
            result = json.loads(resp.read())
        _log.info("Transkribus: login OK (userId=%s)", result.get("userId"))
        return opener
    except urllib.error.HTTPError as e:
        raise OcrError(
            f"Transkribus login falhou ({e.code}). "
            "Verifique PDFSEARCHABLE_TRANSKRIBUS_USER e PDFSEARCHABLE_TRANSKRIBUS_PW.",
            {"status": e.code},
        ) from e
    except urllib.error.URLError as e:
        raise OcrError(
            f"Transkribus inacessível durante login: {e.reason}. "
            "Verifique PDFSEARCHABLE_TRANSKRIBUS_BASE_URL e conectividade.",
            {},
        ) from e


# ---------------------------------------------------------------------------
# Colecção
# ---------------------------------------------------------------------------

def _ensure_collection(opener: urllib.request.OpenerDirector) -> int:
    """Garante colecção de trabalho; retorna colId."""
    global _session_col_id
    env_col = os.environ.get("PDFSEARCHABLE_TRANSKRIBUS_COL_ID", "").strip()
    if env_col:
        try:
            return int(env_col)
        except ValueError:
            _log.warning("PDFSEARCHABLE_TRANSKRIBUS_COL_ID inválido ('%s') — a criar colecção temp.", env_col)
    if _session_col_id is not None:
        return _session_col_id
    base = _base_url()
    col_id = _api_json(
        opener, "POST",
        f"{base}/collections/createCollection?collName=pdfsearchable_htr_temp",
    )
    _session_col_id = int(col_id)
    _log.debug("Transkribus: colecção temporária criada (colId=%s)", _session_col_id)
    return _session_col_id


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def _upload_image(
    opener: urllib.request.OpenerDirector, col_id: int, image_bytes: bytes
) -> tuple[int, int]:
    """Inicializa upload e envia imagem. Retorna (uploadId, docId)."""
    base = _base_url()
    doc_name = f"pdfsearchable_htr_{int(time.time())}_{uuid.uuid4().hex[:6]}"

    # 1. Init upload
    init_body = json.dumps({
        "md": {"docName": doc_name},
        "pageList": {"pages": [{"fileName": "page.png", "pageNr": 1}]},
    }).encode()
    init_result = _api_json(
        opener, "POST", f"{base}/uploads?collId={col_id}",
        data=init_body, content_type="application/json",
    )
    upload_id = int(init_result["uploadId"])

    # 2. Upload imagem (multipart PUT)
    boundary = uuid.uuid4().hex
    body = _build_multipart(boundary, {"img": ("page.png", image_bytes, "image/png")})
    req = urllib.request.Request(
        f"{base}/uploads/{upload_id}",
        data=body,
        method="PUT",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
    )
    try:
        with opener.open(req, timeout=60) as resp:  # nosec B310
            upload_result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise OcrError(
            f"Transkribus: falha ao enviar imagem ({e.code}).",
            {"status": e.code, "upload_id": upload_id},
        ) from e

    doc_id = upload_result.get("docId") or upload_result.get("uploadId")
    _log.debug("Transkribus: imagem enviada (uploadId=%s, docId=%s)", upload_id, doc_id)
    return upload_id, int(doc_id)


# ---------------------------------------------------------------------------
# Job HTR
# ---------------------------------------------------------------------------

def _submit_htr_job(
    opener: urllib.request.OpenerDirector, col_id: int, doc_id: int, model_id: int
) -> int:
    """Submete job HTR. Retorna jobId."""
    base = _base_url()
    payload = json.dumps({
        "docList": {"docs": [{"docId": doc_id, "pageList": {"pages": [{"pageNr": 1}]}}]},
        "config": {"htrId": model_id},
    }).encode()
    result = _api_json(
        opener, "POST", f"{base}/jobs/htrCia",
        data=payload, content_type="application/json",
    )
    # jobId pode vir como int directo ou em lista
    if isinstance(result, int):
        job_id = result
    elif isinstance(result, dict):
        job_id = result.get("jobId") or (result.get("jobIds") or [{}])[0].get("jobId")
    elif isinstance(result, list):
        job_id = (result[0] or {}).get("jobId")
    else:
        job_id = None
    if job_id is None:
        raise OcrError(
            f"Transkribus: resposta inesperada ao submeter job HTR: {result!r}",
            {"col_id": col_id, "doc_id": doc_id},
        )
    _log.debug("Transkribus: job HTR submetido (jobId=%s, model=%s)", job_id, model_id)
    return int(job_id)


def _poll_job(opener: urllib.request.OpenerDirector, job_id: int) -> None:
    """Aguarda job HTR concluir. Levanta OcrError em falha ou timeout."""
    base = _base_url()
    timeout = _htr_timeout()
    interval = 3
    elapsed = 0
    while elapsed < timeout:
        result = _api_json(opener, "GET", f"{base}/jobs/{job_id}")
        state = (result.get("state") or "").upper()
        if state == "FINISHED":
            _log.debug("Transkribus: job %s concluído", job_id)
            return
        if state in ("FAILED", "CANCELED", "CANCELLED"):
            raise OcrError(
                f"Transkribus HTR falhou (jobId={job_id}, state={state}). "
                "Verifique o ID do modelo em PDFSEARCHABLE_TRANSKRIBUS_MODEL_ID.",
                {"job_id": job_id, "state": state},
            )
        _log.debug("Transkribus: job %s estado=%s (%ds)", job_id, state, elapsed)
        time.sleep(interval)
        elapsed += interval
    raise OcrError(
        f"Timeout aguardando Transkribus ({timeout}s, jobId={job_id}). "
        "Aumente PDFSEARCHABLE_HTR_TIMEOUT.",
        {"job_id": job_id, "timeout": timeout},
    )


# ---------------------------------------------------------------------------
# Extracção do resultado
# ---------------------------------------------------------------------------

def _get_transcript_text(
    opener: urllib.request.OpenerDirector, col_id: int, doc_id: int
) -> str:
    """Obtém e processa o transcript mais recente (PAGE XML)."""
    base = _base_url()
    transcripts = _api_json(
        opener, "GET",
        f"{base}/collections/{col_id}/{doc_id}/pages/1/transcripts",
    )
    if not transcripts:
        _log.warning("Transkribus: nenhum transcript disponível para docId=%s", doc_id)
        return ""
    ts_url = transcripts[0].get("url", "")
    if not ts_url:
        return ""
    try:
        with urllib.request.urlopen(ts_url, timeout=30) as resp:  # nosec B310
            page_xml = resp.read()
    except Exception as e:
        _log.warning("Transkribus: falha ao ler PAGE XML (%s): %s", ts_url, e)
        return ""
    return _parse_page_xml(page_xml)


def _parse_page_xml(xml_bytes: bytes) -> str:
    """
    Extrai texto das linhas de um PAGE XML (formato READ Coop / Transkribus).
    Suporta PAGE XML com e sem namespace.
    """
    try:
        root = ET.fromstring(xml_bytes)  # nosec B314 # noqa: S314 — XML do Transkribus API (fonte confiável, não input do utilizador)
    except ET.ParseError as e:
        _log.warning("Transkribus: PAGE XML inválido: %s", e)
        return ""

    lines: list[str] = []

    # Tentar com namespace PAGE padrão
    ns_text_line = f"{{{_PAGE_NS}}}TextLine"
    ns_text_equiv = f"{{{_PAGE_NS}}}TextEquiv"
    ns_unicode = f"{{{_PAGE_NS}}}Unicode"

    for text_line in root.iter(ns_text_line):
        equiv = text_line.find(ns_text_equiv)
        if equiv is not None:
            uni = equiv.find(ns_unicode)
            if uni is not None and uni.text and uni.text.strip():
                lines.append(uni.text.strip())

    if not lines:
        # Fallback: qualquer elemento cujo tag termine em "Unicode"
        for elem in root.iter():
            if elem.tag.endswith("Unicode") and elem.text and elem.text.strip():
                lines.append(elem.text.strip())

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Limpeza
# ---------------------------------------------------------------------------

def _cleanup_doc(
    opener: urllib.request.OpenerDirector, col_id: int, doc_id: int
) -> None:
    """Remove documento temporário da colecção Transkribus (best-effort)."""
    if not _cleanup_enabled():
        return
    base = _base_url()
    try:
        _api_json(opener, "DELETE", f"{base}/collections/{col_id}/{doc_id}/delete")
        _log.debug("Transkribus: documento temporário removido (docId=%s)", doc_id)
    except Exception as e:
        _log.debug("Transkribus: falha ao remover documento temporário (docId=%s): %s", doc_id, e)


# ---------------------------------------------------------------------------
# Ponto de entrada público
# ---------------------------------------------------------------------------

def run(image_bytes: bytes) -> str:
    """
    Reconhece texto manuscrito/cursivo usando a API Transkribus.
    Retorna o texto extraído (linhas separadas por \\n) ou '' em falha.

    Requer:
      PDFSEARCHABLE_TRANSKRIBUS_USER      — e-mail da conta
      PDFSEARCHABLE_TRANSKRIBUS_PW        — senha
      PDFSEARCHABLE_TRANSKRIBUS_MODEL_ID  — ID do modelo HTR

    Modelos públicos recomendados para acervos luso-brasileiros:
      39995 — "Portuguese Handwriting" (séc. XIX–XX)
      48152 — "Generic Handwriting" (multilíngue, séc. XIX–XX)
    """
    global _session_opener

    model_id = _model_id()
    if model_id is None:
        raise OcrError(
            "PDFSEARCHABLE_TRANSKRIBUS_MODEL_ID não configurado. "
            "Defina o ID do modelo HTR Transkribus (ex.: 39995 para Portuguese Handwriting).",
            {},
        )

    # Login lazy (reutiliza sessão dentro do mesmo processo)
    if _session_opener is None:
        _session_opener = _login()
    opener = _session_opener

    col_id = _ensure_collection(opener)
    _, doc_id = _upload_image(opener, col_id, image_bytes)

    try:
        job_id = _submit_htr_job(opener, col_id, doc_id, model_id)
        _poll_job(opener, job_id)
        return _get_transcript_text(opener, col_id, doc_id)
    finally:
        _cleanup_doc(opener, col_id, doc_id)
