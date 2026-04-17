"""
Geração do report HTML com visual Apple-like.

Chamado apenas pelo servidor (pdfsearchable serve) ao arrancar — é o único ponto
do projeto onde o report é gerado. Inclui: estatísticas, lista de documentos,
busca (filtros avançados, sinônimos), nuvem de palavras (por tipo, termos em destaque),
mapa de referências a locais (ViaCEP, IP-API), document-view.
"""

import base64
import contextlib
import hashlib
import io
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

try:
    from pdfsearchable import __version__ as app_version
except ImportError:
    app_version = "0.2.0"

from pdfsearchable.audit import read_audit_trail
from pdfsearchable.config import get_search_synonyms
from pdfsearchable.content_extractors import (
    expand_search_query_ollama,
    extract_keywords_ollama,
    extract_locations_ollama,
)
from pdfsearchable import synonyms_api
from pdfsearchable.locations import (
    enrich_location_refs_geocode,
    enrich_location_refs_with_apis,
    get_location_refs,
    merge_location_refs_with_ia,
)
from pdfsearchable.exceptions import ReportError
from pdfsearchable.store import (
    STORE_DIR,
    PROCESSED_DIR_NAME,
    load_index,
    load_file_text,
    load_page_text,
    get_duplicate_groups,
)

# Onde está o template
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


# Sanitização para exibição: evita reflexão de path e caracteres perigosos no report
def _sanitize_display_name(s: str, max_len: int = 500) -> str:
    if not s or not isinstance(s, str):
        return "?"
    # Usar apenas o nome do arquivo (sem path) para exibição
    s = Path(s).name.strip()
    # Remover caracteres de controlo e quebras de linha
    s = "".join(c for c in s if c.isprintable() or c in " \t")
    s = s.strip() or "?"
    return s[:max_len] if len(s) > max_len else s


REPORT_PATH = STORE_DIR / "report.html"
DOCUMENT_VIEW_PATH = STORE_DIR / "document-view.html"
APP_PATH = STORE_DIR / "app.html"
REPORT_HASH_FILE = STORE_DIR / "report_hash.txt"

# Máximo de termos para enriquecer com API de sinônimos (evitar report lento)
_SYNONYMS_API_TOP_N = 12


# Paginação da lista de documentos (performance/escala)
def _list_page_size() -> int:
    v = (os.environ.get("PDFSEARCHABLE_LIST_PAGE_SIZE") or "50").strip()
    try:
        n = int(v)
        return max(10, min(500, n))
    except ValueError:
        return 50


# Limite de caracteres por documento/página no search_data (reduz tamanho do HTML)
def _search_snippet_max_chars() -> int:
    v = (os.environ.get("PDFSEARCHABLE_SEARCH_SNIPPET_MAX_CHARS") or "10000").strip()
    try:
        n = int(v)
        return max(1000, min(500_000, n))
    except ValueError:
        return 10000


def _enrich_search_synonyms(
    static_synonyms: dict[str, str],
    top_words: list[dict] | None,
    lang: str,
) -> dict[str, str]:
    """
    Mescla sinônimos estáticos (config/env) com sinônimos obtidos via API para as top palavras.
    Só chama a API se PDFSEARCHABLE_SYNONYMS_API_ENABLED estiver ativo (1, true, yes).
    Para pt-BR usa API Dicionário; para en-US usa API Ninjas (requer chave).
    Valores da API são unidos por vírgula (múltiplos sinônimos por termo).
    """
    enabled = (os.environ.get("PDFSEARCHABLE_SYNONYMS_API_ENABLED") or "").strip().lower()
    if enabled not in ("1", "true", "yes"):
        return static_synonyms
    if lang in ("en", "en-US") and not (
        os.environ.get("API_NINJAS_KEY") or os.environ.get("PDFSEARCHABLE_API_NINJAS_KEY")
    ):
        return static_synonyms
    merged = dict(static_synonyms)
    words_to_fetch: list[str] = []
    if top_words:
        for w in top_words:
            word = (w.get("word") or "").strip().lower()
            if len(word) >= 2 and word not in merged and word not in words_to_fetch:
                words_to_fetch.append(word)
                if len(words_to_fetch) >= _SYNONYMS_API_TOP_N:
                    break
    for word in words_to_fetch:
        try:
            syns = synonyms_api.get_synonyms(word, lang=lang)
            if syns:
                merged[word] = ", ".join(syns[:15])  # no máximo 15 sinônimos por termo
        except Exception:
            continue
    # Enriquecimento Ollama: expandir top palavras para busca (termos relacionados)
    if (os.environ.get("PDFSEARCHABLE_AI") or "").strip().lower() == "ollama" and top_words:
        for w in (top_words or [])[:8]:
            word = (w.get("word") or "").strip().lower()
            if len(word) < 2:
                continue
            expanded = expand_search_query_ollama(word, max_terms=4)
            if expanded:
                existing = merged.get(word, "")
                merged[word] = (
                    (existing + ", " + ", ".join(expanded[:6]))
                    if existing
                    else ", ".join(expanded[:6])
                )
    return merged


