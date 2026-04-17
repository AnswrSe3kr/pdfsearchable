"""
Saved searches + alertas.

Persiste consultas nomeadas em .pdfsearchable/saved_searches.json e
mantém um estado de "última execução" (timestamp + set de file_ids
já vistos). Quando re-executada, reporta apenas novos resultados que
não estavam na execução anterior — base para alertas/notificações.

API:
    save_search(name, query, *, options) -> dict
    list_saved_searches() -> list[dict]
    delete_saved_search(name) -> bool
    run_saved_search(name) -> dict with {new_results, total_results}
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.RLock()


def _saved_file() -> Path:
    return Path.cwd() / ".pdfsearchable" / "saved_searches.json"


def _load() -> dict[str, Any]:
    f = _saved_file()
    if not f.exists():
        return {"searches": {}}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"searches": {}}


def _save(data: dict[str, Any]) -> None:
    f = _saved_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)


def save_search(
    name: str,
    query: str,
    *,
    options: dict[str, Any] | None = None,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Cria ou actualiza uma busca salva."""
    with _lock:
        data = _load()
        if name in data["searches"] and not overwrite:
            raise ValueError(f"Busca '{name}' já existe. Use overwrite=True para sobrescrever.")
        entry = {
            "name": name,
            "query": query,
            "options": options or {},
            "created_at": data["searches"].get(name, {}).get("created_at", time.time()),
            "updated_at": time.time(),
            "last_run_at": None,
            "last_seen_ids": [],
        }
        data["searches"][name] = entry
        _save(data)
        return entry


def list_saved_searches() -> list[dict[str, Any]]:
    with _lock:
        data = _load()
        return sorted(
            list(data["searches"].values()),
            key=lambda x: x.get("updated_at", 0),
            reverse=True,
        )


def get_saved_search(name: str) -> dict[str, Any] | None:
    with _lock:
        data = _load()
        return data["searches"].get(name)


def delete_saved_search(name: str) -> bool:
    with _lock:
        data = _load()
        if name in data["searches"]:
            del data["searches"][name]
            _save(data)
            return True
        return False


def run_saved_search(name: str, *, executor=None) -> dict[str, Any]:
    """
    Executa uma busca salva e reporta resultados novos (não vistos na
    última execução).

    Args:
        executor: callable opcional que recebe (query, options) e retorna
                  list[dict] com chaves file_id e page. Se None, usa
                  hybrid_search.

    Returns:
        {
            "name": str,
            "query": str,
            "total_results": int,
            "new_results": list[dict],
            "new_count": int,
        }
    """
    with _lock:
        entry = get_saved_search(name)
        if not entry:
            return {"error": f"busca '{name}' não existe"}

        query = entry["query"]
        options = entry.get("options") or {}

        if executor is None:
            from pdfsearchable.hybrid_search import hybrid_search as _hs
            results = _hs(
                query,
                top_k=options.get("top_k", 50),
                enable_semantic=options.get("enable_semantic"),
            )
        else:
            results = executor(query, options)

        # Identificar novos: chave (file_id, page)
        def _key(r):
            return f"{r.get('file_id', '')}:{r.get('page', 0)}"

        last_seen = set(entry.get("last_seen_ids") or [])
        current_keys = {_key(r) for r in results}
        new_keys = current_keys - last_seen
        new_results = [r for r in results if _key(r) in new_keys]

        # Atualizar estado
        entry["last_run_at"] = time.time()
        entry["last_seen_ids"] = sorted(current_keys)
        data = _load()
        data["searches"][name] = entry
        _save(data)

        return {
            "name": name,
            "query": query,
            "total_results": len(results),
            "new_count": len(new_results),
            "new_results": new_results,
            "results": results,
        }


def run_all_for_alerts() -> list[dict[str, Any]]:
    """Executa todas as saved searches e retorna só as que têm new_count > 0."""
    alerts = []
    for entry in list_saved_searches():
        try:
            r = run_saved_search(entry["name"])
            if r.get("new_count", 0) > 0:
                alerts.append(r)
        except Exception:
            pass
    return alerts


__all__ = [
    "save_search",
    "list_saved_searches",
    "get_saved_search",
    "delete_saved_search",
    "run_saved_search",
    "run_all_for_alerts",
]
