"""
Processamento e indexação de PDFs.
Extração (modos PyMuPDF), validação, senha, OCR para páginas vazias,
texto por página, FTS, incremental (hash), retry e skip_failed.

Melhorias de robustez:
- Detecção de texto corrompido (cmap/encoding inválido): força OCR mesmo com texto nativo
- Coleta de confiança OCR por página; ocr_avg_confidence salvo no índice
"""

import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any
from collections.abc import Callable

import fitz  # PyMuPDF

from pdfsearchable.ai_classifier import classify_document, _get_ai_mode
from pdfsearchable.content_extractors import (
    correct_ocr_with_ollama,
    detect_confidentiality,
    detect_digital_signatures,
    extract_dates,
    extract_entities,
    extract_metadata_ollama,
    extract_monetary_values,
    extract_parties,
    extract_parties_ollama,
    extract_summary_and_subject_ollama,
    extract_tags,
    extract_tags_ollama,
    merge_entities_with_ollama,
)
from pdfsearchable.audit import audit, get_logger
from pdfsearchable.exceptions import IndexingError, ValidationError
from pdfsearchable.language import detect_language
from pdfsearchable.ocr import (
    MIN_CHARS_FOR_NATIVE,
    get_ocr_workers,
    ocr_available,
    ocr_page_from_image_bytes,
    render_page_to_image,
)
from pdfsearchable.pdf_extended import extract_extended_from_doc, extract_embedded_pdfs, is_pdf_portfolio
from pdfsearchable.pdf_processor import (
    ExtractMode,
    content_hash,
    extract_text_from_doc,
    extract_text_from_pdf_partial,
    file_size,
    validate_pdf,
)
from pdfsearchable.store import (
    add_file_meta,
    copy_pdf_to_store,
    find_by_content_hash,
    fts_index_file,
    load_index,
    save_file_text,
    update_path_by_content_hash,
    _file_id,
)

logger = get_logger("indexer")

# Retry
MAX_RETRIES = 3
RETRY_BACKOFF = 1.0  # segundos

# Arquivos grandes: acima deste tamanho (bytes) ativamos compressão automática e log
LARGE_FILE_THRESHOLD_BYTES = 20 * 1024 * 1024  # 20 MB


# Paralelismo: máximo de workers quando workers=0 (auto). Permite throughput elevado (estilo IPED). Teto 64 ou config.
def _max_workers_auto() -> int:
    raw = (os.environ.get("PDFSEARCHABLE_MAX_WORKERS") or "").strip()
    if raw.isdigit():
        return max(1, min(int(raw), 64))
    return min(max(1, (os.cpu_count() or 4) * 2), 16)


# Limiar de caracteres "lixo" para considerar texto nativo corrompido (cmap/encoding)
_CORRUPT_TEXT_THRESHOLD = 0.30  # 30% de caracteres inválidos → forçar OCR


def _is_text_corrupt(text: str, threshold: float = _CORRUPT_TEXT_THRESHOLD) -> bool:
    """
    Detecta se o texto extraído nativamento está corrompido por problemas de
    cmap/encoding de fonte (ex.: PDFs de sistemas legados brasileiros como SEI, SIAFEM).
    Considera corrompido se mais de `threshold` (padrão 30%) dos caracteres forem:
      - Caracteres de controle não-imprimíveis (exceto \\n, \\r, \\t)
      - Área de uso privado Unicode (U+E000–U+F8FF)
      - Caractere de substituição (U+FFFD)
      - Caracteres C1 (U+0080–U+009F)
    Retorna False para texto muito curto (< 20 chars não-espaço) ou vazio.
    Nota: verifica len(text.strip()) para não contar apenas espaços como conteúdo.
    """
    if not text:
        return False
    # Usar chars não-espaço como referência de comprimento mínimo (evita falso-negativo
    # com strings compostas apenas de espaços/newlines que têm len > 20 mas são vazias).
    stripped = text.strip()
    if len(stripped) < 20:
        return False
    garbage = sum(
        1
        for c in text
        if (ord(c) < 32 and c not in "\n\r\t")
        or (0xE000 <= ord(c) <= 0xF8FF)
        or c == "\ufffd"
        or (0x80 <= ord(c) <= 0x9F)
    )
    return (garbage / len(text)) > threshold


def _has_low_entropy(text: str, threshold: float = 1.5) -> bool:
    """
    Detecta texto com entropia Shannon muito baixa (repetitivo/lixo).
    Texto normal em português/inglês tem entropia ~4.0–4.5.
    Texto repetitivo ("aaaaaa") ou corrompido tem <1.5.
    Retorna True se o texto deve ser substituído por OCR.
    """
    import math
    from collections import Counter

    stripped = text.strip()
    if len(stripped) < 50:
        return False
    freq = Counter(stripped.lower())
    total = len(stripped)
    entropy = -sum((count / total) * math.log2(count / total) for count in freq.values() if count)
    return entropy < threshold


def _env_flag(name: str) -> bool:
    """True se a variável de ambiente estiver em ('1','true','yes','on')."""
    return (os.environ.get(name, "") or "").strip().lower() in ("1", "true", "yes", "on")


