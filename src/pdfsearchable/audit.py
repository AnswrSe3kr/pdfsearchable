"""
Sistema de log e auditoria integrado.
Registra ações do usuário e eventos do processamento em arquivo e console.
Escrita segura (falha na auditoria não derruba o app), rotação opcional e log configurável por env.
"""

import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# cwd capturado no import-time como fallback robusto se o cwd em runtime
# desaparecer (ex.: SSD externo desmontado, diretório removido durante OCR longo).
try:
    _IMPORT_CWD: Path | None = Path.cwd()
except (FileNotFoundError, OSError):
    _IMPORT_CWD = None


# Paths calculados em runtime (não em import-time) para suportar mudanças de cwd
# (ex.: testes com monkeypatch.chdir, CLI invocada de directórios diferentes).
def _audit_dir() -> Path:
    try:
        return Path.cwd() / ".pdfsearchable"
    except (FileNotFoundError, OSError):
        # cwd inacessível (ex.: SSD desmontado durante run longo) — usa fallback
        if _IMPORT_CWD is not None:
            return _IMPORT_CWD / ".pdfsearchable"
        # último recurso: temp dir do sistema
        import tempfile

        return Path(tempfile.gettempdir()) / "pdfsearchable-fallback"


def _audit_file() -> Path:
    return _audit_dir() / "audit.jsonl"


def _log_file() -> Path:
    return _audit_dir() / "pdfsearchable.log"


# Compatibilidade retroactiva: variáveis de módulo como aliases lazy
# (usadas em monkeypatch de testes — não remover)
AUDIT_DIR = Path.cwd() / ".pdfsearchable"
AUDIT_FILE = AUDIT_DIR / "audit.jsonl"
LOG_FILE = AUDIT_DIR / "pdfsearchable.log"

# Path fixado na primeira chamada a audit() para evitar dispersão de logs quando o
# cwd muda após a primeira escrita (ex.: servidor HTTP que troca de diretório).
# Reset to None para suportar monkeypatch.chdir em testes (ver _reset_audit_path_for_tests).
_audit_fixed_file: Path | None = None
_audit_fixed_lock = threading.Lock()


def _get_audit_file() -> Path:
    """
    Retorna o path do audit.jsonl, fixado na primeira chamada.
    Uma vez fixado, o mesmo arquivo é sempre usado independentemente do cwd atual.
    Isso evita que logs de uma sessão fiquem dispersos em vários arquivos quando
    o cwd muda (ex.: durante serving HTTP ou testes consecutivos).
    """
    global _audit_fixed_file
    if _audit_fixed_file is not None:
        return _audit_fixed_file
    with _audit_fixed_lock:
        if _audit_fixed_file is None:
            _audit_fixed_file = _audit_file()
    return _audit_fixed_file


def _reset_audit_path_for_tests() -> None:
    """
    Reset do path fixado — usar APENAS em testes (monkeypatch.chdir).
    Permite que cada teste escreva no diretório correcto após chdir.
    """
    global _audit_fixed_file
    with _audit_fixed_lock:
        _audit_fixed_file = None


# Rotação do audit: se AUDIT_MAX_BYTES > 0 e o arquivo ultrapassar, mantém as últimas AUDIT_MAX_LINES
AUDIT_MAX_BYTES = int(os.environ.get("PDFSEARCHABLE_AUDIT_MAX_BYTES", "0"))
AUDIT_MAX_LINES = int(os.environ.get("PDFSEARCHABLE_AUDIT_MAX_LINES", "50000"))

# Log: nível (DEBUG, INFO, WARNING, ERROR) e se envia também para o console
LOG_LEVEL = os.environ.get("PDFSEARCHABLE_LOG_LEVEL", "INFO").upper()
LOG_CONSOLE = os.environ.get("PDFSEARCHABLE_LOG_CONSOLE", "").strip().lower() in (
    "1",
    "true",
    "yes",
)
LOG_ROTATE_BYTES = int(os.environ.get("PDFSEARCHABLE_LOG_MAX_BYTES", str(2 * 1024 * 1024)))  # 2 MB
LOG_BACKUP_COUNT = int(os.environ.get("PDFSEARCHABLE_LOG_BACKUP_COUNT", "3"))

_configured_loggers: set[str] = set()
_fallback_audit_logger: logging.Logger | None = None

# Lock para serializar escritas concorrentes no audit.jsonl (ThreadingHTTPServer + indexação).
_audit_lock = threading.Lock()


