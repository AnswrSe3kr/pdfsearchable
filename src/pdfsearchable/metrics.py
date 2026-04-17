"""
Métricas operacionais em formato Prometheus text-exposition.

Sem dependência do cliente prometheus_client — geramos o texto
manualmente (formato bem definido) para manter deps mínimas.

Métricas expostas:
    - pdfsearchable_docs_total (gauge)
    - pdfsearchable_pages_total (gauge)
    - pdfsearchable_fts_search_seconds (histogram-like)
    - pdfsearchable_ollama_requests_total{result} (counter)
    - pdfsearchable_cache_hits_total{cache} (counter)
    - pdfsearchable_http_requests_total{endpoint,status} (counter)

API:
    record_ollama_request(result)
    record_cache_hit(cache, hit=True)
    record_http(endpoint, status)
    record_search_duration(seconds)
    render_metrics() -> str (formato Prometheus)
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any

_lock = threading.Lock()
_counters: dict[str, int] = defaultdict(int)
_histograms: dict[str, list[float]] = defaultdict(list)
_start_time = time.time()


def record_ollama_request(result: str = "ok") -> None:
    """result: ok|error|timeout|unreachable"""
    with _lock:
        _counters[f"pdfsearchable_ollama_requests_total{{result=\"{result}\"}}"] += 1


def record_cache_hit(cache: str, hit: bool = True) -> None:
    """cache: dashboard|ocr|ollama"""
    kind = "hit" if hit else "miss"
    with _lock:
        _counters[f"pdfsearchable_cache_hits_total{{cache=\"{cache}\",kind=\"{kind}\"}}"] += 1


def record_http(endpoint: str, status: int) -> None:
    with _lock:
        _counters[
            f'pdfsearchable_http_requests_total{{endpoint="{endpoint}",status="{status}"}}'
        ] += 1


def record_search_duration(seconds: float) -> None:
    with _lock:
        _histograms["pdfsearchable_fts_search_seconds"].append(seconds)
        # Limita histórico a 1000 amostras (rolling)
        if len(_histograms["pdfsearchable_fts_search_seconds"]) > 1000:
            _histograms["pdfsearchable_fts_search_seconds"] = _histograms[
                "pdfsearchable_fts_search_seconds"
            ][-1000:]


def _histogram_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0, "sum": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0}
    sorted_v = sorted(values)
    n = len(sorted_v)
    return {
        "count": n,
        "sum": sum(sorted_v),
        "p50": sorted_v[int(n * 0.5)],
        "p95": sorted_v[min(n - 1, int(n * 0.95))],
        "p99": sorted_v[min(n - 1, int(n * 0.99))],
    }


def render_metrics() -> str:
    """Renderiza métricas em formato Prometheus text-exposition."""
    out: list[str] = []
    now = time.time()

    # Process uptime
    out.append("# HELP pdfsearchable_uptime_seconds Seconds since process start.")
    out.append("# TYPE pdfsearchable_uptime_seconds gauge")
    out.append(f"pdfsearchable_uptime_seconds {now - _start_time:.2f}")

    # Doc/page counts via store
    try:
        from pdfsearchable.store import load_index
        idx = load_index() or {}
        files = idx.get("files", {}) if isinstance(idx, dict) else {}
        total_docs = len(files)
        total_pages = sum(int(m.get("pages", 0) or 0) for m in files.values())
        out.append("# HELP pdfsearchable_docs_total Total documents indexed.")
        out.append("# TYPE pdfsearchable_docs_total gauge")
        out.append(f"pdfsearchable_docs_total {total_docs}")
        out.append("# HELP pdfsearchable_pages_total Total pages across all documents.")
        out.append("# TYPE pdfsearchable_pages_total gauge")
        out.append(f"pdfsearchable_pages_total {total_pages}")
    except Exception:
        pass

    with _lock:
        # Counters
        if _counters:
            out.append("# HELP pdfsearchable_counters Various counters.")
            out.append("# TYPE pdfsearchable_counters counter")
        for key, val in sorted(_counters.items()):
            out.append(f"{key} {val}")

        # Histograms
        for metric, values in _histograms.items():
            stats = _histogram_stats(values)
            out.append(f"# TYPE {metric} summary")
            out.append(f'{metric}{{quantile="0.5"}} {stats["p50"]:.6f}')
            out.append(f'{metric}{{quantile="0.95"}} {stats["p95"]:.6f}')
            out.append(f'{metric}{{quantile="0.99"}} {stats["p99"]:.6f}')
            out.append(f"{metric}_count {stats['count']}")
            out.append(f"{metric}_sum {stats['sum']:.6f}")

    return "\n".join(out) + "\n"


def reset_metrics() -> None:
    """Zera contadores (útil em testes)."""
    with _lock:
        _counters.clear()
        _histograms.clear()


def health_status() -> dict[str, Any]:
    """
    Health check JSON separado de /api/status.

    Retorna dict com "status": "ok" | "degraded" | "down" e "checks": {...}
    para cada subsistema. Código HTTP deve ser 200 se ok/degraded, 503 se down.
    """
    checks: dict[str, Any] = {}
    overall = "ok"

    # Store (índice)
    try:
        from pdfsearchable.store import META_FILE
        checks["store"] = {"ok": META_FILE.exists() or True}
    except Exception as e:
        checks["store"] = {"ok": False, "error": str(e)}
        overall = "degraded"

    # FTS
    try:
        from pdfsearchable.store import fts_ensure_healthy
        checks["fts"] = {"ok": bool(fts_ensure_healthy())}
        if not checks["fts"]["ok"]:
            overall = "degraded"
    except Exception as e:
        checks["fts"] = {"ok": False, "error": str(e)}
        overall = "degraded"

    # PyMuPDF
    try:
        import fitz
        checks["pymupdf"] = {"ok": True, "version": fitz.__doc__.split()[1] if fitz.__doc__ else None}
    except Exception as e:
        checks["pymupdf"] = {"ok": False, "error": str(e)}
        overall = "down"

    # Disco
    try:
        import shutil
        from pathlib import Path
        usage = shutil.disk_usage(Path.cwd())
        free_gb = usage.free / (1024 ** 3)
        checks["disk"] = {"ok": free_gb > 0.5, "free_gb": round(free_gb, 2)}
        if not checks["disk"]["ok"]:
            overall = "degraded"
    except Exception as e:
        checks["disk"] = {"ok": False, "error": str(e)}

    return {
        "status": overall,
        "checks": checks,
        "uptime_seconds": round(time.time() - _start_time, 2),
    }


__all__ = [
    "record_ollama_request",
    "record_cache_hit",
    "record_http",
    "record_search_duration",
    "render_metrics",
    "reset_metrics",
    "health_status",
]
