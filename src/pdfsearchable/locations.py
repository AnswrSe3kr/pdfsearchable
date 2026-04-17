"""
Detecção de referências a cidades, estados, regiões e países no texto indexado.
Usado no report para exibir quantidade de documentos que citam cada local.
Suporta enriquecimento via IA (Ollama): nomes extraídos são mesclados e contados.
Enriquecimento via ViaCEP (CEPs) e IP-API (IPs): locais derivados de CEP e geolocalização por IP.
Geocoding (Nominatim) para locais sem coordenadas, com cache em disco.
"""

import contextlib
import json
import logging
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

from pdfsearchable.geo_apis import (
    IP_API_DELAY_SEC,
    MAX_CEPS_PER_RUN,
    MAX_IPS_PER_RUN,
    extract_ceps_from_text,
    fetch_ip_api,
    fetch_via_cep,
    is_ip_api_enabled,
    is_via_cep_enabled,
)

_log = logging.getLogger("pdfsearchable.locations")

# Locais conhecidos: nome, tipo (estado, cidade, região, país), coordenadas opcionais para mapa
# Nomes mais longos vêm primeiro para evitar que "Rio" case antes de "Rio de Janeiro"
LOCATIONS: list[dict] = [
    # Regiões do Brasil
    {"name": "Centro-Oeste", "kind": "região", "lat": -15.5, "lon": -54.5},
    {"name": "Nordeste", "kind": "região", "lat": -8.0, "lon": -38.0},
    {"name": "Norte", "kind": "região", "lat": -3.0, "lon": -60.0},
    {"name": "Sudeste", "kind": "região", "lat": -19.0, "lon": -45.0},
    {"name": "Sul", "kind": "região", "lat": -27.0, "lon": -51.0},
    # Estados brasileiros (nome completo)
    {"name": "Distrito Federal", "kind": "estado", "lat": -15.79, "lon": -47.88},
    {"name": "Espírito Santo", "kind": "estado", "lat": -20.32, "lon": -40.34},
    {"name": "Mato Grosso do Sul", "kind": "estado", "lat": -20.51, "lon": -54.54},
    {"name": "Minas Gerais", "kind": "estado", "lat": -18.10, "lon": -44.38},
    {"name": "Rio Grande do Norte", "kind": "estado", "lat": -5.81, "lon": -36.59},
    {"name": "Rio Grande do Sul", "kind": "estado", "lat": -30.03, "lon": -51.22},
    {"name": "Santa Catarina", "kind": "estado", "lat": -27.45, "lon": -50.95},
    {"name": "São Paulo", "kind": "estado", "lat": -22.19, "lon": -48.79},
    {"name": "Rio de Janeiro", "kind": "estado", "lat": -22.25, "lon": -42.66},
    {"name": "Amapá", "kind": "estado", "lat": 1.41, "lon": -51.77},
    {"name": "Amazonas", "kind": "estado", "lat": -4.20, "lon": -63.83},
    {"name": "Bahia", "kind": "estado", "lat": -12.96, "lon": -41.67},
    {"name": "Ceará", "kind": "estado", "lat": -5.20, "lon": -39.53},
    {"name": "Goiás", "kind": "estado", "lat": -15.98, "lon": -49.86},
    {"name": "Maranhão", "kind": "estado", "lat": -5.42, "lon": -45.44},
    {"name": "Mato Grosso", "kind": "estado", "lat": -12.64, "lon": -56.92},
    {"name": "Pará", "kind": "estado", "lat": -3.79, "lon": -52.96},
    {"name": "Paraíba", "kind": "estado", "lat": -7.28, "lon": -36.72},
    {"name": "Paraná", "kind": "estado", "lat": -24.89, "lon": -51.55},
    {"name": "Pernambuco", "kind": "estado", "lat": -8.38, "lon": -37.86},
    {"name": "Piauí", "kind": "estado", "lat": -6.60, "lon": -42.28},
    {"name": "Rondônia", "kind": "estado", "lat": -10.83, "lon": -63.34},
    {"name": "Roraima", "kind": "estado", "lat": 1.99, "lon": -61.33},
    {"name": "Sergipe", "kind": "estado", "lat": -10.57, "lon": -37.45},
    {"name": "Tocantins", "kind": "estado", "lat": -9.46, "lon": -48.26},
    {"name": "Acre", "kind": "estado", "lat": -9.02, "lon": -70.81},
    {"name": "Alagoas", "kind": "estado", "lat": -9.57, "lon": -36.78},
    # Cidades (capitais e grandes)
    {"name": "Brasília", "kind": "cidade", "lat": -15.79, "lon": -47.88},
    {"name": "São Paulo", "kind": "cidade", "lat": -23.55, "lon": -46.63},
    {"name": "Rio de Janeiro", "kind": "cidade", "lat": -22.90, "lon": -43.21},
    {"name": "Belo Horizonte", "kind": "cidade", "lat": -19.92, "lon": -43.94},
    {"name": "Salvador", "kind": "cidade", "lat": -12.97, "lon": -38.51},
    {"name": "Fortaleza", "kind": "cidade", "lat": -3.72, "lon": -38.52},
    {"name": "Curitiba", "kind": "cidade", "lat": -25.42, "lon": -49.27},
    {"name": "Recife", "kind": "cidade", "lat": -8.05, "lon": -34.90},
    {"name": "Porto Alegre", "kind": "cidade", "lat": -30.03, "lon": -51.22},
    {"name": "Manaus", "kind": "cidade", "lat": -3.12, "lon": -60.02},
    {"name": "Belém", "kind": "cidade", "lat": -1.46, "lon": -48.50},
    {"name": "Goiânia", "kind": "cidade", "lat": -16.69, "lon": -49.25},
    {"name": "Guarulhos", "kind": "cidade", "lat": -23.46, "lon": -46.53},
    {"name": "Campinas", "kind": "cidade", "lat": -22.91, "lon": -47.06},
    {"name": "São Luís", "kind": "cidade", "lat": -2.53, "lon": -44.30},
    {"name": "Natal", "kind": "cidade", "lat": -5.79, "lon": -35.21},
    {"name": "Florianópolis", "kind": "cidade", "lat": -27.60, "lon": -48.55},
    {"name": "Vitória", "kind": "cidade", "lat": -20.32, "lon": -40.34},
    {"name": "Cuiabá", "kind": "cidade", "lat": -15.60, "lon": -56.10},
    {"name": "Campo Grande", "kind": "cidade", "lat": -20.47, "lon": -54.62},
    # País
    {"name": "Brasil", "kind": "país", "lat": -14.24, "lon": -51.93},
    {"name": "Brazil", "kind": "país", "lat": -14.24, "lon": -51.93},
    {"name": "Argentina", "kind": "país", "lat": -34.60, "lon": -58.38},
    {"name": "Estados Unidos", "kind": "país", "lat": 39.83, "lon": -98.58},
    {"name": "United States", "kind": "país", "lat": 39.83, "lon": -98.58},
    {"name": "EUA", "kind": "país", "lat": 39.83, "lon": -98.58},
    {"name": "USA", "kind": "país", "lat": 39.83, "lon": -98.58},
    {"name": "Portugal", "kind": "país", "lat": 38.72, "lon": -9.14},
    {"name": "França", "kind": "país", "lat": 46.23, "lon": 2.21},
    {"name": "France", "kind": "país", "lat": 46.23, "lon": 2.21},
    {"name": "Reino Unido", "kind": "país", "lat": 55.38, "lon": -3.44},
    {"name": "United Kingdom", "kind": "país", "lat": 55.38, "lon": -3.44},
    {"name": "Inglaterra", "kind": "país", "lat": 52.36, "lon": -1.17},
    {"name": "England", "kind": "país", "lat": 52.36, "lon": -1.17},
    {"name": "Espanha", "kind": "país", "lat": 40.46, "lon": -3.75},
    {"name": "Spain", "kind": "país", "lat": 40.46, "lon": -3.75},
    {"name": "Alemanha", "kind": "país", "lat": 51.17, "lon": 10.45},
    {"name": "Germany", "kind": "país", "lat": 51.17, "lon": 10.45},
    {"name": "Itália", "kind": "país", "lat": 41.87, "lon": 12.57},
    {"name": "Italy", "kind": "país", "lat": 41.87, "lon": 12.57},
    {"name": "México", "kind": "país", "lat": 23.63, "lon": -102.55},
    {"name": "Mexico", "kind": "país", "lat": 23.63, "lon": -102.55},
    {"name": "Chile", "kind": "país", "lat": -35.68, "lon": -71.54},
    {"name": "Colômbia", "kind": "país", "lat": 4.57, "lon": -74.30},
    {"name": "Colombia", "kind": "país", "lat": 4.57, "lon": -74.30},
    {"name": "Paraguai", "kind": "país", "lat": -23.44, "lon": -58.44},
    {"name": "Uruguai", "kind": "país", "lat": -32.52, "lon": -55.77},
    {"name": "Uruguay", "kind": "país", "lat": -32.52, "lon": -55.77},
    {"name": "Peru", "kind": "país", "lat": -9.19, "lon": -75.02},
    {"name": "Venezuela", "kind": "país", "lat": 6.42, "lon": -66.59},
    {"name": "Bolívia", "kind": "país", "lat": -16.29, "lon": -63.59},
    {"name": "Bolivia", "kind": "país", "lat": -16.29, "lon": -63.59},
    {"name": "Equador", "kind": "país", "lat": -1.83, "lon": -78.18},
    {"name": "Ecuador", "kind": "país", "lat": -1.83, "lon": -78.18},
    {"name": "Japão", "kind": "país", "lat": 36.20, "lon": 138.25},
    {"name": "Japan", "kind": "país", "lat": 36.20, "lon": 138.25},
    {"name": "China", "kind": "país", "lat": 35.86, "lon": 104.20},
    {"name": "Índia", "kind": "país", "lat": 20.59, "lon": 78.96},
    {"name": "India", "kind": "país", "lat": 20.59, "lon": 78.96},
    {"name": "Rússia", "kind": "país", "lat": 61.52, "lon": 105.32},
    {"name": "Russia", "kind": "país", "lat": 61.52, "lon": 105.32},
    {"name": "Canadá", "kind": "país", "lat": 56.13, "lon": -106.35},
    {"name": "Canada", "kind": "país", "lat": 56.13, "lon": -106.35},
    {"name": "Austrália", "kind": "país", "lat": -25.27, "lon": 133.78},
    {"name": "Australia", "kind": "país", "lat": -25.27, "lon": 133.78},
    {"name": "África", "kind": "região", "lat": -8.78, "lon": 21.09},
    {"name": "Africa", "kind": "região", "lat": -8.78, "lon": 21.09},
    {"name": "Europa", "kind": "região", "lat": 54.53, "lon": 15.25},
    {"name": "Europe", "kind": "região", "lat": 54.53, "lon": 15.25},
    {"name": "América do Sul", "kind": "região", "lat": -14.24, "lon": -51.93},
    {"name": "América do Norte", "kind": "região", "lat": 54.00, "lon": -105.00},
    {"name": "Ásia", "kind": "região", "lat": 34.05, "lon": 100.62},
    {"name": "Asia", "kind": "região", "lat": 34.05, "lon": 100.62},
    # Mais cidades (internacionais e BR)
    {"name": "Lisboa", "kind": "cidade", "lat": 38.72, "lon": -9.14},
    {"name": "Madrid", "kind": "cidade", "lat": 40.42, "lon": -3.70},
    {"name": "Paris", "kind": "cidade", "lat": 48.86, "lon": 2.35},
    {"name": "Londres", "kind": "cidade", "lat": 51.51, "lon": -0.13},
    {"name": "London", "kind": "cidade", "lat": 51.51, "lon": -0.13},
    {"name": "Berlim", "kind": "cidade", "lat": 52.52, "lon": 13.40},
    {"name": "Berlin", "kind": "cidade", "lat": 52.52, "lon": 13.40},
    {"name": "Roma", "kind": "cidade", "lat": 41.90, "lon": 12.50},
    {"name": "Nova York", "kind": "cidade", "lat": 40.71, "lon": -74.01},
    {"name": "New York", "kind": "cidade", "lat": 40.71, "lon": -74.01},
    {"name": "Washington", "kind": "cidade", "lat": 38.91, "lon": -77.04},
    {"name": "Los Angeles", "kind": "cidade", "lat": 34.05, "lon": -118.24},
    {"name": "Cidade do México", "kind": "cidade", "lat": 19.43, "lon": -99.13},
    {"name": "Buenos Aires", "kind": "cidade", "lat": -34.60, "lon": -58.38},
    {"name": "Lima", "kind": "cidade", "lat": -12.05, "lon": -77.04},
    {"name": "Santiago", "kind": "cidade", "lat": -33.45, "lon": -70.67},
    {"name": "Bogotá", "kind": "cidade", "lat": 4.71, "lon": -74.07},
    {"name": "Bogota", "kind": "cidade", "lat": 4.71, "lon": -74.07},
    {"name": "Tóquio", "kind": "cidade", "lat": 35.68, "lon": 139.65},
    {"name": "Tokyo", "kind": "cidade", "lat": 35.68, "lon": 139.65},
    {"name": "Pequim", "kind": "cidade", "lat": 39.90, "lon": 116.41},
    {"name": "Beijing", "kind": "cidade", "lat": 39.90, "lon": 116.41},
    {"name": "Moscou", "kind": "cidade", "lat": 55.75, "lon": 37.62},
    {"name": "Moscow", "kind": "cidade", "lat": 55.75, "lon": 37.62},
    {"name": "João Pessoa", "kind": "cidade", "lat": -7.12, "lon": -34.86},
    {"name": "Aracaju", "kind": "cidade", "lat": -10.95, "lon": -37.07},
    {"name": "Maceió", "kind": "cidade", "lat": -9.67, "lon": -35.74},
    {"name": "Teresina", "kind": "cidade", "lat": -5.09, "lon": -42.80},
    {"name": "São José dos Campos", "kind": "cidade", "lat": -23.19, "lon": -45.88},
]

