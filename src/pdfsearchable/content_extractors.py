"""
Extração de conteúdo do texto indexado: resumo (Ollama), tags, valores monetários, partes/participantes,
e-mails, CPFs, CNPJs e IPs para exibição na visualização do documento.
Usado pelo indexer para enriquecer metadados (IA9, ID5, ID8, ID9).
"""

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from pdfsearchable.audit import get_logger as _get_logger
from pdfsearchable.search import (
    CPF_PATTERN,
    CNPJ_PATTERN,
    EMAIL_PATTERN,
    FQDN_PATTERN,
    IPV4_PATTERN,
    IPV6_PATTERN,
    URL_PATTERN,
)

_log = _get_logger("pdfsearchable.content_extractors")


# ---------------------------------------------------------------------------
# Validação de dígitos verificadores (CPF/CNPJ)
# ---------------------------------------------------------------------------


def _validate_cpf(cpf: str) -> bool:
    """
    Valida dígitos verificadores do CPF (algoritmo oficial Receita Federal).
    Aceita CPF formatado (XXX.XXX.XXX-XX) ou 11 dígitos consecutivos.
    Rejeita sequências trivialmente inválidas (todos iguais: 111.111.111-11, etc.).
    """
    digits = re.sub(r"\D", "", cpf)
    if len(digits) != 11:
        return False
    # Rejeitar todos iguais (00000000000, 11111111111, …)
    if len(set(digits)) == 1:
        return False
    # Primeiro dígito verificador
    total = sum(int(digits[i]) * (10 - i) for i in range(9))
    r = total % 11
    d1 = 0 if r < 2 else 11 - r
    if int(digits[9]) != d1:
        return False
    # Segundo dígito verificador
    total = sum(int(digits[i]) * (11 - i) for i in range(10))
    r = total % 11
    d2 = 0 if r < 2 else 11 - r
    return int(digits[10]) == d2


def _validate_cnpj(cnpj: str) -> bool:
    """
    Valida dígitos verificadores do CNPJ numérico (14 dígitos) — algoritmo oficial.
    CNPJ alfanumérico (2026+) não tem algoritmo público definido; aceito sem validação.
    Rejeita sequências trivialmente inválidas (todos iguais).
    """
    digits = re.sub(r"\D", "", cnpj)
    # CNPJ alfanumérico: se contém letras, aceitar sem validação de dígitos
    if re.search(r"[A-Za-z]", cnpj):
        return True
    if len(digits) != 14:
        return False
    if len(set(digits)) == 1:
        return False
    # Primeiro dígito verificador
    weights1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    total = sum(int(digits[i]) * weights1[i] for i in range(12))
    r = total % 11
    d1 = 0 if r < 2 else 11 - r
    if int(digits[12]) != d1:
        return False
    # Segundo dígito verificador
    weights2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    total = sum(int(digits[i]) * weights2[i] for i in range(13))
    r = total % 11
    d2 = 0 if r < 2 else 11 - r
    return int(digits[13]) == d2


# Padrões para valores monetários (ID8)
# Exige pelo menos um dígito imediatamente após o símbolo (evitar "R$ " sem valor).
# \b evita casar "XEUR" ou "LR$"; BRL/EUR/USD aceitam espaços e símbolos opcionais.
_RE_BRL = re.compile(
    r"\bR\s*\$\s*\d[\d.,]*(?:\s*(?:mil|milhão|milhões|k|mm|mi))?",
    re.IGNORECASE,
)
_RE_EUR = re.compile(
    r"\b(?:EUR\s*(?:€\s*)?[\d.,]+|€\s*[\d.,]+|[\d.,]+\s*€)",
    re.IGNORECASE,
)
_RE_USD = re.compile(
    r"\b(?:USD\s*(?:\$\s*)?[\d.,]+|(?<!R)\$\s*[\d.,]+|[\d.,]+\s*USD)",
    re.IGNORECASE,
)
_RE_NUM = re.compile(r"[\d.,]+")

# ---------------------------------------------------------------------------
# Padrões para entidades adicionais
# ---------------------------------------------------------------------------

# Telefone brasileiro: exige uma de três formas canónicas para evitar falsos
# positivos (ex.: "62 19631965" = ano por extenso). Aceita:
#   +55 (XX) 9XXXX-XXXX / +55 XX 9XXXX-XXXX
#   (XX) 9XXXX-XXXX  (parênteses no DDD)
#   XX 9XXXX-XXXX ou XX XXXX-XXXX (separador hífen/ponto obrigatório no meio)
# DDD válido: 11-99 (primeiro dígito 1-9).
_RE_PHONE_BR = re.compile(
    r"(?<!\d)"
    r"(?:"
    # Forma 1: com +55 obrigatório e DDD (com ou sem parênteses)
    r"\+55[\s.-]?\(?[1-9]\d\)?[\s.-]?9?\d{4}[-.\s]\d{4}"
    r"|"
    # Forma 2: (XX) ... — parênteses no DDD obrigatórios
    r"\([1-9]\d\)[\s.-]?9?\d{4}[-.\s]\d{4}"
    r"|"
    # Forma 3: sem parênteses — exige separador entre DDD e número, e
    # hífen/ponto obrigatório entre prefixo e sufixo (rejeita "62 19631965")
    r"[1-9]\d[\s.-]9?\d{4}[-.]\d{4}"
    r")"
    r"(?!\d)"
)

# CEP brasileiro: exige forma com hífen (XXXXX-XXX). O formato sem hífen gera
# muitos falsos positivos em textos históricos com sequências de anos.
_RE_CEP = re.compile(r"(?<!\d)\d{5}-\d{3}(?!\d)")

# Heurística OCR para normalizar prefixos "www" corrompidos:
# casa variantes como "avww", "vww", "aww", "awww" → serão reescritas para "www".
_OCR_WWW_NOISE = re.compile(r"[a-z]?v?w{2,3}", re.IGNORECASE)

# Palavras curtas comuns (pt/en/es/de/fr) que geram falsos positivos quando
# aparecem antes de um TLD curto (ex.: "in my", "co uk", "de ja"). Usadas para
# rejeitar domínios onde o label raiz é uma stop-word.
_DOMAIN_STOP_LABELS = frozenset(
    {
        # inglês
        "in",
        "on",
        "at",
        "is",
        "it",
        "me",
        "my",
        "or",
        "as",
        "to",
        "do",
        "go",
        "so",
        "be",
        "of",
        "if",
        "no",
        "we",
        "he",
        "us",
        "an",
        "by",
        "up",
        "the",
        "and",
        "for",
        "but",
        "you",
        "are",
        "not",
        "all",
        "any",
        "can",
        "had",
        "her",
        "his",
        "how",
        "one",
        "our",
        "out",
        "see",
        "two",
        "who",
        "use",
        # português
        "de",
        "da",
        "do",
        "em",
        "no",
        "na",
        "os",
        "as",
        "se",
        "um",
        "uma",
        "que",
        "por",
        "com",
        "sem",
        "mas",
        "mio",
        # espanhol / alemão comuns
        "el",
        "la",
        "en",
        "es",
        "lo",
        "ya",
        "su",
        "yo",
        "ver",
        "ser",
    }
)