# Mantém letras (PT/ES: áéíóúàèìòùãõâêîôûçñ), números e espaços para nuvem de palavras
_RE_NON_WORD = re.compile(r"[^\w\sáéíóúàèìòùãõâêîôûçñ]", re.IGNORECASE | re.UNICODE)


def _normalize_for_wordcloud(text: str) -> str:
    """Remove caracteres desnecessários e deixa em minúsculas."""
    if not text:
        return ""
    return _RE_NON_WORD.sub(" ", text.lower())


def _stopwords_en() -> set[str]:
    """Stopwords em inglês (básico)."""
    return {
        "the",
        "of",
        "and",
        "to",
        "in",
        "a",
        "is",
        "that",
        "it",
        "for",
        "was",
        "on",
        "are",
        "as",
        "with",
        "his",
        "they",
        "be",
        "at",
        "one",
        "have",
        "this",
        "from",
        "or",
        "had",
        "by",
        "not",
        "but",
        "what",
        "some",
        "we",
        "can",
        "out",
        "other",
        "were",
        "all",
        "there",
        "when",
        "up",
        "use",
        "your",
        "how",
        "said",
        "each",
        "she",
        "which",
        "their",
        "time",
        "if",
        "will",
        "way",
        "about",
        "many",
        "then",
        "them",
        "would",
        "write",
        "like",
        "so",
        "these",
        "her",
        "long",
        "make",
        "thing",
        "see",
        "him",
        "two",
        "has",
        "look",
        "more",
        "day",
        "could",
        "go",
    }


def _stopwords_es() -> set[str]:
    """Stopwords em espanhol (básico)."""
    return {
        "de",
        "la",
        "que",
        "el",
        "en",
        "y",
        "a",
        "los",
        "del",
        "se",
        "las",
        "por",
        "un",
        "para",
        "con",
        "no",
        "una",
        "su",
        "al",
        "lo",
        "como",
        "más",
        "pero",
        "sus",
        "le",
        "ya",
        "o",
        "fue",
        "porque",
        "esta",
        "entre",
        "cuando",
        "muy",
        "sin",
        "sobre",
        "también",
        "me",
        "hasta",
        "hay",
        "donde",
        "quien",
        "desde",
        "todo",
        "nos",
        "durante",
        "estados",
        "uno",
        "les",
        "ni",
        "contra",
        "otros",
        "ese",
        "eso",
        "ante",
        "ellos",
        "e",
        "esto",
        "mí",
        "antes",
        "algunos",
        "qué",
        "unos",
        "yo",
        "otro",
        "muchos",
        "the",
        "of",
        "and",
        "to",
        "in",
        "is",
        "it",
        "for",
        "was",
        "on",
        "are",
    }


def _stopwords_pt() -> set[str]:
    """Stopwords em português (básico)."""
    return {
        "de",
        "a",
        "o",
        "que",
        "e",
        "do",
        "da",
        "em",
        "um",
        "para",
        "é",
        "com",
        "não",
        "uma",
        "os",
        "no",
        "se",
        "na",
        "por",
        "mais",
        "as",
        "dos",
        "como",
        "mas",
        "foi",
        "ao",
        "ele",
        "das",
        "tem",
        "à",
        "seu",
        "sua",
        "ou",
        "ser",
        "quando",
        "muito",
        "há",
        "nos",
        "já",
        "está",
        "eu",
        "também",
        "só",
        "pelo",
        "pela",
        "até",
        "isso",
        "ela",
        "entre",
        "era",
        "depois",
        "sem",
        "mesmo",
        "aos",
        "ter",
        "seus",
        "quem",
        "nas",
        "me",
        "esse",
        "eles",
        "estão",
        "você",
        "tinha",
        "foram",
        "essa",
        "num",
        "nem",
        "suas",
        "meu",
        "às",
        "minha",
        "têm",
        "numa",
        "pelos",
        "elas",
        "havia",
        "seja",
        "qual",
        "será",
        "nós",
        "tenho",
        "lhe",
        "deles",
        "essas",
        "esses",
        "pelas",
        "este",
        "fosse",
        "dele",
        "tu",
        "te",
        "vocês",
        "vos",
        "lhes",
        "meus",
        "minhas",
        "teu",
        "tua",
        "teus",
        "tuas",
        "nosso",
        "nossa",
        "dela",
        "delas",
        "esta",
        "estes",
        "estas",
        "aquele",
        "aquela",
        "aqueles",
        "aquelas",
        "isto",
        "aquilo",
        "the",
        "of",
        "and",
        "to",
        "in",
        "is",
        "you",
        "that",
        "it",
        "he",
        "for",
        "was",
        "on",
        "are",
        "with",
        "his",
        "they",
        "be",
        "at",
        "one",
        "have",
        "this",
        "from",
        "or",
        "had",
        "by",
        "not",
        "word",
        "but",
        "what",
        "some",
        "we",
        "can",
        "out",
        "other",
        "were",
        "all",
        "there",
        "when",
        "up",
        "use",
        "your",
        "how",
        "said",
        "each",
        "she",
        "which",
        "their",
        "time",
    }