# Ordenar por tamanho do nome (decrescente) para casar "Rio de Janeiro" antes de "Rio"
_LOCATIONS_SORTED = sorted(LOCATIONS, key=lambda x: len(x["name"]), reverse=True)


def _pattern_for_name(name: str) -> re.Pattern:
    """Regex que casa o nome como frase (word boundaries, insensível a maiúsculas)."""
    parts = [re.escape(p) for p in name.split()]
    pattern_str = r"\s+".join(parts)
    pattern = r"(?<!\w)" + pattern_str + r"(?!\w)"
    return re.compile(pattern, re.IGNORECASE)


# Padrões pré-compilados uma única vez ao carregar o módulo (evita re.compile() por doc/loc)
_LOCATION_PATTERNS: dict[str, re.Pattern] = {
    loc["name"]: _pattern_for_name(loc["name"]) for loc in _LOCATIONS_SORTED
}


def get_location_refs(docs_text: list[tuple[str, str]]) -> list[dict]:
    """
    Dado uma lista de (file_id, texto_completo) por documento, retorna lista de
    locais citados com quantidade de documentos que os mencionam.
    Cada item: { "name", "kind", "doc_count", "lat", "lon" } (lat/lon opcionais).
    """
    # location_name -> set of file_ids that mention it
    doc_count_by_name: dict[str, set[str]] = defaultdict(set)
    # name -> first loc dict (for kind, lat, lon)
    info_by_name: dict[str, dict] = {}
    for loc in _LOCATIONS_SORTED:
        name = loc["name"]
        if name not in info_by_name:
            info_by_name[name] = loc

    for file_id, text in docs_text:
        if not (text and text.strip()):
            continue
        for loc in _LOCATIONS_SORTED:
            name = loc["name"]
            pattern = _LOCATION_PATTERNS[name]
            if pattern.search(text):
                doc_count_by_name[name].add(file_id)

    out: list[dict] = []
    for name, file_ids in doc_count_by_name.items():
        info = info_by_name.get(name, {})
        out.append(
            {
                "name": name,
                "kind": info.get("kind", "local"),
                "doc_count": len(file_ids),
                "file_ids": list(file_ids),
                "lat": info.get("lat"),
                "lon": info.get("lon"),
            }
        )
    # Ordenar por doc_count decrescente, depois por nome
    out.sort(key=lambda x: (-x["doc_count"], x["name"]))
    return out