# TLDs frequentes; se o TLD estiver aqui, exige label raiz com pelo menos 3
# chars. Para TLDs menos comuns (ex.: .my, .io) exige raiz mais longa (>=4).
_COMMON_TLDS = frozenset(
    {
        "com",
        "org",
        "net",
        "gov",
        "edu",
        "mil",
        "int",
        "info",
        "biz",
        "br",
        "pt",
        "es",
        "fr",
        "uk",
        "us",
        "ca",
        "au",
        "jp",
        "cn",
        "kr",
        "it",
    }
)


def _is_valid_domain(domain: str) -> bool:
    """Valida um FQDN capturado, rejeitando ruído de OCR comum.

    Regras:
    1. Comprimento total entre 4 e 253 chars (RFC 1035).
    2. Label raiz (antes do TLD) não é stop-word (in, my, de, mio, ...).
    3. Label raiz >= 3 chars se TLD comum, >= 4 se TLD raro.
    4. Nenhum label pode começar/terminar com hífen.
    5. Rejeita domínios com mais de 3 hífens no label raiz (ruído OCR).
    """
    d = domain.strip().lower()
    if not (4 <= len(d) <= 253):
        return False
    labels = d.split(".")
    # Profundidade razoável: a.b.c.d.e no máximo. Mais do que isso quase
    # sempre é ruído OCR com pontos no meio de palavras.
    if len(labels) < 2 or len(labels) > 5:
        return False
    # Hífens nas bordas de labels
    for lab in labels:
        if not lab or lab.startswith("-") or lab.endswith("-"):
            return False
    tld = labels[-1]
    # Label raiz = último label significativo antes do TLD. Se começar com
    # "www" ignoramos o prefixo ao avaliar a raiz.
    start = 1 if labels[0] == "www" and len(labels) >= 3 else 0
    root = labels[start]
    if root in _DOMAIN_STOP_LABELS:
        return False
    min_root = 3 if tld in _COMMON_TLDS else 4
    if len(root) < min_root:
        return False
    # Excesso de hífens no label raiz → quase certamente OCR ruidoso
    if root.count("-") > 2:
        return False
    # Labels intermediários com padrão "xx-NN" (letra + hífen + dígitos) são
    # artefactos típicos de OCR sobre datas/números em texto ("de-15-de").
    for lab in labels[start + 1 : -1]:
        if "-" in lab and any(c.isdigit() for c in lab):
            return False
    return True


# Número de processo judicial (CNJ): NNNNNNN-DD.AAAA.J.TR.OOOO
_RE_PROCESSO_CNJ = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")

# Placa de veículo: ABC-1234 (antiga) ou ABC1D23 (Mercosul)
# Exclui meses abreviados (JAN, FEB, MAR, etc.) seguidos de ano
_RE_PLACA = re.compile(
    r"\b(?!JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[A-Z]{3}-?\d[A-Z0-9]\d{2}\b"
)

# RG: "RG nº 12.345.678-X" ou "RG: 12345678" (contextual)
_RE_RG = re.compile(
    r"\b(?:RG|R\.G\.|Identidade|Carteira\s+de\s+Identidade)"
    r"\s*[:nº.°]*\s*"
    r"(\d[\d.\s-]{4,14}\d[- ]?[A-Za-z0-9]?)",
    re.IGNORECASE,
)

# Protocolo/Referência: "Protocolo nº 12345", "Ref.: ABC-123/2024", "Proc. 0001234"
# Exige pelo menos um dígito no valor capturado para evitar palavras como "process"
_RE_PROTOCOLO = re.compile(
    r"\b(?:Protocolo|Ref|Ofício|Memorando|SEI)"
    r"\.?\s*(?:n[.ºo°]?\s*)?"
    r"([\dA-Z][\dA-Z./-]{2,30}(?=\b))",
    re.IGNORECASE,
)

# Hashes: MD5 (32 hex), SHA-1 (40), SHA-256 (64)
_RE_HASH = re.compile(r"\b[a-f0-9]{32}(?:[a-f0-9]{8}(?:[a-f0-9]{24})?)?\b", re.IGNORECASE)

# Coordenadas GPS: pares lat/lon com 4+ decimais (ex: -23.5505, -46.6333)
_RE_COORD = re.compile(r"(-?\d{1,3}\.\d{4,8})\s*[,;\s]\s*(-?\d{1,3}\.\d{4,8})")

# Timestamps ISO 8601 com hora: 2024-03-15T14:30:00, 2024-03-15 14:30
_RE_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?\b"
)

# Referências legais: Lei, Art., Decreto, Portaria, Resolução, etc.
# Exige palavras-chave específicas (não "IN" sozinho — colide com inglês "in")
_RE_LEI = re.compile(
    r"\b(?:Lei|Art(?:igo)?|Decreto|Portaria|Instrução\s+Normativa|Resolução|Medida\s+Provisória|Emenda\s+Constitucional)"
    r"\.?\s*(?:n[.ºo°]?\s*)?"
    r"(\d[\d./-]{0,20}\d)",
    re.IGNORECASE,
)

# Partes/participantes (ID9): rótulos com word boundary; captura o trecho após (3–80 chars).
_RE_PARTIES = re.compile(
    r"(?:^|\n)\s*(?:Parte\s+[A-Za-z]|Outorgante|Outorgado|Presentes?|Requerente|Autor|Réu|"
    r"Contratante|Contratada|Fornecedor|Cliente|Testemunha|Vendedor|Comprador|Exequente|Executado)\b"
    r"[:\s]*([^\n]{3,80})",
    re.MULTILINE | re.IGNORECASE,
)

# Tags adicionais por palavra-chave no texto (ID5 subtipos)
TAG_HINTS: dict[str, list[str]] = {
    "trabalhista": ["trabalhista", "clt", "consolidação das leis do trabalho"],
    "serviço": ["serviço", "prestação de serviços", "serviços"],
    "compra": ["compra e venda", "compraventa", "aquisição"],
    "societário": ["societário", "sociedade", "quotas", "acionista"],
    "imigração": ["imigração", "imigrantes", "nacionalidade", "procedência", "entrada"],
    "histórico": ["registro do movimento", "livro", "arquivo histórico"],
    "fiscal": ["icms", "cfop", "tributo", "imposto", "nota fiscal"],
    "judicial": ["juiz", "petição", "autor", "réu", "processo"],
}


def _ollama_base_url() -> str:
    return (os.environ.get("PDFSEARCHABLE_OLLAMA_URL") or "http://localhost:11434").rstrip("/")


