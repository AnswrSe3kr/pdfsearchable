"""
APIs de geolocalização e endereço (ViaCEP e IP-API).
Usado para enriquecer o report com locais a partir de CEPs e IPs encontrados nos documentos.

- ViaCEP: https://viacep.com.br/ — CEP brasileiro → endereço (localidade, UF). Gratuito, sem chave.
- IP-API: https://ip-api.com/ — IP → cidade, país, lat, lon. Gratuito (não comercial), 45 req/min, sem chave.
"""

import json
import logging
import os
import re
import urllib.request
from typing import Any

_log = logging.getLogger("pdfsearchable.geo_apis")

# ViaCEP: GET https://viacep.com.br/ws/{cep}/json/
VIA_CEP_BASE = "https://viacep.com.br/ws"
# IP-API: GET http://ip-api.com/json/{ip}?fields=status,country,city,lat,lon,regionName
# Free: 45 requests/minute; HTTP only (no HTTPS on free tier)
IP_API_BASE = "http://ip-api.com/json"

# CEP: exatamente 8 dígitos, opcional hífen; (?!\d)/(?<!\d) evita casar dentro de 9+ dígitos
CEP_PATTERN = re.compile(r"(?<!\d)\d{5}-?\d{3}(?!\d)")

# Limites para não sobrecarregar APIs (report é gerado ao arrancar o serve)
MAX_CEPS_PER_RUN = 25
MAX_IPS_PER_RUN = 15
# IP-API free: 45/min → ~1.4 s entre chamadas para ficar seguro
IP_API_DELAY_SEC = 1.5


def _is_safe_http_url(url: str) -> bool:
    u = (url or "").strip().lower()
    return u.startswith("https://") or u.startswith("http://")


def _urlopen_json(url: str, timeout: int = 10) -> dict | None:
    if not _is_safe_http_url(url):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pdfsearchable/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 — URL pre-validated by _is_safe_http_url()
            data = resp.read().decode("utf-8")
    except Exception as _e:
        _log.debug("_urlopen_json: falha ao abrir %s: %s", url, _e)
        return None
    try:
        return json.loads(data)
    except Exception as _e:
        _log.debug("_urlopen_json: falha ao parsear JSON de %s: %s", url, _e)
        return None


def fetch_via_cep(cep: str) -> dict[str, Any] | None:
    """
    Consulta ViaCEP (https://viacep.com.br/). Retorna dict com localidade, uf, logradouro, bairro, etc.
    cep: string com 8 dígitos (com ou sem hífen).
    """
    cep_clean = re.sub(r"\D", "", cep)
    if len(cep_clean) != 8:
        return None
    url = f"{VIA_CEP_BASE}/{cep_clean}/json/"
    data = _urlopen_json(url, timeout=8)
    if not data or "erro" in data:
        return None
    return data


def fetch_ip_api(ip: str) -> dict[str, Any] | None:
    """
    Consulta IP-API (https://ip-api.com/). Retorna dict com status, country, city, lat, lon, regionName.
    Uso gratuito: até 45 requisições/minuto; apenas HTTP.
    """
    ip = (ip or "").strip()
    if not ip:
        return None
    # Campos mínimos para o report
    url = f"{IP_API_BASE}/{ip}?fields=status,country,city,lat,lon,regionName"
    data = _urlopen_json(url, timeout=8)
    if not data or data.get("status") != "success":
        return None
    return data


def extract_ceps_from_text(text: str) -> list[str]:
    """Extrai CEPs (8 dígitos, opcional hífen) do texto. Retorna lista única normalizada (XXXXXXXX)."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in CEP_PATTERN.finditer(text):
        cep_norm = re.sub(r"\D", "", m.group(0))
        if cep_norm not in seen:
            seen.add(cep_norm)
            out.append(cep_norm)
    return out


def is_via_cep_enabled() -> bool:
    """True se PDFSEARCHABLE_VIA_CEP não estiver definido como 0/false/no."""
    v = (os.environ.get("PDFSEARCHABLE_VIA_CEP") or "1").strip().lower()
    return v in ("1", "true", "yes")


def is_ip_api_enabled() -> bool:
    """True se PDFSEARCHABLE_IP_API não estiver definido como 0/false/no."""
    v = (os.environ.get("PDFSEARCHABLE_IP_API") or "1").strip().lower()
    return v in ("1", "true", "yes")
