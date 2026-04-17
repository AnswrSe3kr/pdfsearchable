"""
Busca em texto indexado com máscaras para:
IPs (IPv4 e IPv6), CPFs, CNPJs, e-mails, domínios (FQDN), links e redes sociais.

Referências:
- IPv4/IPv6: gov.br/gestao (implementação IPv6 GOV.BR)
- CNPJ alfanumérico: gov.br/receitafederal (julho/2026)
"""

import re
from dataclasses import dataclass
from collections.abc import Iterator

# ---------------------------------------------------------------------------
# Constantes reutilizáveis (simplificação e manutenção)
# ---------------------------------------------------------------------------

# IPv4: um octeto 0-255
_OCT = r"(?:25[0-5]|2[0-4]\d|1?\d{1,2})"

# IPv6: um segmento hexadecimal (1-4 caracteres)
_HEX = r"[0-9a-fA-F]{1,4}"

# Prefixo opcional para URLs de redes sociais
_SOCIAL_PREFIX = r"(?:https?://)?(?:www\.)?"

# ---------------------------------------------------------------------------
# Padrões compilados
# ---------------------------------------------------------------------------

# IPv4: 4 octetos (ex.: 192.168.1.1)
IPV4_PATTERN = re.compile(rf"\b(?:{_OCT}\.){{3}}{_OCT}\b")

# IPv6: formas mais comuns em texto (completo, comprimido ::, e IPv4-mapped)
# Não valida rigorosamente; suficiente para detecção em documentos.
IPV6_PATTERN = re.compile(
    rf"\b(?:{_HEX}:){{7}}{_HEX}\b"
    rf"|\b::(?:{_HEX}:)*{_HEX}?\b"
    rf"|\b(?:{_HEX}:)+::(?:{_HEX}:)*{_HEX}?\b"
    rf"|\b(?:{_HEX}:){{6}}{_OCT}\.{_OCT}\.{_OCT}\.{_OCT}\b"
)

# CPF: formatado XXX.XXX.XXX-XX ou 11 dígitos; (?!\d) evita casar dentro de CNPJ/outros números
CPF_PATTERN = re.compile(r"\b\d{3}(?:\.\d{3}){2}-\d{2}(?!\d)\b|\b\d{11}(?!\d)\b")

# CNPJ: numérico 00.000.000/0000-00 ou 14 dígitos; alfanumérico (2026+) XX.XXX.XXX/XXXX-XX
_CNPJ_NUM = r"\b\d{2}(?:\.\d{3}){2}/\d{4}-\d{2}(?!\d)\b|\b\d{14}(?!\d)\b"
_CNPJ_ALNUM = r"\b[A-Za-z0-9]{2}(?:\.[A-Za-z0-9]{3}){2}/[A-Za-z0-9]{4}-\d{2}(?!\d)\b"
CNPJ_PATTERN = re.compile(f"{_CNPJ_NUM}|{_CNPJ_ALNUM}")

# E-mail: local@domínio.tld (local pode ter +; domínio sem espaço)
EMAIL_PATTERN = re.compile(
    r"\b[\w.%-+]+@(?:[\w-]+\.)+[A-Za-z]{2,}\b",
    re.IGNORECASE,
)

# Domínio (FQDN) sem esquema: subdomínio.domínio.tld
# Usa whitelist de TLDs comuns (gTLDs + ccTLDs relevantes) para reduzir
# falsos positivos como "MP.Alder", "G.P.ERMANALD", etc.
_FQDN_TLDS = (
    r"(?:com|org|net|gov|edu|mil|int|info|biz|name|pro|aero|asia|cat|coop|"
    r"jobs|mobi|museum|tel|travel|xxx|io|ai|app|dev|tech|online|site|xyz|"
    r"me|co|tv|cc|ly|us|uk|br|pt|es|fr|de|it|nl|be|lu|ch|at|se|no|fi|dk|"
    r"pl|cz|sk|hu|gr|ro|bg|ua|ru|tr|il|ae|sa|za|eg|ma|ng|ke|jp|cn|hk|tw|"
    r"kr|in|id|my|sg|th|ph|vn|au|nz|ca|mx|ar|cl|co|pe|uy|bo|ve|cr|pa)"
)
FQDN_PATTERN = re.compile(
    r"(?<![\w.-])"
    r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+" + _FQDN_TLDS + r"(?![\w-])",
    re.IGNORECASE,
)

# URL com esquema; para de consumir em espaço, <>, aspas (reduz captura de pontuação final)
URL_PATTERN = re.compile(
    r"https?://[^\s<>\"']+",
    re.IGNORECASE,
)