def _apply_optional_detections(
    metadata: dict[str, Any],
    pdf_path: Path,
    password: str | None,
    enriched: dict[str, Any],
    file_id: str | None = None,
    page_texts: list[str] | None = None,
) -> list[str]:
    """
    Aplica detecções opcionais (controladas por variáveis de ambiente) e
    mete os resultados em ``metadata`` (mutação in-place). Retorna lista de
    warnings legíveis para serem anexados ao ``ocr_warnings`` do CLI.

    Features (opt-in):
      - PDFSEARCHABLE_DETECT_REDACTIONS → metadata['redaction_report']
      - PDFSEARCHABLE_FORENSICS         → metadata['forensics']
      - PDFSEARCHABLE_CONTRACTS         → metadata['contract_data']
        (apenas quando doc_type classificado contém "contrato")

    Cada bloco é isolado para que uma falha numa feature não afecte as outras
    nem a indexação. Erros são registados no log e em audit para observabilidade.
    """
    warnings_out: list[str] = []
    name_hint = pdf_path.name

    # ---- Redacções (zonas negras) --------------------------------------
    if _env_flag("PDFSEARCHABLE_DETECT_REDACTIONS"):
        try:
            from pdfsearchable.redaction import detect_redactions

            rr = detect_redactions(pdf_path, password=password)
            if rr.has_redactions or rr.suspicious:
                metadata["redaction_report"] = {
                    "has_redactions": rr.has_redactions,
                    "suspicious": rr.suspicious,
                    "total_zones": rr.total_redacted_zones,
                    "summary": rr.summary,
                    "pages": rr.pages,
                }
                audit(
                    "redaction_detected",
                    {
                        "file_id": file_id,
                        "name": name_hint,
                        "total_zones": rr.total_redacted_zones,
                        "suspicious": rr.suspicious,
                        "pages_with_zones": len(rr.pages or []),
                    },
                )
                if rr.suspicious:
                    warnings_out.append(f"redacções: {rr.summary}")
                    logger.warning(
                        "%s: redacções suspeitas detectadas (%s)",
                        name_hint,
                        rr.summary,
                    )
        except ImportError as _ie:
            logger.error(
                "PDFSEARCHABLE_DETECT_REDACTIONS activo mas módulo indisponível: %s",
                _ie,
            )
            audit(
                "redaction_module_missing",
                {"name": name_hint, "error": str(_ie)},
                level="error",
            )
        except Exception as _e:
            logger.warning(
                "Detecção de redacções falhou para %s: %s", name_hint, _e
            )
            audit(
                "redaction_error",
                {"file_id": file_id, "name": name_hint, "error": str(_e)},
                level="warning",
            )

    # ---- Análise forense (anomalias estruturais) -----------------------
    if _env_flag("PDFSEARCHABLE_FORENSICS"):
        try:
            from pdfsearchable.forensics import analyse_forensics

            fr = analyse_forensics(pdf_path, password=password)
            if fr.anomalies:
                metadata["forensics"] = {
                    "risk_score": fr.risk_score,
                    "suspicious": fr.suspicious,
                    "summary": fr.summary,
                    "anomalies": fr.anomalies,
                }
                audit(
                    "forensics_anomalies",
                    {
                        "file_id": file_id,
                        "name": name_hint,
                        "risk_score": fr.risk_score,
                        "suspicious": fr.suspicious,
                        "num_anomalies": len(fr.anomalies or []),
                    },
                )
                if fr.suspicious:
                    warnings_out.append(f"forense: {fr.summary}")
                    logger.warning(
                        "%s: anomalias forenses (%s, risk=%.2f)",
                        name_hint,
                        fr.summary,
                        fr.risk_score,
                    )
        except ImportError as _ie:
            logger.error(
                "PDFSEARCHABLE_FORENSICS activo mas módulo indisponível: %s", _ie
            )
            audit(
                "forensics_module_missing",
                {"name": name_hint, "error": str(_ie)},
                level="error",
            )
        except Exception as _e:
            logger.warning("Análise forense falhou para %s: %s", name_hint, _e)
            audit(
                "forensics_error",
                {"file_id": file_id, "name": name_hint, "error": str(_e)},
                level="warning",
            )

    # ---- Extracção de datas de contrato --------------------------------
    if _env_flag("PDFSEARCHABLE_CONTRACTS") and "contrato" in (
        enriched.get("doc_type") or ""
    ).lower():
        try:
            from pdfsearchable.contracts import extract_contract_dates

            cd = extract_contract_dates(
                enriched.get("full_text") or "", filename=name_hint
            )
            if cd.confidence > 0:
                metadata["contract_data"] = {
                    "start_date": cd.start_date,
                    "end_date": cd.end_date,
                    "renewal_date": cd.renewal_date,
                    "duration_months": cd.duration_months,
                    "auto_renewal": cd.auto_renewal,
                    "confidence": cd.confidence,
                }
                audit(
                    "contract_extracted",
                    {
                        "file_id": file_id,
                        "name": name_hint,
                        "confidence": cd.confidence,
                        "has_start": bool(cd.start_date),
                        "has_end": bool(cd.end_date),
                        "auto_renewal": cd.auto_renewal,
                    },
                )
        except ImportError as _ie:
            logger.error(
                "PDFSEARCHABLE_CONTRACTS activo mas módulo indisponível: %s", _ie
            )
            audit(
                "contracts_module_missing",
                {"name": name_hint, "error": str(_ie)},
                level="error",
            )
        except Exception as _e:
            logger.warning(
                "Extracção de contrato falhou para %s: %s", name_hint, _e
            )
            audit(
                "contracts_error",
                {"file_id": file_id, "name": name_hint, "error": str(_e)},
                level="warning",
            )

    # ---- Fórmulas matemáticas (LaTeX / Unicode) -----------------------
    if _env_flag("PDFSEARCHABLE_DETECT_FORMULAS"):
        try:
            from pdfsearchable.formulas import detect_formulas

            pts = page_texts if page_texts is not None else []
            if not pts:
                # fallback: split do full_text em uma "página" única
                ft = enriched.get("full_text") or ""
                pts = [ft] if ft else []
            fr = detect_formulas(pts)
            if fr.total > 0:
                metadata["formulas"] = fr.to_dict()
                audit(
                    "formulas_detected",
                    {
                        "file_id": file_id,
                        "name": name_hint,
                        "total": fr.total,
                        "by_kind": dict(fr.by_kind),
                    },
                )
                logger.info(
                    "%s: %d fórmulas detectadas (%s)",
                    name_hint,
                    fr.total,
                    ", ".join(f"{k}={v}" for k, v in fr.by_kind.items()),
                )
        except ImportError as _ie:
            logger.error(
                "PDFSEARCHABLE_DETECT_FORMULAS activo mas módulo indisponível: %s",
                _ie,
            )
            audit(
                "formulas_module_missing",
                {"name": name_hint, "error": str(_ie)},
                level="error",
            )
        except Exception as _e:
            logger.warning(
                "Detecção de fórmulas falhou para %s: %s", name_hint, _e
            )
            audit(
                "formulas_error",
                {"file_id": file_id, "name": name_hint, "error": str(_e)},
                level="warning",
            )

    return warnings_out


def _enrich_document(
    full_text: str,
    pdf_path: Path,
    metadata: dict[str, Any],
    doc_type_arg: str | None,
    ocr_per_page: list[bool],
    page_texts: list[str],
    page_confidences: list[float],
) -> dict[str, Any]:
    """
    Classifica o documento, detecta idioma e extrai metadados via regex/IA.
    Partilhado por index_pdf (processo principal) e _worker_extract_and_classify (subprocesso).
    Retorna dict com: word_count, doc_type, classification_source, classification_confidence,
    language, ocr_percentage, ocr_avg_confidence, summary, subject, tags, monetary_values,
    parties, entities, identified_locations.
    """
    word_count = len(full_text.split()) if full_text else 0
    text_chars = len(full_text) if full_text else 0

    # Classificação do tipo de documento
    if doc_type_arg is None:
        classification = classify_document(
            full_text,
            pdf_path,
            metadata_hint={
                k: v
                for k, v in (metadata or {}).items()
                if k in ("title", "subject", "keywords") and v
            },
        )
        doc_type = classification.label
        classification_source = classification.source
        classification_confidence = getattr(classification, "confidence", None)
    else:
        doc_type = doc_type_arg
        classification_source = None
        classification_confidence = None

    # Idioma
    language = detect_language(full_text)

    # Estatísticas de OCR
    num_pages_with_ocr = sum(1 for b in ocr_per_page if b)
    ocr_percentage = round(100.0 * num_pages_with_ocr / len(page_texts)) if page_texts else 0
    valid_confs = [c for c in page_confidences if c >= 0]
    ocr_avg_confidence = round(sum(valid_confs) / len(valid_confs), 1) if valid_confs else None

    # Resumo, tags, parties e metadata Ollama em paralelo (evita 4× tempo sequencial)
    summary = None
    subject = (metadata or {}).get("subject")
    subject = subject.strip() or None if isinstance(subject, str) else None

    # Tags heurísticas (regex, sem IA) — sempre executadas
    tags = extract_tags(doc_type or "documento", full_text)
    # Valores monetários e partes (regex) — sempre executados
    monetary_values = extract_monetary_values(full_text) if full_text else []
    parties = extract_parties(full_text) if full_text else []
    # Entidades (regex)
    entities = extract_entities(full_text) if full_text else {}

    # Rastrear falhas de enriquecimento IA para surfaçar ao utilizador
    _ollama_failed_tasks: list[str] = []
    results_ollama: dict = {}

    if _get_ai_mode() == "ollama" and full_text:
        from concurrent.futures import ThreadPoolExecutor

        def _task_summary() -> tuple[str | None, str | None] | None:
            return extract_summary_and_subject_ollama(full_text)

        def _task_tags() -> list[str]:
            return extract_tags_ollama(full_text, max_tags=5)

        def _task_parties() -> list[str]:
            return extract_parties_ollama(full_text, max_parties=8)

        def _task_meta() -> dict | None:
            return extract_metadata_ollama(full_text)

        ollama_futures: dict = {}
        with ThreadPoolExecutor(max_workers=4) as _ollama_pool:
            ollama_futures["summary"] = _ollama_pool.submit(_task_summary)
            ollama_futures["tags"] = _ollama_pool.submit(_task_tags)
            ollama_futures["parties"] = _ollama_pool.submit(_task_parties)
            ollama_futures["meta"] = _ollama_pool.submit(_task_meta)
            # Aguardar todos — timeout individual controlado por PDFSEARCHABLE_OLLAMA_TIMEOUT
            for key, fut in ollama_futures.items():
                try:
                    results_ollama[key] = fut.result()
                except Exception as _oe:
                    logger.debug("Ollama task '%s' falhou: %s", key, _oe)
                    results_ollama[key] = None
                    _ollama_failed_tasks.append(key)

        summary_subject = results_ollama.get("summary")
        if summary_subject:
            summary, subject_from_ollama = summary_subject
            if subject_from_ollama and not subject:
                subject = subject_from_ollama

        ia_tags = results_ollama.get("tags") or []
        for t in ia_tags:
            if t and t not in tags:
                tags.append(t)
        tags = tags[:12]

        ia_parties = results_ollama.get("parties") or []
        for p in ia_parties:
            if p and p not in parties:
                parties.append(p)
        parties = parties[:20]

        ollama_meta = results_ollama.get("meta")
        if ollama_meta:
            entities = merge_entities_with_ollama(entities, ollama_meta)
            for p in ollama_meta.get("parties") or []:
                if p and p not in parties:
                    parties.append(p)
            parties = parties[:20]
            for v in ollama_meta.get("monetary_values") or []:
                if v and not any(m.get("value_str") == v for m in monetary_values):
                    monetary_values.append({"currency": "OTHER", "value_str": v})
            monetary_values = monetary_values[:25]

    identified_locations: list[str] = []

    # Datas extraídas por regex (DD/MM/AAAA, ISO, extenso PT/EN)
    identified_dates = extract_dates(full_text) if full_text else []

    # Mesclar datas do Ollama com regex (deduplicar)
    if _get_ai_mode() == "ollama" and results_ollama.get("meta"):
        ollama_dates = results_ollama["meta"].get("dates") or []
        dates_lower = {d.lower() for d in identified_dates}
        for d in ollama_dates:
            d = (d or "").strip()
            if d and d.lower() not in dates_lower and len(identified_dates) < 30:
                identified_dates.append(d)
                dates_lower.add(d.lower())

    # Confidencialidade detectada por padrões no texto
    confidentiality = detect_confidentiality(full_text) if full_text else None

    # Indicar enriquecimento parcial quando alguma tarefa Ollama falhou,
    # para que o chamador possa informar o utilizador.
    _enrichment_partial = bool(_ollama_failed_tasks)
    _ollama_warnings: str = ""
    if _enrichment_partial:
        _failed = locals().get("_ollama_failed_tasks") or []
        _ollama_warnings = (
            f"Ollama indisponível para: {', '.join(_failed)}. "
            "Enriquecimento com IA incompleto."
        )
        logger.warning("Enriquecimento parcial — tarefas Ollama falharam: %s", _failed)

    return {
        "full_text": full_text,
        "word_count": word_count,
        "text_chars": text_chars,
        "doc_type": doc_type,
        "classification_source": classification_source,
        "classification_confidence": classification_confidence,
        "language": language,
        "ocr_percentage": ocr_percentage,
        "ocr_avg_confidence": ocr_avg_confidence,
        "summary": summary,
        "subject": subject,
        "tags": tags,
        "monetary_values": monetary_values,
        "parties": parties,
        "entities": entities,
        "identified_locations": identified_locations,
        "identified_dates": identified_dates,
        "confidentiality": confidentiality,
        "enrichment_partial": _enrichment_partial,
        "ocr_warnings": _ollama_warnings,
    }