def _stopwords_for_lang(lang: str) -> set[str]:
    """Stopwords por idioma (pt-BR, en, es). Fallback para PT."""
    lang = (lang or "").strip().lower()
    if lang in ("en", "en-us", "en_us"):
        return _stopwords_en()
    if lang in ("es", "es-es", "es_es"):
        return _stopwords_es()
    return _stopwords_pt()


def _stopwords_with_exclusions() -> set[str]:
    """Stopwords conforme PDFSEARCHABLE_WORDCLOUD_LANG + exclusões (env PDFSEARCHABLE_WORDCLOUD_STOP)."""
    lang = (os.environ.get("PDFSEARCHABLE_WORDCLOUD_LANG") or "pt-BR").strip() or "pt-BR"
    stop = _stopwords_for_lang(lang)
    stop.update(("0001", "0002", "xxx"))
    extra = os.environ.get("PDFSEARCHABLE_WORDCLOUD_STOP", "").strip()
    if extra:
        for w in extra.split(","):
            w = w.strip().lower()
            if w:
                stop.add(w)
    return stop


# Paleta estilo Apple para nuvem de palavras (backend e referência no front)
WORDCLOUD_COLORS = [
    "#0071e3",
    "#5856d6",
    "#af52de",
    "#34c759",
    "#30b0c7",
    "#ff9500",
    "#ff2d55",
    "#5e5ce6",
    "#bf5af2",
    "#ffcc00",
]


