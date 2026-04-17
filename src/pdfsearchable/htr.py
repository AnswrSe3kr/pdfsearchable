"""
Handwritten Text Recognition (HTR) — roteamento de backends com suporte multilíngue.

Backends disponíveis (PDFSEARCHABLE_HTR_BACKEND):
  trocr         — TrOCR local (Hugging Face). Seleciona automaticamente o modelo
                  adequado ao idioma detectado no documento. Modelos mantidos em cache.
                  Requer extra [htr]: pip install pdfsearchable[htr]
  transkribus   — Transkribus Cloud API. Requer PDFSEARCHABLE_TRANSKRIBUS_USER,
                  PDFSEARCHABLE_TRANSKRIBUS_PW e PDFSEARCHABLE_TRANSKRIBUS_MODEL_ID.
  escriptorium  — eScriptorium REST API. Requer PDFSEARCHABLE_ESCRIPTORIUM_URL,
                  PDFSEARCHABLE_ESCRIPTORIUM_TOKEN e PDFSEARCHABLE_ESCRIPTORIUM_MODEL.

Idiomas suportados no TrOCR local (modo padrão):
  en          — inglês (microsoft/trocr-base-handwritten)
  de          — alemão (fhswf/TrOCR_german_handwritten)
  fr          — francês (agomberto/trocr-large-handwritten-fr)
  ru/uk/bg/sr/be/mk — cirílico (cyrillic-trocr/trocr-handwritten-cyrillic)
  sv          — sueco histórico (Riksarkivet/trocr-base-handwritten-hist-swe-2)
  ar          — árabe (RayR1/trocr-base-arabic-handwritten)
  th          — tailandês (openthaigpt/thai-trocr)
  pt/es/it/.. — latim genérico: usa modelo inglês (melhor disponível para script latino)
  printed     — texto impresso multilíngue (microsoft/trocr-base-printed)

Modo histórico (PDFSEARCHABLE_OCR_HISTORICAL=on/auto):
  Seleciona modelos especializados para documentos antigos/manuscritos:
  pt/es/fr/it/de/ca/gl/ro/la — TRIDIS v2 medieval (séc. XI-XVI, script Textualis/Cursiva)
  en/nl/pl/..                — microsoft/trocr-large-handwritten (maior capacidade)
  fi                         — Kansallisarkisto multi-century HTR (arquivo finlandês)
  sv                         — Riksarkivet histórico (séc. XVII-XX)
  ru/uk/bg/sr                — cirílico (eslavo eclesiástico + moderno)
  Pipeline histórico inclui: CLAHE + Sauvola + limpeza morfológica + segmentação adaptativa

Configuração:
  PDFSEARCHABLE_HTR_MODEL         — override manual do modelo TrOCR (ignora auto-detecção)
  PDFSEARCHABLE_HTR_LANG          — forçar idioma HTR (ex: de, ru, fr). Padrão: auto-detect
  PDFSEARCHABLE_HTR_PRINTED       — 1 para usar modelo printed em vez de handwritten
  PDFSEARCHABLE_HTR_MAX_MODELS    — máximo de modelos em cache simultâneo (padrão: 3)
  PDFSEARCHABLE_OCR_HISTORICAL    — on/auto/off: ativa modelos e pipeline para docs históricos
  PDFSEARCHABLE_HTR=0             — desativar HTR completamente

Use PDFSEARCHABLE_HTR=0 para desativar HTR completamente e usar só Tesseract.
"""

import io
import os
import threading

from pdfsearchable.audit import get_logger as _get_logger

_log = _get_logger("pdfsearchable.htr")

# Backend constants
HTR_BACKEND_TROCR = "trocr"
HTR_BACKEND_TRANSKRIBUS = "transkribus"
HTR_BACKEND_ESCRIPTORIUM = "escriptorium"

_KNOWN_BACKENDS = (HTR_BACKEND_TROCR, HTR_BACKEND_TRANSKRIBUS, HTR_BACKEND_ESCRIPTORIUM)

# ---------------------------------------------------------------------------
# Registo de modelos TrOCR por idioma/script
# ---------------------------------------------------------------------------