def _worker_extract_and_classify(args: tuple) -> dict[str, Any]:
    """
    Executado em subprocesso (ProcessPoolExecutor). Usa apenas PyMuPDF e classificadores.
    Retorna dict serializável para o processo principal persistir (store).
    PyMuPDF não é thread-safe; multiprocessing evita Lock blocking.
    Inclui ocr_confidences (lista por página) e ocr_avg_confidence no resultado.
    """
    path_str, mode, password, use_ocr, doc_type_arg = args
    pdf_path = Path(path_str).resolve()
    from pdfsearchable.store import _file_id as _store_file_id

    file_id = _store_file_id(pdf_path)
    pwd = password or os.environ.get("PDF_PASSWORD", "").strip() or None
    c_hash = content_hash(pdf_path)
    for attempt in range(MAX_RETRIES):
        try:
            ok, err = validate_pdf(pdf_path, pwd)
            if not ok:
                # PDF inválido/corrompido: tentar recuperação parcial página a página
                if pdf_path.exists() and "corrompido" in (err or "").lower():
                    logger.warning(
                        "PDF corrompido detectado (%s) — tentando recuperação parcial página a página.",
                        pdf_path.name,
                    )
                    try:
                        full_text, num_pages, page_texts, metadata, failed = (
                            extract_text_from_pdf_partial(pdf_path, password=pwd, normalize=True)
                        )
                        if full_text.strip() or num_pages > 0:
                            ocr_per_page = [False] * len(page_texts)
                            page_confidences = [-1.0] * len(page_texts)
                            if failed:
                                logger.warning(
                                    "Recuperação parcial: %d/%d página(s) com erro: %s",
                                    len(failed), num_pages, failed[:10],
                                )
                            break  # Usa resultado parcial como dados do documento
                    except Exception as _partial_err:
                        logger.debug("Recuperação parcial falhou: %s", _partial_err)
                raise ValidationError(err or "PDF inválido", {"path": str(pdf_path)})
            full_text, num_pages, page_texts, metadata, ocr_per_page, page_confidences = (
                _extract_with_ocr(
                    pdf_path,
                    mode,
                    pwd,
                    normalize=True,
                    use_ocr=use_ocr,
                    file_id=file_id,
                    content_hash=c_hash,
                )
            )
            # Extração estendida já feita em _extract_with_ocr (uma única abertura do PDF)
            break
        except (ValidationError, IndexingError):
            raise
        except Exception as _retry_err:
            if attempt == MAX_RETRIES - 1:
                raise
            logger.warning(
                "Tentativa %d/%d falhou para %s: %s — a tentar novamente",
                attempt + 1, MAX_RETRIES, pdf_path.name, _retry_err,
            )
            time.sleep(RETRY_BACKOFF * (2**attempt))
    enriched = _enrich_document(
        full_text, pdf_path, metadata, doc_type_arg, ocr_per_page, page_texts, page_confidences
    )
    metadata["text_chars"] = enriched.get("text_chars", len(full_text) if full_text else 0)
    # Aplicar detecções opcionais (redacções/forense/contratos) no subprocesso
    # para que metadata e warnings sejam persistidos no caminho multiprocessing.
    _post_warnings = _apply_optional_detections(
        metadata, pdf_path, pwd, enriched, file_id=file_id, page_texts=page_texts
    )
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {
        "file_id": file_id,
        "content_hash": c_hash,
        "full_text": full_text,
        "page_texts": page_texts,
        "metadata": metadata,
        "post_warnings": _post_warnings,
        "ocr_per_page": ocr_per_page,
        "ocr_confidences": page_confidences,
        "num_pages": num_pages,
        "now_iso": now_iso,
        **enriched,
    }


