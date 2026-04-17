"""
Armazenamento do índice: metadados, texto por página e full-text search.
Schema versionado com migração ao abrir.
"""

import copy
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

from pdfsearchable.audit import get_logger as _get_logger
from pdfsearchable.exceptions import StoreError

_store_logger = _get_logger("pdfsearchable.store")

# Última mensagem de erro do índice FTS, para surfaçar ao utilizador quando a busca falha.
# Actualizado por fts_ensure_healthy() e fts_search(); lido pelo CLI via fts_last_error().
_fts_last_error: str = ""


def fts_last_error() -> str:
    """Retorna a última mensagem de erro do índice FTS, ou string vazia se tudo OK."""
    return _fts_last_error


# file_id é sempre 16 caracteres hex (sha256[:16]); aceita maiúsculas/minúsculas
_FILE_ID_RE = re.compile(r"^[a-fA-F0-9]{16}$")

# RLock: permite aquisição reentrante pela mesma thread (load_index chamado de dentro de
# add_file_meta que já segura o lock). Lock simples causaria deadlock nesse cenário.
_index_lock = threading.RLock()

# Cache em memória do índice JSON, invalidado por mtime do arquivo.
# Evita leituras redundantes de disco em operações frequentes (search, status, etc.).
# TTL: durante _INDEX_CACHE_TTL segundos após a última verificação de mtime, o cache é
# retornado sem fazer stat() — reduz I/O em rajadas de pesquisa de alta frequência.
# O path de META_FILE é rastreado no cache: se mudar (ex.: monkeypatch em testes), o
# TTL é invalidado imediatamente para evitar devolver dados de um índice diferente.
_index_cache: dict[str, Any] | None = None
_index_cache_mtime: float = -1.0
_index_cache_checked: float = 0.0  # monotonic timestamp da última verificação de mtime
_index_cache_path: "Path | None" = None  # META_FILE aquando do último cache
_INDEX_CACHE_TTL: float = 5.0  # segundos; 0 para desactivar (sempre verifica mtime)

PROJECT_DIR = Path.cwd()
STORE_DIR = PROJECT_DIR / ".pdfsearchable"
# PDFs e texto extraído ficam na pasta do projeto (ex.: arquivos-processados)
PROCESSED_DIR_NAME = "arquivos-processados"
FILES_DIR = PROJECT_DIR / PROCESSED_DIR_NAME
META_FILE = STORE_DIR / "index.json"
OCR_CACHE_DIR = STORE_DIR / "ocr_cache"
FTS_DB = STORE_DIR / "fts.sqlite"

# Versão atual do esquema do índice
INDEX_VERSION = 3


def _ensure_store() -> Path:
    STORE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    OCR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _migrate_files_to_processed_dir()
    return STORE_DIR


def ensure_store() -> Path:
    """Cria a estrutura .pdfsearchable/ e arquivos-processados/ se não existir. Público para uso em init."""
    return _ensure_store()


def _migrate_files_to_processed_dir() -> None:
    """Uma vez: copia .pdfsearchable/files para arquivos-processados se existir."""
    legacy_files = STORE_DIR / "files"
    if not legacy_files.exists() or not legacy_files.is_dir():
        return
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    if any(FILES_DIR.iterdir()):
        return
    for item in legacy_files.iterdir():
        dest = FILES_DIR / item.name
        if dest.exists():
            continue
        if item.is_file():
            shutil.copy2(item, dest)
        else:
            shutil.copytree(item, dest)


def _file_id(path: Path) -> str:
    """ID estável baseado no caminho absoluto (normalizado).

    O path é sempre normalizado para NFC antes do hash para garantir
    resultados consistentes em sistemas de arquivos que usam NFD
    (ex.: macOS HFS+/APFS), evitando IDs duplicados para o mesmo arquivo.
    """
    key = unicodedata.normalize("NFC", str(path.resolve()))
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


def _migrate_index(data: dict[str, Any]) -> dict[str, Any]:
    """
    Migra index para a versão atual, aplicando cada bloco de migração em sequência.
    Cada bloco verifica data["version"] (que é atualizado a cada passo), evitando
    que migrações de versões anteriores corram novamente em re-migrações futuras.
    """
    if data.get("version", 1) >= INDEX_VERSION:
        return data
    # v1 → v2: adicionar campos file_size, content_hash, metadata, pages
    if data.get("version", 1) < 2:
        for f in data.get("files", []):
            f.setdefault("file_size", None)
            f.setdefault("content_hash", None)
            f.setdefault("metadata", {})
            f.setdefault("pages", [])
        data["version"] = 2
    # v2 → v3: adicionar indexed_at, updated_at, language, has_ocr por página
    if data.get("version", 1) < 3:
        for f in data.get("files", []):
            f.setdefault("indexed_at", None)
            f.setdefault("updated_at", None)
            f.setdefault("language", None)
            for p in f.get("pages", []):
                if "has_ocr" not in p:
                    p["has_ocr"] = False
        data["version"] = 3
    # v3 → v3 (INDEX_VERSION): preencher ocr_percentage a partir de has_ocr por página
    if data.get("version", 1) < INDEX_VERSION:
        for f in data.get("files", []):
            if "ocr_percentage" not in f and f.get("pages"):
                has_ocr_count = sum(1 for p in f.get("pages", []) if p.get("has_ocr"))
                f["ocr_percentage"] = (
                    round(100.0 * has_ocr_count / len(f["pages"])) if f["pages"] else 0
                )
        data["version"] = INDEX_VERSION
    return data