def _normalize_name_for_match(name: str) -> str:
    """Normaliza nome para comparação (lower, collapse spaces)."""
    return " ".join((name or "").lower().split())


def _find_location_info(name: str) -> dict[str, Any] | None:
    """Retorna primeiro LOCATIONS que case o nome (case-insensitive)."""
    key = _normalize_name_for_match(name)
    for loc in LOCATIONS:
        if _normalize_name_for_match(loc["name"]) == key:
            return dict(loc)
    return None


def merge_location_refs_with_ia(
    base_refs: list[dict],
    docs_text: list[tuple[str, str]],
    ia_location_names: list[str],
) -> list[dict]:
    """
    Mescla lista de locais extraídos por IA com base_refs (de get_location_refs).
    Para cada nome em ia_location_names: se já estiver em base_refs, mantém; senão
    tenta obter kind/lat/lon de LOCATIONS e conta em quantos docs o nome aparece.
    """
    existing_names = {_normalize_name_for_match(r["name"]) for r in base_refs}
    out = list(base_refs)
    for name in ia_location_names:
        if not name or not name.strip():
            continue
        key = _normalize_name_for_match(name)
        if key in existing_names:
            continue
        existing_names.add(key)
        info = _find_location_info(name)
        file_ids = [
            fid for fid, text in docs_text if text and name.strip().lower() in (text or "").lower()
        ]
        doc_count = len(file_ids) or 1
        out.append(
            {
                "name": name.strip(),
                "kind": info.get("kind", "local") if info else "local",
                "doc_count": doc_count,
                "file_ids": file_ids if file_ids else [],
                "lat": info.get("lat") if info else None,
                "lon": info.get("lon") if info else None,
            }
        )
    out.sort(key=lambda x: (-x["doc_count"], x["name"]))
    return out