def _extract_with_ocr(
    pdf_path: Path,
    mode: ExtractMode,
    password: str | None,
    normalize: bool,
    use_ocr: bool,
    file_id: str,
    content_hash: str | None = None,
) -> tuple[str, int, list[str], dict, list[bool], list[float]]:
    """
    Extrai texto; aplica OCR conforme PDFSEARCHABLE_OCR_ALWAYS (padrão: todas as páginas).
    Cache por content_hash quando fornecido (reutiliza OCR entre arquivos com mesmo conteúdo).
    OCR paralelo por página quando PDFSEARCHABLE_OCR_WORKERS > 1.
    Detecta texto nativo corrompido e força OCR; fallback HTR quando Tesseract vazio/baixa confiança.
    Retorna (full_text, num_pages, page_texts, metadata, ocr_per_page, page_confidences).
    O PDF é aberto uma única vez (texto + estendido + render) e fechado antes de qualquer OCR para evitar lock blocking do MuPDF.
    """
    ocr_per_page: list[bool] = []
    page_confidences: list[float] = []
    ocr_always = os.environ.get("PDFSEARCHABLE_OCR_ALWAYS", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    cache_key = (content_hash or file_id).strip()
    num_workers = get_ocr_workers()

    def _merge_extended(
        page_texts_in: list[str],
        full_in: str,
        metadata_in: dict,
        extended: dict,
    ) -> tuple[str, list[str], dict]:
        """Funde tabelas e extended em page_texts, full e metadata."""
        tables = extended.get("tables") or []
        if not tables:
            if (
                extended.get("form_fields")
                or extended.get("annotations")
                or extended.get("xmp")
                or extended.get("outline")
                or extended.get("hyperlinks")
                or extended.get("attached_files")
                or extended.get("fonts")
                or extended.get("page_dimensions")
            ):
                meta = dict(metadata_in or {})
                meta["extended"] = {
                    "form_fields": (extended.get("form_fields") or [])[:50] or None,
                    "annotations": (extended.get("annotations") or [])[:30] or None,
                    "xmp": extended.get("xmp") or None,
                    "outline": (extended.get("outline") or [])[:500] or None,
                    "hyperlinks": (extended.get("hyperlinks") or [])[:500] or None,
                    "page_dimensions": extended.get("page_dimensions") or None,
                    "attached_files": extended.get("attached_files") or None,
                    "fonts": extended.get("fonts") or None,
                }
                return full_in, list(page_texts_in), meta
            return full_in, list(page_texts_in), metadata_in or {}
        tables_by_page: dict[int, list[str]] = {}
        for t in tables:
            pn = t.get("page", 1)
            if pn not in tables_by_page:
                tables_by_page[pn] = []
            tables_by_page[pn].append(t.get("text", "") or "")
        page_texts_out = list(page_texts_in) if page_texts_in else []
        for i in range(len(page_texts_out)):
            pn = i + 1
            if pn in tables_by_page:
                extra = "\n\n".join(tables_by_page[pn])
                page_texts_out[i] = (page_texts_out[i] or "") + "\n\n" + extra
        full_out = "\n\n".join(page_texts_out)
        meta = dict(metadata_in or {})
        meta["extended"] = {
            "form_fields": (extended.get("form_fields") or [])[:50] or None,
            "annotations": (extended.get("annotations") or [])[:30] or None,
            "xmp": extended.get("xmp") or None,
            "signatures": extended.get("signatures") or None,
            "outline": (extended.get("outline") or [])[:500] or None,
            "hyperlinks": (extended.get("hyperlinks") or [])[:500] or None,
            "page_dimensions": extended.get("page_dimensions") or None,
            "attached_files": extended.get("attached_files") or None,
            "fonts": extended.get("fonts") or None,
        }
        return full_out, page_texts_out, meta

    doc = fitz.open(pdf_path)
    try:
        if doc.is_encrypted and password:
            doc.authenticate(password)
        full, num_pages, page_texts, metadata = extract_text_from_doc(
            doc, mode=mode, normalize=True
        )
        ocr_per_page = [False] * len(page_texts)
        page_confidences = [-1.0] * len(page_texts)
        # Extração estendida na mesma abertura (evita segunda abertura e lock blocking)
        try:
            extended = extract_extended_from_doc(doc)
        except Exception as _ext_err:
            logger.debug("extract_extended_from_doc falhou: %s — a prosseguir sem dados estendidos", _ext_err)
            extended = {
                "tables": [], "form_fields": [], "annotations": [], "xmp": {},
                "outline": [], "hyperlinks": [], "page_dimensions": [],
                "attached_files": [], "fonts": [],
            }
        # Assinaturas digitais (campos de assinatura no PDF)
        extended["signatures"] = detect_digital_signatures(doc)
        if not use_ocr or not ocr_available():
            full, page_texts, metadata = _merge_extended(page_texts, full, metadata, extended)
            return full, num_pages, page_texts, metadata, ocr_per_page, page_confidences

        # Decidir por página se usa OCR e pré-renderizar imagens (doc aberto só aqui)
        items: list[tuple[int, int, bytes | None, str, bool, bool]] = []
        for i, pt in enumerate(page_texts):
            native_text = pt or ""
            is_corrupt = _is_text_corrupt(native_text) if native_text.strip() else False
            low_entropy = _has_low_entropy(native_text) if native_text.strip() else False
            use_ocr_this_page = (
                ocr_always or len(native_text.strip()) < MIN_CHARS_FOR_NATIVE or is_corrupt or low_entropy
            )
            img_bytes: bytes | None = None
            if use_ocr_this_page:
                try:
                    page = doc[i]
                    img_bytes = render_page_to_image(page)
                except Exception as _render_err:
                    logger.debug(
                        "render_page_to_image falhou na página %d: %s — OCR ignorado para esta página",
                        i, _render_err,
                    )
            items.append((i, i + 1, img_bytes, native_text, use_ocr_this_page, is_corrupt))

        # Fechar o documento antes de qualquer OCR para evitar lock blocking do MuPDF
        # (Tesseract/HTR podem demorar; manter doc aberto segura o mutex internamente)
        doc.close()
        doc = None

        # Detectar idioma a partir do texto nativo disponível ou metadados do PDF
        # para que o HTR selecione o modelo TrOCR adequado ao idioma.
        _htr_lang_hint: str | None = None
        try:
            # Tentar obter idioma dos metadados do PDF (campo "language" ou XMP)
            _meta_lang = (metadata or {}).get("language", "")
            if _meta_lang and len(_meta_lang) <= 10:
                _htr_lang_hint = _meta_lang
            if not _htr_lang_hint:
                # Tentar detecção pelo texto nativo (quando há pelo menos algum)
                _native_sample = " ".join(
                    pt.strip() for pt in page_texts if pt and len(pt.strip()) > 30
                )[:3000]
                if len(_native_sample) >= 80:
                    _htr_lang_hint = detect_language(_native_sample)
                    if _htr_lang_hint == "unknown":
                        _htr_lang_hint = None
        except Exception:
            _htr_lang_hint = None

        if num_workers != 1 and any(it[4] for it in items):
            # OCR paralelo por página com timeout cumulativo por documento.
            # PDFSEARCHABLE_OCR_PAGE_TIMEOUT: limite por página (padrão 120s).
            # PDFSEARCHABLE_OCR_DOC_TIMEOUT: limite total por documento (padrão = páginas × 60s, mín 300s).
            from concurrent.futures import ThreadPoolExecutor, as_completed

            workers = num_workers if num_workers > 0 else min(8, max(1, (os.cpu_count() or 4) // 2))
            results_by_i: dict[int, tuple[str, float]] = {}

            _page_timeout = max(
                30, int(os.environ.get("PDFSEARCHABLE_OCR_PAGE_TIMEOUT", "120") or 120)
            )
            # Timeout cumulativo: evita que documentos longos bloqueiem por horas.
            _ocr_pages = sum(1 for it in items if it[4])  # páginas que vão a OCR
            _doc_timeout_default = max(300, _ocr_pages * 60)
            _doc_timeout = max(
                _page_timeout,
                int(os.environ.get("PDFSEARCHABLE_OCR_DOC_TIMEOUT", str(_doc_timeout_default)) or _doc_timeout_default),
            )
            _doc_deadline = time.monotonic() + _doc_timeout

            def _run_one(
                item: tuple[int, int, bytes | None, str, bool, bool],
            ) -> tuple[int, str, float, bool, bool]:
                i, page_num, img_bytes, native_text, use_ocr_this_page, is_corrupt = item
                if not use_ocr_this_page or not img_bytes:
                    return (i, native_text, -1.0, False, is_corrupt)
                text, conf = ocr_page_from_image_bytes(
                    img_bytes, cache_key, page_num, use_cache=True, lang=_htr_lang_hint
                )
                return (i, text, conf, True, is_corrupt)

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_run_one, it): it[0] for it in items}
                for fut in as_completed(futures):
                    page_i = futures[fut]
                    # Tempo restante para o documento completo
                    remaining = _doc_deadline - time.monotonic()
                    if remaining <= 0:
                        logger.warning(
                            "OCR timeout cumulativo do documento atingido (%ds) — %d páginas restantes serão ignoradas.",
                            _doc_timeout,
                            sum(1 for f in futures if not f.done()),
                        )
                        # Cancelar futures pendentes
                        for f in futures:
                            f.cancel()
                        break
                    effective_timeout = min(_page_timeout, remaining)
                    try:
                        i, text, conf, used_ocr, is_corrupt = fut.result(timeout=effective_timeout)
                    except Exception as _fut_err:
                        logger.warning(
                            "OCR falhou na página %d: %s", page_i, _fut_err
                        )
                        results_by_i[page_i] = ("", -1.0)
                        continue
                    results_by_i[i] = (text, conf)
                    ocr_per_page[i] = used_ocr
                    if used_ocr:
                        page_confidences[i] = conf

            # Limiar de confiança: se OCR retornar texto com confiança abaixo deste valor
            # E o texto nativo não estiver corrompido, prefere o nativo.
            _ocr_min_conf = float(
                os.environ.get("PDFSEARCHABLE_OCR_MIN_CONFIDENCE_VS_NATIVE", "15") or 15
            )

            merged = []
            for i, (_, _page_num, _, native_text, use_ocr_this_page, is_corrupt) in enumerate(items):
                if not use_ocr_this_page:
                    merged.append(native_text)
                    continue
                ocr_text, ocr_conf = results_by_i.get(i, ("", -1.0))
                if ocr_text.strip():
                    corrected = correct_ocr_with_ollama(ocr_text)
                    if corrected:
                        ocr_text = corrected
                if ocr_always or is_corrupt:
                    fallback = native_text if not is_corrupt else ""
                    merged.append(ocr_text.strip() or fallback)
                else:
                    # Se o OCR tem confiança muito baixa e há texto nativo disponível,
                    # preferir o texto nativo (evita substituir boa extração por OCR ruim).
                    has_native = bool(native_text.strip())
                    ocr_low_conf = ocr_conf >= 0 and ocr_conf < _ocr_min_conf
                    if ocr_text.strip() and not (has_native and ocr_low_conf):
                        merged.append(ocr_text)
                    else:
                        merged.append(native_text)
            full, merged, metadata = _merge_extended(merged, "\n\n".join(merged), metadata, extended)
            return full, num_pages, merged, metadata, ocr_per_page, page_confidences

        # Sequencial (PDFSEARCHABLE_OCR_WORKERS=1 ou sem páginas para OCR)
        # Usar apenas img_bytes já renderizados (doc já fechado) para evitar lock do MuPDF
        _ocr_min_conf_seq = float(
            os.environ.get("PDFSEARCHABLE_OCR_MIN_CONFIDENCE_VS_NATIVE", "15") or 15
        )
        merged = []
        for i, (_, page_num, img_bytes, native_text, use_ocr_this_page, is_corrupt) in enumerate(
            items
        ):
            if not use_ocr_this_page:
                merged.append(native_text)
                continue
            if img_bytes:
                ocr_text, ocr_conf = ocr_page_from_image_bytes(
                    img_bytes, cache_key, page_num, use_cache=True, lang=_htr_lang_hint
                )
            else:
                ocr_text, ocr_conf = "", -1.0
            if ocr_text.strip():
                corrected = correct_ocr_with_ollama(ocr_text)
                if corrected:
                    ocr_text = corrected
            if ocr_always or is_corrupt:
                fallback = native_text if not is_corrupt else ""
                merged.append(ocr_text.strip() or fallback)
                ocr_per_page[i] = True
            else:
                # Se OCR tem confiança muito baixa e há texto nativo, preferir nativo
                has_native = bool(native_text.strip())
                ocr_low_conf = ocr_conf >= 0 and ocr_conf < _ocr_min_conf_seq
                if ocr_text.strip() and not (has_native and ocr_low_conf):
                    merged.append(ocr_text)
                    ocr_per_page[i] = True
                else:
                    merged.append(native_text)
                    ocr_per_page[i] = False
            if ocr_per_page[i]:
                page_confidences[i] = ocr_conf
        full, merged, metadata = _merge_extended(merged, "\n\n".join(merged), metadata, extended)
        return full, num_pages, merged, metadata, ocr_per_page, page_confidences
    finally:
        if doc is not None:
            doc.close()


def index_pdf(
    pdf_path: Path,
    doc_type: str | None = None,
    *,
    mode: ExtractMode = "text",
    password: str | None = None,
    use_ocr: bool = True,
    compress: bool = False,
    skip_existing: bool = True,
) -> dict | None:
    """
    Processa e indexa um PDF. Retorna metadados ou None se skip_existing e já indexado.
    """
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.exists():
        raise ValidationError(f"Arquivo não encontrado: {pdf_path}", {"path": str(pdf_path)})
    if pdf_path.suffix.lower() != ".pdf":
        raise ValidationError("O arquivo deve ser um PDF.", {"path": str(pdf_path)})

    pwd = password or os.environ.get("PDF_PASSWORD", "").strip() or None
    ok, err = validate_pdf(pdf_path, pwd)
    if not ok:
        # PDF corrompido: tentar recuperação parcial página a página antes de desistir
        if pdf_path.exists() and "corrompido" in (err or "").lower():
            logger.warning(
                "PDF corrompido (%s) — tentando recuperação parcial.", pdf_path.name
            )
            try:
                full_text, num_pages, page_texts, metadata, failed_pages = (
                    extract_text_from_pdf_partial(pdf_path, password=pwd, normalize=True)
                )
                if full_text.strip() or num_pages > 0:
                    logger.info(
                        "Recuperação parcial: %d página(s) extraídas, %d com erro.",
                        num_pages - len(failed_pages), len(failed_pages),
                    )
                    audit("index_partial_recovery", {
                        "path": str(pdf_path),
                        "pages_ok": num_pages - len(failed_pages),
                        "pages_failed": len(failed_pages),
                    })
                    # Continuar indexação com texto parcial — saltar validate abaixo
                    c_hash = content_hash(pdf_path)
                    f_size = file_size(pdf_path)
                    file_id = _file_id(pdf_path)
                    # Indexação direta com texto já extraído (sem OCR adicional)
                    ocr_per_page = [False] * len(page_texts)
                    page_confidences = [-1.0] * len(page_texts)
                    enriched = _enrich_document(
                        full_text, pdf_path, metadata, doc_type,
                        ocr_per_page, page_texts, page_confidences,
                    )
                    metadata["text_chars"] = enriched.get("text_chars", len(full_text) if full_text else 0)
                    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    is_large = f_size >= LARGE_FILE_THRESHOLD_BYTES
                    compress = compress or is_large
                    page_tuples = [(i + 1, pt) for i, pt in enumerate(page_texts)]
                    save_file_text(file_id, full_text, compress=compress, page_texts=page_tuples)
                    copy_pdf_to_store(file_id, pdf_path)
                    add_file_meta(
                        file_id=file_id, original_path=str(pdf_path),
                        num_pages=num_pages, doc_type=enriched["doc_type"],
                        word_count=enriched["word_count"],
                        classification_source=enriched["classification_source"],
                        classification_confidence=enriched["classification_confidence"],
                        file_size=f_size, content_hash=c_hash, metadata=metadata,
                        pages=[{"n": i+1, "char_count": len(pt), "has_ocr": False, "ocr_confidence": None}
                               for i, pt in enumerate(page_texts)],
                        indexed_at=now_iso, updated_at=now_iso,
                        language=enriched["language"],
                        ocr_percentage=0, summary=enriched["summary"],
                        subject=enriched["subject"],
                        tags=enriched["tags"] or None,
                        identified_dates=enriched["identified_dates"] or None,
                        confidentiality=enriched.get("confidentiality"),
                    )
                    fts_index_file(file_id, page_tuples)
                    audit("index_done", {"file_id": file_id, "pages": num_pages, "partial": True})
                    _partial_ret: dict[str, Any] = {
                        "id": file_id,
                        "name": pdf_path.name,
                        "num_pages": num_pages,
                        "doc_type": enriched["doc_type"] or "documento",
                        "partial_recovery": True,
                    }
                    if failed_pages:
                        _partial_ret["failed_pages"] = failed_pages
                        _partial_ret["ocr_warnings"] = (
                            f"Recuperação parcial: {len(failed_pages)} página(s) com erro "
                            f"({', '.join(str(p) for p in failed_pages[:5])}"
                            f"{'...' if len(failed_pages) > 5 else ''})."
                        )
                    return _partial_ret
            except Exception as _rec_err:
                logger.debug("Recuperação parcial falhou: %s", _rec_err)
        raise ValidationError(err or "PDF inválido", {"path": str(pdf_path)})

    # Verificar se é um PDF Portfolio (contém PDFs embutidos)
    embedded_results: list[dict | None] = []
    if is_pdf_portfolio(pdf_path, password=pwd):
        logger.info("PDF Portfolio detectado: %s — extraindo PDFs embutidos.", pdf_path.name)
        audit("portfolio_detected", {"path": str(pdf_path)})
        embedded_pdfs = extract_embedded_pdfs(pdf_path, password=pwd)
        if embedded_pdfs:
            import tempfile
            for emb_name, emb_data in embedded_pdfs:
                try:
                    with tempfile.NamedTemporaryFile(
                        suffix=".pdf", prefix=f"emb_{pdf_path.stem}_", delete=False
                    ) as tmp:
                        tmp.write(emb_data)
                        tmp_path = Path(tmp.name)
                    try:
                        result = index_pdf(
                            tmp_path,
                            doc_type=doc_type,
                            mode=mode,
                            password=pwd,
                            use_ocr=use_ocr,
                            compress=compress,
                            skip_existing=skip_existing,
                        )
                        if result:
                            result["embedded_from"] = pdf_path.name
                            result["embedded_name"] = emb_name
                            embedded_results.append(result)
                        logger.info("PDF embutido indexado: %s (de %s)", emb_name, pdf_path.name)
                    finally:
                        tmp_path.unlink(missing_ok=True)
                except Exception as _emb_err:
                    logger.warning("Falha ao indexar PDF embutido %s: %s", emb_name, _emb_err)
            if embedded_results:
                audit("portfolio_indexed", {
                    "path": str(pdf_path),
                    "embedded_count": len(embedded_results),
                })
                return {
                    "portfolio": True,
                    "source": pdf_path.name,
                    "embedded": embedded_results,
                }

    c_hash = content_hash(pdf_path)
    f_size = file_size(pdf_path)
    file_id = _file_id(pdf_path)

    # Arquivos grandes (>20 MB): compressão automática e aviso
    is_large = f_size >= LARGE_FILE_THRESHOLD_BYTES
    if is_large:
        logger.info(
            "Arquivo grande detectado (%.1f MB). Será usada compressão para reduzir uso de memória e disco.",
            f_size / (1024 * 1024),
        )
        audit(
            "index_large_file", {"path": str(pdf_path), "size_mb": round(f_size / (1024 * 1024), 1)}
        )
        compress = True

    if skip_existing:
        existing = find_by_content_hash(c_hash)
        if existing:
            if existing.get("id") == file_id:
                audit("index_skipped_unchanged", {"path": str(pdf_path), "content_hash": c_hash})
                return None
            if update_path_by_content_hash(c_hash, str(pdf_path), pdf_path.name):
                audit("index_updated_path", {"path": str(pdf_path), "file_id": existing.get("id")})
            return None

    audit("index_start", {"path": str(pdf_path), "file_id": file_id})

    for attempt in range(MAX_RETRIES):
        try:
            full_text, num_pages, page_texts, metadata, ocr_per_page, page_confidences = (
                _extract_with_ocr(
                    pdf_path,
                    mode,
                    pwd,
                    normalize=True,
                    use_ocr=use_ocr,
                    file_id=file_id,
                    content_hash=c_hash,
                )
            )
            # Extração estendida (tabelas, formulários, anotações, XMP) já feita em _extract_with_ocr na mesma abertura do PDF
            break
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                raise IndexingError(str(e), {"path": str(pdf_path)}) from e
            time.sleep(RETRY_BACKOFF * (2**attempt))
            logger.warning("Retry %s/%s após %s", attempt + 2, MAX_RETRIES, e)

    enriched = _enrich_document(
        full_text, pdf_path, metadata, doc_type, ocr_per_page, page_texts, page_confidences
    )
    metadata["text_chars"] = enriched.get("text_chars", len(full_text) if full_text else 0)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Features opcionais (redacções/forense/contratos/fórmulas) — ver _apply_optional_detections
    _post_warnings = _apply_optional_detections(
        metadata, pdf_path, pwd, enriched, file_id=file_id, page_texts=page_texts
    )

    page_tuples = [(i + 1, pt) for i, pt in enumerate(page_texts)]
    # Gravar arquivos primeiro (texto + PDF) e só depois os metadados no índice.
    # Garante que o relatório nunca serve um arquivo ainda não copiado caso
    # haja uma falha de I/O entre a escrita do PDF e a do índice.
    save_file_text(file_id, full_text, compress=compress, page_texts=page_tuples)
    copy_pdf_to_store(file_id, pdf_path)
    # Metadados persistidos só após os arquivos estarem no disco
    add_file_meta(
        file_id=file_id,
        original_path=str(pdf_path),
        num_pages=num_pages,
        doc_type=enriched["doc_type"],
        word_count=enriched["word_count"],
        classification_source=enriched["classification_source"],
        classification_confidence=enriched["classification_confidence"],
        file_size=f_size,
        content_hash=c_hash,
        metadata=metadata,
        pages=[
            {
                "n": i + 1,
                "char_count": len(pt),
                "has_ocr": ocr_per_page[i],
                "ocr_confidence": page_confidences[i] if ocr_per_page[i] else None,
            }
            for i, pt in enumerate(page_texts)
        ],
        indexed_at=now_iso,
        updated_at=now_iso,
        language=enriched["language"],
        ocr_percentage=enriched["ocr_percentage"],
        ocr_avg_confidence=enriched["ocr_avg_confidence"],
        summary=enriched["summary"],
        subject=enriched["subject"],
        tags=enriched["tags"] if enriched["tags"] else None,
        monetary_values=enriched["monetary_values"] if enriched["monetary_values"] else None,
        parties=enriched["parties"] if enriched["parties"] else None,
        identified_emails=enriched["entities"].get("emails") or None,
        identified_cpfs=enriched["entities"].get("cpfs") or None,
        identified_cnpjs=enriched["entities"].get("cnpjs") or None,
        identified_ips=enriched["entities"].get("ips") or None,
        identified_addresses=enriched["entities"].get("addresses") or None,
        identified_phones=enriched["entities"].get("phones") or None,
        identified_locations=enriched["identified_locations"] if enriched["identified_locations"] else None,
        identified_dates=enriched["identified_dates"] if enriched.get("identified_dates") else None,
        confidentiality=enriched.get("confidentiality"),
        identified_urls=enriched["entities"].get("urls") or None,
        identified_domains=enriched["entities"].get("domains") or None,
        identified_ceps=enriched["entities"].get("ceps") or None,
        identified_processos=enriched["entities"].get("processos") or None,
        identified_placas=enriched["entities"].get("placas") or None,
        identified_rgs=enriched["entities"].get("rgs") or None,
        identified_protocolos=enriched["entities"].get("protocolos") or None,
        identified_hashes=enriched["entities"].get("hashes") or None,
        identified_coordenadas=enriched["entities"].get("coordenadas") or None,
        identified_timestamps=enriched["entities"].get("timestamps") or None,
        identified_leis=enriched["entities"].get("leis") or None,
    )
    # Com PDFSEARCHABLE_FTS_DEFERRED=1, FTS é indexado ao final do lote (ou via index-fts)
    if (os.environ.get("PDFSEARCHABLE_FTS_DEFERRED") or "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        fts_index_file(file_id, page_tuples)

    audit(
        "index_done",
        {
            "file_id": file_id,
            "pages": num_pages,
            "words": enriched["word_count"],
            "doc_type": enriched["doc_type"],
            "classification_source": enriched["classification_source"],
            "ocr_avg_confidence": enriched["ocr_avg_confidence"],
        },
    )
    logger.info("Indexado: %s (%d páginas)", pdf_path.name, num_pages)

    _ret: dict[str, Any] = {
        "id": file_id,
        "name": pdf_path.name,
        "num_pages": num_pages,
        "word_count": enriched["word_count"],
        "doc_type": enriched["doc_type"] or "documento",
        "classification_source": enriched["classification_source"],
        "file_size": f_size,
        "content_hash": c_hash,
        "metadata": metadata,
        "ocr_avg_confidence": enriched["ocr_avg_confidence"],
    }
    # Avisos de estrutura do PDF (capturados do MuPDF) — superfície no CLI
    _pdf_warns = metadata.get("pdf_warnings") or []
    if _pdf_warns:
        logger.warning(
            "%s: avisos de estrutura PDF: %s",
            pdf_path.name,
            "; ".join(_pdf_warns),
        )
        _ret["pdf_structure_warnings"] = _pdf_warns
        # Acumular na coluna "Avisos" da tabela já usada para outros avisos
        _existing_warn = _ret.get("ocr_warnings") or ""
        _struct_warn = f"estrutura PDF: {'; '.join(_pdf_warns[:2])}"
        _ret["ocr_warnings"] = (
            f"{_existing_warn}; {_struct_warn}" if _existing_warn else _struct_warn
        )
    # Incluir avisos de qualidade no resultado para superfície no CLI
    if enriched.get("enrichment_partial"):
        _ret["enrichment_partial"] = True
        if not _ret.get("ocr_warnings"):
            _ret["ocr_warnings"] = enriched.get("ocr_warnings") or ""

    # Anexar warnings acumulados na fase de detecção (redacções/forense)
    # ao _ret, depois de pdf_structure_warnings e enrichment_partial já
    # terem sido tratados acima.
    for _w in _post_warnings:
        prev = _ret.get("ocr_warnings") or ""
        _ret["ocr_warnings"] = f"{prev}; {_w}" if prev else _w

    # --- Loop de aprendizagem do classificador ---
    if os.environ.get("PDFSEARCHABLE_CLASSIFIER_FEEDBACK", "").strip() in ("1", "true", "yes"):
        try:
            from pdfsearchable.classifier_feedback import record_correction
            if enriched.get("classification_source") == "heuristics" and enriched.get("doc_type"):
                record_correction(
                    file_id,
                    enriched["doc_type"],
                    (enriched.get("full_text") or "")[:500],
                    source="auto_heuristics",
                )
        except Exception as _e:
            logger.debug("Feedback do classificador falhou para %s: %s", pdf_path.name, _e)

    return _ret


def _index_one(
    p: Path,
    doc_type: str | None,
    mode: ExtractMode,
    password: str | None,
    use_ocr: bool,
    compress: bool,
    skip_existing: bool,
) -> dict | None:
    """Wrapper para index_pdf usada no pool paralelo."""
    return index_pdf(
        p,
        doc_type=doc_type,
        mode=mode,
        password=password,
        use_ocr=use_ocr,
        compress=compress,
        skip_existing=skip_existing,
    )


def index_pdfs(
    paths: list[Path],
    doc_types: dict[str, str] | None = None,
    *,
    mode: ExtractMode = "text",
    password: str | None = None,
    use_ocr: bool = True,
    compress: bool = False,
    skip_existing: bool = True,
    skip_failed: bool = False,
    batch_size: int | None = None,
    workers: int = 0,
    on_file_progress: Callable[[Path, int, int], None] | None = None,
    on_file_start: Callable[[Path], None] | None = None,
) -> list[dict]:
    """
    Indexa vários PDFs em lote.
    workers=0: automático (até _max_workers_auto(), padrão máx. 16). workers=1: sequencial no processo atual.
    workers>1: multiprocessing (ProcessPoolExecutor), cada PDF num processo — evita Lock blocking do PyMuPDF.
    skip_failed=True: não propaga exceção, omite arquivos com erro (modo contínuo).
    batch_size: processa em chunks de N arquivos e faz gc entre chunks para controlar RAM.
    on_file_progress(path, current, total): chamado ao iniciar (workers=1) ou ao concluir (workers>1) cada arquivo.
    on_file_start(path): chamado antes de processar cada arquivo (apenas workers=1; útil para --verbose).
    """
    import gc as _gc

    doc_types = doc_types or {}
    paths = [Path(p).resolve() for p in paths]
    if not paths:
        return []

    if workers <= 0:
        workers = _max_workers_auto()
    total = len(paths)

    def run_chunk(chunk_paths: list[Path], start_index: int) -> list[dict]:
        chunk_results: list[dict] = []
        if workers == 1:
            for i, p in enumerate(chunk_paths):
                if on_file_start:
                    on_file_start(p)
                if on_file_progress:
                    on_file_progress(p, start_index + i + 1, total)
                doc_type = doc_types.get(str(p)) or doc_types.get(_file_id(p))
                try:
                    meta = index_pdf(
                        p,
                        doc_type=doc_type,
                        mode=mode,
                        password=password,
                        use_ocr=use_ocr,
                        compress=compress,
                        skip_existing=skip_existing,
                    )
                    if meta:
                        chunk_results.append(meta)
                except (ValidationError, IndexingError) as e:
                    audit(
                        "index_error",
                        {"path": str(p), "error": str(e), "code": type(e).__name__},
                        level="error",
                    )
                    logger.exception("Erro ao indexar %s", p)
                    if not skip_failed:
                        raise
                except Exception as e:
                    audit(
                        "index_error",
                        {"path": str(p), "error": str(e), "code": "Exception"},
                        level="error",
                    )
                    logger.exception("Erro ao indexar %s", p)
                    if not skip_failed:
                        raise IndexingError(str(e), {"path": str(p)}) from e
        else:
            # Multiprocessing: PyMuPDF não é thread-safe; processos evitam Lock blocking
            to_process: list[tuple[Path, str | None]] = []
            for p in chunk_paths:
                doc_type = doc_types.get(str(p)) or doc_types.get(_file_id(p))
                c_hash = content_hash(p)
                file_id = _file_id(p)
                if skip_existing:
                    existing = find_by_content_hash(c_hash)
                    if existing:
                        if existing.get("id") == file_id:
                            audit(
                                "index_skipped_unchanged", {"path": str(p), "content_hash": c_hash}
                            )
                            continue
                        if update_path_by_content_hash(c_hash, str(p), p.name):
                            audit(
                                "index_updated_path",
                                {"path": str(p), "file_id": existing.get("id")},
                            )
                            continue
                audit("index_start", {"path": str(p), "file_id": file_id})
                to_process.append((p, doc_type))
            if not to_process:
                return chunk_results
            w = min(workers, len(to_process))
            _fts_deferred = (
                os.environ.get("PDFSEARCHABLE_FTS_DEFERRED") or ""
            ).strip().lower() in ("1", "true", "yes")
            with ProcessPoolExecutor(max_workers=w) as executor:
                futures = {
                    executor.submit(
                        _worker_extract_and_classify,
                        (str(p), mode, password, use_ocr, doc_type),
                    ): p
                    for p, doc_type in to_process
                }
                completed_paths: set[Path] = set()
                ac_iter = as_completed(futures)
                completed = 0
                _broken_pool_unfinished: list[tuple[Path, str | None]] | None = None
                while True:
                    try:
                        future = next(ac_iter)
                    except StopIteration:
                        break
                    except BrokenProcessPool as bpe:
                        # Worker morreu (ex.: SSD desmontou, OOM, segfault).
                        # Em vez de só marcar como erro, vamos retry SEQUENCIALMENTE no
                        # processo actual (mais robusto a problemas transitórios de I/O
                        # e isola falhas a arquivos individuais).
                        _broken_pool_unfinished = [
                            (p, dt) for p, dt in to_process if p not in completed_paths
                        ]
                        logger.warning(
                            "BrokenProcessPool: %s — %d arquivos serão reprocessados sequencialmente",
                            bpe,
                            len(_broken_pool_unfinished),
                        )
                        audit(
                            "broken_pool_recovery",
                            {
                                "error": str(bpe),
                                "unfinished": len(_broken_pool_unfinished),
                            },
                            level="warning",
                        )
                        break
                    completed += 1
                    p = futures[future]
                    completed_paths.add(p)
                    try:
                        res = future.result()
                        file_id = res["file_id"]
                        c_hash = res["content_hash"]
                        f_size = file_size(p)
                        comp = compress or f_size >= LARGE_FILE_THRESHOLD_BYTES
                        page_tuples = [(i + 1, t) for i, t in enumerate(res["page_texts"])]
                        save_file_text(
                            file_id, res["full_text"], compress=comp, page_texts=page_tuples
                        )
                        copy_pdf_to_store(file_id, p)
                        add_file_meta(
                            file_id=file_id,
                            original_path=str(p),
                            num_pages=res["num_pages"],
                            doc_type=res["doc_type"],
                            word_count=res["word_count"],
                            classification_source=res["classification_source"],
                            classification_confidence=res.get("classification_confidence"),
                            file_size=f_size,
                            content_hash=c_hash,
                            metadata=res["metadata"],
                            pages=[
                                {
                                    "n": i + 1,
                                    "char_count": len(pt),
                                    "has_ocr": res["ocr_per_page"][i],
                                    "ocr_confidence": res["ocr_confidences"][i]
                                    if res["ocr_per_page"][i]
                                    else None,
                                }
                                for i, pt in enumerate(res["page_texts"])
                            ],
                            indexed_at=res["now_iso"],
                            updated_at=res["now_iso"],
                            language=res["language"],
                            ocr_percentage=res["ocr_percentage"],
                            ocr_avg_confidence=res.get("ocr_avg_confidence"),
                            summary=res["summary"],
                            subject=res["subject"],
                            tags=res["tags"] if res["tags"] else None,
                            monetary_values=res["monetary_values"]
                            if res["monetary_values"]
                            else None,
                            parties=res["parties"] if res["parties"] else None,
                            identified_emails=res["entities"].get("emails") or None,
                            identified_cpfs=res["entities"].get("cpfs") or None,
                            identified_cnpjs=res["entities"].get("cnpjs") or None,
                            identified_ips=res["entities"].get("ips") or None,
                            identified_addresses=res["entities"].get("addresses") or None,
                            identified_phones=res["entities"].get("phones") or None,
                            identified_locations=res.get("identified_locations") or None,
                            identified_dates=res.get("identified_dates") or None,
                            confidentiality=res.get("confidentiality"),
                            identified_urls=res["entities"].get("urls") or None,
                            identified_domains=res["entities"].get("domains") or None,
                            identified_ceps=res["entities"].get("ceps") or None,
                            identified_processos=res["entities"].get("processos") or None,
                            identified_placas=res["entities"].get("placas") or None,
                            identified_rgs=res["entities"].get("rgs") or None,
                            identified_protocolos=res["entities"].get("protocolos") or None,
                            identified_hashes=res["entities"].get("hashes") or None,
                            identified_coordenadas=res["entities"].get("coordenadas") or None,
                            identified_timestamps=res["entities"].get("timestamps") or None,
                            identified_leis=res["entities"].get("leis") or None,
                        )
                        if not _fts_deferred:
                            fts_index_file(file_id, page_tuples)
                        audit(
                            "index_done",
                            {
                                "file_id": file_id,
                                "pages": res["num_pages"],
                                "words": res["word_count"],
                                "doc_type": res["doc_type"],
                                "classification_source": res["classification_source"],
                                "ocr_avg_confidence": res.get("ocr_avg_confidence"),
                            },
                        )
                        logger.info("Indexado: %s (%d páginas)", p.name, res["num_pages"])
                        _chunk_entry: dict[str, Any] = {
                            "id": file_id,
                            "name": p.name,
                            "num_pages": res["num_pages"],
                            "word_count": res["word_count"],
                            "doc_type": res["doc_type"] or "documento",
                            "classification_source": res["classification_source"],
                            "classification_confidence": res.get("classification_confidence"),
                            "file_size": f_size,
                            "content_hash": c_hash,
                            "metadata": res["metadata"],
                            "ocr_avg_confidence": res.get("ocr_avg_confidence"),
                        }
                        # Acumular warnings de detecções opcionais (workers>1)
                        _pw = res.get("post_warnings") or []
                        if _pw:
                            _chunk_entry["ocr_warnings"] = "; ".join(_pw)
                        if res.get("enrichment_partial"):
                            _chunk_entry["enrichment_partial"] = True
                            _prev = _chunk_entry.get("ocr_warnings") or ""
                            _ow = res.get("ocr_warnings") or ""
                            if _ow:
                                _chunk_entry["ocr_warnings"] = f"{_prev}; {_ow}" if _prev else _ow
                        chunk_results.append(_chunk_entry)
                    except (ValidationError, IndexingError) as e:
                        audit(
                            "index_error",
                            {"path": str(p), "error": str(e), "code": type(e).__name__},
                            level="error",
                        )
                        logger.exception("Erro ao indexar %s", p)
                        if not skip_failed:
                            raise
                    except Exception as e:
                        audit(
                            "index_error",
                            {"path": str(p), "error": str(e), "code": "Exception"},
                            level="error",
                        )
                        logger.exception("Erro ao indexar %s", p)
                        if not skip_failed:
                            raise IndexingError(str(e), {"path": str(p)}) from e
                    if on_file_progress:
                        on_file_progress(p, start_index + completed, total)
            # Recovery sequencial após BrokenProcessPool: tentar reprocessar os
            # arquivos não-completos directamente neste processo (mais lento, mais robusto).
            if _broken_pool_unfinished:
                logger.info(
                    "Iniciando recovery sequencial de %d arquivo(s)",
                    len(_broken_pool_unfinished),
                )
                for p_un, doc_type_un in _broken_pool_unfinished:
                    try:
                        meta = index_pdf(
                            p_un,
                            doc_type=doc_type_un,
                            mode=mode,
                            password=password,
                            use_ocr=use_ocr,
                            compress=compress,
                            skip_existing=skip_existing,
                        )
                        if meta:
                            chunk_results.append(meta)
                            audit(
                                "broken_pool_recovered",
                                {"path": str(p_un), "file_id": meta.get("id")},
                            )
                    except (ValidationError, IndexingError) as e:
                        audit(
                            "index_error",
                            {"path": str(p_un), "error": str(e), "code": type(e).__name__},
                            level="error",
                        )
                        logger.exception("Recovery sequencial falhou: %s", p_un)
                        if not skip_failed:
                            raise
                    except Exception as e:
                        audit(
                            "index_error",
                            {"path": str(p_un), "error": str(e), "code": "Exception"},
                            level="error",
                        )
                        logger.exception("Recovery sequencial falhou: %s", p_un)
                        if not skip_failed:
                            raise IndexingError(str(e), {"path": str(p_un)}) from e
                    completed += 1
                    if on_file_progress:
                        on_file_progress(p_un, start_index + completed, total)
        return chunk_results

    results: list[dict] = []
    if batch_size and batch_size > 0:
        for offset in range(0, total, batch_size):
            chunk = paths[offset : offset + batch_size]
            results.extend(run_chunk(chunk, offset))
            if offset + batch_size < total:
                _gc.collect()
    else:
        results = run_chunk(paths, 0)

    return results


def get_stats() -> dict:
    idx = load_index()
    files = idx.get("files", [])
    total_pages = sum(f.get("num_pages", 0) for f in files)
    return {"total_files": len(files), "total_pages": total_pages, "files": files}
