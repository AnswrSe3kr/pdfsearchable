"""
Detecção e preservação de fórmulas matemáticas em PDFs.

Estratégia (leve, sem modelos pesados):
  1. Regex sobre o texto extraído — capta fórmulas em notação LaTeX (``$...$``,
     ``$$...$$``, ``\\[...\\]``, ``\\begin{equation}...\\end{equation}``) e
     blocos com densidade alta de símbolos matemáticos Unicode (∫ ∑ ∏ √ ∂ ± ≤ ≥ ≠ ≈ π θ λ α β γ …).
  2. Resultado: lista de ``{page, raw, kind, latex}``, onde ``kind`` é um de
     ``"inline"``, ``"display"``, ``"environment"``, ``"unicode_heuristic"``.
  3. Preservação em export Markdown: delimitadores LaTeX já passam intactos
     pelo template; fórmulas Unicode-heurísticas ficam em bloco ``$$ … $$``
     (conversão best-effort em :func:`latex_from_unicode`).

Opt-in via ``PDFSEARCHABLE_DETECT_FORMULAS=1``. Sem dependências externas.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger("pdfsearchable.formulas")

__all__ = [
    "FormulaHit",
    "FormulaReport",
    "detect_formulas",
    "latex_from_unicode",
    "render_markdown_section",
]


# --- Regex patterns --------------------------------------------------------

# ``$$…$$`` display math (não guloso, não atravessa quebras duplas)
_RE_DISPLAY = re.compile(r"\$\$(?P<body>.+?)\$\$", re.DOTALL)
# ``\[…\]`` display math
_RE_BRACKET = re.compile(r"\\\[(?P<body>.+?)\\\]", re.DOTALL)
# ``$…$`` inline (um único ``$`` de cada lado, sem ``$$`` à volta)
_RE_INLINE = re.compile(r"(?<!\$)\$(?!\$)(?P<body>[^$\n]{2,200}?)(?<!\$)\$(?!\$)")
# ``\begin{env}…\end{env}`` (equation, align, gather, math, displaymath, eqnarray)
_RE_ENV = re.compile(
    r"\\begin\{(?P<env>equation\*?|align\*?|gather\*?|eqnarray\*?|math|displaymath)\}"
    r"(?P<body>.+?)"
    r"\\end\{(?P=env)\}",
    re.DOTALL,
)

# Símbolos Unicode matemáticos: blocos principais + alguns operadores comuns.
_UNICODE_MATH_CHARS = (
    "∫∮∯∑∏∐√∂∇∞±×÷≤≥≠≈≡≅∼∝∈∉∋⊂⊃⊆⊇∪∩∧∨¬→←↔⇒⇐⇔"
    "αβγδεζηθικλμνξοπρστυφχψωΓΔΘΛΞΠΣΦΨΩ"
    "°′″"
)
_RE_UNICODE_CHUNK = re.compile(
    r"(?:[A-Za-z0-9_(),.\s=+\-*/^]*[" + re.escape(_UNICODE_MATH_CHARS) + r"]"
    r"[A-Za-z0-9_(),.\s=+\-*/^" + re.escape(_UNICODE_MATH_CHARS) + r"]{0,120})"
)

# Densidade mínima de símbolos matemáticos para aceitar uma linha como fórmula
# (evita capturar prosa que contenha um único símbolo por acaso).
_UNICODE_DENSITY_THRESHOLD = 0.08  # 8% dos caracteres do trecho
_UNICODE_MIN_LEN = 4


@dataclass(frozen=True)
class FormulaHit:
    """Uma fórmula detectada numa página."""

    page: int  # 1-based
    raw: str  # trecho original tal como aparece no texto
    kind: str  # "inline" | "display" | "environment" | "unicode_heuristic"
    latex: str  # forma LaTeX (igual a ``raw`` para hits LaTeX; convertida para unicode_heuristic)

    def to_dict(self) -> dict[str, object]:
        return {"page": self.page, "raw": self.raw, "kind": self.kind, "latex": self.latex}


@dataclass
class FormulaReport:
    """Resultado agregado da detecção num documento."""

    total: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    hits: list[FormulaHit] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "by_kind": dict(self.by_kind),
            "hits": [h.to_dict() for h in self.hits],
        }


# --- Conversão unicode → LaTeX (best-effort) -------------------------------

_UNICODE_TO_LATEX: dict[str, str] = {
    # Operadores
    "∫": r"\int ", "∮": r"\oint ", "∯": r"\oiint ",
    "∑": r"\sum ", "∏": r"\prod ", "∐": r"\coprod ",
    "√": r"\sqrt ", "∂": r"\partial ", "∇": r"\nabla ",
    "∞": r"\infty ", "±": r"\pm ", "×": r"\times ", "÷": r"\div ",
    "≤": r"\le ", "≥": r"\ge ", "≠": r"\ne ", "≈": r"\approx ",
    "≡": r"\equiv ", "≅": r"\cong ", "∼": r"\sim ", "∝": r"\propto ",
    "∈": r"\in ", "∉": r"\notin ", "∋": r"\ni ",
    "⊂": r"\subset ", "⊃": r"\supset ", "⊆": r"\subseteq ", "⊇": r"\supseteq ",
    "∪": r"\cup ", "∩": r"\cap ", "∧": r"\wedge ", "∨": r"\vee ", "¬": r"\neg ",
    "→": r"\to ", "←": r"\gets ", "↔": r"\leftrightarrow ",
    "⇒": r"\Rightarrow ", "⇐": r"\Leftarrow ", "⇔": r"\Leftrightarrow ",
    "°": r"^{\circ}", "′": r"'", "″": r"''",
    # Letras gregas minúsculas
    "α": r"\alpha ", "β": r"\beta ", "γ": r"\gamma ", "δ": r"\delta ",
    "ε": r"\varepsilon ", "ζ": r"\zeta ", "η": r"\eta ", "θ": r"\theta ",
    "ι": r"\iota ", "κ": r"\kappa ", "λ": r"\lambda ", "μ": r"\mu ",
    "ν": r"\nu ", "ξ": r"\xi ", "ο": "o", "π": r"\pi ", "ρ": r"\rho ",
    "σ": r"\sigma ", "τ": r"\tau ", "υ": r"\upsilon ", "φ": r"\varphi ",
    "χ": r"\chi ", "ψ": r"\psi ", "ω": r"\omega ",
    # Letras gregas maiúsculas
    "Γ": r"\Gamma ", "Δ": r"\Delta ", "Θ": r"\Theta ", "Λ": r"\Lambda ",
    "Ξ": r"\Xi ", "Π": r"\Pi ", "Σ": r"\Sigma ", "Φ": r"\Phi ",
    "Ψ": r"\Psi ", "Ω": r"\Omega ",
}


def latex_from_unicode(text: str) -> str:
    """
    Converte símbolos matemáticos Unicode para os seus macros LaTeX equivalentes.
    Não tenta parse estrutural — é um mapeamento lexical, suficiente para preservar
    o significado ao exportar para markdown/pipelines de RAG.
    """
    if not text:
        return ""
    out = []
    for ch in text:
        out.append(_UNICODE_TO_LATEX.get(ch, ch))
    # Colapsa espaços múltiplos criados pelos macros
    return re.sub(r" {2,}", " ", "".join(out)).strip()


# --- Detecção --------------------------------------------------------------


def _iter_latex_hits(page_num: int, text: str) -> Iterable[FormulaHit]:
    """Captura `$...$`, `$$...$$`, `\\[...\\]` e ambientes de equação."""
    for m in _RE_ENV.finditer(text):
        body = m.group("body").strip()
        if body:
            yield FormulaHit(
                page=page_num,
                raw=m.group(0),
                kind="environment",
                latex=body,
            )
    for m in _RE_DISPLAY.finditer(text):
        body = m.group("body").strip()
        if body:
            yield FormulaHit(page=page_num, raw=m.group(0), kind="display", latex=body)
    for m in _RE_BRACKET.finditer(text):
        body = m.group("body").strip()
        if body:
            yield FormulaHit(page=page_num, raw=m.group(0), kind="display", latex=body)
    for m in _RE_INLINE.finditer(text):
        body = m.group("body").strip()
        if not body:
            continue
        # Rejeita se parece preço ou data (ex.: "$5,00", "$2024")
        if re.fullmatch(r"[\d.,\-\s]+", body):
            continue
        # Rejeita prosa: se tem >= 3 espaços (4+ palavras) sem qualquer indicador math,
        # é quase certamente texto entre dois cifrões de preços/moeda.
        has_math_indicator = bool(
            re.search(r"[=^_\\+\-*/]|\d[a-zA-Z]|[a-zA-Z]\d", body)
        ) or any(c in body for c in _UNICODE_MATH_CHARS)
        if body.count(" ") >= 3 and not has_math_indicator:
            continue
        yield FormulaHit(page=page_num, raw=m.group(0), kind="inline", latex=body)


def _iter_unicode_hits(page_num: int, text: str) -> Iterable[FormulaHit]:
    """Detecta blocos com densidade de símbolos matemáticos Unicode."""
    if not text:
        return
    seen: set[str] = set()
    for m in _RE_UNICODE_CHUNK.finditer(text):
        chunk = m.group(0).strip()
        if len(chunk) < _UNICODE_MIN_LEN:
            continue
        # densidade de caracteres matemáticos
        math_chars = sum(1 for c in chunk if c in _UNICODE_MATH_CHARS)
        if math_chars == 0:
            continue
        density = math_chars / max(len(chunk), 1)
        if density < _UNICODE_DENSITY_THRESHOLD:
            continue
        # dedup por chunk já visto nesta página
        key = chunk.lower()
        if key in seen:
            continue
        seen.add(key)
        yield FormulaHit(
            page=page_num,
            raw=chunk,
            kind="unicode_heuristic",
            latex=latex_from_unicode(chunk),
        )


def detect_formulas(
    page_texts: list[str],
    *,
    max_hits: int = 500,
) -> FormulaReport:
    """
    Percorre o texto por página e devolve um :class:`FormulaReport`.

    ``page_texts[i]`` corresponde à página ``i+1`` (1-based).
    ``max_hits`` limita o tamanho do resultado para documentos patológicos
    (ex.: PDF gerado automaticamente com milhares de linhas de símbolos).
    """
    report = FormulaReport()
    if not page_texts:
        return report
    for idx, text in enumerate(page_texts):
        if not text:
            continue
        page_num = idx + 1
        for hit in _iter_latex_hits(page_num, text):
            report.hits.append(hit)
            report.by_kind[hit.kind] = report.by_kind.get(hit.kind, 0) + 1
            if len(report.hits) >= max_hits:
                break
        if len(report.hits) >= max_hits:
            break
        for hit in _iter_unicode_hits(page_num, text):
            report.hits.append(hit)
            report.by_kind[hit.kind] = report.by_kind.get(hit.kind, 0) + 1
            if len(report.hits) >= max_hits:
                break
        if len(report.hits) >= max_hits:
            break
    report.total = len(report.hits)
    return report


# --- Renderização para Markdown --------------------------------------------


def render_markdown_section(report: FormulaReport, *, limit: int = 50) -> str:
    """
    Gera um bloco Markdown com as fórmulas detectadas, em formato LaTeX
    (display math ``$$ … $$``). Retorna string vazia se não houver fórmulas.
    """
    if not report or not report.hits:
        return ""
    lines = ["## Fórmulas detectadas", ""]
    lines.append(f"Total: **{report.total}** (")
    lines.append(
        ", ".join(f"{k}: {v}" for k, v in sorted(report.by_kind.items()))
    )
    lines.append(")")
    lines.append("")
    for hit in report.hits[:limit]:
        body = hit.latex.strip() or hit.raw.strip()
        lines.append(f"- Página {hit.page} ({hit.kind}):")
        lines.append("")
        lines.append(f"  $$ {body} $$")
        lines.append("")
    if len(report.hits) > limit:
        lines.append(f"_…e mais {len(report.hits) - limit} fórmulas omitidas._")
    return "\n".join(lines)