def enrich_location_refs_with_apis(
    base_refs: list[dict],
    docs_text: list[tuple[str, str]],
    files_list: list[dict],
) -> list[dict]:
    """
    Enriquece base_refs com locais obtidos via ViaCEP (CEPs no texto) e IP-API (IPs identificados).
    Respeita PDFSEARCHABLE_VIA_CEP e PDFSEARCHABLE_IP_API; aplica limites e delay para IP-API (45 req/min).
    """
    existing_names = {_normalize_name_for_match(r["name"]) for r in base_refs}
    out = list(base_refs)

    # --- CEPs (ViaCEP) ---
    if is_via_cep_enabled() and docs_text:
        all_ceps: list[str] = []
        seen_cep: set[str] = set()
        for _fid, text in docs_text:
            for cep in extract_ceps_from_text(text or ""):
                if cep not in seen_cep:
                    seen_cep.add(cep)
                    all_ceps.append(cep)
        for cep in all_ceps[:MAX_CEPS_PER_RUN]:
            data = fetch_via_cep(cep)
            if not data:
                continue
            localidade = (data.get("localidade") or "").strip()
            uf = (data.get("uf") or "").strip()
            if not localidade:
                continue
            name = f"{localidade} ({uf})" if uf else localidade
            if _normalize_name_for_match(name) in existing_names:
                continue
            existing_names.add(_normalize_name_for_match(name))
            info = _find_location_info(localidade)
            file_ids = [
                fid for fid, text in docs_text if text and cep in (text or "").replace("-", "")
            ]
            doc_count = len(file_ids) or 1
            out.append(
                {
                    "name": name,
                    "kind": "CEP",
                    "doc_count": doc_count,
                    "file_ids": file_ids,
                    "lat": info.get("lat") if info else None,
                    "lon": info.get("lon") if info else None,
                }
            )

    # --- IPs (IP-API) ---
    if is_ip_api_enabled() and files_list:
        all_ips: list[str] = []
        seen_ip: set[str] = set()
        for f in files_list:
            for ip in f.get("identified_ips") or []:
                ip = (ip or "").strip()
                if ip and ip not in seen_ip:
                    seen_ip.add(ip)
                    all_ips.append(ip)
        for i, ip in enumerate(all_ips[:MAX_IPS_PER_RUN]):
            if i > 0:
                time.sleep(IP_API_DELAY_SEC)
            data = fetch_ip_api(ip)
            if not data:
                continue
            city = (data.get("city") or "").strip()
            country = (data.get("country") or "").strip()
            lat = data.get("lat")
            lon = data.get("lon")
            name = f"{city}, {country}" if city and country else (city or country or "Unknown")
            if _normalize_name_for_match(name) in existing_names:
                continue
            existing_names.add(_normalize_name_for_match(name))
            file_ids = [
                f.get("id")
                for f in files_list
                if f.get("id") and ip in (f.get("identified_ips") or [])
            ]
            doc_count = len(file_ids) or 1
            out.append(
                {
                    "name": name,
                    "kind": "IP",
                    "doc_count": doc_count,
                    "file_ids": file_ids,
                    "lat": lat if lat is not None else None,
                    "lon": lon if lon is not None else None,
                }
            )

    out.sort(key=lambda x: (-x["doc_count"], x["name"]))
    return out


