"""
Identificação do tipo do documento: heurísticas (sempre disponível) e IA externa (opcional).

Modos de classificação (env PDFSEARCHABLE_AI):
- auto: usa OpenAI se OPENAI_API_KEY estiver definida; senão heurísticas
- heuristics: só heurísticas (rápido, sem custo)
- openai: só OpenAI (requer OPENAI_API_KEY)
- ollama: modelo local Ollama (http://localhost:11434); fallback para heurísticas se indisponível

Provedor (env PDFSEARCHABLE_AI_PROVIDER): openai | ollama.
Referência: pip install -e ".[ai]" para suporte a OpenAI. Ollama não requer dependência extra.
"""

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pdfsearchable.audit import get_logger as _get_logger

_log = _get_logger("pdfsearchable.ai_classifier")

# URL base da API Ollama (modelo local)
OLLAMA_BASE_URL = "http://localhost:11434"

# Padrões compilados (fallback para hints de tipo)
_RE_EMAIL_HINT = re.compile(
    r"\b(?:e-?mail|assunto|from:|to:|destinatário|remetente|para:|cc:)\b",
    re.IGNORECASE,
)
_RE_INVOICE_HINT = re.compile(
    r"\b(?:invoice|fatura|faturamento|nota\s*fiscal|nf-?e|valor\s*total)\b",
    re.IGNORECASE,
)

# Tipos conhecidos (para normalizar resposta da IA e para prompt)
KNOWN_TYPES = [
    "contrato",
    "edital",
    "nota_fiscal",
    "relatório",
    "procuração",
    "petição",
    "recibo",
    "ata",
    "certidão",
    "comprovante",
    "apresentação",
    "manual",
    "artigo",
    "registro",
    "lista",
    "política",
    "e-mail",
    "fatura",
    "documento",
]

# Heurísticas: tipo → palavras-chave (quanto mais no início do texto, mais relevante).
# Regra de ouro: prefer keywords muito específicas do tipo; evitar palavras genéricas
# que aparecem em muitos outros contextos (ex.: "valor total", "pagamento", "autorização").
DOC_TYPE_KEYWORDS: dict[str, list[str]] = {
    "contrato": [
        "contrato",
        "cláusula",
        "obrigações",
        "vigência",
        "pacto",
        "outorgantes",
        "partes contratantes",
        "contratante",
        "contratada",
    ],
    "edital": [
        "edital",
        "concurso público",
        "licitação",
        "pregão",
        "tomada de preços",
        "convite",
        "chamamento público",
        "processo seletivo",
        "inscrições",
        "candidatos",
    ],
    "nota_fiscal": [
        "nota fiscal",
        "nf-e",
        "danfe",
        "cfop",
        "icms",
        "chave de acesso",
        "natureza da operação",
        "dados do emitente",
    ],
    "relatório": [
        "relatório",
        "recomendações",
        "análise técnica",
        "objetivo do relatório",
        "executive summary",
        "findings",
        "scope of work",
    ],
    "procuração": ["procuração", "outorgante", "outorgado", "substabelecer", "poderes especiais"],
    "petição": ["petição", "exmo", "meritíssimo", "juiz", "requer", "impetrante", "réu"],
    "recibo": ["recibo", "recebi de", "quitância", "quitado"],
    "ata": [
        "ata de reunião",
        "ata da reunião",
        "sessão pública",
        "deliberação",
        "lavrada a ata",
        "ordem do dia",
    ],
    "certidão": [
        "certidão",
        "certifico",
        "cartório",
        "atesto que",
        "certifica-se",
        "certidão de nascimento",
        "certidão de óbito",
    ],
    "comprovante": [
        "comprovante de pagamento",
        "comprovante de transferência",
        "nº do cartão",
        "código de autenticação",
        "ted realizada",
        "pix realizado",
        "boleto pago",
    ],
    "apresentação": [
        "slide",
        "apresentação executiva",
        "sumário executivo",
        "agenda da reunião",
    ],
    "manual": ["manual de", "instruções de uso", "passo a passo", "como usar", "guia do usuário"],
    "artigo": [
        "abstract",
        "keywords",
        "referências bibliográficas",
        "metodologia",
        "doi:",
        "issn",
        "received:",
        "accepted:",
    ],
    "registro": [
        "registro do movimento",
        "livro nº",
        "procedência",
        "nacionalidade",
        "imigrantes",
        "vapor em que veio",
        "número de ordem",
    ],
    "lista": ["relação nominal", "nome sexo idade", "lista de presença", "lista de inscritos"],
    "política": [
        "política de privacidade",
        "política de segurança",
        "política de uso",
        "termos de uso",
        "termos e condições",
        "tratamento de dados",
        "lgpd",
        "gdpr",
        "dados pessoais",
    ],
}