def _ollama_model() -> str:
    return os.environ.get("PDFSEARCHABLE_OLLAMA_MODEL", "llama3.2")


def _ollama_timeout() -> int:
    try:
        return max(30, min(300, int(os.environ.get("PDFSEARCHABLE_OLLAMA_TIMEOUT", "90"))))
    except ValueError:
        return 90


def _ollama_retries() -> int:
    try:
        return max(0, min(5, int(os.environ.get("PDFSEARCHABLE_OLLAMA_RETRY", "2"))))
    except ValueError:
        return 2


def _ollama_cache_enabled() -> bool:
    return os.environ.get("PDFSEARCHABLE_OLLAMA_CACHE", "1").strip().lower() in ("1", "true", "yes")


def _ollama_keep_alive() -> str:
    """
    Controla o tempo que o modelo permanece carregado entre chamadas.
    Valores aceites pelo Ollama: '5m' (padrão), '1h', '-1' (manter até reload), '0' (descarregar imediatamente).
    Para lotes grandes, PDFSEARCHABLE_OLLAMA_KEEP_ALIVE=-1 elimina o reload entre docs (ganho 20-30%).
    """
    return (os.environ.get("PDFSEARCHABLE_OLLAMA_KEEP_ALIVE") or "5m").strip()


def ollama_health_check() -> bool:
    """Verifica se o Ollama está acessível. Retorna True se disponível."""
    base_url = _ollama_base_url()
    try:
        req = urllib.request.Request(
            f"{base_url}/api/tags",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310 — URL is localhost Ollama endpoint validated by _ollama_base_url()
            return resp.status == 200
    except Exception as _e:
        _log.debug("Ollama health check falhou: %s", _e)
        return False


def _ollama_cache_dir() -> Path:
    return Path.cwd() / ".pdfsearchable" / "ollama_cache"


def _ollama_request(
    prompt: str,
    max_tokens: int = 150,
    cache_key: str | None = None,
    timeout: int | None = None,
    json_mode: bool = False,
    model_override: str | None = None,
) -> str | None:
    """
    Chama a API Ollama para geração de texto. Retorna None em erro.
    Com PDFSEARCHABLE_OLLAMA_CACHE=1, usa cache por hash do prompt.
    Retry com backoff exponencial em falhas (PDFSEARCHABLE_OLLAMA_RETRY).
    ``model_override`` permite usar modelo diferente de PDFSEARCHABLE_OLLAMA_MODEL
    (útil para classificação com modelo menor sem afectar summary/tags).
    """
    base_url = _ollama_base_url()
    model = model_override or _ollama_model()
    timeout = timeout if timeout is not None else _ollama_timeout()
    retries = _ollama_retries()

    # Cache
    if _ollama_cache_enabled() and cache_key:
        cache_dir = _ollama_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Cache keyed inclui json_mode e model para evitar colisões entre chamadas
        ck = f"{cache_key}|json={int(json_mode)}|model={model}"
        h = hashlib.sha256(ck.encode()).hexdigest()[:32]
        cache_file = cache_dir / f"{h}.json"
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                return data.get("content", "")
            except Exception as _e:
                _log.debug(
                    "Falha ao ler cache Ollama %s: %s — a fazer nova chamada", cache_file, _e
                )

    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "keep_alive": _ollama_keep_alive(),
    }
    if json_mode:
        # Ollama structured-output: força o modelo a retornar JSON válido.
        payload["format"] = "json"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                f"{base_url}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 — localhost Ollama only
                data = json.loads(resp.read().decode("utf-8"))
            content = (data.get("message") or {}).get("content") or ""
            if content and cache_key and _ollama_cache_enabled():
                try:
                    cache_file.write_text(json.dumps({"content": content}), encoding="utf-8")
                except Exception as _e:
                    _log.debug("Falha ao gravar cache Ollama %s: %s", cache_file, _e)
            return content
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
            KeyError,
        ) as _req_err:
            _log.debug(
                "Ollama request falhou (tentativa %d/%d): %s", attempt + 1, retries + 1, _req_err
            )
            if attempt < retries:
                time.sleep(1 * (2**attempt))
            else:
                # Esgotou retries: registar em warning para surface ao utilizador
                _log.warning(
                    "Ollama indisponível após %d tentativas (%s): %s",
                    retries + 1,
                    type(_req_err).__name__,
                    _req_err,
                )
                return None
    return None