def enrich_location_refs_geocode(
    refs: list[dict],
    cache_dir: Path,
    *,
    max_new_lookups: int = 10,
    delay_sec: float = 1.1,
) -> list[dict]:
    """
    Para cada ref sem lat/lon, tenta obter coordenadas via Nominatim (OSM).
    Usa cache em cache_dir/geocode_cache.json (1 req/s) e limita novas lookups.
    """
    cache_file = cache_dir / "geocode_cache.json"
    cache: dict[str, dict] = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception as _e:
            _log.debug("Falha ao carregar geocode_cache.json: %s", _e)
            cache = {}

    out = []
    new_lookups = 0
    cache_updated = False

    for r in list(refs):
        if r.get("lat") is not None and r.get("lon") is not None:
            out.append(r)
            continue
        name = (r.get("name") or "").strip()
        if not name:
            out.append(r)
            continue
        key = _normalize_name_for_match(name)
        if key in cache:
            coords = cache[key]
            out.append({**r, "lat": coords.get("lat"), "lon": coords.get("lon")})
            continue
        if new_lookups >= max_new_lookups:
            out.append(r)
            continue
        try:
            url = f"https://nominatim.openstreetmap.org/search?q={quote(name)}&format=json&limit=1"
            req = Request(url, headers={"User-Agent": "pdfsearchable-report/1.0"})
            with urlopen(req, timeout=8) as resp:  # nosec B310 — HTTPS Nominatim OSM endpoint with URL-encoded query
                data = json.loads(resp.read().decode("utf-8"))
            if data and isinstance(data, list) and len(data) > 0:
                first = data[0]
                lat = first.get("lat")
                lon = first.get("lon")
                if lat is not None and lon is not None:
                    try:
                        lat_f = float(lat)
                        lon_f = float(lon)
                        cache[key] = {"lat": lat_f, "lon": lon_f}
                        cache_updated = True
                        out.append({**r, "lat": lat_f, "lon": lon_f})
                    except (TypeError, ValueError):
                        out.append(r)
                else:
                    out.append(r)
            else:
                out.append(r)
        except Exception as _e:
            _log.debug("Geocoding falhou para '%s': %s", name, _e)
            out.append(r)
        new_lookups += 1
        time.sleep(delay_sec)

    if cache_updated and cache_file.parent.exists():
        with contextlib.suppress(Exception):
            cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    return out
