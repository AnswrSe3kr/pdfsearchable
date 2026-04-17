"""
Backend HTR — eScriptorium REST API.

eScriptorium é uma plataforma open-source baseada em Kraken para transcrição
de manuscritos históricos. Requer instância própria (auto-hosted) ou acesso a
uma instância partilhada (ex.: universidade ou arquivo histórico).

Workflow:
  1. Garantir projecto (usa PDFSEARCHABLE_ESCRIPTORIUM_PROJECT ou cria temporário)
  2. Criar documento no projecto
  3. Enviar imagem como "parte" do documento
  4. Aguardar carregamento da parte
  5. Executar transcrição HTR com modelo configurado
  6. Aguardar conclusão
  7. Extrair texto das linhas transcritas
  8. Limpar documento temporário (opcional)

Variáveis de ambiente:
  PDFSEARCHABLE_ESCRIPTORIUM_URL      — URL base da instância (obrigatório)
                                        Ex.: https://escriptorium.example.org
  PDFSEARCHABLE_ESCRIPTORIUM_TOKEN    — API token da conta (obrigatório)
                                        Obter em: Perfil → API Key
  PDFSEARCHABLE_ESCRIPTORIUM_MODEL    — pk (inteiro) ou nome do modelo HTR (obrigatório)
  PDFSEARCHABLE_ESCRIPTORIUM_PROJECT  — pk do projecto de trabalho (opcional;
                                        cria projecto temporário se omitido)
  PDFSEARCHABLE_ESCRIPTORIUM_CLEANUP  — 0 para manter documentos temporários
                                        (padrão: 1 = remove após transcrição)
  PDFSEARCHABLE_HTR_TIMEOUT           — timeout de polling em segundos (padrão: 120)
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from pdfsearchable.audit import get_logger as _get_logger
from pdfsearchable.exceptions import OcrError

_log = _get_logger("pdfsearchable.htr.escriptorium")

# Estado de sessão (singleton por processo)
_session_project_pk: int | None = None


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------


def _base_url() -> str:
    raw = (os.environ.get("PDFSEARCHABLE_ESCRIPTORIUM_URL") or "").rstrip("/")
    return raw


def _token() -> str:
    return (os.environ.get("PDFSEARCHABLE_ESCRIPTORIUM_TOKEN") or "").strip()


def _model_spec() -> str:
    """Retorna nome ou pk do modelo HTR (string)."""
    return (os.environ.get("PDFSEARCHABLE_ESCRIPTORIUM_MODEL") or "").strip()


def _htr_timeout() -> int:
    try:
        return max(10, int(os.environ.get("PDFSEARCHABLE_HTR_TIMEOUT") or "120"))
    except ValueError:
        return 120


def _cleanup_enabled() -> bool:
    raw = os.environ.get("PDFSEARCHABLE_ESCRIPTORIUM_CLEANUP", "1").strip().lower()
    return raw not in ("0", "false", "no")


# ---------------------------------------------------------------------------
# Verificação de disponibilidade
# ---------------------------------------------------------------------------


def available() -> bool:
    """True se URL, token e modelo estiverem configurados."""
    return bool(_base_url() and _token() and _model_spec())


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _api_request(
    method: str,
    url: str,
    data: bytes | None = None,
    content_type: str = "application/json",
    timeout: int = 30,
) -> Any:
    """Chamada JSON autenticada à API eScriptorium. Levanta OcrError em falha HTTP."""
    tok = _token()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Token {tok}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        snippet = e.read()[:300].decode(errors="replace")
        raise OcrError(
            f"eScriptorium API erro {e.code} em {url}: {snippet}",
            {"url": url, "status": e.code},
        ) from e
    except urllib.error.URLError as e:
        raise OcrError(
            f"eScriptorium inacessível ({url}): {e.reason}",
            {"url": url},
        ) from e


def _build_multipart(boundary: str, fields: dict[str, tuple[str, bytes, str]]) -> bytes:
    """Constrói corpo multipart/form-data. fields: {campo: (filename, bytes, content_type)}"""
    body = b""
    for field_name, (filename, file_bytes, ctype) in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += (
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n"
        ).encode()
        body += file_bytes + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body


def _api_multipart(
    method: str,
    url: str,
    fields: dict[str, tuple[str, bytes, str]],
    timeout: int = 60,
) -> Any:
    """Chamada multipart/form-data autenticada. fields: {campo: (filename, bytes, ctype)}"""
    tok = _token()
    boundary = uuid.uuid4().hex
    body = _build_multipart(boundary, fields)
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Token {tok}")
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            result_body = resp.read()
            return json.loads(result_body) if result_body else {}
    except urllib.error.HTTPError as e:
        snippet = e.read()[:300].decode(errors="replace")
        raise OcrError(
            f"eScriptorium upload erro {e.code} em {url}: {snippet}",
            {"url": url, "status": e.code},
        ) from e
    except urllib.error.URLError as e:
        raise OcrError(
            f"eScriptorium inacessível durante upload ({url}): {e.reason}",
            {"url": url},
        ) from e


# ---------------------------------------------------------------------------
# Projecto
# ---------------------------------------------------------------------------


def _ensure_project() -> int:
    """Garante projecto de trabalho; retorna pk."""
    global _session_project_pk
    base = _base_url()

    env_project = os.environ.get("PDFSEARCHABLE_ESCRIPTORIUM_PROJECT", "").strip()
    if env_project:
        try:
            return int(env_project)
        except ValueError:
            _log.warning(
                "PDFSEARCHABLE_ESCRIPTORIUM_PROJECT inválido ('%s') — a criar projecto temp.",
                env_project,
            )

    if _session_project_pk is not None:
        return _session_project_pk

    # Criar projecto temporário
    project_name = f"pdfsearchable_htr_temp_{int(time.time())}"
    result = _api_request(
        "POST",
        f"{base}/api/projects/",
        data=json.dumps({"name": project_name}).encode(),
        content_type="application/json",
    )
    pk = int(result["pk"])
    _session_project_pk = pk
    _log.debug("eScriptorium: projecto temporário criado (pk=%s)", pk)
    return pk


# ---------------------------------------------------------------------------
# Documento
# ---------------------------------------------------------------------------


def _create_document(project_pk: int) -> int:
    """Cria documento no projecto. Retorna pk do documento."""
    base = _base_url()
    doc_name = f"pdfsearchable_htr_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    result = _api_request(
        "POST",
        f"{base}/api/documents/",
        data=json.dumps(
            {
                "name": doc_name,
                "project": project_pk,
                "read_direction": "ltr",
                "main_script": "Latin",
            }
        ).encode(),
        content_type="application/json",
    )
    pk = int(result["pk"])
    _log.debug("eScriptorium: documento criado (pk=%s, projecto=%s)", pk, project_pk)
    return pk


# ---------------------------------------------------------------------------
# Upload de imagem
# ---------------------------------------------------------------------------


def _upload_part(doc_pk: int, image_bytes: bytes) -> int:
    """Envia imagem como parte do documento. Retorna pk da parte."""
    base = _base_url()
    result = _api_multipart(
        "POST",
        f"{base}/api/documents/{doc_pk}/parts/",
        fields={"image": ("page.png", image_bytes, "image/png")},
        timeout=60,
    )
    pk = int(result["pk"])
    _log.debug("eScriptorium: parte enviada (pk=%s, doc=%s)", pk, doc_pk)
    return pk


# ---------------------------------------------------------------------------
# Polling de status da parte
# ---------------------------------------------------------------------------


def _poll_part_workflow(doc_pk: int, part_pk: int, workflow_key: str) -> None:
    """
    Aguarda que o campo workflow[workflow_key] seja 'done' na parte.
    Levanta OcrError em falha ou timeout.
    """
    base = _base_url()
    timeout = _htr_timeout()
    interval = 3
    elapsed = 0
    while elapsed < timeout:
        result = _api_request("GET", f"{base}/api/documents/{doc_pk}/parts/{part_pk}/")
        workflow = result.get("workflow") or {}
        state = (workflow.get(workflow_key) or "").lower()
        if state == "done":
            _log.debug(
                "eScriptorium: %s concluído para parte %s (%ds)", workflow_key, part_pk, elapsed
            )
            return
        if state in ("error", "failed", "canceled", "cancelled"):
            raise OcrError(
                f"eScriptorium: {workflow_key} falhou (parte {part_pk}, state={state}). "
                "Verifique o modelo em PDFSEARCHABLE_ESCRIPTORIUM_MODEL.",
                {
                    "doc_pk": doc_pk,
                    "part_pk": part_pk,
                    "workflow_key": workflow_key,
                    "state": state,
                },
            )
        _log.debug(
            "eScriptorium: %s estado=%s (parte %s, %ds)",
            workflow_key,
            state or "pending",
            part_pk,
            elapsed,
        )
        time.sleep(interval)
        elapsed += interval
    raise OcrError(
        f"Timeout aguardando eScriptorium/{workflow_key} ({timeout}s, parte {part_pk}). "
        "Aumente PDFSEARCHABLE_HTR_TIMEOUT.",
        {"doc_pk": doc_pk, "part_pk": part_pk, "workflow_key": workflow_key, "timeout": timeout},
    )


# ---------------------------------------------------------------------------
# Resolução do modelo
# ---------------------------------------------------------------------------


def _resolve_model_pk() -> int | str:
    """
    Retorna pk (int) ou nome (str) do modelo HTR configurado.
    Se PDFSEARCHABLE_ESCRIPTORIUM_MODEL for um inteiro, retorna-o.
    Caso contrário, tenta encontrar o pk listando os modelos disponíveis.
    """
    spec = _model_spec()
    try:
        return int(spec)
    except ValueError:
        pass  # é um nome — tentar resolver via API

    base = _base_url()
    try:
        result = _api_request("GET", f"{base}/api/mlmodels/?name={urllib.parse.quote(spec)}")
        models = result.get("results") or (result if isinstance(result, list) else [])
        for m in models:
            if m.get("name", "").lower() == spec.lower():
                return int(m["pk"])
        if models:
            _log.warning(
                "eScriptorium: modelo '%s' não encontrado por nome exacto; a usar pk=%s",
                spec,
                models[0].get("pk"),
            )
            return int(models[0]["pk"])
    except OcrError:
        pass

    # Retorna o spec como string para a API tentar resolver
    _log.warning(
        "eScriptorium: não foi possível resolver pk do modelo '%s' — a tentar como nome.", spec
    )
    return spec


# ---------------------------------------------------------------------------
# Transcrição
# ---------------------------------------------------------------------------


def _run_transcription(doc_pk: int, part_pk: int) -> None:
    """Submete job de transcrição HTR para a parte."""
    base = _base_url()
    model = _resolve_model_pk()
    payload: dict = {"parts": [part_pk]}
    if isinstance(model, int):
        payload["model"] = model
    else:
        payload["model"] = model  # API pode aceitar nome
    _api_request(
        "POST",
        f"{base}/api/documents/{doc_pk}/transcribe/",
        data=json.dumps(payload).encode(),
        content_type="application/json",
    )
    _log.debug(
        "eScriptorium: transcrição submetida (doc=%s, parte=%s, modelo=%s)", doc_pk, part_pk, model
    )


# ---------------------------------------------------------------------------
# Extracção do texto
# ---------------------------------------------------------------------------


def _get_part_text(doc_pk: int, part_pk: int) -> str:
    """
    Extrai texto transcrito da parte.
    Tenta primeiro via export de texto plano; fallback para iteração de linhas.
    """
    base = _base_url()

    # Tentativa 1: endpoint de export (texto plano)
    try:
        export_url = f"{base}/api/documents/{doc_pk}/export/?file_format=text&parts={part_pk}"
        result = _api_request("GET", export_url, timeout=30)
        if isinstance(result, dict) and result.get("text"):
            return result["text"].strip()
    except OcrError:
        pass  # endpoint pode não existir na versão da instância

    # Tentativa 2: listar linhas da parte com transcrições
    try:
        lines_result = _api_request(
            "GET",
            f"{base}/api/documents/{doc_pk}/parts/{part_pk}/lines/?limit=500",
            timeout=30,
        )
        lines_data = lines_result.get("results") or (
            lines_result if isinstance(lines_result, list) else []
        )
        texts: list[str] = []
        for line in lines_data:
            # Cada linha tem "transcriptions": [{"content": "...", ...}]
            transcriptions = line.get("transcriptions") or []
            if transcriptions:
                content = (transcriptions[0].get("content") or "").strip()
                if content:
                    texts.append(content)
        if texts:
            return "\n".join(texts)
    except OcrError as e:
        _log.debug(
            "eScriptorium: falha ao listar linhas (doc=%s, parte=%s): %s", doc_pk, part_pk, e
        )

    # Tentativa 3: listar transcriptions da parte e extrair conteúdo via URL
    try:
        ts_list = _api_request(
            "GET",
            f"{base}/api/documents/{doc_pk}/parts/{part_pk}/transcriptions/",
            timeout=30,
        )
        ts_items = ts_list.get("results") or (ts_list if isinstance(ts_list, list) else [])
        if ts_items:
            ts_pk = ts_items[-1].get("pk")  # mais recente
            if ts_pk:
                lines_result2 = _api_request(
                    "GET",
                    f"{base}/api/documents/{doc_pk}/parts/{part_pk}/lines/"
                    f"?transcription={ts_pk}&limit=500",
                    timeout=30,
                )
                lines_data2 = lines_result2.get("results") or (
                    lines_result2 if isinstance(lines_result2, list) else []
                )
                texts2: list[str] = []
                for line in lines_data2:
                    transcriptions = line.get("transcriptions") or []
                    if transcriptions:
                        content = (transcriptions[0].get("content") or "").strip()
                        if content:
                            texts2.append(content)
                if texts2:
                    return "\n".join(texts2)
    except OcrError as e:
        _log.debug(
            "eScriptorium: falha ao ler transcription (doc=%s, parte=%s): %s", doc_pk, part_pk, e
        )

    _log.warning("eScriptorium: texto vazio para parte %s do documento %s", part_pk, doc_pk)
    return ""


# ---------------------------------------------------------------------------
# Limpeza
# ---------------------------------------------------------------------------


def _cleanup_document(doc_pk: int) -> None:
    """Remove documento temporário (best-effort)."""
    if not _cleanup_enabled():
        return
    base = _base_url()
    try:
        req = urllib.request.Request(
            f"{base}/api/documents/{doc_pk}/",
            method="DELETE",
        )
        req.add_header("Authorization", f"Token {_token()}")
        with urllib.request.urlopen(req, timeout=15) as _:  # nosec B310
            pass
        _log.debug("eScriptorium: documento temporário removido (pk=%s)", doc_pk)
    except Exception as e:
        _log.debug("eScriptorium: falha ao remover documento %s: %s", doc_pk, e)


# ---------------------------------------------------------------------------
# Ponto de entrada público
# ---------------------------------------------------------------------------


def run(image_bytes: bytes) -> str:
    """
    Reconhece texto manuscrito/cursivo usando a API eScriptorium (Kraken).
    Retorna o texto extraído (linhas separadas por \\n) ou '' em falha.

    Requer:
      PDFSEARCHABLE_ESCRIPTORIUM_URL    — URL da instância eScriptorium
      PDFSEARCHABLE_ESCRIPTORIUM_TOKEN  — API token
      PDFSEARCHABLE_ESCRIPTORIUM_MODEL  — pk ou nome do modelo HTR

    Exemplo de modelos Kraken públicos:
      - Pesquisar em https://zenodo.org/communities/ocr_models
      - Importar em eScriptorium via Interface → Modelos → Importar URL
    """
    if not available():
        raise OcrError(
            "eScriptorium não configurado. "
            "Defina PDFSEARCHABLE_ESCRIPTORIUM_URL, PDFSEARCHABLE_ESCRIPTORIUM_TOKEN "
            "e PDFSEARCHABLE_ESCRIPTORIUM_MODEL.",
            {},
        )

    project_pk = _ensure_project()
    doc_pk = _create_document(project_pk)

    try:
        part_pk = _upload_part(doc_pk, image_bytes)

        # Aguardar carregamento (convert → done)
        _poll_part_workflow(doc_pk, part_pk, "convert")

        # Executar transcrição e aguardar
        _run_transcription(doc_pk, part_pk)
        _poll_part_workflow(doc_pk, part_pk, "transcribe")

        return _get_part_text(doc_pk, part_pk)

    finally:
        _cleanup_document(doc_pk)