def build_wordcloud_b64(
    all_text: str,
    width: int = 800,
    height: int = 400,
    stopwords: set[str] | None = None,
) -> str | None:
    """
    Gera nuvem de palavras a partir do texto e retorna PNG em base64.
    Usa paleta suave (estilo Apple) e hierarquia de tamanhos clara.
    """
    if not all_text or len(all_text.strip()) < 50:
        return None
    try:
        from wordcloud import WordCloud
    except ImportError:
        return None
    import random

    stop = stopwords if stopwords is not None else _stopwords_with_exclusions()
    max_word_len = 0
    with contextlib.suppress(ValueError):
        max_word_len = max(0, int(os.environ.get("PDFSEARCHABLE_WORDCLOUD_MAX_WORD_LEN", "0")))
    text = _normalize_for_wordcloud(all_text)
    words = [
        w
        for w in text.split()
        if len(w) > 2
        and w not in stop
        and w.isalnum()
        and not w.isdigit()
        and (max_word_len <= 0 or len(w) <= max_word_len)
    ]
    if not words:
        return None
    rng = random.Random(42)  # noqa: S311 — RNG para cores de word cloud; sem uso criptográfico

    def color_func(*args, **kwargs):
        return rng.choice(WORDCLOUD_COLORS)

    wordcloud = WordCloud(
        width=width,
        height=height,
        background_color="white",
        max_words=100,
        min_font_size=10,
        max_font_size=140,
        relative_scaling=0.5,
        prefer_horizontal=0.7,
        color_func=color_func,
        random_state=42,
    ).generate(" ".join(words))
    buf = io.BytesIO()
    wordcloud.to_image().save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _get_stemmer():
    """Se PDFSEARCHABLE_WORDCLOUD_STEMMING=1 e NLTK disponível, retorna stemmer (pt + en). Senão None."""
    if (os.environ.get("PDFSEARCHABLE_WORDCLOUD_STEMMING") or "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return None
    try:
        from nltk.stem import SnowballStemmer

        # Português cobre a maioria; para texto misto podemos stemar com ambos e preferir o que existir
        pt = SnowballStemmer("portuguese")
        en = SnowballStemmer("english")

        def stem(w: str) -> str:
            s_pt, s_en = pt.stem(w.lower()), en.stem(w.lower())
            return s_pt if len(s_pt) >= 2 else s_en

        return stem
    except Exception:
        return None


def _tokenize_for_stats(text: str, stopwords: set[str], max_word_len: int = 0) -> list[str]:
    """Tokeniza texto para contagem (palavras > 2 chars, não stopword, alfanum; exclui tokens só numéricos)."""
    norm = _normalize_for_wordcloud(text)
    return [
        w
        for w in norm.split()
        if len(w) > 2
        and w not in stopwords
        and w.isalnum()
        and not w.isdigit()
        and (max_word_len <= 0 or len(w) <= max_word_len)
    ]


def _wordcloud_min_count() -> int:
    """Mínimo de ocorrências para termo entrar na nuvem (env PDFSEARCHABLE_WORDCLOUD_MIN_COUNT)."""
    try:
        return max(1, int(os.environ.get("PDFSEARCHABLE_WORDCLOUD_MIN_COUNT", "1")))
    except ValueError:
        return 1


def _wordcloud_max_word_len() -> int:
    """Máximo de caracteres por palavra (0 = sem limite). env PDFSEARCHABLE_WORDCLOUD_MAX_WORD_LEN."""
    try:
        return max(0, int(os.environ.get("PDFSEARCHABLE_WORDCLOUD_MAX_WORD_LEN", "0")))
    except ValueError:
        return 0


def build_top_words(all_text: str, top_n: int = 50) -> list[dict]:
    """Retorna lista [{word, count}] das top N palavras. Com PDFSEARCHABLE_WORDCLOUD_STEMMING=1 agrupa por radical (NLTK) e exibe a forma mais frequente."""
    stop = _stopwords_with_exclusions()
    max_len = _wordcloud_max_word_len()
    tokens = _tokenize_for_stats(all_text, stop, max_word_len=max_len)
    stemmer = _get_stemmer()
    if stemmer:
        # Agrupar por stem; contar por forma de superfície; exibir a forma mais frequente
        stem_to_forms: dict[str, Counter] = {}
        for t in tokens:
            s = stemmer(t)
            stem_to_forms.setdefault(s, Counter())[t] += 1
        items = []
        for _stem, form_counts in stem_to_forms.items():
            total = sum(form_counts.values())
            best_form = form_counts.most_common(1)[0][0]
            items.append({"word": best_form, "count": total})
        items.sort(key=lambda x: -x["count"])
        min_count = _wordcloud_min_count()
        items = [x for x in items if x["count"] >= min_count]
        return items[:top_n]
    cnt = Counter(tokens)
    min_count = _wordcloud_min_count()
    items = [{"word": w, "count": c} for w, c in cnt.most_common(top_n * 2) if c >= min_count]
    return items[:top_n]


def build_bigrams(all_text: str, top_n: int = 30) -> list[dict]:
    """Retorna lista [{phrase, count}] dos top N bigramas (ex.: 'nota fiscal')."""
    stop = _stopwords_with_exclusions()
    tokens = _tokenize_for_stats(all_text, stop)
    if len(tokens) < 2:
        return []
    pairs = [f"{tokens[i]} {tokens[i + 1]}" for i in range(len(tokens) - 1)]
    cnt = Counter(pairs)
    return [{"phrase": p, "count": c} for p, c in cnt.most_common(top_n)]


def _wordcloud_words_with_bigrams(
    top_words: list[dict] | None,
    bigrams: list[dict] | None,
    max_words: int = 75,
    max_bigrams: int = 20,
) -> list[list[str | int]]:
    """Junta top palavras e bigramas para a nuvem: [[palavra ou frase, count], ...]. Bigramas aparecem como termos únicos na nuvem."""
    out: list[list[str | int]] = []
    for w in (top_words or [])[:max_words]:
        out.append([w["word"], w["count"]])
    for b in (bigrams or [])[:max_bigrams]:
        out.append([b["phrase"], b["count"]])
    return out


def build_highlight_snippets(
    all_text: str,
    top_words: list[dict],
    max_terms: int = 12,
    snippet_chars: int = 120,
) -> list[dict]:
    """
    Para cada uma das top N palavras, extrai um trecho de texto onde a palavra aparece.
    Retorna lista de { "word", "snippet" } para a seção "Termos em destaque".
    """
    if not all_text or not top_words:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for wd in top_words[:max_terms]:
        word = (wd.get("word") or "").strip()
        if not word or len(word) < 3 or word in seen:
            continue
        seen.add(word)
        escaped = re.escape(word)
        pattern = re.compile(r"(?<!\w)" + escaped + r"(?!\w)", re.IGNORECASE)
        m = pattern.search(all_text)
        if not m:
            continue
        start = m.start()
        half = snippet_chars // 2
        s_start = max(0, start - half)
        s_end = min(len(all_text), start + len(word) + half)
        snippet = all_text[s_start:s_end]
        if s_start > 0:
            snippet = "…" + snippet
        if s_end < len(all_text):
            snippet = snippet + "…"
        snippet = " ".join(snippet.split())
        if snippet:
            out.append({"word": word, "snippet": snippet})
    return out


def _report_index_hash(idx: dict) -> str:
    """Hash estável do índice para cache do report (evita regerar se nada mudou)."""
    canonical = json.dumps(
        {
            "version": idx.get("version"),
            "files": [
                {
                    "id": f.get("id"),
                    "updated_at": f.get("updated_at"),
                    "indexed_at": f.get("indexed_at"),
                }
                for f in idx.get("files", [])
            ],
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_search_data(max_chars: int | None = None) -> list[dict]:
    """
    Lista por arquivo: name, text (concat), pages: [{n, text}] para busca com número de página.
    Se max_chars for definido (env PDFSEARCHABLE_SEARCH_SNIPPET_MAX_CHARS), texto e páginas
    são truncados para limitar o tamanho do HTML (performance com muitos documentos).
    """
    idx = load_index()
    files = idx.get("files", [])
    limit = max_chars if max_chars is not None else _search_snippet_max_chars()
    out = []
    for f in files:
        fid = f.get("id")
        if not fid:
            continue
        full_text = load_file_text(fid)
        page_list = f.get("pages") or []
        pages = []
        for p in page_list:
            n = p.get("n")
            if n is None:
                continue
            pt = load_page_text(fid, n)
            if limit and len(pt) > limit:
                pt = pt[:limit] + "\n[… texto truncado para o report …]"
            pages.append({"n": n, "text": pt})
        if not pages:
            pages = [{"n": 1, "text": full_text}]
        if limit and len(full_text) > limit:
            full_text = full_text[:limit] + "\n[… texto truncado para o report …]"
        out.append(
            {
                "name": _sanitize_display_name(f.get("name") or "?"),
                "id": fid,
                "text": full_text,
                "pages": pages,
                "doc_type": f.get("doc_type") or "documento",
                "num_pages": f.get("num_pages") or 0,
                "indexed_at": (f.get("indexed_at") or "")[:10],
                "parties": f.get("parties") or [],
            }
        )
    return out


def generate_report(title: str = "Report — pdfsearchable", force: bool = False) -> Path:
    """
    Gera o report e a página de visualização de documento em .pdfsearchable/.

    Invocado apenas pelo servidor (pdfsearchable serve) ao arrancar. Produz:
    - .pdfsearchable/report.html (home do report)
    - .pdfsearchable/document-view.html (visualização de PDF + metadados)
    Cache: se o índice não mudou (hash em report_hash.txt), não regera, a menos que force=True.
    Levanta StoreError se o índice não for acessível; ReportError se a geração falhar.
    """
    try:
        STORE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ReportError(
            f"Não foi possível criar a pasta do report: {e}",
            {"phase": "init", "path": str(STORE_DIR)},
        ) from e
    idx = load_index()
    current_hash = _report_index_hash(idx)
    if not force and REPORT_HASH_FILE.exists():
        try:
            if REPORT_HASH_FILE.read_text(encoding="utf-8").strip() == current_hash:
                return REPORT_PATH
        except OSError:
            pass

    files = idx.get("files", [])
    total_files = len(files)
    total_pages = sum(f.get("num_pages", 0) for f in files)
    total_words = sum(f.get("word_count", 0) for f in files)
    total_size_bytes = sum(f.get("file_size") or 0 for f in files)
    total_size_mb = round(total_size_bytes / (1024 * 1024), 2)
    count_by_type = Counter(f.get("doc_type") or "documento" for f in files)
    doc_types = sorted(count_by_type.keys())
    count_by_language = Counter(f.get("language") or "—" for f in files)
    languages = sorted(count_by_language.keys(), key=lambda x: (x == "—", str(x).lower()))
    docs_classified_by_ia = sum(
        1 for f in files if f.get("classification_source") in ("openai", "ollama")
    )
    total_pages_with_ocr = 0
    for f in files:
        pages = f.get("pages") or []
        if pages:
            has_ocr_count = sum(1 for p in pages if p.get("has_ocr"))
            if has_ocr_count > 0 or any(p.get("has_ocr") is False for p in pages):
                total_pages_with_ocr += has_ocr_count
            else:
                n = len(pages)
                pct = f.get("ocr_percentage") or 0
                total_pages_with_ocr += round(n * pct / 100) if n else 0
        else:
            n = f.get("num_pages") or 0
            pct = f.get("ocr_percentage") or 0
            total_pages_with_ocr += round(n * pct / 100) if n else 0
    ocr_percentage_total = round(100.0 * total_pages_with_ocr / total_pages) if total_pages else 0
    # Tamanho: documento médio e documentos grandes
    try:
        docs_large_threshold = max(
            10, int(os.environ.get("PDFSEARCHABLE_STATS_LARGE_DOC_PAGES", "50"))
        )
    except ValueError:
        docs_large_threshold = 50
    avg_pages_per_doc = round(total_pages / total_files) if total_files else 0
    docs_large_count = sum(1 for f in files if (f.get("num_pages") or 0) > docs_large_threshold)
    # Temporal: distribuição por ano (indexed_at ou updated_at)
    count_by_year: Counter[str] = Counter()
    for f in files:
        dt_str = (f.get("updated_at") or f.get("indexed_at") or "").strip()
        if len(dt_str) >= 4 and dt_str[:4].isdigit():
            count_by_year[dt_str[:4]] += 1
    years_sorted = sorted(count_by_year.keys(), reverse=True)
    index_version = idx.get("version", 1)
    report_generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Ordenar arquivos por data (updated_at ou indexed_at, mais recente primeiro)
    def _sort_key(f: dict) -> str:
        return f.get("updated_at") or f.get("indexed_at") or ""

    files_sorted = sorted(files, key=_sort_key, reverse=True)
    list_page_size = _list_page_size()

    all_text = ""
    for f in files:
        text = load_file_text(f.get("id", ""))
        all_text += " " + text
    stop = _stopwords_with_exclusions()
    wordcloud_b64 = build_wordcloud_b64(all_text, stopwords=stop)
    wordcloud_by_type: dict[str, str | None] = {}
    for dt in doc_types:
        text_type = ""
        for f in files:
            if (f.get("doc_type") or "documento") == dt:
                text_type += " " + load_file_text(f.get("id", ""))
        wordcloud_by_type[dt] = build_wordcloud_b64(text_type, stopwords=stop)

    top_words = build_top_words(all_text, top_n=120)
    wordcloud_ai_enriched = False
    if (os.environ.get("PDFSEARCHABLE_AI") or "").strip().lower() == "ollama" and all_text.strip():
        ia_keywords = extract_keywords_ollama(all_text, max_keywords=20)
        if ia_keywords:
            existing = {w["word"].lower() for w in (top_words or [])}
            boost = max(1, (top_words[0]["count"] if top_words else 5) // 2)
            for kw in ia_keywords:
                k = kw.strip().lower()
                if not k or len(k) < 2 or k in existing:
                    continue
                existing.add(k)
                top_words = [*(top_words or []), {"word": kw.strip(), "count": boost}]
            top_words = sorted(top_words, key=lambda x: -x["count"])[:55]
            wordcloud_ai_enriched = True
    bigrams = build_bigrams(all_text, top_n=30)
    highlight_snippets = build_highlight_snippets(
        all_text, top_words or [], max_terms=12, snippet_chars=120
    )
    search_data = build_search_data()
    all_parties: list[str] = []
    seen_parties: set[str] = set()
    for f in files:
        for p in f.get("parties") or []:
            if p and p.strip() and p.strip() not in seen_parties:
                seen_parties.add(p.strip())
                all_parties.append(p.strip())
    all_parties.sort(key=str.lower)

    recent_activity = [
        e
        for e in read_audit_trail(50)
        if e.get("action")
        in (
            "index_done",
            "index_error",
            "index_skipped_unchanged",
            "index_start",
            "index_updated_path",
            "index_large_file",
        )
    ][:25]

    duplicate_groups = get_duplicate_groups()
    duplicate_groups_count = len(duplicate_groups) if duplicate_groups else 0
    duplicate_files_count = sum(len(g) for g in duplicate_groups) if duplicate_groups else 0

    docs_text = [(f.get("id", ""), load_file_text(f.get("id", ""))) for f in files]
    location_refs = get_location_refs(docs_text) if docs_text else []
    if (
        (os.environ.get("PDFSEARCHABLE_AI") or "").strip().lower() == "ollama"
        and all_text.strip()
        and docs_text
    ):
        ia_locations = extract_locations_ollama(all_text, max_locations=30)
        if ia_locations:
            location_refs = merge_location_refs_with_ia(location_refs, docs_text, ia_locations)
    location_refs = enrich_location_refs_with_apis(location_refs, docs_text, files)
    geocode_env = (os.environ.get("PDFSEARCHABLE_GEOCODE") or "1").strip().lower()
    if geocode_env not in ("0", "false", "no"):
        max_geocode = 25
        with contextlib.suppress(ValueError):
            max_geocode = max(5, min(100, int(os.environ.get("PDFSEARCHABLE_GEOCODE_MAX", "25"))))
        location_refs = enrich_location_refs_geocode(
            location_refs, STORE_DIR, max_new_lookups=max_geocode
        )

    # --- Timeline: agrupar documentos por ano (data do conteúdo ou indexação) ---
    _year_re = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")

    def _doc_year(f: dict) -> str | None:
        # 1. metadata do PDF (criação / modificação)
        meta = f.get("metadata") or {}
        for key in ("creationDate", "creation_date", "CreationDate", "ModDate"):
            v = str(meta.get(key, "") or "")
            m = _year_re.search(v)
            if m:
                return m.group(1)
        # 2. data de indexação / atualização como fallback
        for key in ("updated_at", "indexed_at"):
            v = str(f.get(key) or "")
            if len(v) >= 4 and v[:4].isdigit():
                return v[:4]
        return None

    timeline_map: dict[str, list[dict]] = {}
    for f in files:
        y = _doc_year(f)
        if y:
            entry = {
                "name": _sanitize_display_name(f.get("name") or "?"),
                "id": f.get("id", ""),
                "doc_type": f.get("doc_type") or "documento",
            }
            timeline_map.setdefault(y, []).append(entry)
    timeline_entries = [
        {"year": y, "docs": timeline_map[y]}
        for y in sorted(timeline_map.keys(), reverse=True)
    ]

    # --- Dashboard de qualidade ---
    quality_no_text: list[dict] = []
    quality_low_ocr: list[dict] = []
    quality_unclassified: list[dict] = []
    for f in files:
        fid = f.get("id", "")
        fname = _sanitize_display_name(f.get("name") or "?")
        ftype = f.get("doc_type") or "documento"
        # Arquivos sem texto extraído
        try:
            t = load_file_text(fid)
            if not t or len(t.strip()) < 20:
                quality_no_text.append({"id": fid, "name": fname, "doc_type": ftype})
        except Exception:
            quality_no_text.append({"id": fid, "name": fname, "doc_type": ftype})
        # OCR com baixa confiança (< 60 %)
        conf = f.get("ocr_avg_confidence")
        if conf is not None and float(conf) > 0 and float(conf) < 0.60:
            quality_low_ocr.append({
                "id": fid,
                "name": fname,
                "confidence": round(float(conf) * 100),
            })
        # Documentos sem classificação IA (tipo = "documento" e sem source)
        src = f.get("classification_source")
        if ftype == "documento" and src not in ("openai", "ollama"):
            quality_unclassified.append({"id": fid, "name": fname})

    quality_data = {
        "no_text": quality_no_text,
        "low_ocr": quality_low_ocr,
        "unclassified": quality_unclassified,
    }
    quality_issues_count = len(quality_no_text) + len(quality_low_ocr)

    synonyms_lang = (os.environ.get("PDFSEARCHABLE_SYNONYMS_LANG") or "pt-BR").strip() or "pt-BR"
    search_synonyms = _enrich_search_synonyms(
        get_search_synonyms(),
        top_words,
        synonyms_lang,
    )

    try:
        env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        env.filters["sanitize_display_name"] = _sanitize_display_name
        template = env.get_template("report.html")
        html = template.render(
            title=title,
            total_files=total_files,
            total_pages=total_pages,
            total_words=total_words,
            total_size_mb=total_size_mb,
            count_by_type=dict(count_by_type),
            doc_types=doc_types,
            count_by_language=dict(count_by_language),
            languages=languages,
            docs_classified_by_ia=docs_classified_by_ia,
            total_pages_with_ocr=total_pages_with_ocr,
            ocr_percentage_total=ocr_percentage_total,
            duplicate_groups_count=duplicate_groups_count,
            duplicate_files_count=duplicate_files_count,
            total_locations=len(location_refs),
            avg_pages_per_doc=avg_pages_per_doc,
            docs_large_threshold=docs_large_threshold,
            docs_large_count=docs_large_count,
            count_by_year=dict(count_by_year),
            years_sorted=years_sorted,
            index_version=index_version,
            app_version=app_version,
            report_generated_at=report_generated_at,
            wordcloud_b64=wordcloud_b64,
            wordcloud_by_type=wordcloud_by_type,
            top_words=top_words,
            bigrams=bigrams,
            search_data=search_data,
            recent_activity=recent_activity,
            duplicate_groups=duplicate_groups,
            location_refs=location_refs,
            files_base_url=f"../{PROCESSED_DIR_NAME}",
            report_home_url="report.html",
            document_view_url="document-view.html",
            wordcloud_words=_wordcloud_words_with_bigrams(top_words, bigrams),
            wordcloud_ai_enriched=wordcloud_ai_enriched,
            highlight_snippets=highlight_snippets,
            all_parties=all_parties,
            search_synonyms=search_synonyms,
            list_page_size=list_page_size,
            files=files_sorted[:list_page_size],
            files_rest=files_sorted[list_page_size:],
            timeline_entries=timeline_entries,
            quality_data=quality_data,
            quality_issues_count=quality_issues_count,
        )
    except Exception as e:
        raise ReportError(
            f"Falha ao gerar o report (template ou dados): {e}",
            {"phase": "report"},
        ) from e
    try:
        REPORT_PATH.write_text(html, encoding="utf-8")
    except OSError as e:
        raise ReportError(
            f"Não foi possível gravar report.html: {e}",
            {"phase": "report", "path": str(REPORT_PATH)},
        ) from e

    # Metadados por documento para a página de visualização (sem páginas/texto pesado)
    def _format_size(size_bytes: int | None) -> str:
        if size_bytes is None or size_bytes < 0:
            return "—"
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes / (1024 * 1024):.1f} MB"

    docs_meta = {
        f["id"]: {
            "id": f.get("id"),
            "name": f.get("name"),
            "doc_type": f.get("doc_type"),
            "num_pages": f.get("num_pages"),
            "word_count": f.get("word_count"),
            "file_size": f.get("file_size"),
            "file_size_formatted": _format_size(f.get("file_size")),
            "original_path": f.get("original_path"),
            "content_hash": f.get("content_hash"),
            "indexed_at": f.get("indexed_at"),
            "updated_at": f.get("updated_at"),
            "language": f.get("language"),
            "metadata": f.get("metadata") or {},
            "ocr_percentage": f.get("ocr_percentage"),
            "classification_source": f.get("classification_source"),
            "classification_confidence": f.get("classification_confidence"),
            "summary": f.get("summary"),
            "subject": f.get("subject"),
            "tags": f.get("tags") or [],
            "monetary_values": f.get("monetary_values") or [],
            "parties": f.get("parties") or [],
            "identified_emails": f.get("identified_emails") or [],
            "identified_cpfs": f.get("identified_cpfs") or [],
            "identified_cnpjs": f.get("identified_cnpjs") or [],
            "identified_ips": f.get("identified_ips") or [],
            "identified_addresses": f.get("identified_addresses") or [],
            "identified_phones": f.get("identified_phones") or [],
            "identified_locations": f.get("identified_locations") or [],
        }
        for f in files
    }
    try:
        doc_view_html = env.get_template("document-view.html").render(
            docs_meta=docs_meta,
            files_base_url=f"../{PROCESSED_DIR_NAME}",
            report_home_url="report.html",
        )
    except Exception as e:
        raise ReportError(
            f"Falha ao gerar document-view.html: {e}",
            {"phase": "document-view"},
        ) from e
    try:
        DOCUMENT_VIEW_PATH.write_text(doc_view_html, encoding="utf-8")
    except OSError as e:
        raise ReportError(
            f"Não foi possível gravar document-view.html: {e}",
            {"phase": "document-view", "path": str(DOCUMENT_VIEW_PATH)},
        ) from e
    with contextlib.suppress(OSError):
        REPORT_HASH_FILE.write_text(current_hash, encoding="utf-8")

    # Copy static SPA (app.html) — no Jinja rendering needed, pure client-side
    _app_src = TEMPLATES_DIR / "app.html"
    if _app_src.exists():
        try:
            import shutil as _shutil
            _shutil.copy2(_app_src, APP_PATH)
        except OSError as e:
            # Non-fatal: log and continue — report.html is the primary output
            import logging as _logging
            _logging.getLogger(__name__).warning("Não foi possível copiar app.html: %s", e)

    return REPORT_PATH