# Modelo padrão (inglês handwritten — funciona razoavelmente para scripts latinos)
DEFAULT_HTR_MODEL = "microsoft/trocr-base-handwritten"

# Modelo large (mais preciso, mais lento — usado para documentos históricos/difíceis)
LARGE_HTR_MODEL = "microsoft/trocr-large-handwritten"

# Modelo impresso multilíngue (melhor para texto impresso em qualquer idioma)
PRINTED_HTR_MODEL = "microsoft/trocr-base-printed"

# Modelo TRIDIS v2: manuscritos medievais e early-modern multilíngue (lat/fr/es, séc. XI-XVI)
TRIDIS_HTR_MODEL = "magistermilitum/tridis_v2_HTR_historical_manuscripts"

# ---------------------------------------------------------------------------
# Mapeamento: código de idioma → modelo HuggingFace especializado
# Cada entrada: (model_id, description)
# ---------------------------------------------------------------------------
_LANG_MODEL_REGISTRY: dict[str, tuple[str, str]] = {
    # ── Inglês ──
    "en": (DEFAULT_HTR_MODEL, "English handwritten"),
    # ── Alemão ──
    "de": ("fhswf/TrOCR_german_handwritten", "German handwritten"),
    # ── Francês ──
    "fr": ("agomberto/trocr-large-handwritten-fr", "French handwritten"),
    # ── Cirílico (russo, ucraniano, búlgaro, sérvio, bielorrusso, macedónio) ──
    "ru": ("cyrillic-trocr/trocr-handwritten-cyrillic", "Cyrillic handwritten"),
    "uk": ("cyrillic-trocr/trocr-handwritten-cyrillic", "Cyrillic handwritten"),
    "bg": ("cyrillic-trocr/trocr-handwritten-cyrillic", "Cyrillic handwritten"),
    "sr": ("cyrillic-trocr/trocr-handwritten-cyrillic", "Cyrillic handwritten"),
    "be": ("cyrillic-trocr/trocr-handwritten-cyrillic", "Cyrillic handwritten"),
    "mk": ("cyrillic-trocr/trocr-handwritten-cyrillic", "Cyrillic handwritten"),
    # ── Sueco histórico (Riksarkivet, séc. XVII-XX) ──
    "sv": ("Riksarkivet/trocr-base-handwritten-hist-swe-2", "Swedish historical handwritten"),
    # ── Árabe ──
    "ar": ("RayR1/trocr-base-arabic-handwritten", "Arabic handwritten"),
    # ── Tailandês ──
    "th": ("openthaigpt/thai-trocr", "Thai handwritten"),
}