@dataclass
class ClassificationResult:
    """Resultado da classificação: rótulo, origem e confiança (0–1 ou None para IA)."""

    label: str
    source: Literal["heuristics", "openai", "ollama"] = "heuristics"
    confidence: float | None = None  # 0–1 para heurísticas; None para OpenAI/Ollama


def _get_ai_mode() -> Literal["auto", "heuristics", "openai", "ollama"]:
    """Lê modo de IA da variável de ambiente."""
    mode = (os.environ.get("PDFSEARCHABLE_AI") or "auto").strip().lower()
    if mode in ("auto", "heuristics", "openai", "ollama"):
        return mode
    return "auto"


def _has_openai_key() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _metadata_hint_text(metadata: dict | None) -> str:
    """Monta texto de hint a partir dos metadados do PDF (título, assunto, palavras-chave)."""
    if not metadata:
        return ""
    parts = []
    for key in ("title", "subject", "keywords"):
        v = (metadata.get(key) or "").strip()
        if v:
            parts.append(f"{key}: {v}")
    return " | ".join(parts)


def _classify_by_heuristics(
    text: str,
    path: Path | None = None,
    metadata_hint: dict | None = None,
    max_chars: int = 8000,
) -> tuple[str, float]:
    """
    Classificação por palavras-chave com peso por posição.
    Retorna (rótulo, confiança 0–1). O início do texto tem mais peso.
    """
    raw = (text or "")[:max_chars].lower()
    if not raw.strip():
        return "documento", 0.0

    meta_text = _metadata_hint_text(metadata_hint).lower() if metadata_hint else ""
    if meta_text:
        raw = meta_text + " " + raw

    head, rest = raw[:1500], raw[1500:]
    scores: dict[str, float] = {}

    for doc_type, keywords in DOC_TYPE_KEYWORDS.items():
        score = 0.0
        for kw in keywords:
            if kw in head:
                score += 2.0
            if kw in rest:
                score += 1.0
        if score > 0:
            scores[doc_type] = scores.get(doc_type, 0) + score

    if scores:
        best_type, best_score = max(scores.items(), key=lambda x: x[1])
        # Normalizar confiança para 0–1 (empírico: score típico 2–15)
        conf = min(1.0, best_score / 15.0)
        # Threshold mínimo de confiança: abaixo de 0.10 a classificação é pouco fiável;
        # retornar "documento" para evitar rótulos errados que confundem o utilizador.
        if conf < 0.10:
            return "documento", conf
        return best_type, round(conf, 2)

    # Fallback por regex: apenas nos primeiros 500 chars para evitar falsos positivos.
    # Cabeçalhos de e-mail ("From:", "To:", "Subject:") aparecem no início;
    # "nota fiscal" e "DANFE" também ficam no topo do documento.
    leading = raw[:500]
    if _RE_EMAIL_HINT.search(leading):
        return "e-mail", 0.5
    if _RE_INVOICE_HINT.search(leading):
        return "fatura", 0.5
    return "documento", 0.0


def _build_classification_prompt(
    sample: str,
    name_hint: str,
    metadata_hint: dict | None,
) -> str:
    """Monta o prompt de classificação com few-shot examples quando disponíveis."""
    types_str = ", ".join(KNOWN_TYPES[:-1])
    meta_line = ""
    if metadata_hint:
        mt = _metadata_hint_text(metadata_hint)
        if mt:
            meta_line = f"\nMetadados do PDF: {mt}\n"

    # Injectar few-shot examples se disponíveis
    few_shot_block = ""
    try:
        from pdfsearchable.classifier_feedback import get_few_shot_examples
        examples = get_few_shot_examples(max_n=5)
        if examples:
            lines = []
            for ex in examples:
                snippet = (ex.get("text_snippet") or "")[:200].replace("\n", " ")
                ctype = ex.get("correct_type", "documento")
                lines.append(f'Texto: "{snippet}…" → Tipo: {ctype}')
            few_shot_block = "\nExemplos de classificações correctas:\n" + "\n".join(lines) + "\n"
    except Exception:
        pass

    return f"""Classifique o tipo deste documento em UMA palavra, apenas uma das opções abaixo.

Opções: {types_str}, documento
{few_shot_block}
Nome do arquivo (referência): {name_hint}
{meta_line}
Trecho do documento:
---
{sample[:2800]}
---

Responda somente com a palavra (ex: contrato, nota_fiscal, relatório, documento)."""


