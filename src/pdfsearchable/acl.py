"""
Per-document Access Control List.

Hoje o app é tudo-ou-nada (auth básica global). Este módulo adiciona
ACLs por documento: user → set de file_ids permitidos. Se o user não
tem entrada, aplica política padrão (`default_allow_all=True` por
compatibilidade).

Persistido em `.pdfsearchable/acl.json`:

    {
        "default_allow_all": true,
        "users": {
            "alice": {"allowed": ["abc123...", "def456..."], "deny": []},
            "bob": {"allowed": ["*"], "deny": ["sensitive_id"]}
        },
        "groups": {}
    }

API:
    can_read(user, file_id) -> bool
    grant(user, file_id)
    revoke(user, file_id)
    set_default_allow_all(flag)
    filter_readable(user, file_ids) -> list
    audit_read(user, file_id, *, ip, endpoint)  -> append to .pdfsearchable/read_audit.jsonl
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.RLock()
_acl_cache: dict[str, Any] | None = None


def _acl_path() -> Path:
    return Path.cwd() / ".pdfsearchable" / "acl.json"


def _audit_path() -> Path:
    return Path.cwd() / ".pdfsearchable" / "read_audit.jsonl"


def _load() -> dict[str, Any]:
    global _acl_cache
    if _acl_cache is not None:
        return _acl_cache
    p = _acl_path()
    if not p.exists():
        _acl_cache = {"default_allow_all": True, "users": {}, "groups": {}}
        return _acl_cache
    try:
        _acl_cache = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        _acl_cache = {"default_allow_all": True, "users": {}, "groups": {}}
    return _acl_cache


def _save(data: dict[str, Any]) -> None:
    global _acl_cache
    p = _acl_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    _acl_cache = data


def can_read(user: str | None, file_id: str) -> bool:
    """Autoriza ou nega leitura."""
    if not file_id:
        return False
    with _lock:
        data = _load()
        default_allow = bool(data.get("default_allow_all", True))
        users = data.get("users", {})

        if not user:
            return default_allow

        u = users.get(user)
        if u is None:
            return default_allow

        deny = set(u.get("deny") or [])
        if file_id in deny:
            return False

        allowed = u.get("allowed") or []
        if "*" in allowed:
            return True
        return file_id in set(allowed)


def grant(user: str, file_id: str) -> None:
    with _lock:
        data = _load()
        users = data.setdefault("users", {})
        u = users.setdefault(user, {"allowed": [], "deny": []})
        if file_id not in u["allowed"]:
            u["allowed"].append(file_id)
        if file_id in (u.get("deny") or []):
            u["deny"].remove(file_id)
        _save(data)


def revoke(user: str, file_id: str) -> None:
    with _lock:
        data = _load()
        users = data.setdefault("users", {})
        u = users.setdefault(user, {"allowed": [], "deny": []})
        if file_id in u.get("allowed", []):
            u["allowed"].remove(file_id)
        if file_id not in u.get("deny", []):
            u.setdefault("deny", []).append(file_id)
        _save(data)


def set_default_allow_all(flag: bool) -> None:
    with _lock:
        data = _load()
        data["default_allow_all"] = bool(flag)
        _save(data)


def filter_readable(user: str | None, file_ids: list[str]) -> list[str]:
    return [fid for fid in file_ids if can_read(user, fid)]


def audit_read(
    user: str | None,
    file_id: str,
    *,
    ip: str = "",
    endpoint: str = "",
    allowed: bool = True,
) -> None:
    """Registra leitura em audit log JSONL (append-only)."""
    try:
        p = _audit_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "user": user or "anonymous",
            "file_id": file_id,
            "allowed": allowed,
            "ip": ip,
            "endpoint": endpoint,
        }
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_audit_log(*, limit: int = 100) -> list[dict[str, Any]]:
    """Lê últimas N entradas do audit log de leituras."""
    p = _audit_path()
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, Any]] = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def invalidate_cache() -> None:
    """Limpa cache de ACL (usar após modificações externas)."""
    global _acl_cache
    with _lock:
        _acl_cache = None


__all__ = [
    "can_read",
    "grant",
    "revoke",
    "set_default_allow_all",
    "filter_readable",
    "audit_read",
    "read_audit_log",
    "invalidate_cache",
]