def _ensure_audit_dir() -> Path:
    """Garante que o diretório de auditoria existe (usa cwd em runtime)."""
    d = _audit_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _fallback_audit_log() -> logging.Logger:
    """Logger de fallback quando a escrita no audit.jsonl falha (evita perder evento)."""
    global _fallback_audit_logger
    if _fallback_audit_logger is None:
        _fallback_audit_logger = logging.getLogger("pdfsearchable.audit.fallback")
        if not _fallback_audit_logger.handlers:
            h = logging.StreamHandler(sys.stderr)
            h.setFormatter(logging.Formatter("%(asctime)s [audit fallback] %(message)s"))
            _fallback_audit_logger.addHandler(h)
            _fallback_audit_logger.setLevel(logging.WARNING)
    return _fallback_audit_logger


def _maybe_rotate_audit(af: Path) -> None:
    """
    Se audit.jsonl ultrapassar AUDIT_MAX_BYTES, mantém apenas as últimas AUDIT_MAX_LINES.
    Usa escrita atômica (temp + rename) para evitar corrupção se o processo for morto
    a meio da rotação. Deve ser chamado dentro do _audit_lock.
    """
    if AUDIT_MAX_BYTES <= 0 or not af.exists():
        return
    try:
        if af.stat().st_size < AUDIT_MAX_BYTES:
            return
    except OSError:
        return
    lines: list[str] = []
    try:
        with open(af, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)
    except OSError:
        return
    if len(lines) <= AUDIT_MAX_LINES:
        return
    keep = lines[-AUDIT_MAX_LINES:]
    try:
        # Escrita atômica: temp file + rename (evita corrupção em crash durante rotação)
        tmp = af.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for line in keep:
                f.write(line + "\n")
        tmp.replace(af)
    except OSError as _rot_err:
        # Rotação falhou: o arquivo vai crescer além do limite mas os dados estão intactos.
        # Registar no stderr para que o utilizador possa investigar.
        import sys as _sys

        _sys.stderr.write(
            f"[pdfsearchable] Aviso: não foi possível rodar o arquivo de auditoria "
            f"'{af}': {_rot_err}. "
            f"O arquivo continuará a crescer. Verifique as permissões do directório.\n"
        )


def audit(action: str, details: dict[str, Any] | None = None, level: str = "info") -> None:
    """
    Registra uma ação na auditoria (arquivo JSONL). Thread-safe.
    Cada linha é um JSON com: timestamp, action, details, level.
    O path do arquivo é fixado na primeira chamada (_get_audit_file) para evitar
    dispersão de logs quando o cwd muda durante a sessão.
    Em falha de I/O, registra no stderr via logger de fallback sem propagar exceção.
    """
    af = _get_audit_file()
    with _audit_lock:
        _ensure_audit_dir()
        # Rotação dentro do lock para evitar duas threads rotacionando simultaneamente
        _maybe_rotate_audit(af)
        entry = {
            "timestamp": _timestamp(),
            "action": action,
            "details": details or {},
            "level": level,
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        try:
            with open(af, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            _fallback_audit_log().warning(
                "Auditoria: falha ao escrever em %s: %s | entry: %s", af, e, line.strip()
            )


def get_logger(name: str) -> logging.Logger:
    """
    Retorna um logger configurado para o projeto:
    - Arquivo em .pdfsearchable/pdfsearchable.log com rotação (tamanho e backup por env).
    - Console opcional (PDFSEARCHABLE_LOG_CONSOLE=1).
    - Nível por PDFSEARCHABLE_LOG_LEVEL (DEBUG, INFO, WARNING, ERROR).
    - Re-configurável: se o handler de arquivo está num directório diferente do cwd actual,
      reconfigura (suporta mudanças de cwd entre testes e invocações).
    """
    logger = logging.getLogger(name)
    lf = _log_file()

    # Verificar se o logger já está configurado para o directório correcto
    if name in _configured_loggers:
        # Verificar se o arquivo de log ainda aponta para o cwd actual
        for h in logger.handlers:
            if isinstance(h, RotatingFileHandler) and Path(h.baseFilename) != lf:
                # cwd mudou (ex.: monkeypatch em testes) — reconfigurar
                logger.handlers.clear()
                _configured_loggers.discard(name)
                break
        else:
            return logger

    _ensure_audit_dir()
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    try:
        fh = RotatingFileHandler(
            lf,
            maxBytes=LOG_ROTATE_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        # Fallback para stderr se não for possível criar o arquivo de log
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(level)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    if LOG_CONSOLE:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(level)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    _configured_loggers.add(name)
    return logger


def read_audit_trail(limit: int = 100) -> list[dict]:
    """Lê as últimas N entradas do arquivo de auditoria (ordem reversa, mais recente primeiro)."""
    af = _get_audit_file()
    if not af.exists():
        return []
    lines: list[str] = []
    try:
        with open(af, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)
    except OSError:
        return []
    out: list[dict] = []
    for line in reversed(lines[-limit:]):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
