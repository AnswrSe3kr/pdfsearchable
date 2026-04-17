"""
Módulo de sinônimos e dicionário: PT-BR (API Dicionário) e EN-US (API Ninjas Thesaurus).

PT-BR: API inspirada em https://github.com/atrikx/api-dicionario-ptbr
  - Base URL configurável (ex.: api-dicionario-ptbr.herokuapp.com ou api-dicionario-ptbr.com)
  - GET /<palavra> retorna JSON com chave "sinonimos": ["...", ...]

EN-US: API Ninjas Thesaurus https://api-ninjas.com/api/thesaurus
  - GET https://api.api-ninjas.com/v1/thesaurus?word=<word>
  - Header X-Api-Key obrigatório (env API_NINJAS_KEY ou PDFSEARCHABLE_API_NINJAS_KEY)
  - Resposta: {"word": "...", "synonyms": [...], "antonyms": [...]}
"""

import ipaddress
import json
import os
import urllib.parse
import urllib.request
from typing import Literal

# PT-BR: base URL da API (pode ser alterada por env)
API_DICIONARIO_PTBR_BASE = os.environ.get(
    "PDFSEARCHABLE_API_DICIONARIO_PTBR",
    "https://api-dicionario-ptbr.herokuapp.com",
).rstrip("/")

# EN-US: API Ninjas requer chave
API_NINJAS_THESAURUS_URL = "https://api.api-ninjas.com/v1/thesaurus"


def _is_safe_http_url(url: str) -> bool:
    """
    Garante que a URL é http/https e que o hostname não é um IP privado/reservado.
    Bloqueia SSRF via file://, http://localhost, http://169.254.x.x, etc.
    """
    u = (url or "").strip()
    if not (u.lower().startswith("https://") or u.lower().startswith("http://")):
        return False
    try:
        parsed = urllib.parse.urlparse(u)
        hostname = parsed.hostname or ""
        if not hostname:
            return False
        # Rejeitar localhost e variantes
        if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return False
        # Rejeitar IPs privados/reservados (ex.: 169.254.x.x, 10.x, 192.168.x, etc.)
        try:
            addr = ipaddress.ip_address(hostname)
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                return False
        except ValueError:
            pass  # hostname é um FQDN, não um IP — permitido
    except Exception:
        return False
    return True


def get_synonyms_ptbr(word: str, base_url: str | None = None) -> list[str]:
    """
    Obtém sinônimos em português do Brasil via API de dicionário (estilo api-dicionario-ptbr).
    Retorna lista de strings; lista vazia em caso de erro ou palavra não encontrada.
    """
    word = (word or "").strip()
    if not word:
        return []
    base = (base_url or API_DICIONARIO_PTBR_BASE).rstrip("/")
    if not _is_safe_http_url(base):
        return []
    url = base + "/" + urllib.parse.quote(word)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pdfsearchable/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 — URL constructed from known API base + URL-encoded word
            data = resp.read().decode("utf-8")
    except Exception:
        return []
    try:
        obj = json.loads(data)
        sinonimos = obj.get("sinonimos") or obj.get("synonyms") or []
        if isinstance(sinonimos, list):
            return [str(s).strip() for s in sinonimos if s and str(s).strip()]
        return []
    except (json.JSONDecodeError, TypeError):
        return []


def get_synonyms_en(word: str, api_key: str | None = None) -> list[str]:
    """
    Obtém sinônimos em inglês (EUA) via API Ninjas Thesaurus.
    Requer chave em api_key ou env API_NINJAS_KEY / PDFSEARCHABLE_API_NINJAS_KEY.
    Retorna lista de strings; lista vazia em caso de erro ou palavra não encontrada.
    """
    word = (word or "").strip()
    if not word:
        return []
    key = (
        api_key
        or os.environ.get("API_NINJAS_KEY")
        or os.environ.get("PDFSEARCHABLE_API_NINJAS_KEY")
    )
    if not key:
        return []
    url = API_NINJAS_THESAURUS_URL + "?" + urllib.parse.urlencode({"word": word})
    try:
        req = urllib.request.Request(
            url, headers={"X-Api-Key": key, "User-Agent": "pdfsearchable/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 — URL constructed from known API base + URL-encoded word
            data = resp.read().decode("utf-8")
    except Exception:
        return []
    try:
        obj = json.loads(data)
        syn = obj.get("synonyms") or []
        if isinstance(syn, list):
            return [str(s).strip() for s in syn if s and str(s).strip()]
        return []
    except (json.JSONDecodeError, TypeError):
        return []


def get_synonyms(
    word: str,
    lang: Literal["pt-BR", "en-US", "pt", "en"] = "pt-BR",
    *,
    api_key_en: str | None = None,
    base_url_ptbr: str | None = None,
) -> list[str]:
    """
    Obtém sinônimos para uma palavra conforme o idioma.
    - pt-BR / pt: API Dicionário PT-BR (base_url_ptbr ou env PDFSEARCHABLE_API_DICIONARIO_PTBR).
    - en-US / en: API Ninjas Thesaurus (api_key_en ou env API_NINJAS_KEY).
    Retorna lista de sinônimos; lista vazia se API indisponível ou sem chave (EN).
    """
    word = (word or "").strip()
    if not word:
        return []
    lang = (lang or "pt-BR").strip()
    if lang in ("en", "en-US"):
        return get_synonyms_en(word, api_key_en)
    return get_synonyms_ptbr(word, base_url_ptbr)