def _normalize_llm_label(content: str) -> str | None:
    """Extrai e normaliza o rótulo retornado pela IA para um KNOWN_TYPE."""
    content = (content or "").strip().lower()
    match = re.match(r"[\wáéíóúàèìòùãõâêîôûç]+", content.replace("-", "_"))
    label = (match.group(0) if match else "").strip()
    if label in KNOWN_TYPES:
        return label
    if label == "nota_fiscal" or ("nota" in label and "fiscal" in content):
        return "nota_fiscal"
    if label in ("relatorio", "relatório", "relatorio_pericial"):
        return "relatório"
    if label in ("procuracao", "procuração"):
        return "procuração"
    if label in ("peticao", "petição"):
        return "petição"
    if label in ("certidao", "certidão"):
        return "certidão"
    if label in ("apresentacao", "apresentação"):
        return "apresentação"
    if label in ("edital", "licitacao", "licitação", "pregao", "pregão"):
        return "edital"
    if label in ("politica", "política", "privacidade", "termos"):
        return "política"
    if len(label) > 1 and label.replace("_", "").isalnum():
        return label.replace(" ", "_")
    return None


def _classify_with_ollama(
    text: str,
    path: Path | None = None,
    metadata_hint: dict | None = None,
) -> ClassificationResult:
    """
    Classificação via Ollama (modelo local). Fallback para heurísticas se indisponível.
    Usa _ollama_request (retry + cache) de content_extractors.
    """
    sample = (text or "")[:3500].strip()
    if not sample:
        return ClassificationResult("documento", "heuristics")

    name_hint = path.name if path else ""
    prompt = _build_classification_prompt(sample, name_hint, metadata_hint)
    cache_key = hashlib.sha256((sample[:500] + name_hint).encode()).hexdigest()[:24]

    from pdfsearchable.content_extractors import _ollama_request

    # PDFSEARCHABLE_OLLAMA_CLASSIFY_MODEL permite usar modelo menor/mais rápido
    # só para classificação (ex.: qwen2:0.5b ou phi3:3.8b), mantendo o modelo principal
    # (PDFSEARCHABLE_OLLAMA_MODEL) para summary/tags/parties onde a qualidade importa mais.
    classify_model = os.environ.get("PDFSEARCHABLE_OLLAMA_CLASSIFY_MODEL", "").strip() or None
    content = _ollama_request(
        prompt, max_tokens=100, cache_key=cache_key, model_override=classify_model
    )
    if content:
        label = _normalize_llm_label(content)
        if label:
            # Ollama não expõe logprobs; usamos 0.85 como confiança nominal
            # quando o modelo devolve um rótulo reconhecido pela nossa taxonomia.
            # Heurísticas têm 0.7–0.95; manter Ollama em patamar comparável facilita
            # consumidores (relatórios, CLI) que filtram por confiança.
            return ClassificationResult(label, "ollama", confidence=0.85)
    label, conf = _classify_by_heuristics(text, path, metadata_hint)
    return ClassificationResult(label, "heuristics", confidence=conf)