# Redes sociais: mesmo padrão, domínio varia
def _social(domains: str) -> re.Pattern:
    return re.compile(rf"{_SOCIAL_PREFIX}(?:{domains})/\S+", re.IGNORECASE)


SOCIAL_TWITTER = _social(r"twitter\.com|x\.com")
SOCIAL_INSTAGRAM = _social(r"instagram\.com")
SOCIAL_FACEBOOK = _social(r"fb\.com|facebook\.com")
SOCIAL_LINKEDIN = _social(r"linkedin\.com")

# Dicionário unificado
PATTERNS = {
    "ipv4": IPV4_PATTERN,
    "ipv6": IPV6_PATTERN,
    "cpf": CPF_PATTERN,
    "cnpj": CNPJ_PATTERN,
    "email": EMAIL_PATTERN,
    "domain": FQDN_PATTERN,
    "url": URL_PATTERN,
    "social_twitter": SOCIAL_TWITTER,
    "social_instagram": SOCIAL_INSTAGRAM,
    "social_facebook": SOCIAL_FACEBOOK,
    "social_linkedin": SOCIAL_LINKEDIN,
}

# Ordem para apply_masks e search_with_masks (evita duplicatas)
_PATTERN_ORDER = [
    "ipv4",
    "ipv6",
    "cpf",
    "cnpj",
    "email",
    "domain",
    "url",
    "social_twitter",
    "social_instagram",
    "social_facebook",
    "social_linkedin",
]


def literal_pattern(term: str) -> re.Pattern:
    """Termo literal para busca (case-insensitive)."""
    return re.compile(re.escape(term), re.IGNORECASE)


@dataclass
class SearchHit:
    """Trecho onde a busca encontrou algo."""

    file_id: str
    file_name: str
    page: int
    mask_type: str
    matched_text: str
    snippet: str
    start_offset: int
    end_offset: int


def apply_masks(text: str) -> dict[str, list[tuple[int, int, str]]]:
    """Aplica todas as máscaras; retorna por tipo (start, end, matched). Sem duplicatas."""
    result: dict[str, list[tuple[int, int, str]]] = {}
    seen: set[tuple[int, int]] = set()
    for name in _PATTERN_ORDER:
        pattern = PATTERNS[name]
        matches = []
        for m in pattern.finditer(text):
            key = (m.start(), m.end())
            if key in seen:
                continue
            seen.add(key)
            matches.append((m.start(), m.end(), m.group()))
        if matches:
            result[name] = matches
    return result


def search_term_in_text(term: str, text: str) -> list[tuple[int, int, str]]:
    """Busca termo literal; retorna (start, end, matched)."""
    pat = literal_pattern(term)
    return [(m.start(), m.end(), m.group()) for m in pat.finditer(text)]


def mask_type_from_alias(alias: str) -> str | list[str] | None:
    """Alias → chave em PATTERNS. 'ip' → ['ipv4', 'ipv6']."""
    alias = alias.lower().strip()
    if alias in PATTERNS:
        return alias
    aliases: dict[str, str | list[str]] = {
        "ip": ["ipv4", "ipv6"],
        "ipv4": "ipv4",
        "ipv6": "ipv6",
        "cpf": "cpf",
        "cnpj": "cnpj",
        "email": "email",
        "e-mail": "email",
        "dominio": "domain",
        "domínio": "domain",
        "fqdn": "domain",
        "url": "url",
        "link": "url",
        "twitter": "social_twitter",
        "instagram": "social_instagram",
        "facebook": "social_facebook",
        "linkedin": "social_linkedin",
    }
    return aliases.get(alias)


def search_with_masks(
    term: str,
    text: str,
    *,
    use_masks: bool = True,
) -> Iterator[tuple[str, int, int, str]]:
    """Busca termo + máscaras; gera (mask_type, start, end, matched)."""
    for start, end, matched in search_term_in_text(term, text):
        yield ("term", start, end, matched)
    if not use_masks:
        return
    key = mask_type_from_alias(term)
    if key:
        keys = [key] if isinstance(key, str) else key
        for k in keys:
            if k not in PATTERNS:
                continue
            for m in PATTERNS[k].finditer(text):
                yield (k, m.start(), m.end(), m.group())
    else:
        reported: set[tuple[int, int]] = set()
        for name in _PATTERN_ORDER:
            for m in PATTERNS[name].finditer(text):
                pos = (m.start(), m.end())
                if pos not in reported:
                    reported.add(pos)
                    yield (name, m.start(), m.end(), m.group())