# Modelos específicos para documentos históricos (séc. XI-XX)
# Usados quando PDFSEARCHABLE_OCR_HISTORICAL=on/auto detecta documento antigo.
# Preferem modelos large/especializados com maior capacidade para texto degradado.
_HISTORICAL_MODEL_REGISTRY: dict[str, tuple[str, str]] = {
    # ── Script latino histórico: modelo large tem mais capacidade para texto difícil ──
    "en": (LARGE_HTR_MODEL, "English large handwritten (historical)"),
    # ── Medieval/Early Modern: TRIDIS v2 — treinado em manuscritos séc. XI-XVI ──
    #    Latim, francês antigo, espanhol antigo, alemão antigo (script Textualis/Cursiva)
    "la": (TRIDIS_HTR_MODEL, "TRIDIS medieval multilingual (Latin/OldFr/OldEs)"),
    # ── Português histórico: TRIDIS é o melhor disponível para script latino antigo
    #    (coloniais séc. XVI-XVIII partilham características com manuscritos ibéricos) ──
    "pt": (TRIDIS_HTR_MODEL, "TRIDIS medieval (Iberian historical)"),
    # ── Espanhol histórico: TRIDIS foi treinado com manuscritos espanhóis ──
    "es": (TRIDIS_HTR_MODEL, "TRIDIS medieval (Spanish historical)"),
    # ── Francês histórico: TRIDIS inclui francês antigo ──
    "fr": (TRIDIS_HTR_MODEL, "TRIDIS medieval (French historical)"),
    # ── Italiano histórico: script latino similar, TRIDIS como melhor opção ──
    "it": (TRIDIS_HTR_MODEL, "TRIDIS medieval (Italian historical, Latin-script)"),
    # ── Alemão histórico: TRIDIS inclui manuscritos germânicos ──
    "de": (TRIDIS_HTR_MODEL, "TRIDIS medieval (German historical)"),
    # ── Catalão, galego, romeno: scripts latinos ibéricos/românicos históricos ──
    "ca": (TRIDIS_HTR_MODEL, "TRIDIS medieval (Catalan historical)"),
    "gl": (TRIDIS_HTR_MODEL, "TRIDIS medieval (Galician historical)"),
    "ro": (TRIDIS_HTR_MODEL, "TRIDIS medieval (Romanian historical)"),
    # ── Holandês histórico: script latino, fallback para large model ──
    "nl": (LARGE_HTR_MODEL, "English large handwritten (Dutch historical)"),
    # ── Cirílico histórico: mesmo modelo cirílico (treinado em eslavo eclesiástico) ──
    "ru": ("cyrillic-trocr/trocr-handwritten-cyrillic", "Cyrillic historical"),
    "uk": ("cyrillic-trocr/trocr-handwritten-cyrillic", "Cyrillic historical"),
    "bg": ("cyrillic-trocr/trocr-handwritten-cyrillic", "Cyrillic historical"),
    "sr": ("cyrillic-trocr/trocr-handwritten-cyrillic", "Cyrillic historical"),
    # ── Sueco histórico: Riksarkivet já é modelo histórico ──
    "sv": ("Riksarkivet/trocr-base-handwritten-hist-swe-2", "Swedish historical handwritten"),
    # ── Finlandês histórico: modelo multi-century do Arquivo Nacional finlandês ──
    "fi": ("Kansallisarkisto/multicentury-htr-model", "Finnish multi-century historical"),
    # ── Árabe histórico ──
    "ar": ("RayR1/trocr-base-arabic-handwritten", "Arabic historical"),
}

# Idiomas que usam script latino — usam o modelo inglês (melhor generalista disponível)
# No modo histórico, estes idiomas usam _HISTORICAL_MODEL_REGISTRY ou LARGE_HTR_MODEL
_LATIN_SCRIPT_LANGS = frozenset({
    "pt", "pt-br", "es", "it", "nl", "pl", "ro", "ca", "gl", "hr",
    "cs", "sk", "hu", "fi", "da", "no", "nb", "nn", "et", "lt", "lv",
    "sl", "tr", "vi", "sw", "id", "ms", "tl", "af", "la",
})


# ---------------------------------------------------------------------------
# Cache de modelos carregados (LRU por idioma)
# ---------------------------------------------------------------------------

_model_cache: dict[str, tuple] = {}  # model_id → (processor, model)
_model_cache_order: list[str] = []   # LRU order
_model_cache_lock = threading.Lock()

_HTR_AVAILABLE: bool | None = None


def _max_cached_models() -> int:
    raw = os.environ.get("PDFSEARCHABLE_HTR_MAX_MODELS", "3").strip()
    try:
        return max(1, min(10, int(raw)))
    except ValueError:
        return 3


def _use_printed_model() -> bool:
    return os.environ.get("PDFSEARCHABLE_HTR_PRINTED", "0").strip().lower() in ("1", "true", "yes")


def _forced_lang() -> str | None:
    raw = os.environ.get("PDFSEARCHABLE_HTR_LANG", "").strip().lower()
    return raw or None


def _manual_model_override() -> str | None:
    raw = os.environ.get("PDFSEARCHABLE_HTR_MODEL", "").strip()
    return raw or None


# ---------------------------------------------------------------------------
# Seleção do modelo por idioma
# ---------------------------------------------------------------------------

def _historical_htr_enabled() -> bool:
    """True se o modo histórico HTR está ativo (usa modelos maiores/especializados)."""
    raw = os.environ.get("PDFSEARCHABLE_OCR_HISTORICAL", "off").strip().lower()
    return raw in ("1", "true", "yes", "on", "auto")