def load_index() -> dict[str, Any]:
    """
    Carrega index.json com cache em memória (invalidado por mtime).
    Retorna uma cópia profunda para que o chamador possa modificar livremente sem
    corromper o cache. Levanta StoreError se o arquivo estiver corrompido ou inacessível.
    TTL: durante _INDEX_CACHE_TTL segundos após a última verificação de mtime, o cache é
    devolvido sem stat() — reduz I/O em rajadas de pesquisa de alta frequência.
    """
    global _index_cache, _index_cache_mtime, _index_cache_checked, _index_cache_path
    try:
        _ensure_store()
    except OSError as e:
        raise StoreError(
            f"Não foi possível criar a pasta do índice: {e}",
            {"path": str(STORE_DIR)},
        ) from e
    with _index_lock:
        now = time.monotonic()
        # Dentro do TTL: devolver o cache sem verificar o mtime (evita stat()).
        # Guarda também o path de META_FILE: se mudou (ex.: monkeypatch em testes)
        # o TTL não é aplicado e o cache é invalidado.
        if (
            _index_cache is not None
            and _INDEX_CACHE_TTL > 0
            and (now - _index_cache_checked) < _INDEX_CACHE_TTL
            and _index_cache_path == META_FILE
        ):
            # Deep copy: funções como update_doc_type modificam os dicts de files in-place
            # na cópia retornada, o que corromperia o cache se usássemos shallow copy.
            return copy.deepcopy(_index_cache)
        try:
            current_mtime = META_FILE.stat().st_mtime if META_FILE.exists() else 0.0
        except OSError:
            current_mtime = 0.0
        _index_cache_checked = now
        if _index_cache is not None and current_mtime == _index_cache_mtime:
            return copy.deepcopy(_index_cache)
        if not META_FILE.exists():
            fresh: dict[str, Any] = {"version": INDEX_VERSION, "files": []}
            _index_cache = fresh
            _index_cache_mtime = 0.0
            _index_cache_path = META_FILE
            return {"version": INDEX_VERSION, "files": []}
        try:
            with open(META_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise StoreError(
                "O arquivo do índice (.pdfsearchable/index.json) está corrompido ou não é JSON válido.",
                {"path": str(META_FILE), "error": str(e)},
            ) from e
        except OSError as e:
            raise StoreError(
                "Não foi possível ler o índice (.pdfsearchable/index.json).",
                {"path": str(META_FILE), "error": str(e)},
            ) from e
        data = _migrate_index(data)
        _index_cache = copy.deepcopy(data)  # cópia profunda apenas ao carregar do disco
        _index_cache_path = META_FILE
        try:
            _index_cache_mtime = META_FILE.stat().st_mtime
        except OSError:
            _index_cache_mtime = 0.0
        if data.get("version") != INDEX_VERSION:
            save_index(data)
        return copy.deepcopy(_index_cache)


def _maybe_snapshot_index() -> None:
    """
    Cria snapshot rotativo do index.json em .pdfsearchable/.snapshots/ antes
    de sobrescrever. Desligado por default (PDFSEARCHABLE_AUTO_SNAPSHOT=1 para activar).
    Mantém últimos N snapshots (PDFSEARCHABLE_SNAPSHOT_KEEP, padrão 5).
    Útil para recuperar de escritas incorrectas mesmo sem git.
    """
    if (os.environ.get("PDFSEARCHABLE_AUTO_SNAPSHOT") or "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return
    if not META_FILE.exists():
        return
    try:
        snap_dir = STORE_DIR / ".snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        dest = snap_dir / f"index_{ts}.json"
        # Copy-through-temp para evitar ficheiros parciais em caso de crash
        import shutil as _shutil

        _shutil.copy2(META_FILE, dest)
        # Rotação: manter apenas os N mais recentes
        try:
            keep = max(1, int(os.environ.get("PDFSEARCHABLE_SNAPSHOT_KEEP", "5")))
        except ValueError:
            keep = 5
        snaps = sorted(snap_dir.glob("index_*.json"), reverse=True)
        for old in snaps[keep:]:
            try:
                old.unlink()
            except OSError:
                pass
    except Exception as _e:
        # Snapshots são best-effort: nunca bloquear save_index
        _store_logger.debug("snapshot do index falhou: %s", _e)


def save_index(data: dict[str, Any]) -> None:
    """
    Grava index.json de forma atômica (temp + rename) e actualiza o cache em memória.
    Levanta StoreError em falha de I/O.
    Com PDFSEARCHABLE_AUTO_SNAPSHOT=1, cria snapshot rotativo antes de sobrescrever.
    """
    global _index_cache, _index_cache_mtime, _index_cache_checked, _index_cache_path
    try:
        _ensure_store()
        _maybe_snapshot_index()
        data["version"] = INDEX_VERSION
        tmp = META_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(META_FILE)
        # Actualizar cache com cópia limpa do que foi persistido
        _index_cache = copy.deepcopy(data)
        _index_cache_path = META_FILE
        try:
            _index_cache_mtime = META_FILE.stat().st_mtime
        except OSError:
            _index_cache_mtime = -1.0
        # Forçar verificação de mtime na próxima leitura (TTL expirado)
        _index_cache_checked = 0.0
        # Invalidar cache do dashboard
        try:
            invalidate_dashboard_stats_cache()
        except Exception:
            pass
    except OSError as e:
        raise StoreError(
            "Não foi possível gravar o índice (.pdfsearchable/index.json).",
            {"path": str(META_FILE), "error": str(e)},
        ) from e


def get_file_meta(file_id: str) -> dict | None:
    idx = load_index()
    for f in idx.get("files", []):
        if f.get("id") == file_id:
            return f
    return None


def find_by_content_hash(content_hash: str) -> dict | None:
    """Retorna metadados do primeiro arquivo com esse content_hash, ou None."""
    idx = load_index()
    for f in idx.get("files", []):
        if f.get("content_hash") == content_hash:
            return f
    return None


def get_duplicate_groups() -> list[list[dict[str, Any]]]:
    """
    Agrupa arquivos pelo content_hash. Retorna apenas grupos com 2+ arquivos
    (mesmo conteúdo, paths diferentes).
    """
    idx = load_index()
    by_hash: dict[str, list[dict[str, Any]]] = {}
    for f in idx.get("files", []):
        h = f.get("content_hash")
        if not h:
            continue
        by_hash.setdefault(h, []).append(f)
    return [g for g in by_hash.values() if len(g) >= 2]


def add_file_meta(
    file_id: str,
    original_path: str,
    num_pages: int,
    doc_type: str | None = None,
    word_count: int = 0,
    classification_source: str | None = None,
    classification_confidence: float | None = None,
    file_size: int | None = None,
    content_hash: str | None = None,
    metadata: dict[str, Any] | None = None,
    pages: list[dict[str, Any]] | None = None,
    indexed_at: str | None = None,
    updated_at: str | None = None,
    language: str | None = None,
    ocr_percentage: int | None = None,
    ocr_avg_confidence: float | None = None,
    summary: str | None = None,
    subject: str | None = None,
    tags: list[str] | None = None,
    monetary_values: list[dict[str, Any]] | None = None,
    parties: list[str] | None = None,
    identified_emails: list[str] | None = None,
    identified_cpfs: list[str] | None = None,
    identified_cnpjs: list[str] | None = None,
    identified_ips: list[str] | None = None,
    identified_addresses: list[str] | None = None,
    identified_phones: list[str] | None = None,
    identified_locations: list[str] | None = None,
    identified_dates: list[str] | None = None,
    confidentiality: str | None = None,
    identified_urls: list[str] | None = None,
    identified_domains: list[str] | None = None,
    identified_ceps: list[str] | None = None,
    identified_processos: list[str] | None = None,
    identified_placas: list[str] | None = None,
    identified_rgs: list[str] | None = None,
    identified_protocolos: list[str] | None = None,
    identified_hashes: list[str] | None = None,
    identified_coordenadas: list[str] | None = None,
    identified_timestamps: list[str] | None = None,
    identified_leis: list[str] | None = None,
) -> None:
    with _index_lock:
        _add_file_meta_unsafe(
            file_id,
            original_path,
            num_pages,
            doc_type,
            word_count,
            classification_source,
            classification_confidence,
            file_size,
            content_hash,
            metadata,
            pages,
            indexed_at=indexed_at,
            updated_at=updated_at,
            language=language,
            ocr_percentage=ocr_percentage,
            ocr_avg_confidence=ocr_avg_confidence,
            summary=summary,
            subject=subject,
            tags=tags,
            monetary_values=monetary_values,
            parties=parties,
            identified_emails=identified_emails,
            identified_cpfs=identified_cpfs,
            identified_cnpjs=identified_cnpjs,
            identified_ips=identified_ips,
            identified_addresses=identified_addresses,
            identified_phones=identified_phones,
            identified_locations=identified_locations,
            identified_dates=identified_dates,
            confidentiality=confidentiality,
            identified_urls=identified_urls,
            identified_domains=identified_domains,
            identified_ceps=identified_ceps,
            identified_processos=identified_processos,
            identified_placas=identified_placas,
            identified_rgs=identified_rgs,
            identified_protocolos=identified_protocolos,
            identified_hashes=identified_hashes,
            identified_coordenadas=identified_coordenadas,
            identified_timestamps=identified_timestamps,
            identified_leis=identified_leis,
        )


def _add_file_meta_unsafe(
    file_id: str,
    original_path: str,
    num_pages: int,
    doc_type: str | None,
    word_count: int,
    classification_source: str | None,
    classification_confidence: float | None,
    file_size: int | None,
    content_hash: str | None,
    metadata: dict[str, Any] | None,
    pages: list[dict[str, Any]] | None,
    *,
    indexed_at: str | None = None,
    updated_at: str | None = None,
    language: str | None = None,
    ocr_percentage: int | None = None,
    ocr_avg_confidence: float | None = None,
    summary: str | None = None,
    subject: str | None = None,
    tags: list[str] | None = None,
    monetary_values: list[dict[str, Any]] | None = None,
    parties: list[str] | None = None,
    identified_emails: list[str] | None = None,
    identified_cpfs: list[str] | None = None,
    identified_cnpjs: list[str] | None = None,
    identified_ips: list[str] | None = None,
    identified_addresses: list[str] | None = None,
    identified_phones: list[str] | None = None,
    identified_locations: list[str] | None = None,
    identified_dates: list[str] | None = None,
    confidentiality: str | None = None,
    identified_urls: list[str] | None = None,
    identified_domains: list[str] | None = None,
    identified_ceps: list[str] | None = None,
    identified_processos: list[str] | None = None,
    identified_placas: list[str] | None = None,
    identified_rgs: list[str] | None = None,
    identified_protocolos: list[str] | None = None,
    identified_hashes: list[str] | None = None,
    identified_coordenadas: list[str] | None = None,
    identified_timestamps: list[str] | None = None,
    identified_leis: list[str] | None = None,
) -> None:
    idx = load_index()
    files = idx.get("files", [])
    meta: dict[str, Any] = {
        "id": file_id,
        "original_path": original_path,
        "name": Path(original_path).name,
        "num_pages": num_pages,
        "doc_type": doc_type or "documento",
        "word_count": word_count,
        "file_size": file_size,
        "content_hash": content_hash,
        "metadata": metadata or {},
        "pages": pages or [],
        "indexed_at": indexed_at,
        "updated_at": updated_at,
        "language": language,
    }
    if classification_source:
        meta["classification_source"] = classification_source
    if classification_confidence is not None:
        meta["classification_confidence"] = round(classification_confidence, 2)
    if ocr_percentage is not None:
        meta["ocr_percentage"] = ocr_percentage
    if ocr_avg_confidence is not None:
        meta["ocr_avg_confidence"] = ocr_avg_confidence
    if summary:
        meta["summary"] = summary
    if subject:
        meta["subject"] = subject
    if tags:
        meta["tags"] = tags
    if monetary_values:
        meta["monetary_values"] = monetary_values
    if parties:
        meta["parties"] = parties
    if identified_emails:
        meta["identified_emails"] = identified_emails
    if identified_cpfs:
        meta["identified_cpfs"] = identified_cpfs
    if identified_cnpjs:
        meta["identified_cnpjs"] = identified_cnpjs
    if identified_ips:
        meta["identified_ips"] = identified_ips
    if identified_addresses:
        meta["identified_addresses"] = identified_addresses
    if identified_phones:
        meta["identified_phones"] = identified_phones
    if identified_locations:
        meta["identified_locations"] = identified_locations
    if identified_dates:
        meta["identified_dates"] = identified_dates
    if confidentiality:
        meta["confidentiality"] = confidentiality
    if identified_urls:
        meta["identified_urls"] = identified_urls
    if identified_domains:
        meta["identified_domains"] = identified_domains
    if identified_ceps:
        meta["identified_ceps"] = identified_ceps
    if identified_processos:
        meta["identified_processos"] = identified_processos
    if identified_placas:
        meta["identified_placas"] = identified_placas
    if identified_rgs:
        meta["identified_rgs"] = identified_rgs
    if identified_protocolos:
        meta["identified_protocolos"] = identified_protocolos
    if identified_hashes:
        meta["identified_hashes"] = identified_hashes
    if identified_coordenadas:
        meta["identified_coordenadas"] = identified_coordenadas
    if identified_timestamps:
        meta["identified_timestamps"] = identified_timestamps
    if identified_leis:
        meta["identified_leis"] = identified_leis
    # Atualizar se já existir (reindexação): preservar indexed_at original
    for i, f in enumerate(files):
        if f.get("id") == file_id:
            if meta.get("indexed_at") is None and f.get("indexed_at"):
                meta["indexed_at"] = f["indexed_at"]
            files[i] = meta
            idx["files"] = files
            save_index(idx)
            return
    files.append(meta)
    idx["files"] = files
    save_index(idx)


def update_path_by_content_hash(content_hash: str, new_path: str, new_name: str) -> bool:
    """Atualiza path/nome do primeiro arquivo com esse content_hash. Thread-safe. Retorna True se atualizou."""
    with _index_lock:
        idx = load_index()
        for f in idx.get("files", []):
            if f.get("content_hash") == content_hash:
                f["original_path"] = new_path
                f["name"] = new_name
                save_index(idx)
                return True
        return False


def update_doc_type(file_id: str, new_type: str, *, source: str | None = None) -> bool:
    """
    Atualiza o doc_type de um arquivo já indexado.
    Opcionalmente atualiza classification_source (ex.: 'manual').
    Retorna True se encontrou e atualizou.
    """
    with _index_lock:
        idx = load_index()
        changed = False
        for f in idx.get("files", []):
            if f.get("id") == file_id:
                f["doc_type"] = new_type or "documento"
                if source:
                    f["classification_source"] = source
                changed = True
                break
        if changed:
            save_index(idx)
        return changed


def remove_file_meta(file_id: str) -> bool:
    """Remove metadados e arquivos associados ao file_id. Retorna False se não existir; levanta StoreError em falha de I/O."""
    if not _validate_file_id(file_id):
        return False
    try:
        with _index_lock:
            idx = load_index()
            files = idx.get("files", [])
            new_files = [f for f in files if f.get("id") != file_id]
            if len(new_files) == len(files):
                return False
            idx["files"] = new_files
            save_index(idx)
        # Remover texto completo
        text_path = FILES_DIR / file_id / "full.txt"
        if text_path.exists():
            text_path.unlink()
        gz_path = FILES_DIR / file_id / "full.txt.gz"
        if gz_path.exists():
            gz_path.unlink()
        # Remover páginas
        pages_dir = FILES_DIR / file_id / "pages"
        if pages_dir.exists():
            try:
                shutil.rmtree(pages_dir)
            except OSError as _rm_err:
                _store_logger.debug(
                    "Não foi possível remover directório de páginas %s: %s", pages_dir, _rm_err
                )
        # Diretório do arquivo (se vazio, remover)
        file_dir = FILES_DIR / file_id
        if file_dir.exists() and not any(file_dir.iterdir()):
            file_dir.rmdir()
        # Legado: arquivo único .txt
        legacy = FILES_DIR / f"{file_id}.txt"
        if legacy.exists():
            legacy.unlink()
        # PDF em arquivos-processados/ (servido pelo serve para visualização no report)
        pdf_copy = FILES_DIR / f"{file_id}.pdf"
        if pdf_copy.exists():
            pdf_copy.unlink()
        # FTS
        _fts_delete_file(file_id)
        return True
    except StoreError:
        raise
    except OSError as e:
        raise StoreError(
            f"Falha ao remover arquivos do documento do índice: {e}",
            {"file_id": file_id},
        ) from e


def _validate_file_id(file_id: str) -> bool:
    """Garante formato seguro (16 hex) para evitar path traversal."""
    return bool(file_id and _FILE_ID_RE.match(file_id))


def _file_dir(file_id: str) -> Path:
    _ensure_store()
    if not _validate_file_id(file_id):
        raise ValueError(
            f"file_id inválido: esperado 16 caracteres hex, obtido {repr(file_id)[:50]}"
        )
    d = FILES_DIR / file_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_file_text(
    file_id: str,
    text: str,
    *,
    compress: bool = False,
    page_texts: list[tuple[int, str]] | None = None,
) -> None:
    """Salva texto completo e opcionalmente por página. compress=True usa gzip."""
    fd = _file_dir(file_id)
    full_path = fd / "full.txt.gz" if compress else fd / "full.txt"
    if compress:
        full_path.write_bytes(gzip.compress(text.encode("utf-8")))
    else:
        full_path.write_text(text, encoding="utf-8")
    if page_texts:
        pages_dir = fd / "pages"
        pages_dir.mkdir(exist_ok=True)
        for page_num, page_text in page_texts:
            if compress:
                (pages_dir / f"{page_num:04d}.txt.gz").write_bytes(
                    gzip.compress(page_text.encode("utf-8"))
                )
            else:
                (pages_dir / f"{page_num:04d}.txt").write_text(page_text, encoding="utf-8")


def load_file_text(file_id: str, *, decompress: bool = True) -> str:
    """Carrega texto completo. Detecta .gz automaticamente. Levanta StoreError em falha de I/O."""
    if not _validate_file_id(file_id):
        return ""
    fd = FILES_DIR / file_id
    for name in ("full.txt", "full.txt.gz"):
        path = fd / name
        if path.exists():
            try:
                if name.endswith(".gz"):
                    return gzip.decompress(path.read_bytes()).decode("utf-8")
                return path.read_text(encoding="utf-8")
            except OSError as e:
                raise StoreError(
                    f"Falha ao ler o texto do documento: {e}",
                    {"file_id": file_id, "path": str(path)},
                ) from e
    legacy = FILES_DIR / f"{file_id}.txt"
    if legacy.exists():
        try:
            return legacy.read_text(encoding="utf-8")
        except OSError as e:
            raise StoreError(
                f"Falha ao ler o texto do documento: {e}",
                {"file_id": file_id, "path": str(legacy)},
            ) from e
    return ""


def load_page_text(file_id: str, page_num: int, *, decompress: bool = True) -> str:
    """Carrega texto de uma página (1-based). Levanta StoreError em falha de I/O."""
    if not _validate_file_id(file_id):
        return ""
    fd = FILES_DIR / file_id / "pages"
    for suf in (".txt", ".txt.gz"):
        path = fd / f"{page_num:04d}{suf}"
        if path.exists():
            try:
                if suf == ".txt.gz":
                    return gzip.decompress(path.read_bytes()).decode("utf-8")
                return path.read_text(encoding="utf-8")
            except OSError as e:
                raise StoreError(
                    f"Falha ao ler a página do documento: {e}",
                    {"file_id": file_id, "page_num": page_num, "path": str(path)},
                ) from e
    return ""


def copy_pdf_to_store(file_id: str, pdf_path: Path) -> Path:
    """Copia o PDF para arquivos-processados/<id>.pdf; o serve serve-o para o report/document-view."""
    _ensure_store()
    if not _validate_file_id(file_id):
        raise ValueError(
            f"file_id inválido: esperado 16 caracteres hex, obtido {repr(file_id)[:50]}"
        )
    dest = FILES_DIR / f"{file_id}.pdf"
    shutil.copy2(pdf_path, dest)
    return dest


# ---------------------------------------------------------------------------
# Full-Text Search (SQLite FTS5)
# ---------------------------------------------------------------------------


def _fts_init(conn: sqlite3.Connection) -> None:
    # WAL mode: permite leituras concorrentes enquanto há um escritor activo,
    # evitando "database is locked" em cenários servidor + indexação simultânea.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS fts_idx USING fts5("
        "content, file_id, page_num, tokenize='unicode61')"
    )


def _fts_delete_file(file_id: str) -> None:
    conn = sqlite3.connect(FTS_DB, timeout=30)
    try:
        _fts_init(conn)
        conn.execute("DELETE FROM fts_idx WHERE file_id = ?", (file_id,))
        conn.commit()
    finally:
        conn.close()


def fts_index_file(file_id: str, page_texts: list[tuple[int, str]]) -> None:
    """Indexa texto por página no FTS."""
    _ensure_store()
    conn = sqlite3.connect(FTS_DB, timeout=30)
    try:
        _fts_init(conn)
        conn.execute("DELETE FROM fts_idx WHERE file_id = ?", (file_id,))
        for page_num, text in page_texts:
            if text and text.strip():
                conn.execute(
                    "INSERT INTO fts_idx(content, file_id, page_num) VALUES (?, ?, ?)",
                    (text.strip(), file_id, page_num),
                )
        conn.commit()
    finally:
        conn.close()


_FTS5_OPERATORS = frozenset({"AND", "OR", "NOT", "NEAR"})
_FTS5_SPECIAL_RE = re.compile(r"[.\-/:@#$%^&*()+={}\[\]|\\<>,;!?~`]")


def _fts_sanitize_query(query: str) -> str:
    """
    Sanitiza a query para FTS5: termos com caracteres especiais (pontos, hífens,
    barras, etc.) são envolvidos em aspas duplas para evitar OperationalError.
    Preserva operadores FTS5 (AND, OR, NOT, NEAR) e termos já entre aspas.
    Exemplos: '192.168' → '"192.168"', 'contrato AND nota' → 'contrato AND nota'
    """
    if not query or query.startswith('"'):
        return query
    tokens = query.split()
    result = []
    for token in tokens:
        if token.upper() in _FTS5_OPERATORS:
            result.append(token)
        elif token.startswith('"') and token.endswith('"'):
            result.append(token)
        elif _FTS5_SPECIAL_RE.search(token):
            safe = token.replace('"', "")
            result.append(f'"{safe}"')
        else:
            result.append(token)
    return " ".join(result)


def fts_search(query: str, limit: int = 200) -> list[tuple[str, int, str]]:
    """
    Busca full-text; retorna lista de (file_id, page_num, snippet).
    Chama fts_ensure_healthy() para garantir que o índice está operacional antes
    de executar a pesquisa (reconstrói automaticamente se corrompido).
    """
    global _fts_last_error
    q = query.strip()
    if not q:
        return []
    # Garantir índice saudável antes de pesquisar (reconstrói se corrompido)
    if not fts_ensure_healthy():
        _fts_last_error = (
            "O índice de busca (FTS) não está disponível e não foi possível reconstruí-lo. "
            "Execute 'pdfsearchable index-fts' para tentar reconstruir manualmente."
        )
        _store_logger.error("fts_search: índice FTS não está disponível — pesquisa ignorada.")
        return []
    _fts_last_error = ""
    # Sanitizar query para FTS5: termos com caracteres especiais (pontos, hífens,
    # barras, etc.) causam OperationalError — envolver cada termo em aspas duplas
    # para que o FTS5 os trate como frases literais. Preservar operadores FTS5
    # (AND, OR, NOT, NEAR) e termos já entre aspas.
    q = _fts_sanitize_query(q)
    conn = sqlite3.connect(FTS_DB, timeout=30)
    try:
        _fts_init(conn)
        # FTS5: termos separados por espaço = AND implícito
        cur = conn.execute(
            "SELECT file_id, page_num, snippet(fts_idx, 0, '<mark>', '</mark>', '…', 32) "
            "FROM fts_idx WHERE fts_idx MATCH ? LIMIT ?",
            (q, limit),
        )
        return list(cur.fetchall())
    except sqlite3.OperationalError as exc:
        _store_logger.warning("fts_search: OperationalError para query %r: %s", q, exc)
        return []
    finally:
        conn.close()


def _collect_page_tuples(fid: str, f: dict[str, Any]) -> list[tuple[int, str]]:
    """Carrega os textos de páginas de um arquivo para indexação FTS."""
    page_list = f.get("pages") or []
    page_tuples: list[tuple[int, str]] = []
    for p in page_list:
        n = p.get("n")
        if n is not None:
            pt = load_page_text(fid, n)
            page_tuples.append((n, pt))
    if not page_tuples:
        full = load_file_text(fid)
        if full:
            page_tuples = [(1, full)]
    return page_tuples


def fts_index_all_files() -> int:
    """
    Indexa FTS para todos os arquivos do índice numa única transação SQLite.
    Usado quando PDFSEARCHABLE_FTS_DEFERRED=1 ou via comando index-fts.
    Retorna o número de arquivos indexados.
    """
    idx = load_index()
    files = idx.get("files", [])
    _ensure_store()
    conn = sqlite3.connect(FTS_DB, timeout=30)
    count = 0
    try:
        _fts_init(conn)
        # Remover entradas órfãs: arquivos que já não existem no índice
        current_ids = {f["id"] for f in files if f.get("id")}
        if current_ids:
            placeholders = ",".join("?" * len(current_ids))
            conn.execute(
                f"DELETE FROM fts_idx WHERE file_id NOT IN ({placeholders})",  # nosec B608
                tuple(current_ids),
            )
        else:
            conn.execute("DELETE FROM fts_idx")
        for f in files:
            fid = f.get("id")
            if not fid:
                continue
            page_tuples = _collect_page_tuples(fid, f)
            if not page_tuples:
                continue
            conn.execute("DELETE FROM fts_idx WHERE file_id = ?", (fid,))
            conn.executemany(
                "INSERT INTO fts_idx(content, file_id, page_num) VALUES (?, ?, ?)",
                [
                    (text.strip(), fid, page_num)
                    for page_num, text in page_tuples
                    if text and text.strip()
                ],
            )
            count += 1
        conn.commit()
    finally:
        conn.close()
    return count


def fts_index_new_files() -> int:
    """
    Indexa no FTS apenas arquivos sem entradas FTS, numa única transação SQLite.
    Mais eficiente que fts_index_all_files() para atualizações incrementais após add.
    Retorna o número de arquivos novos indexados.
    """
    idx = load_index()
    files = idx.get("files", [])
    if not files:
        return 0
    _ensure_store()
    conn = sqlite3.connect(FTS_DB, timeout=30)
    count = 0
    try:
        _fts_init(conn)
        try:
            rows = conn.execute("SELECT DISTINCT file_id FROM fts_idx").fetchall()
            already_indexed: set[str] = {r[0] for r in rows}
        except sqlite3.DatabaseError as _db_err:
            _store_logger.warning(
                "FTS: falha ao ler file_ids indexados: %s — reindexação completa", _db_err
            )
            already_indexed = set()
        for f in files:
            fid = f.get("id")
            if not fid or fid in already_indexed:
                continue
            page_tuples = _collect_page_tuples(fid, f)
            if not page_tuples:
                continue
            conn.executemany(
                "INSERT INTO fts_idx(content, file_id, page_num) VALUES (?, ?, ?)",
                [
                    (text.strip(), fid, page_num)
                    for page_num, text in page_tuples
                    if text and text.strip()
                ],
            )
            count += 1
        if count:
            conn.commit()
    finally:
        conn.close()
    return count


def update_file_tags(file_id: str, tags: list[str]) -> bool:
    """Actualiza as tags de um documento. Retorna True se encontrou e actualizou."""
    with _index_lock:
        idx = load_index()
        for f in idx.get("files", []):
            if f.get("id") == file_id:
                cleaned = [t.strip() for t in tags if t.strip()]
                f["tags"] = cleaned if cleaned else None
                save_index(idx)
                return True
        return False


def update_file_subject(file_id: str, subject: str) -> bool:
    """Actualiza o assunto/subject de um documento. Retorna True se encontrou e actualizou."""
    with _index_lock:
        idx = load_index()
        for f in idx.get("files", []):
            if f.get("id") == file_id:
                meta = f.setdefault("metadata", {})
                meta["subject"] = subject.strip()
                save_index(idx)
                return True
        return False


def get_semantic_duplicate_groups(threshold: float = 0.92) -> list[list[dict]]:
    """
    Retorna grupos de documentos semanticamente semelhantes (cosine >= threshold).
    Requer embeddings previamente gerados. Retorna lista vazia se não disponível.
    """
    emb_db = STORE_DIR / "embeddings.sqlite"
    if not emb_db.exists():
        return []
    try:
        from pdfsearchable.semantic_search import find_semantic_duplicate_groups

        groups_ids = find_semantic_duplicate_groups(threshold)
        if not groups_ids:
            return []
        idx = load_index()
        files_by_id = {f.get("id", ""): f for f in idx.get("files", [])}
        return [
            [files_by_id[fid] for fid in group if fid in files_by_id]
            for group in groups_ids
            if len(group) >= 2
        ]
    except Exception as exc:
        _store_logger.debug("get_semantic_duplicate_groups falhou: %s", exc)
        return []


def fts_ensure_healthy() -> bool:
    """
    Verifica se o índice FTS está operacional executando uma query de teste.
    Se estiver corrompido ou vazio, reconstrói automaticamente.
    Retorna True se saudável (ou reconstruído com sucesso), False em caso de falha.
    Actualiza _fts_last_error com mensagem amigável em caso de falha.
    """
    global _fts_last_error
    try:
        conn = sqlite3.connect(FTS_DB, timeout=10)
        try:
            _fts_init(conn)
            conn.execute("SELECT count(*) FROM fts_idx").fetchone()
        finally:
            conn.close()
        _fts_last_error = ""
        return True
    except sqlite3.DatabaseError:
        _store_logger.warning("fts_ensure_healthy: índice FTS corrompido — reconstruindo…")
        try:
            if FTS_DB.exists():
                FTS_DB.unlink()
            fts_index_all_files()
            _store_logger.info("fts_ensure_healthy: reconstrução concluída.")
            _fts_last_error = ""
            return True
        except Exception as exc:
            _fts_last_error = (
                f"Falha ao reconstruir o índice de busca: {exc}. "
                "Execute 'pdfsearchable index-fts' para tentar novamente."
            )
            _store_logger.error("fts_ensure_healthy: falha na reconstrução: %s", exc)
            return False


# Cache de resultados do dashboard para escalar a milhares de docs.
# Chave: mtime do index.json. TTL: 30s. Invalidação automática em writes do índice.
_dash_stats_cache: dict[str, Any] = {}
_dash_stats_cache_mtime: float = 0.0
_dash_stats_cache_ts: float = 0.0
_dash_stats_lock = threading.Lock()
_DASH_STATS_TTL_SECONDS = 30.0


def invalidate_dashboard_stats_cache() -> None:
    """Invalida o cache do dashboard (chamado quando o índice muda)."""
    global _dash_stats_cache, _dash_stats_cache_mtime, _dash_stats_cache_ts
    with _dash_stats_lock:
        _dash_stats_cache = {}
        _dash_stats_cache_mtime = 0.0
        _dash_stats_cache_ts = 0.0


def compute_dashboard_stats() -> dict[str, Any]:
    """
    Calcula agregados ricos para o dashboard a partir do índice.

    Retorna dict com KPIs, distribuições, top-N entidades, alertas, e
    contagens para os campos extraídos (outline, hyperlinks, fonts, XMP, etc.).
    Todas as operações são O(n_files) — adequado a índices até ~50k docs.

    Cache: resultado memoizado por mtime do index.json e TTL de 30s.
    Em escalas grandes (>10k docs), uma chamada custa ~50-200ms; o cache
    evita recomputar a cada requisição do dashboard.
    """
    global _dash_stats_cache, _dash_stats_cache_mtime, _dash_stats_cache_ts
    # Verificar cache antes de carregar índice
    try:
        current_mtime = META_FILE.stat().st_mtime if META_FILE.exists() else 0.0
    except OSError:
        current_mtime = 0.0
    now = time.time()
    with _dash_stats_lock:
        if (
            _dash_stats_cache
            and _dash_stats_cache_mtime == current_mtime
            and (now - _dash_stats_cache_ts) < _DASH_STATS_TTL_SECONDS
        ):
            return copy.deepcopy(_dash_stats_cache)
    try:
        idx = load_index()
    except Exception as exc:
        _store_logger.error("compute_dashboard_stats: falha ao carregar índice: %s", exc)
        return {"error": str(exc)[:200]}

    files: list[dict[str, Any]] = idx.get("files", []) or []
    n = len(files)

    stats: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_docs": n,
    }

    if n == 0:
        return stats

    # KPIs básicos
    total_pages = 0
    total_size = 0
    total_words = 0
    doc_types: dict[str, int] = {}
    languages: dict[str, int] = {}
    sizes: list[int] = []
    pages_list: list[int] = []

    # OCR quality
    ocr_pcts: list[float] = []
    ocr_confs: list[float] = []
    weighted_ocr_sum = 0.0
    weighted_ocr_weight = 0

    # Entity totals
    entity_fields = [
        "identified_emails",
        "identified_cpfs",
        "identified_cnpjs",
        "identified_ips",
        "identified_addresses",
        "identified_phones",
        "identified_locations",
        "identified_dates",
        "identified_urls",
        "identified_domains",
        "identified_ceps",
        "identified_processos",
        "identified_placas",
        "identified_rgs",
        "identified_protocolos",
        "identified_hashes",
        "identified_coordenadas",
        "identified_timestamps",
        "identified_leis",
        "parties",
    ]
    entity_totals: dict[str, int] = {k: 0 for k in entity_fields}
    top_parties: dict[str, int] = {}
    top_locations: dict[str, int] = {}
    top_domains: dict[str, int] = {}

    # Extended-field counts (Fase 1 desta sessão)
    docs_with_outline = 0
    docs_with_hyperlinks = 0
    docs_with_attached = 0
    docs_with_signatures = 0
    docs_with_forms = 0
    docs_with_annotations = 0
    total_outline_entries = 0
    total_hyperlinks = 0
    total_attached = 0
    creator_tools: dict[str, int] = {}
    unique_fonts: set[str] = set()

    # Time series
    by_day: dict[str, int] = {}
    latest_indexed = ""

    # Alertas
    alerts_low_ocr: list[dict[str, Any]] = []
    alerts_empty_text: list[dict[str, Any]] = []
    alerts_encrypted: list[dict[str, Any]] = []
    alerts_high_confidentiality: list[dict[str, Any]] = []

    for f in files:
        np_ = int(f.get("num_pages", 0) or 0)
        sz = int(f.get("file_size", 0) or 0)
        wc = int(f.get("word_count", 0) or 0)
        total_pages += np_
        total_size += sz
        total_words += wc
        pages_list.append(np_)
        sizes.append(sz)

        dtype = f.get("doc_type") or "documento"
        doc_types[dtype] = doc_types.get(dtype, 0) + 1

        lang = f.get("language")
        if lang:
            languages[lang] = languages.get(lang, 0) + 1

        # OCR
        ocr_pct = f.get("ocr_percentage")
        if ocr_pct is not None:
            try:
                pct = float(ocr_pct)
                ocr_pcts.append(pct)
                if np_ > 0:
                    weighted_ocr_sum += pct * np_
                    weighted_ocr_weight += np_
            except (TypeError, ValueError):
                pass
        ocr_conf = f.get("ocr_avg_confidence")
        if ocr_conf is not None:
            try:
                cf = float(ocr_conf)
                ocr_confs.append(cf)
                if cf < 70 and cf >= 0:
                    alerts_low_ocr.append(
                        {
                            "id": f.get("id", ""),
                            "name": f.get("name", ""),
                            "confidence": round(cf, 1),
                        }
                    )
            except (TypeError, ValueError):
                pass

        # OCR pendente: nenhuma palavra extraída e praticamente nenhum char no
        # texto salvo → OCR falhou ou não foi executado. Semântica mais
        # precisa que "sem texto" (documentos podem ter poucos chars de ruído).
        text_chars = 0
        try:
            tlen = f.get("text_chars")
            if tlen is None:
                md = f.get("metadata") or {}
                if isinstance(md, dict):
                    tlen = md.get("text_chars")
            if tlen is None:
                tlen = f.get("char_count")
            if tlen is not None:
                text_chars = int(tlen)
        except (TypeError, ValueError):
            text_chars = 0
        if wc == 0 and np_ > 0 and text_chars < 200:
            alerts_empty_text.append(
                {
                    "id": f.get("id", ""),
                    "name": f.get("name", ""),
                    "pages": np_,
                    "text_chars": text_chars,
                }
            )

        # Confidentiality
        conf_level = (f.get("confidentiality") or "").lower()
        if conf_level in ("alta", "high", "confidencial", "secreto", "ultrassecreto"):
            alerts_high_confidentiality.append(
                {
                    "id": f.get("id", ""),
                    "name": f.get("name", ""),
                    "level": conf_level,
                }
            )

        # Entities
        for ef in entity_fields:
            v = f.get(ef)
            if isinstance(v, list):
                entity_totals[ef] += len(v)
        # Top-N entities
        for p in (f.get("parties") or [])[:20]:
            if isinstance(p, str) and p.strip():
                key = p.strip()[:80]
                top_parties[key] = top_parties.get(key, 0) + 1
        for loc in (f.get("identified_locations") or [])[:20]:
            if isinstance(loc, str) and loc.strip():
                key = loc.strip()[:80]
                top_locations[key] = top_locations.get(key, 0) + 1
        for dom in (f.get("identified_domains") or [])[:20]:
            if isinstance(dom, str) and dom.strip():
                key = dom.strip().lower()[:80]
                top_domains[key] = top_domains.get(key, 0) + 1

        # Extended fields
        ext = (f.get("metadata") or {}).get("extended") or {}
        if ext:
            outline = ext.get("outline") or []
            if outline:
                docs_with_outline += 1
                total_outline_entries += len(outline)
            hlinks = ext.get("hyperlinks") or []
            if hlinks:
                docs_with_hyperlinks += 1
                total_hyperlinks += len(hlinks)
            att = ext.get("attached_files") or []
            if att:
                docs_with_attached += 1
                total_attached += len(att)
            if ext.get("signatures"):
                docs_with_signatures += 1
            if ext.get("form_fields"):
                docs_with_forms += 1
            if ext.get("annotations"):
                docs_with_annotations += 1
            xmp = ext.get("xmp") or {}
            ct = xmp.get("xmp_creator_tool") or xmp.get("pdf_producer")
            if ct:
                key = str(ct)[:60]
                creator_tools[key] = creator_tools.get(key, 0) + 1
            for fnt in ext.get("fonts") or []:
                bf = fnt.get("basefont") if isinstance(fnt, dict) else None
                if bf:
                    unique_fonts.add(str(bf)[:80])

        # Encrypted detection (from metadata)
        mraw = f.get("metadata") or {}
        if mraw.get("is_encrypted"):
            alerts_encrypted.append(
                {
                    "id": f.get("id", ""),
                    "name": f.get("name", ""),
                }
            )

        # Time series
        iat = (f.get("indexed_at") or "")[:10]  # YYYY-MM-DD
        if iat:
            by_day[iat] = by_day.get(iat, 0) + 1
            if iat > latest_indexed:
                latest_indexed = iat

    # Size stats
    sizes_sorted = sorted(sizes)
    median_size = sizes_sorted[n // 2] if sizes_sorted else 0
    avg_size = total_size // n if n else 0

    # OCR aggregates
    weighted_ocr_pct = (weighted_ocr_sum / weighted_ocr_weight) if weighted_ocr_weight else 0
    avg_ocr_conf = (sum(ocr_confs) / len(ocr_confs)) if ocr_confs else None
    min_ocr_conf = min(ocr_confs) if ocr_confs else None

    # Top-N helper
    def _topn(d: dict[str, int], n_: int = 10) -> list[dict[str, Any]]:
        return [
            {"label": k, "count": v}
            for k, v in sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:n_]
        ]

    # Histograma de tamanhos (faixas)
    size_buckets = {"<100KB": 0, "100KB-1MB": 0, "1-10MB": 0, "10-100MB": 0, ">100MB": 0}
    for sz in sizes:
        if sz < 100_000:
            size_buckets["<100KB"] += 1
        elif sz < 1_000_000:
            size_buckets["100KB-1MB"] += 1
        elif sz < 10_000_000:
            size_buckets["1-10MB"] += 1
        elif sz < 100_000_000:
            size_buckets["10-100MB"] += 1
        else:
            size_buckets[">100MB"] += 1

    # Embeddings (semantic search)
    embeddings_count = 0
    chunks_count = 0
    try:
        emb_db = STORE_DIR / "embeddings.sqlite"
        if emb_db.exists():
            conn = sqlite3.connect(emb_db, timeout=5)
            try:
                row = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()
                embeddings_count = int(row[0]) if row else 0
                try:
                    row = conn.execute("SELECT COUNT(*) FROM embeddings_chunks").fetchone()
                    chunks_count = int(row[0]) if row else 0
                except sqlite3.Error:
                    chunks_count = 0
            finally:
                conn.close()
    except sqlite3.Error as _e:
        _store_logger.debug("compute_dashboard_stats: falha ao contar embeddings: %s", _e)

    stats.update(
        {
            "total_pages": total_pages,
            "total_size": total_size,
            "total_words": total_words,
            "avg_size": avg_size,
            "median_size": median_size,
            "avg_pages": round(total_pages / n, 1) if n else 0,
            "doc_types": _topn(doc_types, 50),
            "languages": _topn(languages, 20),
            "latest_indexed": latest_indexed,
            "ocr": {
                "weighted_pct": round(weighted_ocr_pct, 1),
                "avg_confidence": round(avg_ocr_conf, 1) if avg_ocr_conf is not None else None,
                "min_confidence": round(min_ocr_conf, 1) if min_ocr_conf is not None else None,
                "docs_with_ocr": sum(1 for p in ocr_pcts if p > 0),
            },
            "entities": {
                "totals": entity_totals,
                "sum": sum(entity_totals.values()),
                "top_parties": _topn(top_parties, 10),
                "top_locations": _topn(top_locations, 10),
                "top_domains": _topn(top_domains, 10),
            },
            "extended": {
                "docs_with_outline": docs_with_outline,
                "docs_with_hyperlinks": docs_with_hyperlinks,
                "docs_with_attached": docs_with_attached,
                "docs_with_signatures": docs_with_signatures,
                "docs_with_forms": docs_with_forms,
                "docs_with_annotations": docs_with_annotations,
                "total_outline_entries": total_outline_entries,
                "total_hyperlinks": total_hyperlinks,
                "total_attached": total_attached,
                "unique_fonts": len(unique_fonts),
                "top_creators": _topn(creator_tools, 10),
            },
            "size_histogram": [{"bucket": k, "count": v} for k, v in size_buckets.items()],
            "indexing_timeline": [{"date": k, "count": v} for k, v in sorted(by_day.items())][-90:],
            "alerts": {
                "low_ocr_confidence": alerts_low_ocr[:20],
                "empty_text": alerts_empty_text[:20],
                "encrypted": alerts_encrypted[:20],
                "high_confidentiality": alerts_high_confidentiality[:20],
                "total": (
                    len(alerts_low_ocr)
                    + len(alerts_empty_text)
                    + len(alerts_encrypted)
                    + len(alerts_high_confidentiality)
                ),
            },
            "semantic": {
                "doc_embeddings": embeddings_count,
                "page_chunks": chunks_count,
            },
        }
    )

    # Memoizar resultado para próximas chamadas dentro do TTL
    with _dash_stats_lock:
        _dash_stats_cache = copy.deepcopy(stats)
        _dash_stats_cache_mtime = current_mtime
        _dash_stats_cache_ts = now
    return stats
