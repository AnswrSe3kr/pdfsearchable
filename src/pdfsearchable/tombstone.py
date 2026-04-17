"""
Tombstone / undo de remoções.

Quando o utilizador remove um documento, em vez de deletar fisicamente
o registro, movemos para `.pdfsearchable/tombstones/<file_id>.json` com
timestamp de remoção. Um job de housekeeping apaga fisicamente
tombstones mais antigos que TTL (default 24h).

API:
    tombstone_add(file_id, metadata)
    tombstone_restore(file_id) -> dict | None
    tombstone_list() -> list[dict]
    tombstone_cleanup(*, ttl_hours=24) -> int  # número de tombstones purgados
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_DEFAULT_TTL_HOURS = 24.0


def _tomb_dir() -> Path:
    return Path.cwd() / ".pdfsearchable" / "tombstones"


def tombstone_add(file_id: str, metadata: dict[str, Any]) -> Path:
    with _lock:
        d = _tomb_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{file_id}.json"
        payload = {
            "file_id": file_id,
            "metadata": metadata,
            "deleted_at": time.time(),
            "deleted_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def tombstone_restore(file_id: str) -> dict[str, Any] | None:
    """Recupera metadados; chamador é responsável por re-inserir no índice."""
    with _lock:
        path = _tomb_dir() / f"{file_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        # Apaga tombstone após restore
        try:
            path.unlink()
        except Exception:
            pass
        return data


def tombstone_list() -> list[dict[str, Any]]:
    d = _tomb_dir()
    if not d.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in d.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            data["_path"] = str(p)
            out.append(data)
        except Exception:
            pass
    return sorted(out, key=lambda x: x.get("deleted_at", 0), reverse=True)


def tombstone_cleanup(*, ttl_hours: float = _DEFAULT_TTL_HOURS) -> int:
    """Apaga tombstones mais antigos que ttl_hours. Retorna nº removidos."""
    cutoff = time.time() - (ttl_hours * 3600)
    removed = 0
    d = _tomb_dir()
    if not d.exists():
        return 0
    with _lock:
        for p in d.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if data.get("deleted_at", 0) < cutoff:
                    p.unlink()
                    removed += 1
            except Exception:
                pass
    return removed


def tombstone_get(file_id: str) -> dict[str, Any] | None:
    path = _tomb_dir() / f"{file_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


__all__ = [
    "tombstone_add",
    "tombstone_restore",
    "tombstone_list",
    "tombstone_cleanup",
    "tombstone_get",
]