def get_model_for_lang(lang: str | None, historical: bool = False) -> tuple[str, str]:
    """
    Retorna (model_id, description) para o idioma dado.
    Prioridade:
      1. PDFSEARCHABLE_HTR_MODEL — override manual
      2. PDFSEARCHABLE_HTR_PRINTED=1 — modelo impresso multilíngue
      3. Modo histórico (historical=True ou env var) — _HISTORICAL_MODEL_REGISTRY
      4. Modo padrão — _LANG_MODEL_REGISTRY
      5. Fallback: modelo inglês (base ou large conforme modo)
    """
    override = _manual_model_override()
    if override:
        return override, "manual override"

    if _use_printed_model():
        return PRINTED_HTR_MODEL, "printed multilingual"

    use_historical = historical or _historical_htr_enabled()

    if not lang:
        if use_historical:
            return LARGE_HTR_MODEL, "large model (historical, no language detected)"
        return DEFAULT_HTR_MODEL, "default (no language detected)"

    lang = lang.lower().replace("-", "").replace("_", "")[:5]
    # Normalizar pt-br → pt
    if lang.startswith("pt"):
        lang = "pt"

    # Modo histórico: consultar registo de modelos históricos primeiro
    if use_historical:
        if lang in _HISTORICAL_MODEL_REGISTRY:
            return _HISTORICAL_MODEL_REGISTRY[lang]
        # Para script latino sem modelo histórico específico, usar modelo large
        if lang in _LATIN_SCRIPT_LANGS:
            return LARGE_HTR_MODEL, f"Large model (historical Latin-script fallback for '{lang}')"

    # Lookup direto no registo padrão
    if lang in _LANG_MODEL_REGISTRY:
        return _LANG_MODEL_REGISTRY[lang]

    # Script latino → modelo inglês (melhor generalista)
    if lang in _LATIN_SCRIPT_LANGS:
        fallback = LARGE_HTR_MODEL if use_historical else DEFAULT_HTR_MODEL
        label = "Large" if use_historical else "Base"
        return fallback, f"{label} Latin script fallback for '{lang}'"

    # Desconhecido
    fallback = LARGE_HTR_MODEL if use_historical else DEFAULT_HTR_MODEL
    _log.debug("Sem modelo HTR específico para '%s' — a usar modelo %s.", lang, fallback)
    return fallback, f"fallback for unknown '{lang}'"


def list_supported_languages() -> dict[str, str]:
    """Retorna dict {código_idioma: descrição} de todos os idiomas suportados."""
    result: dict[str, str] = {}
    for lang, (model_id, desc) in _LANG_MODEL_REGISTRY.items():
        result[lang] = f"{desc} ({model_id})"
    for lang in sorted(_LATIN_SCRIPT_LANGS):
        if lang not in result:
            result[lang] = f"Latin script — {DEFAULT_HTR_MODEL}"
    # Adicionar modelos históricos
    for lang, (model_id, desc) in _HISTORICAL_MODEL_REGISTRY.items():
        key = f"{lang}-historical"
        result[key] = f"{desc} ({model_id})"
    result["printed"] = f"Printed multilingual — {PRINTED_HTR_MODEL}"
    return result


# ---------------------------------------------------------------------------
# Gestão de backend
# ---------------------------------------------------------------------------

def get_htr_backend() -> str:
    """
    Retorna o backend HTR activo (PDFSEARCHABLE_HTR_BACKEND).
    Padrão: 'trocr'. Valores válidos: 'trocr', 'transkribus', 'escriptorium'.
    """
    raw = os.environ.get("PDFSEARCHABLE_HTR_BACKEND", "").strip().lower()
    return raw if raw in _KNOWN_BACKENDS else HTR_BACKEND_TROCR


def htr_available() -> bool:
    """
    True se o backend HTR configurado estiver disponível.
    TrOCR requer o extra [htr] (transformers + torch).
    Transkribus e eScriptorium requerem credenciais configuradas.
    Use PDFSEARCHABLE_HTR=0 para desativar.
    """
    global _HTR_AVAILABLE
    if _HTR_AVAILABLE is not None:
        return _HTR_AVAILABLE
    backend = get_htr_backend()
    try:
        if backend == HTR_BACKEND_TRANSKRIBUS:
            from pdfsearchable.htr_transkribus import available as _avail
            _HTR_AVAILABLE = _avail()
        elif backend == HTR_BACKEND_ESCRIPTORIUM:
            from pdfsearchable.htr_escriptorium import available as _avail
            _HTR_AVAILABLE = _avail()
        else:
            import torch  # noqa: F401
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel  # noqa: F401
            _HTR_AVAILABLE = True
    except Exception:
        _HTR_AVAILABLE = False
    return _HTR_AVAILABLE


