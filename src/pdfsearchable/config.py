"""
Configuração opcional via arquivo .pdfsearchable/config.json ou config.toml.
Valores do arquivo são aplicados como defaults quando a variável de ambiente não está definida.
"""

import json
import os
from pathlib import Path
from typing import Any

PROJECT_DIR = Path.cwd()
CONFIG_DIR = PROJECT_DIR / ".pdfsearchable"
CONFIG_JSON = CONFIG_DIR / "config.json"
CONFIG_TOML = CONFIG_DIR / "config.toml"

_loaded: dict[str, Any] | None = None


def _load_toml() -> dict[str, Any] | None:
    """Carrega config.toml se existir e tomllib/tomli disponível."""
    if not CONFIG_TOML.exists():
        return None
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            return None
    with open(CONFIG_TOML, "rb") as f:
        data = tomllib.load(f)
    return data.get("pdfsearchable") or data.get("tool", {}).get("pdfsearchable") or data


def _load_json() -> dict[str, Any] | None:
    """Carrega config.json se existir."""
    if not CONFIG_JSON.exists():
        return None
    try:
        with open(CONFIG_JSON, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _load_from_path(path: Path) -> dict[str, Any]:
    """Carrega config a partir de um arquivo (config.toml ou config.json). Usado com --config."""
    raw: dict[str, Any] | None = None
    if path.suffix.lower() == ".toml":
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                return {}
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
            raw = data.get("pdfsearchable") or data.get("tool", {}).get("pdfsearchable") or data
        except (OSError, TypeError):
            return {}
    elif path.suffix.lower() == ".json":
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("pdfsearchable", data) if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}
    if not raw or not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    prefix = "PDFSEARCHABLE_"
    for k, v in raw.items():
        if not isinstance(k, str) or k.startswith("_"):
            continue
        key_upper = (prefix + k).upper() if prefix else k.upper()
        if isinstance(v, (str, int, float, bool)) and v is not None:
            out[key_upper] = "1" if v is True else "0" if v is False else str(v)
        elif key_upper == "PDFSEARCHABLE_SEARCH_SYNONYMS" and isinstance(v, dict):
            out["PDFSEARCHABLE_SEARCH_SYNONYMS"] = {
                str(a).strip().lower(): str(b).strip() for a, b in v.items() if a and b
            }
        elif isinstance(v, dict):
            for k2, v2 in v.items():
                if (
                    isinstance(k2, str)
                    and not k2.startswith("_")
                    and isinstance(v2, (str, int, float, bool))
                    and v2 is not None
                ):
                    out[(key_upper + "_" + k2.upper())] = (
                        "1" if v2 is True else "0" if v2 is False else str(v2)
                    )
    return out


def load_config() -> dict[str, Any]:
    """
    Carrega a configuração de .pdfsearchable/config.toml ou config.json.
    Se PDFSEARCHABLE_CONFIG_FILE estiver definido (ex.: via --config), carrega desse arquivo.
    TOML tem precedência se existir; depois JSON. Retorna dict vazio se nenhum existir.
    Chaves podem ser em UPPER (env-style) ou em lower (seção [pdfsearchable]).
    """
    global _loaded
    config_file = os.environ.get("PDFSEARCHABLE_CONFIG_FILE", "").strip()
    if config_file:
        path = Path(config_file).resolve()
        if path.exists():
            _loaded = _load_from_path(path)
            return _loaded or {}
        _loaded = {}
        return _loaded
    if _loaded is not None:
        return _loaded
    _loaded = {}
    raw = _load_toml() or _load_json()
    if not raw:
        return _loaded
    # Suporte a chave "pdfsearchable" ou "tool.pdfsearchable" aninhada
    prefix = ""
    if "pdfsearchable" in raw and isinstance(raw["pdfsearchable"], dict):
        raw = raw["pdfsearchable"]
        prefix = "PDFSEARCHABLE_"
    elif "tool" in raw and isinstance(raw["tool"], dict) and "pdfsearchable" in raw["tool"]:
        raw = raw["tool"]["pdfsearchable"]
        prefix = "PDFSEARCHABLE_"
    for k, v in raw.items():
        if not isinstance(k, str) or k.startswith("_"):
            continue
        key_upper = (prefix + k).upper() if prefix else k.upper()
        if isinstance(v, (str, int, float, bool)) and v is not None:
            _loaded[key_upper] = "1" if v is True else "0" if v is False else str(v)
        elif (
            key_upper == "PDFSEARCHABLE_SEARCH_SYNONYMS" or key_upper == "SEARCH_SYNONYMS"
        ) and isinstance(v, dict):
            _loaded["PDFSEARCHABLE_SEARCH_SYNONYMS" if prefix else "SEARCH_SYNONYMS"] = {
                str(a).strip().lower(): str(b).strip() for a, b in v.items() if a and b
            }
        elif isinstance(v, dict):
            for k2, v2 in v.items():
                if (
                    isinstance(k2, str)
                    and not k2.startswith("_")
                    and isinstance(v2, (str, int, float, bool))
                    and v2 is not None
                ):
                    _loaded[(key_upper + "_" + k2.upper())] = (
                        "1" if v2 is True else "0" if v2 is False else str(v2)
                    )
    return _loaded


