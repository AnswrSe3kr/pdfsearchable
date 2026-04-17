"""
Detecção de idioma do texto (heurística, langdetect ou Ollama se disponível).
Retorna código curto: pt-BR, en, es, etc. ou "unknown".
"""

import os

from pdfsearchable.audit import get_logger as _get_logger

_log = _get_logger("pdfsearchable.language")

# Palavras comuns para heurística (pt vs en)
_COMMON_PT = frozenset(
    ["de", "da", "do", "que", "e", "um", "uma", "os", "as", "no", "na", "por", "para", "com", "não", "mais", "ao", "ela", "em", "dos", "das"]
)
_COMMON_EN = frozenset(
    ["the", "of", "and", "to", "a", "in", "that", "is", "it", "for", "you", "was", "on", "are", "with", "as", "be", "have", "this"]
)

# Mapeamento de códigos Ollama / Hugging Face para retorno
_LANG_MAP = {
    "pt": "pt-BR",
    "pt-br": "pt-BR",
    "ptbr": "pt-BR",
    "portuguese": "pt-BR",
    "en": "en",
    "english": "en",
    "es": "es",
    "spanish": "es",
    "fr": "fr",
    "french": "fr",
    "de": "de",
    "german": "de",
    "it": "it",
    "italian": "it",
    "ar": "ar",
    "arabic": "ar",
    "ru": "ru",
    "russian": "ru",
    "zh": "zh",
    "chinese": "zh",
    "ja": "ja",
    "japanese": "ja",
    "hi": "hi",
    "hindi": "hi",
    "nl": "nl",
    "dutch": "nl",
    "pl": "pl",
    "polish": "pl",
    "tr": "tr",
    "turkish": "tr",
    "el": "el",
    "greek": "el",
    "th": "th",
    "thai": "th",
    "vi": "vi",
    "vietnamese": "vi",
    "bg": "bg",
    "bulgarian": "bg",
    "sw": "sw",
    "swahili": "sw",
    "ur": "ur",
}


def _detect_language_ollama(text: str) -> str | None:
    """Fallback: usa Ollama para detectar idioma quando heurística/langdetect retornam unknown."""
    if (os.environ.get("PDFSEARCHABLE_AI") or "").strip().lower() != "ollama":
        return None
    sample = (text or "").strip()[:2000]
    if len(sample) < 50:
        return None
    try:
        from pdfsearchable.content_extractors import _ollama_request

        prompt = f"""Qual o idioma principal deste texto? Responda APENAS com o código ISO (ex: pt-BR, en, es, fr, de, it).
Texto:
---
{sample[:1500]}
---
Código do idioma:"""
        out = _ollama_request(prompt, max_tokens=10, cache_key=None)
        if not out:
            return None
        code = out.strip().lower().replace(" ", "").replace(".", "").replace(",", "")
        return _LANG_MAP.get(code, code if len(code) <= 10 else "unknown")
    except Exception:
        return None


def detect_language(text: str | None) -> str:
    """
    Detecta idioma predominante. Tenta langdetect se instalado; senão heurística por palavras.
    Se resultado for "unknown" e PDFSEARCHABLE_AI=ollama, tenta Ollama como fallback.
    Retorna código: pt-BR, en, es, etc. ou "unknown".
    """
    if not text or not text.strip():
        return "unknown"
    result = "unknown"
    try:
        import langdetect

        lang = langdetect.detect(text)
        if lang == "pt":
            return "pt-BR"
        result = lang if isinstance(lang, str) else "unknown"
    except Exception as _e:
        _log.debug("langdetect falhou: %s — a usar heurística", _e)
    if result == "unknown":
        tokens = set(text.lower().split())
        pt_count = sum(1 for w in tokens if w in _COMMON_PT)
        en_count = sum(1 for w in tokens if w in _COMMON_EN)
        if pt_count > en_count:
            return "pt-BR"
        if en_count > pt_count:
            return "en"
        # Fallback Ollama
        ollama_result = _detect_language_ollama(text)
        if ollama_result:
            return ollama_result
    return result