# ---------------------------------------------------------------------------
# Carregamento de modelo com cache LRU
# ---------------------------------------------------------------------------

def _load_model(model_id: str) -> tuple:
    """
    Carrega (ou reutiliza do cache) processador e modelo TrOCR.
    Mantém até _max_cached_models() modelos em memória; evicta o mais antigo.
    Thread-safe.
    """
    with _model_cache_lock:
        if model_id in _model_cache:
            # Move para o fim (mais recente)
            if model_id in _model_cache_order:
                _model_cache_order.remove(model_id)
            _model_cache_order.append(model_id)
            return _model_cache[model_id]

    # Carregar fora do lock (pode demorar)
    _log.info("A carregar modelo HTR: %s", model_id)
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel

    # use_fast=True silencia o warning de "slow image processor" e é mais rápido
    try:
        processor = TrOCRProcessor.from_pretrained(model_id, use_fast=True)  # nosec B615
    except (TypeError, ValueError):
        # Fallback para versões antigas de transformers que não aceitam use_fast
        processor = TrOCRProcessor.from_pretrained(model_id)  # nosec B615
    model = VisionEncoderDecoderModel.from_pretrained(model_id)  # nosec B615
    model.eval()

    with _model_cache_lock:
        _model_cache[model_id] = (processor, model)
        _model_cache_order.append(model_id)
        # Evictar modelos antigos se exceder o limite
        max_models = _max_cached_models()
        while len(_model_cache_order) > max_models:
            oldest = _model_cache_order.pop(0)
            removed = _model_cache.pop(oldest, None)
            if removed:
                _log.debug("Modelo HTR evictado do cache: %s", oldest)

    return (processor, model)


# ---------------------------------------------------------------------------
# Segmentação de linhas
# ---------------------------------------------------------------------------

def _split_lines(image, historical: bool = False) -> list:
    """
    Segmenta imagem em linhas de texto (projeção horizontal).
    Retorna lista de imagens PIL, cada uma uma linha.

    Para documentos históricos (historical=True):
      - Limiar mais baixo (3% vs 5%) para detectar texto desbotado
      - Altura mínima reduzida (2 vs 3 px) para linhas finas
      - Margem maior (4 vs 2 px) para preservar ascendentes/descendentes
      - Merge de linhas próximas (gap < 5 px) para palavras fragmentadas
    """
    import numpy as np

    img = image.convert("L")
    arr = np.array(img)
    # Soma por linha: onde há texto, a soma de pixels escuros é maior
    row_sums = np.sum(255 - arr, axis=1)
    # Parâmetros adaptativos
    thresh_ratio = 0.03 if historical else 0.05
    min_height = 2 if historical else 3
    margin = 4 if historical else 2
    merge_gap = 5 if historical else 0

    threshold = max(10, row_sums.max() * thresh_ratio)
    in_line = row_sums >= threshold
    # Agrupar linhas consecutivas
    lines: list[tuple[int, int]] = []
    start = None
    for i, v in enumerate(in_line):
        if v and start is None:
            start = i
        elif not v and start is not None:
            end = i
            if end - start >= min_height:
                lines.append((start, end))
            start = None
    if start is not None:
        lines.append((start, len(in_line)))

    # Merge de linhas próximas (documentos históricos com baseline irregular)
    if merge_gap > 0 and len(lines) > 1:
        merged: list[tuple[int, int]] = [lines[0]]
        for s, e in lines[1:]:
            prev_s, prev_e = merged[-1]
            if s - prev_e <= merge_gap:
                merged[-1] = (prev_s, e)
            else:
                merged.append((s, e))
        lines = merged

    # Recortar cada linha (com margem)
    crops = []
    for y1, y2 in lines:
        y1 = max(0, y1 - margin)
        y2 = min(arr.shape[0], y2 + margin)
        crop = image.crop((0, y1, arr.shape[1], y2))
        if crop.width >= 8 and crop.height >= 4:
            crops.append(crop)
    return crops if crops else [image]  # se não encontrou linhas, usa página inteira


