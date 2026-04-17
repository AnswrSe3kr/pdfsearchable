"""Testes unitários para pdfsearchable.formulas."""

from __future__ import annotations

from pdfsearchable.formulas import (
    FormulaReport,
    detect_formulas,
    latex_from_unicode,
    render_markdown_section,
)


def test_detect_inline_latex():
    pages = ["O valor é $E = mc^2$ na equação de Einstein."]
    rep = detect_formulas(pages)
    assert rep.total == 1
    assert rep.hits[0].kind == "inline"
    assert rep.hits[0].latex == "E = mc^2"


def test_detect_display_dollar():
    pages = ["Texto.\n$$\\int_0^1 x^2 dx$$\nMais texto."]
    rep = detect_formulas(pages)
    assert rep.total == 1
    assert rep.hits[0].kind == "display"
    assert "int_0^1" in rep.hits[0].latex.replace("\\", "")


def test_detect_bracket_display():
    pages = [r"Prova: \[a^2 + b^2 = c^2\] (Pitágoras)."]
    rep = detect_formulas(pages)
    assert rep.total == 1
    assert rep.hits[0].kind == "display"


def test_detect_environment():
    pages = [r"\begin{equation} y = ax + b \end{equation}"]
    rep = detect_formulas(pages)
    assert rep.total == 1
    assert rep.hits[0].kind == "environment"


def test_detect_unicode_heuristic():
    # bloco com ∫, ∑ e letras gregas — densidade alta
    pages = ["Seja ∫ f(x) dx = ∑ α·β para θ ≥ π."]
    rep = detect_formulas(pages)
    assert rep.total >= 1
    kinds = {h.kind for h in rep.hits}
    assert "unicode_heuristic" in kinds
    # LaTeX convertido deve conter macros
    assert any("\\int" in h.latex or "\\sum" in h.latex for h in rep.hits)


def test_inline_rejects_price():
    # $5,00 e $2024 não devem ser capturados como fórmula
    pages = ["Preço $5,00 por unidade; ano $2024."]
    rep = detect_formulas(pages)
    assert rep.total == 0


def test_page_numbering_is_1_based():
    pages = ["sem fórmula", "com $x = 1$"]
    rep = detect_formulas(pages)
    assert rep.total == 1
    assert rep.hits[0].page == 2


def test_by_kind_counts():
    # entre inline: texto claro entre os pares para que o regex não "case"
    # o meio e não junte dois pares num só match.
    pages = [
        "Seja $x=1$. Então temos $y=2$ também.",
        "$$c=a+b$$",
        r"\begin{equation} d = e + f \end{equation}",
    ]
    rep = detect_formulas(pages)
    assert rep.by_kind.get("inline", 0) == 2
    assert rep.by_kind.get("display", 0) == 1
    assert rep.by_kind.get("environment", 0) == 1
    assert rep.total == 4


def test_max_hits_cap():
    # doc patológico: muitas fórmulas inline com separadores textuais
    # (para garantir que o regex faz match de cada uma individualmente)
    big = " ALGO ".join(f"$x_{i}=1$" for i in range(1000))
    rep = detect_formulas([big], max_hits=50)
    assert rep.total == 50
    assert len(rep.hits) == 50


def test_empty_input():
    assert detect_formulas([]).total == 0
    assert detect_formulas(["", ""]).total == 0


def test_latex_from_unicode_basic():
    assert "\\int" in latex_from_unicode("∫ x dx")
    assert "\\sum" in latex_from_unicode("∑")
    assert "\\alpha" in latex_from_unicode("α")
    assert "\\leftrightarrow" not in latex_from_unicode("α")  # só os ch presentes
    assert "\\Omega" in latex_from_unicode("Ω")


def test_latex_from_unicode_empty():
    assert latex_from_unicode("") == ""
    assert latex_from_unicode("texto sem símbolos") == "texto sem símbolos"


def test_render_markdown_section_empty():
    rep = FormulaReport()
    assert render_markdown_section(rep) == ""


def test_render_markdown_section_with_hits():
    pages = ["$E=mc^2$", "$$F = ma$$"]
    rep = detect_formulas(pages)
    md = render_markdown_section(rep)
    assert "## Fórmulas detectadas" in md
    assert "$$ E=mc^2 $$" in md
    assert "$$ F = ma $$" in md
    assert "Total: **2**" in md


def test_report_to_dict_roundtrip():
    pages = ["$x=1$"]
    rep = detect_formulas(pages)
    d = rep.to_dict()
    assert d["total"] == 1
    assert d["by_kind"]["inline"] == 1
    assert d["hits"][0]["page"] == 1
    assert d["hits"][0]["latex"] == "x=1"