def get_config_value(key: str, default: Any = None) -> Any:
    """
    Retorna o valor da configuração: primeiro variável de ambiente (key),
    depois arquivo de config (key ou key em UPPER), depois default.
    """
    env_val = os.environ.get(key)
    if env_val is not None and env_val != "":
        return env_val
    cfg = load_config()
    val = cfg.get(key) or cfg.get(key.upper())
    return val if val is not None else default


def get_search_synonyms() -> dict[str, str]:
    """
    Retorna dicionário de sinônimos para busca: { "termo": "equivalente", ... }.
    Fonte: env PDFSEARCHABLE_SEARCH_SYNONYMS (JSON) ou config search_synonyms.
    """
    env_raw = os.environ.get("PDFSEARCHABLE_SEARCH_SYNONYMS", "").strip()
    if env_raw:
        try:
            data = json.loads(env_raw)
            if isinstance(data, dict):
                return {str(k).strip().lower(): str(v).strip() for k, v in data.items() if k and v}
        except json.JSONDecodeError:
            pass
    cfg = load_config()
    raw = (
        cfg.get("PDFSEARCHABLE_SEARCH_SYNONYMS")
        or cfg.get("SEARCH_SYNONYMS")
        or cfg.get("search_synonyms")
    )
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {str(k).strip().lower(): str(v).strip() for k, v in raw.items() if k and v}
    if isinstance(raw, (list, tuple)):
        out = {}
        for item in raw:
            if isinstance(item, str) and "=" in item:
                k, _, v = item.partition("=")
                if k.strip() and v.strip():
                    out[k.strip().lower()] = v.strip()
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                out[str(item[0]).strip().lower()] = str(item[1]).strip()
        return out
    return {}


def apply_config_to_env() -> None:
    """
    Preenche os.environ com as chaves do config que ainda não estão definidas.
    Útil para que o resto do código continue usando os.environ.get() sem mudanças.
    Chaves no config devem estar em UPPER (ex.: PDFSEARCHABLE_AI).
    """
    cfg = load_config()
    for k, v in cfg.items():
        if isinstance(k, str) and k not in os.environ and v is not None:
            os.environ[k] = "1" if v is True else "0" if v is False else str(v)


# Variáveis de ambiente inteiras: (min, max, default_display).
# default_display é o valor padrão exibido ao utilizador quando o valor é inválido.
_INT_ENV_VARS: dict[str, tuple[int, int, str]] = {
    "PDFSEARCHABLE_WORKERS": (1, 64, "número de CPUs disponíveis"),
    "PDFSEARCHABLE_PAGE_TIMEOUT": (10, 3600, "120"),
    "PDFSEARCHABLE_OCR_DOC_TIMEOUT": (60, 86400, "máx(300, páginas × 60)"),
    "PDFSEARCHABLE_ASK_RATE_LIMIT": (0, 10000, "30"),
    "PDFSEARCHABLE_ASK_TIMEOUT": (10, 600, "90"),
    "PDFSEARCHABLE_PORT": (1, 65535, "8000"),
    "PDFSEARCHABLE_FTS_SNIPPET_TOKENS": (5, 200, "30"),
    "PDFSEARCHABLE_MAX_SEARCH_RESULTS": (1, 10000, "50"),
    "PDFSEARCHABLE_OCR_MIN_CONFIDENCE_VS_NATIVE": (0, 100, "15"),
    "PDFSEARCHABLE_MAX_ANNOTATIONS": (1, 5000, "200"),
    "PDFSEARCHABLE_MAX_IMAGES": (1, 5000, "100"),
}

# Variáveis que devem ser URLs HTTP/HTTPS válidas.
_URL_ENV_VARS: tuple[str, ...] = ("PDFSEARCHABLE_WEBHOOK_URL",)


def validate_config_env() -> list[str]:
    """
    Valida os tipos e intervalos das variáveis de ambiente de configuração.
    Retorna lista de strings de aviso — lista vazia significa tudo OK.
    Cada aviso inclui o valor inválido definido, o intervalo aceite e o padrão
    que será usado em substituição, para que o utilizador saiba o que corrigir.
    Não lança excepção; apenas colecta avisos para o chamador exibir.
    Deve ser chamada após apply_config_to_env() no arranque do programa.
    """
    warnings: list[str] = []

    for var, (lo, hi, default_display) in _INT_ENV_VARS.items():
        val = os.environ.get(var, "").strip()
        if not val:
            continue
        try:
            n = int(val)
            if not lo <= n <= hi:
                warnings.append(
                    f"{var}={val!r} está fora do intervalo válido [{lo}–{hi}]. "
                    f"Valor ignorado; será usado o padrão: {default_display}. "
                    f"Corrija com: export {var}=<valor entre {lo} e {hi}>"
                )
        except ValueError:
            warnings.append(
                f"{var}={val!r} não é um número inteiro válido. "
                f"Valor ignorado; será usado o padrão: {default_display}. "
                f"Corrija com: export {var}=<inteiro entre {lo} e {hi}>"
            )

    for var in _URL_ENV_VARS:
        val = os.environ.get(var, "").strip()
        if val and not (val.startswith("http://") or val.startswith("https://")):
            warnings.append(
                f"{var}={val!r} não é uma URL HTTP/HTTPS válida "
                f"(deve começar com http:// ou https://). "
                f"Webhooks não serão disparados até que o valor seja corrigido."
            )

    return warnings