# ---------------------------------------------------------------------------
# Detecção rápida de script (sem dependência de langdetect)
# ---------------------------------------------------------------------------

def _detect_script_from_image(image) -> str:
    """
    Heurística rápida para detectar o script predominante numa imagem.
    Usa Tesseract OSD (Orientation and Script Detection) se disponível.
    Retorna: 'Latin', 'Cyrillic', 'Arabic', 'Hebrew', 'CJK' ou 'Unknown'.
    """
    try:
        import pytesseract
        osd = pytesseract.image_to_osd(image)
        for line in osd.splitlines():
            if line.startswith("Script:"):
                script = line.split(":", 1)[1].strip()
                return script
    except Exception:
        pass
    return "Unknown"


def _script_to_lang_hint(script: str) -> str | None:
    """Converte nome de script Tesseract para código de idioma hint."""
    mapping = {
        "Latin": None,          # ambíguo — usar detecção de idioma
        "Cyrillic": "ru",
        "Arabic": "ar",
        "Hebrew": "he",
        "Han": "zh",
        "Katakana": "ja",
        "Hiragana": "ja",
        "Hangul": "ko",
        "Devanagari": "hi",
        "Thai": "th",
        "Greek": "el",
    }
    return mapping.get(script)


# ---------------------------------------------------------------------------
# Inferência TrOCR
# ---------------------------------------------------------------------------

def _run_trocr(image_bytes: bytes, lang: str | None = None) -> str:
    """
    Executa reconhecimento HTR com TrOCR, selecionando modelo pelo idioma.
    Se lang=None, tenta detectar o script da imagem automaticamente.
    Usa modelo histórico e segmentação adaptativa quando PDFSEARCHABLE_OCR_HISTORICAL está ativo.
    """
    from PIL import Image
    import torch

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # Determinar idioma para seleção do modelo
    effective_lang = _forced_lang() or lang

    # Se ainda sem idioma, tentar detecção de script na imagem
    if not effective_lang:
        script = _detect_script_from_image(image)
        script_hint = _script_to_lang_hint(script)
        if script_hint:
            effective_lang = script_hint
            _log.debug("Script detectado na imagem: %s → lang=%s", script, effective_lang)

    historical = _historical_htr_enabled()
    model_id, desc = get_model_for_lang(effective_lang, historical=historical)
    _log.debug("HTR: lang=%s, historical=%s → modelo=%s (%s)",
               effective_lang or "auto", historical, model_id, desc)

    processor, model = _load_model(model_id)

    line_images = _split_lines(image, historical=historical)
    texts = []
    with torch.no_grad():
        for crop in line_images:
            try:
                pixel_values = processor(crop, return_tensors="pt").pixel_values
                generated_ids = model.generate(pixel_values)
                line_text = processor.batch_decode(generated_ids, skip_special_tokens=True)[
                    0
                ].strip()
                if line_text:
                    texts.append(line_text)
            except Exception:
                continue
    return "\n".join(texts) if texts else ""


# ---------------------------------------------------------------------------
# Ponto de entrada público
# ---------------------------------------------------------------------------

def run_htr_on_image(image_bytes: bytes, lang: str | None = None) -> str:
    """
    Reconhece texto manuscrito/cursivo em imagem (PNG/JPEG).
    Roteia para o backend configurado em PDFSEARCHABLE_HTR_BACKEND.

    Args:
        image_bytes: imagem em bytes (PNG/JPEG).
        lang: código de idioma (pt-BR, en, de, ru, etc.). Se None, auto-detecta.

    Retorna texto ou '' se HTR não disponível.
    """
    if not htr_available():
        return ""

    backend = get_htr_backend()

    if backend == HTR_BACKEND_TRANSKRIBUS:
        from pdfsearchable.htr_transkribus import run as _run
        return _run(image_bytes)

    if backend == HTR_BACKEND_ESCRIPTORIUM:
        from pdfsearchable.htr_escriptorium import run as _run
        return _run(image_bytes)

    # Backend padrão: TrOCR local multilíngue
    return _run_trocr(image_bytes, lang=lang)