def ollama_stream_chat(context: str, question: str):  # -> Iterator[str] | None
    """
    Versão em streaming de ask_document_ollama.
    Faz POST a /api/chat com stream=True e itera tokens à medida que chegam.
    Retorna None (não um iterator) se Ollama estiver inacessível.
    """
    import io

    base_url = _ollama_base_url()
    model = _ollama_model()
    timeout = _ollama_timeout()

    sample = (context or "").strip()[:6000]
    if not sample or not (question or "").strip():
        return None

    prompt = (
        "Com base no texto do documento abaixo, responda de forma clara à pergunta.\n\n"
        f"Documento:\n---\n{sample}\n---\n\n"
        f"Pergunta: {question.strip()}\n\nResposta:"
    )

    payload = json.dumps(
        {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": True}
    ).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"{base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        def _gen():
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec
                    for raw_line in io.TextIOWrapper(resp, encoding="utf-8"):
                        raw_line = raw_line.strip()
                        if not raw_line:
                            continue
                        try:
                            chunk = json.loads(raw_line)
                        except json.JSONDecodeError:
                            continue
                        token = (chunk.get("message") or {}).get("content") or ""
                        if token:
                            yield token
                        if chunk.get("done"):
                            break
            except Exception as _stream_err:
                _log.debug("Ollama streaming interrompido: %s", _stream_err)
                return

        return _gen()
    except Exception as _conn_err:
        _log.debug("Ollama stream_chat conexão falhou: %s", _conn_err)
        return None


def extract_summary_ollama(text: str) -> str | None:
    """
    Gera resumo de 1–3 frases via Ollama (IA9).
    Retorna None se Ollama indisponível ou texto vazio.
    """
    result = extract_summary_and_subject_ollama(text)
    return result[0] if result else None


def extract_summary_and_subject_ollama(text: str) -> tuple[str | None, str | None] | None:
    """
    Gera resumo e assunto em uma chamada Ollama (IA9).
    Retorna (resumo, assunto) ou None se Ollama indisponível.
    Com PDFSEARCHABLE_SUMMARY_SHORT=1 usa prompt e tokens reduzidos (resumo em 1 frase).
    """
    sample = (text or "").strip()[:4000]
    if not sample:
        return None
    short = (os.environ.get("PDFSEARCHABLE_SUMMARY_SHORT") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if short:
        prompt = f"""Resuma em UMA frase o conteúdo do documento:

---
{sample[:2500]}
---

Responda só com a frase (máximo 30 palavras)."""
        max_tokens = 80
    else:
        prompt = f"""Analise o documento abaixo e responda em duas linhas:
1) ASSUNTO: (uma frase curta que descreve o tema/conteúdo do documento)
2) RESUMO: (1 a 3 frases objetivas sobre o conteúdo)

Documento:
---
{sample[:3000]}
---

Responda exatamente no formato:
ASSUNTO: ...
RESUMO: ..."""
        max_tokens = 200
    cache_key = (
        f"summary_subject:{hashlib.sha256(sample.encode()).hexdigest()[:32]}" if sample else None
    )
    out = _ollama_request(prompt, max_tokens=max_tokens, cache_key=cache_key)
    if not out:
        return None
    out = out.strip()
    summary = None
    subject = None
    for line in out.split("\n"):
        line = line.strip()
        if line.upper().startswith("ASSUNTO:"):
            subject = line.split(":", 1)[-1].strip()
            subject = subject[:200] if subject else None
        elif line.upper().startswith("RESUMO:"):
            summary = line.split(":", 1)[-1].strip()
            summary = summary[:500] if summary else None
    if not summary and out:
        summary = out[:500]
    return (summary or None, subject or None)


def extract_tags(doc_type: str, text: str) -> list[str]:
    """
    Retorna lista de tags: [doc_type] + subtipos detectados por palavra-chave (ID5).
    """
    tags = [doc_type] if doc_type and doc_type != "documento" else []
    raw = (text or "")[:6000].lower()
    for tag, keywords in TAG_HINTS.items():
        if tag in tags:
            continue
        if any(kw in raw for kw in keywords):
            tags.append(tag)
    return tags[:10]


def extract_tags_ollama(text: str, max_tags: int = 5) -> list[str]:
    """
    Extrai 3–5 palavras-chave ou temas via Ollama para enriquecer tags.
    Retorna lista vazia se Ollama indisponível.
    """
    max_tags = max(3, max_tags)
    sample = (text or "").strip()[:4000]
    if len(sample) < 100:
        return []
    prompt = f"""Liste entre 3 e {max_tags} palavras-chave ou temas que descrevem este documento.
Use apenas palavras ou expressões curtas (até 3 palavras). Uma por linha, sem número.
Use minúsculas. Ex.: imigração, registro histórico, contratos.

Documento:
---
{sample[:3000]}
---

Palavras-chave/temas (um por linha):"""
    cache_key = f"tags_ia:{hashlib.sha256(sample.encode()).hexdigest()[:32]}"
    out = _ollama_request(prompt, max_tokens=120, cache_key=cache_key)
    if not out:
        return []
    result: list[str] = []
    for line in out.splitlines():
        term = line.strip().strip(".-").strip()
        if term and len(term) >= 2 and len(term) <= 50 and term not in result:
            result.append(term)
    return result[:max_tags]


def extract_parties_ollama(text: str, max_parties: int = 10) -> list[str]:
    """
    Extrai nomes de pessoas, empresas ou entidades que são partes, participantes ou protagonistas via Ollama.
    Retorna lista de nomes; lista vazia se Ollama indisponível.
    """
    sample = (text or "").strip()[:5000]
    if len(sample) < 150:
        return []
    prompt = f"""Liste os nomes de pessoas, empresas ou entidades que são partes, participantes ou protagonistas deste documento.
Ex.: partes de contrato, presentes em ata, outorgantes, participantes. Apenas o nome, um por linha, sem número nem explicação.

Documento:
---
{sample[:3500]}
---

Nomes (um por linha):"""
    cache_key = f"parties:{hashlib.sha256(sample.encode()).hexdigest()[:32]}"
    out = _ollama_request(prompt, max_tokens=200, cache_key=cache_key)
    if not out:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for line in out.splitlines():
        name = line.strip().strip(".-").strip()
        if not name or len(name) < 3 or len(name) > 100:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result[:max_parties]


def extract_monetary_values(text: str) -> list[dict[str, Any]]:
    """
    Extrai menções a valores monetários (ID8). Retorna lista de {currency, value_str}.
    Exclui entradas sem valor numérico (ex.: "USD $.").
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pattern, currency in [(_RE_BRL, "BRL"), (_RE_EUR, "EUR"), (_RE_USD, "USD")]:
        for m in pattern.finditer(text or ""):
            val = m.group(0).strip()
            if not re.search(r"\d", val):
                continue
            key = f"{currency}:{val}"
            if key not in seen:
                seen.add(key)
                out.append({"currency": currency, "value_str": val})
    return out[:20]


def extract_parties(text: str) -> list[str]:
    """
    Extrai menções a partes/participantes (ID9): Parte A, Outorgante, Presentes, etc.
    Retorna lista de strings (trecho após o rótulo).
    """
    out: list[str] = []
    seen: set[str] = set()
    for m in _RE_PARTIES.finditer(text or ""):
        part = m.group(1).strip()
        part = re.sub(r"\s+", " ", part)[:100]
        if part and part not in seen and len(part) > 2:
            seen.add(part)
            out.append(part)
    return out[:15]


def extract_entities(text: str) -> dict[str, list[str]]:
    """
    Extrai entidades estruturadas do texto para exibição e destaque.
    Retorna dict com listas deduplicadas e limitadas por tipo.
    """
    text = text or ""
    keys = [
        "emails",
        "cpfs",
        "cnpjs",
        "ips",
        "urls",
        "domains",
        "phones",
        "ceps",
        "processos",
        "placas",
        "rgs",
        "protocolos",
        "hashes",
        "coordenadas",
        "timestamps",
        "leis",
    ]
    out: dict[str, list[str]] = {k: [] for k in keys}
    seen: dict[str, set[str]] = {k: set() for k in keys}
    max_per_type = 30

    def _add(key: str, val: str) -> None:
        val = val.strip()
        if val and val not in seen[key] and len(out[key]) < max_per_type:
            seen[key].add(val)
            out[key].append(val)

    for m in EMAIL_PATTERN.finditer(text):
        _add("emails", m.group(0))
    for m in CPF_PATTERN.finditer(text):
        val = m.group(0).strip()
        if val and _validate_cpf(val):
            _add("cpfs", val)
    for m in CNPJ_PATTERN.finditer(text):
        val = m.group(0).strip()
        if val and _validate_cnpj(val):
            _add("cnpjs", val)
    for m in IPV4_PATTERN.finditer(text):
        _add("ips", m.group(0))
    for m in IPV6_PATTERN.finditer(text):
        _add("ips", m.group(0))
    for m in URL_PATTERN.finditer(text):
        url = m.group(0).rstrip(".,;:)\"'")
        _add("urls", url)
    for m in FQDN_PATTERN.finditer(text):
        domain = m.group(0)
        # Evitar domínios que já fazem parte de e-mails ou URLs capturados
        if domain in seen["emails"] or any(domain in u for u in out["urls"]):
            continue
        # Heurística anti-OCR: normaliza prefixos "www" corrompidos pelo OCR.
        # Casos comuns: "Avww.dominio.org", "Aww.site.net", "vww.x.com" — letra
        # extra ou 'w' confundido com 'v'/'vw'. Corrige para "www." se o primeiro
        # label parecer uma variante noisy de "www".
        parts = domain.split(".", 1)
        first = parts[0].lower()
        if len(parts) == 2 and first != "www" and _OCR_WWW_NOISE.fullmatch(first):
            domain = "www." + parts[1]
        # Validação pós-match: rejeita ruído OCR (labels stop-word, muito
        # curtos, ou com excesso de hífens).
        if not _is_valid_domain(domain):
            continue
        _add("domains", domain)
    for m in _RE_PHONE_BR.finditer(text):
        _add("phones", m.group(0))
    for m in _RE_CEP.finditer(text):
        _add("ceps", m.group(0))
    for m in _RE_PROCESSO_CNJ.finditer(text):
        _add("processos", m.group(0))
    for m in _RE_PLACA.finditer(text):
        val = m.group(0).upper()
        # Filtrar falsos positivos: ignorar se parece hash ou código genérico
        if len(val) >= 7:
            _add("placas", val)
    for m in _RE_RG.finditer(text):
        _add("rgs", m.group(1).strip())
    for m in _RE_PROTOCOLO.finditer(text):
        val = m.group(0).strip()
        # Exige pelo menos um dígito no valor para evitar falsos positivos
        if any(c.isdigit() for c in m.group(1)):
            _add("protocolos", val)
    for m in _RE_HASH.finditer(text):
        val = m.group(0)
        # Só hashes com exatamente 32, 40 ou 64 chars
        if len(val) in (32, 40, 64):
            _add("hashes", val)
    for m in _RE_COORD.finditer(text):
        lat, lon = m.group(1), m.group(2)
        try:
            flat, flon = float(lat), float(lon)
            if -90 <= flat <= 90 and -180 <= flon <= 180:
                _add("coordenadas", f"{lat}, {lon}")
        except ValueError:
            pass
    for m in _RE_TIMESTAMP.finditer(text):
        _add("timestamps", m.group(0))
    for m in _RE_LEI.finditer(text):
        _add("leis", m.group(0).strip())
    return out


def extract_keywords_ollama(text: str, max_keywords: int = 25) -> list[str]:
    """
    Extrai termos mais relevantes para nuvem de palavras via Ollama: substantivos, conceitos-chave,
    nomes próprios relevantes. Evita verbos genéricos e palavras vazias.
    Retorna lista de termos (uma palavra ou expressão curta); lista vazia se Ollama indisponível.
    """
    sample = (text or "").strip()[:5000]
    if len(sample) < 100:
        return []
    prompt = f"""Liste as {max_keywords} palavras ou expressões curtas (até 3 palavras) mais relevantes para uma nuvem de palavras do texto abaixo.
Priorize: substantivos, conceitos-chave, nomes próprios importantes, temas. Use sempre palavras ou expressões COMPLETAS (ex.: "Ltda" como um único termo, não fragmentos).
Evite: verbos genéricos (ser, ter, fazer), artigos, preposições, palavras muito comuns, números isolados.
Uma por linha, sem número nem explicação. Use minúsculas.

Texto:
---
{sample[:3500]}
---

Termos para nuvem de palavras (um por linha):"""
    cache_key = f"keywords:{hashlib.sha256(sample.encode()).hexdigest()[:32]}"
    out = _ollama_request(prompt, max_tokens=250, cache_key=cache_key)
    if not out:
        return []
    keywords: list[str] = []
    for line in out.splitlines():
        term = line.strip().strip(".-").strip()
        if term and len(term) >= 2 and len(term) <= 50 and term not in keywords:
            keywords.append(term)
    return keywords[:max_keywords]


def _parse_ollama_metadata_section(line: str, prefix: str, max_items: int = 30) -> list[str]:
    """Extrai itens de uma linha no formato 'PREFIX: item1; item2, item3'. Retorna lista deduplicada."""
    if not line.strip().upper().startswith(prefix.upper() + ":"):
        return []
    rest = line.split(":", 1)[-1].strip()
    if not rest:
        return []
    items: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[;,]", rest):
        val = part.strip().strip(".-")
        if not val or len(val) > 200:
            continue
        key = val.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(val)
        if len(items) >= max_items:
            break
    return items


def extract_metadata_ollama(text: str) -> dict[str, Any]:
    """
    Extrai o máximo de metadados e informações importantes do documento via Ollama:
    CPFs, CNPJs, e-mails, IPs, endereços, telefones, valores monetários, partes, datas,
    URLs, domínios, CEPs, processos judiciais, placas, RGs, protocolos, hashes,
    coordenadas GPS, timestamps, referências legais.
    Retorna dict com listas por categoria.
    Usado em modo PDFSEARCHABLE_AI=ollama para enriquecer o índice; mescla com extração por regex.
    """
    sample = (text or "").strip()[:6000]
    if len(sample) < 200:
        return {}
    # Tentativa 1: JSON mode (mais robusto — formato garantido pelo Ollama)
    json_prompt = f"""Extraia do texto abaixo todas as informações estruturadas e responda com um JSON válido seguindo EXACTAMENTE este schema (listas vazias se não houver):
{{"cpfs": [], "cnpjs": [], "emails": [], "ips": [], "addresses": [], "phones": [], "monetary_values": [], "parties": [], "dates": [], "urls": [], "domains": [], "ceps": [], "processos": [], "placas": [], "rgs": [], "protocolos": [], "hashes": [], "coordenadas": [], "timestamps": [], "leis": []}}

Texto:
---
{sample[:4000]}
---
JSON:"""
    json_cache = f"meta_ollama_json_v1:{hashlib.sha256(sample.encode()).hexdigest()[:32]}"
    json_out = _ollama_request(json_prompt, max_tokens=1500, cache_key=json_cache, json_mode=True)
    if json_out:
        try:
            parsed = json.loads(json_out)
            if isinstance(parsed, dict):
                _allowed = {
                    "cpfs",
                    "cnpjs",
                    "emails",
                    "ips",
                    "addresses",
                    "phones",
                    "monetary_values",
                    "parties",
                    "dates",
                    "urls",
                    "domains",
                    "ceps",
                    "processos",
                    "placas",
                    "rgs",
                    "protocolos",
                    "hashes",
                    "coordenadas",
                    "timestamps",
                    "leis",
                }
                cleaned: dict[str, Any] = {}
                for k, v in parsed.items():
                    if k in _allowed and isinstance(v, list):
                        cleaned[k] = [str(x).strip() for x in v if str(x).strip()][:30]
                if cleaned:
                    return cleaned
        except (json.JSONDecodeError, TypeError) as _je:
            _log.debug("extract_metadata_ollama: JSON inválido (%s), a tentar fallback texto", _je)
    # Tentativa 2 (fallback): formato linha-a-linha legacy
    prompt = f"""Extraia do texto abaixo TODAS as informações estruturadas listadas. Responda APENAS no formato indicado, uma linha por categoria. Use ";" para separar vários itens na mesma linha. Se não houver nenhum item para uma categoria, omita a linha.

Formato obrigatório (copie os rótulos exatamente):
CPFS: xxx.xxx.xxx-xx; ...
CNPJS: xx.xxx.xxx/xxxx-xx; ...
EMAILS: email@dominio.com; ...
IPS: endereço IPv4 ou IPv6; ...
ENDERECOS: endereço completo (rua, número, bairro, cidade, UF, CEP quando houver); ...
TELEFONES: (DDD) número; +55 ...; ...
VALORES: R$ 1.234,56; USD 500,00; € 100; ...
PARTES: nome de pessoa ou empresa (partes de contrato, presentes em ata, outorgantes); ...
DATAS: datas importantes no formato DD/MM/AAAA ou AAAA-MM-DD; ...
URLS: https://exemplo.com; http://site.org/pagina; ...
DOMINIOS: dominio.com.br; exemplo.org; ...
CEPS: 01001-000; 70040-020; ...
PROCESSOS: número de processo judicial (NNNNNNN-DD.AAAA.J.TR.OOOO); ...
PLACAS: ABC-1234; ABC1D23; ...
RGS: número do RG (ex: 12.345.678-X); ...
PROTOCOLOS: número de protocolo, referência, ofício ou memorando; ...
HASHES: hash MD5, SHA-1 ou SHA-256 (sequência hexadecimal); ...
COORDENADAS: latitude, longitude (ex: -23.5505, -46.6333); ...
TIMESTAMPS: data e hora no formato ISO 8601 (ex: 2024-03-15T14:30:00); ...
LEIS: referências legais — Lei, Artigo, Decreto, Portaria, Resolução nº ...; ...

Texto do documento:
---
{sample[:4000]}
---

Responda apenas com as linhas das categorias que tiverem pelo menos um valor:"""
    cache_key = f"meta_ollama_v2:{hashlib.sha256(sample.encode()).hexdigest()[:32]}"
    out = _ollama_request(prompt, max_tokens=1200, cache_key=cache_key)
    if not out:
        return {}
    result: dict[str, Any] = {
        "cpfs": [],
        "cnpjs": [],
        "emails": [],
        "ips": [],
        "addresses": [],
        "phones": [],
        "monetary_values": [],
        "parties": [],
        "dates": [],
        "urls": [],
        "domains": [],
        "ceps": [],
        "processos": [],
        "placas": [],
        "rgs": [],
        "protocolos": [],
        "hashes": [],
        "coordenadas": [],
        "timestamps": [],
        "leis": [],
    }
    # Mapeamento: prefixo da linha → chave do resultado + limite
    _line_map: list[tuple[str, str, int]] = [
        ("CPFS:", "cpfs", 25),
        ("CNPJS:", "cnpjs", 25),
        ("EMAILS:", "emails", 25),
        ("IPS:", "ips", 20),
        ("ENDERECOS:", "addresses", 15),
        ("TELEFONES:", "phones", 15),
        ("VALORES:", "monetary_values", 20),
        ("PARTES:", "parties", 20),
        ("DATAS:", "dates", 15),
        ("URLS:", "urls", 25),
        ("DOMINIOS:", "domains", 20),
        ("CEPS:", "ceps", 20),
        ("PROCESSOS:", "processos", 15),
        ("PLACAS:", "placas", 15),
        ("RGS:", "rgs", 15),
        ("PROTOCOLOS:", "protocolos", 15),
        ("HASHES:", "hashes", 10),
        ("COORDENADAS:", "coordenadas", 15),
        ("TIMESTAMPS:", "timestamps", 15),
        ("LEIS:", "leis", 20),
    ]
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        upper = line.upper()
        for prefix, key, limit in _line_map:
            if upper.startswith(prefix):
                result[key] = _parse_ollama_metadata_section(line, prefix.rstrip(":"), limit)
                break
    return result


def merge_entities_with_ollama(
    entities: dict[str, list[str]],
    ollama_meta: dict[str, Any],
    *,
    max_per_type: int = 35,
) -> dict[str, list[str]]:
    """
    Mescla entidades extraídas por regex (entities) com as extraídas por Ollama (ollama_meta).
    Deduplica e limita o tamanho das listas. Cobre todas as 16+ categorias de entidades.
    """
    # Todas as chaves que podem existir em entities (regex) ou ollama_meta
    _all_keys = [
        "emails",
        "cpfs",
        "cnpjs",
        "ips",
        "urls",
        "domains",
        "phones",
        "ceps",
        "processos",
        "placas",
        "rgs",
        "protocolos",
        "hashes",
        "coordenadas",
        "timestamps",
        "leis",
    ]
    out: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}

    # Inicializar com dados do regex
    for key in _all_keys:
        out[key] = list(entities.get(key) or [])
        seen[key] = {v.lower() for v in out[key]}

    # Mesclar dados do Ollama (deduplicar por lowercase)
    for key in _all_keys:
        ollama_list = ollama_meta.get(key) or []
        for v in ollama_list:
            v = (v or "").strip()
            if not v or len(v) > 200:
                continue
            k = v.lower()
            if k not in seen[key] and len(out[key]) < max_per_type:
                seen[key].add(k)
                out[key].append(v)

    # Addresses vem apenas do Ollama (sem regex equivalente)
    out["addresses"] = list(ollama_meta.get("addresses") or [])[:15]
    # Phones: mesclar regex + Ollama (phones já está no loop acima)
    return out


def extract_locations_ollama(text: str, max_locations: int = 30) -> list[str]:
    """
    Extrai nomes de locais geográficos (cidades, estados, regiões, países) mencionados no texto via Ollama.
    Retorna lista de nomes; usada para enriquecer a seção Referências a locais no report.
    """
    sample = (text or "").strip()[:6000]
    if len(sample) < 150:
        return []
    prompt = f"""Liste todos os locais geográficos mencionados no texto abaixo: cidades, estados, regiões, países.
Apenas o nome do local, um por linha, sem número nem explicação. Use a forma mais comum (ex.: Brasil, São Paulo, Nordeste).

Texto:
---
{sample[:4000]}
---

Locais (um por linha):"""
    cache_key = f"locations:{hashlib.sha256(sample.encode()).hexdigest()[:32]}"
    out = _ollama_request(prompt, max_tokens=300, cache_key=cache_key)
    if not out:
        return []
    locations: list[str] = []
    seen_lower: set[str] = set()
    for line in out.splitlines():
        name = line.strip().strip(".-").strip()
        if not name or len(name) < 2 or len(name) > 80:
            continue
        key = name.lower().strip()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        locations.append(name)
    return locations[:max_locations]


def ask_document_ollama(
    text: str, question: str, max_tokens: int = 300, timeout: int | None = None
) -> str | None:
    """
    Responde uma pergunta sobre o documento usando o texto como contexto (RAG).
    Retorna a resposta ou None se Ollama indisponível.
    """
    sample = (text or "").strip()[:8000]
    if not sample or not (question or "").strip():
        return None
    prompt = f"""Com base no texto do documento abaixo, responda de forma objetiva à pergunta.

Documento:
---
{sample[:6000]}
---

Pergunta: {question.strip()}

Resposta:"""
    cache_key = f"ask:{hashlib.sha256((sample[:500] + question).encode()).hexdigest()[:32]}"
    return _ollama_request(prompt, max_tokens=max_tokens, cache_key=cache_key, timeout=timeout)


def correct_ocr_with_ollama(text: str, max_chars: int = 2000) -> str | None:
    """
    Corrige erros comuns de OCR usando LLM (Ollama).
    Útil para texto com caracteres trocados (0/O, 1/l, etc.) ou palavras fragmentadas.
    Retorna texto corrigido ou None se Ollama indisponível.
    Ativado via PDFSEARCHABLE_OCR_CORRECT=1.
    """
    if os.environ.get("PDFSEARCHABLE_OCR_CORRECT", "0").strip().lower() not in ("1", "true", "yes"):
        return None
    sample = (text or "").strip()[:max_chars]
    if len(sample) < 50:
        return None
    prompt = f"""Corrija erros comuns de OCR no texto abaixo. Mantenha o significado e a estrutura.
Corrija: caracteres trocados (0/O, 1/l/I, 5/S), palavras fragmentadas, espaços duplos, hífens incorretos.
NÃO altere números, datas ou nomes próprios a menos que claramente estejam errados.
Retorne APENAS o texto corrigido, sem explicações.

Texto:
---
{sample}
---

Texto corrigido:"""
    cache_key = f"ocr_correct:{hashlib.sha256(sample.encode()).hexdigest()[:32]}"
    return _ollama_request(prompt, max_tokens=500, cache_key=cache_key)


def expand_search_query_ollama(query: str, max_terms: int = 5) -> list[str]:
    """
    Usa Ollama para expandir uma consulta de busca: retorna termos relacionados,
    sinônimos ou conceitos próximos para enriquecer a pesquisa.
    Retorna lista de termos adicionais; lista vazia se Ollama indisponível.
    """
    q = (query or "").strip()
    if len(q) < 2:
        return []
    prompt = f"""Você é um assistente de busca em documentos. O usuário vai pesquisar por: "{q}"

Liste entre {max_terms} e {max_terms + 2} palavras ou expressões curtas (até 3 palavras) que também devemos buscar para encontrar documentos relevantes: sinônimos, termos relacionados, variações ou conceitos próximos.
Uma por linha, sem número nem explicação. Use minúsculas. Não repita o termo original.

Termos adicionais para busca (um por linha):"""
    cache_key = f"expand:{hashlib.sha256(q.encode()).hexdigest()[:32]}"
    out = _ollama_request(prompt, max_tokens=120, cache_key=cache_key)
    if not out:
        return []
    terms: list[str] = []
    seen_lower: set[str] = set()
    original_lower = q.lower()
    for line in out.splitlines():
        term = line.strip().strip(".-").strip()
        if not term or len(term) < 2 or len(term) > 60:
            continue
        key = term.lower()
        if key in seen_lower or key == original_lower:
            continue
        seen_lower.add(key)
        terms.append(term)
    return terms[:max_terms]


# ── Extracção de datas ────────────────────────────────────────────────────────

# Meses em português e inglês para o formato "20 de março de 2024" / "20 March 2024"
_PT_MONTHS = {
    "janeiro": "01",
    "fevereiro": "02",
    "março": "03",
    "marco": "03",
    "abril": "04",
    "maio": "05",
    "junho": "06",
    "julho": "07",
    "agosto": "08",
    "setembro": "09",
    "outubro": "10",
    "novembro": "11",
    "dezembro": "12",
}
_EN_MONTHS = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}

# DD/MM/AAAA  DD-MM-AAAA  DD.MM.AAAA  (aceita 1 ou 2 dígitos no dia/mês)
_RE_DATE_DMY = re.compile(r"\b(0?[1-9]|[12]\d|3[01])[-/.](0?[1-9]|1[0-2])[-/.](\d{4})\b")
# AAAA-MM-DD  (ISO 8601)
_RE_DATE_ISO = re.compile(r"\b(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b")
# "20 de março de 2024" / "20 março 2024" / "20 March 2024"
_RE_DATE_TEXT = re.compile(
    r"\b(0?[1-9]|[12]\d|3[01])\s+(?:de\s+)?([a-záéíóúàâêôãõç]+)\s+(?:de\s+)?(\d{4})\b",
    re.IGNORECASE | re.UNICODE,
)


def extract_dates(text: str, max_dates: int = 30) -> list[str]:
    """
    Extrai datas do texto em formatos comuns (PT-BR e ISO).
    Retorna lista de strings no formato normalizado AAAA-MM-DD, sem duplicados.
    Formatos suportados:
      - DD/MM/AAAA, DD-MM-AAAA, DD.MM.AAAA
      - AAAA-MM-DD (ISO 8601)
      - "20 de março de 2024" / "20 March 2024"
    Filtra datas absurdas (ano fora do intervalo 1800–2100).
    """
    if not text or not text.strip():
        return []
    raw = (text or "")[:50_000]  # limita o texto para evitar regex lentos
    seen: set[str] = set()
    results: list[str] = []

    def _add(year: str, month: str, day: str) -> None:
        try:
            y, m, d = int(year), int(month), int(day)
        except ValueError:
            return
        if not (1800 <= y <= 2100) or not (1 <= m <= 12) or not (1 <= d <= 31):
            return
        norm = f"{y:04d}-{m:02d}-{d:02d}"
        if norm not in seen:
            seen.add(norm)
            results.append(norm)

    # DD/MM/AAAA
    for m in _RE_DATE_DMY.finditer(raw):
        _add(m.group(3), m.group(2), m.group(1))
        if len(results) >= max_dates:
            break

    # AAAA-MM-DD
    for m in _RE_DATE_ISO.finditer(raw):
        _add(m.group(1), m.group(2), m.group(3))
        if len(results) >= max_dates:
            break

    # "20 de março de 2024"
    if len(results) < max_dates:
        all_months = {**_PT_MONTHS, **_EN_MONTHS}
        for m in _RE_DATE_TEXT.finditer(raw):
            month_word = m.group(2).lower().replace("ç", "c")
            month_num = all_months.get(month_word)
            if month_num:
                _add(m.group(3), month_num, m.group(1))
            if len(results) >= max_dates:
                break

    return results[:max_dates]


# ---------------------------------------------------------------------------
# Detecção de confidencialidade
# ---------------------------------------------------------------------------

# Detecção de confidencialidade: a palavra-chave sozinha no corpo do texto gera
# muitos falsos positivos (ex.: documentos desclassificados da CIA/JFK que citam
# "SECRET" como marcação original). Para reduzir ruído, exigimos que o marcador
# apareça em posição de "selo/classificação":
#   - no início de linha (cabeçalho/carimbo) em MAIÚSCULAS, ou
#   - próximo de rótulos explícitos (Classificação:, Nível de sigilo:, etc.).
_CONFIDENTIALITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # pt/en + prefixos multilíngues em rótulos de classificação:
    #   ar: سري للغاية / سري / محرم              (top secret / secret / confidential)
    #   zh: 绝密 / 机密 / 秘密                     (top secret / secret / confidential)
    #   ru: совершенно секретно / секретно / конфиденциально
    #   fr: très secret / secret / confidentiel
    #   es: altísimo secreto / secreto / confidencial
    #   de: streng geheim / geheim / vertraulich
    #   it: segretissimo / segreto / riservato
    (
        "ULTRASSECRETO",
        re.compile(
            r"(?im)(?:^[\t ]*|Classifica[cç][aã]o\s*[:\-]\s*|N[ií]vel\s+de\s+sigilo\s*[:\-]\s*)"
            r"(?:ULTRA[- ]?SECRETO|TOP\s*SECRET|TR[EÈ]S\s+SECRET|STRENG\s+GEHEIM|"
            r"SEGRETISSIMO|ALT[IÍ]SIMO\s+SECRETO|СОВЕРШЕННО\s+СЕКРЕТНО|绝密|極秘|극비|سري\s+للغاية)\b"
        ),
    ),
    (
        "SECRETO",
        re.compile(
            r"(?im)(?:^[\t ]*|Classifica[cç][aã]o\s*[:\-]\s*|N[ií]vel\s+de\s+sigilo\s*[:\-]\s*)"
            r"(?:SECRETO|SECRET|GEHEIM|SEGRETO|СЕКРЕТНО|机密|秘密|機密|비밀|سري)\b"
        ),
    ),
    (
        "CONFIDENCIAL",
        re.compile(
            r"(?im)(?:^[\t ]*|Classifica[cç][aã]o\s*[:\-]\s*|N[ií]vel\s+de\s+sigilo\s*[:\-]\s*)"
            r"(?:CONFIDENCIAL|CONFIDENTIAL|CONFIDENTIEL|VERTRAULICH|CONFIDENZIALE|"
            r"КОНФИДЕНЦИАЛЬНО|机密文件|محرم)\b"
        ),
    ),
    (
        "RESERVADO",
        re.compile(
            r"(?im)(?:^[\t ]*|Classifica[cç][aã]o\s*[:\-]\s*|N[ií]vel\s+de\s+sigilo\s*[:\-]\s*)"
            r"(?:RESERVADO|RESTRICTED|DIFFUSION\s+RESTREINTE|RESERVIERT|"
            r"RISERVATO|ДЛЯ\s+СЛУЖЕБНОГО\s+ПОЛЬЗОВАНИЯ|内部使用)\b"
        ),
    ),
    (
        "SIGILOSO",
        re.compile(
            r"(?im)(?:^[\t ]*|Classifica[cç][aã]o\s*[:\-]\s*|N[ií]vel\s+de\s+sigilo\s*[:\-]\s*)"
            r"(?:SIGILOSO|CLASSIFIED|CLASSIFI[EÉ])\b"
        ),
    ),
    (
        "USO INTERNO",
        re.compile(
            r"(?im)(?:^[\t ]*|Classifica[cç][aã]o\s*[:\-]\s*)"
            r"(?:USO\s+INTERNO|INTERNAL\s+USE(?:\s+ONLY)?)\b"
        ),
    ),
    (
        "PÚBLICO",
        re.compile(
            r"(?im)(?:^[\t ]*|Classifica[cç][aã]o\s*[:\-]\s*)"
            r"(?:P[ÚU]BLICO|PUBLIC\s+DOCUMENT)\b"
        ),
    ),
]

# Sinais que indicam que o documento é uma desclassificação/arquivo histórico,
# onde a palavra "SECRET" aparece como parte de carimbos originais retirados.
_DECLASSIFIED_MARKERS = re.compile(
    r"\b(?:DECLASSIFIED|DESCLASSIFICADO|APPROVED\s+FOR\s+RELEASE|"
    r"SANITIZED\s+COPY|FOIA|FREEDOM\s+OF\s+INFORMATION|NARA|"
    r"JFK\s+ASSASSINATION\s+RECORDS)\b",
    re.IGNORECASE,
)


def detect_confidentiality(text: str) -> str | None:
    """
    Detecta o nível de confidencialidade/sigilo do documento.
    Retorna o nível mais restritivo encontrado. Retorna None se nenhum marcador
    for identificado ou se o documento for claramente uma desclassificação.
    """
    if not text or not text.strip():
        return None
    sample = text[:30_000]
    # Documentos desclassificados/arquivos históricos não devem acionar alerta:
    # "SECRET" ali é a classificação ORIGINAL, já retirada por autoridade pública.
    if _DECLASSIFIED_MARKERS.search(sample):
        return None
    for level, pattern in _CONFIDENTIALITY_PATTERNS:
        if pattern.search(sample):
            return level
    return None


# ---------------------------------------------------------------------------
# Detecção de assinaturas digitais em PDF
# ---------------------------------------------------------------------------


def detect_digital_signatures(doc: Any) -> list[dict[str, Any]]:
    """
    Detecta campos de assinatura digital num documento PyMuPDF aberto.
    Retorna lista de dicts com info de cada assinatura encontrada:
      {"field_name": str, "signer": str|None, "signed": bool, "page": int}
    """
    signatures: list[dict[str, Any]] = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            for widget in page.widgets():
                if widget.field_type == 7:  # fitz.PDF_WIDGET_TYPE_SIGNATURE
                    sig_info: dict[str, Any] = {
                        "field_name": widget.field_name or "",
                        "signer": None,
                        "signed": False,
                        "page": i + 1,
                    }
                    # Tentar extrair info do valor da assinatura
                    try:
                        val = widget.field_value
                        if val:
                            sig_info["signed"] = True
                            sig_info["signer"] = str(val)[:200]
                    except Exception:
                        pass
                    signatures.append(sig_info)
    except Exception as _e:
        _log.debug("detect_digital_signatures falhou: %s", _e)
    return signatures