def _classify_with_openai(
    text: str,
    path: Path | None = None,
    metadata_hint: dict | None = None,
) -> ClassificationResult:
    """
    Classificação via API OpenAI (ou compatível).
    Usa amostra do texto para caber no contexto; fallback para heurísticas em erro.
    """
    try:
        from openai import OpenAI
    except ImportError:
        label, conf = _classify_by_heuristics(text, path, metadata_hint)
        return ClassificationResult(label, "heuristics", confidence=conf)

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        label, conf = _classify_by_heuristics(text, path, metadata_hint)
        return ClassificationResult(label, "heuristics", confidence=conf)

    sample = (text or "")[:3500].strip()
    if not sample:
        return ClassificationResult("documento", "heuristics")

    name_hint = path.name if path else ""
    prompt = _build_classification_prompt(sample, name_hint, metadata_hint)

    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=os.environ.get("PDFSEARCHABLE_OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=30,
            temperature=0,
        )
        content = (resp.choices[0].message.content or "").strip()
        label = _normalize_llm_label(content)
        if label:
            return ClassificationResult(label, "openai", confidence=None)
    except Exception as _e:
        _log.warning("Classificação OpenAI falhou: %s — a usar heurísticas", _e)
    label, conf = _classify_by_heuristics(text, path, metadata_hint)
    return ClassificationResult(label, "heuristics", confidence=conf)


def classify_document(
    text: str,
    path: Path | None = None,
    metadata_hint: dict | None = None,
) -> ClassificationResult:
    """
    Identifica o tipo do documento conforme PDFSEARCHABLE_AI e provedor configurado.
    metadata_hint: opcional dict com title, subject, keywords do PDF (fitz) para reforçar classificação.
    Retorna ClassificationResult(label, source).
    """
    name_hint = (path.name if path else "").lower()
    # Dicas por nome de arquivo: confiança alta (0.9) pois o nome é forte indicador.
    # Ordem importa: tipos mais específicos primeiro para evitar matches parciais errados.
    if "edital" in name_hint:
        return ClassificationResult("edital", "heuristics", confidence=0.9)
    if "contrat" in name_hint or "contract" in name_hint:
        return ClassificationResult("contrato", "heuristics", confidence=0.9)
    if "nota_fiscal" in name_hint or "notafiscal" in name_hint or "danfe" in name_hint:
        return ClassificationResult("nota_fiscal", "heuristics", confidence=0.9)
    if "recibo" in name_hint:
        return ClassificationResult("recibo", "heuristics", confidence=0.9)
    if "procuracao" in name_hint or "procuração" in name_hint:
        return ClassificationResult("procuração", "heuristics", confidence=0.9)
    if "peticao" in name_hint or "petição" in name_hint:
        return ClassificationResult("petição", "heuristics", confidence=0.9)
    if "politica" in name_hint or "política" in name_hint or "privacidade" in name_hint:
        return ClassificationResult("política", "heuristics", confidence=0.9)
    if "relatorio" in name_hint or "relatório" in name_hint or "report" in name_hint:
        return ClassificationResult("relatório", "heuristics", confidence=0.9)
    if (
        "registro" in name_hint
        or "livro" in name_hint
        or "rjanrio" in name_hint
        or "hif" in name_hint
    ):
        return ClassificationResult("registro", "heuristics", confidence=0.9)
    if "ata" in name_hint and len(name_hint) < 30:
        # "ata" como parte significativa do nome (não apenas em "catalogar", "data", etc.)
        return ClassificationResult("ata", "heuristics", confidence=0.9)

    mode = _get_ai_mode()
    if mode == "heuristics":
        label, conf = _classify_by_heuristics(text, path, metadata_hint)
        return ClassificationResult(label, "heuristics", confidence=conf)
    if mode == "ollama":
        return _classify_with_ollama(text, path, metadata_hint)
    if mode == "openai":
        if not _has_openai_key():
            label, conf = _classify_by_heuristics(text, path, metadata_hint)
            return ClassificationResult(label, "heuristics", confidence=conf)
        return _classify_with_openai(text, path, metadata_hint)
    # auto: tentar OpenAI, senão heurísticas
    if _has_openai_key():
        result = _classify_with_openai(text, path, metadata_hint)
        if result.source == "openai":
            return result
    label, conf = _classify_by_heuristics(text, path, metadata_hint)
    return ClassificationResult(label, "heuristics", confidence=conf)


def classify_with_ai(
    text: str,
    path: Path | None = None,
    metadata_hint: dict | None = None,
) -> ClassificationResult:
    """Alias que força tentativa de IA quando possível (OpenAI ou Ollama)."""
    mode = _get_ai_mode()
    if mode == "ollama":
        return _classify_with_ollama(text, path, metadata_hint)
    if _has_openai_key():
        return _classify_with_openai(text, path, metadata_hint)
    label, conf = _classify_by_heuristics(text, path, metadata_hint)
    return ClassificationResult(label, "heuristics", confidence=conf)
