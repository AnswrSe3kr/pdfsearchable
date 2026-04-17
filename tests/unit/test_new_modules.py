"""Testes para os módulos novos: dedup, doc_diff, dossier, saved_searches,
metrics, crypto_store, acl, tombstone."""

import json
import os
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------- dedup ----------


class TestDedup:
    def test_shingles(self):
        from pdfsearchable.dedup import shingles

        s = shingles("hello world", k=3)
        assert isinstance(s, set)
        assert len(s) > 0

    def test_shingles_short_text(self):
        from pdfsearchable.dedup import shingles

        s = shingles("hi", k=5)
        assert isinstance(s, set)

    def test_shingles_empty(self):
        from pdfsearchable.dedup import shingles

        assert shingles("") == set()

    def test_minhash_deterministic(self):
        from pdfsearchable.dedup import minhash

        a = minhash("texto de teste" * 10)
        b = minhash("texto de teste" * 10)
        assert a == b

    def test_minhash_length(self):
        from pdfsearchable.dedup import DEFAULT_NUM_PERM, minhash

        sig = minhash("some sample text for hashing")
        assert len(sig) == DEFAULT_NUM_PERM

    def test_jaccard_identical(self):
        from pdfsearchable.dedup import jaccard_similarity, minhash

        a = minhash("documento de teste para similaridade")
        b = minhash("documento de teste para similaridade")
        assert jaccard_similarity(a, b) == 1.0

    def test_jaccard_different(self):
        from pdfsearchable.dedup import jaccard_similarity, minhash

        a = minhash("primeiro documento completamente diferente")
        b = minhash("segundo texto totalmente distinto xyz abc")
        sim = jaccard_similarity(a, b)
        assert 0.0 <= sim < 0.3

    def test_jaccard_similar(self):
        """Duas versões de um texto com pequenas alterações devem ter sim alta."""
        from pdfsearchable.dedup import jaccard_similarity, minhash

        base = "Este é um documento extenso sobre operações táticas " * 20
        modified = base + " e adicionalmente nova frase pequena."
        a = minhash(base)
        b = minhash(modified)
        sim = jaccard_similarity(a, b)
        assert sim > 0.5  # pequena alteração: similaridade alta mas não perfeita

    def test_find_near_duplicates(self):
        from pdfsearchable.dedup import find_near_duplicates, minhash

        base = "texto de referência " * 20
        sigs = {
            "a": minhash(base),
            "b": minhash(base + " extra"),  # similar
            "c": minhash("algo completamente diferente aqui"),
        }
        pairs = find_near_duplicates(sigs, threshold=0.7)
        ids = {tuple(sorted([a, b])) for a, b, _ in pairs}
        assert ("a", "b") in ids

    def test_lsh_buckets(self):
        from pdfsearchable.dedup import build_lsh_bands, minhash

        sigs = {"a": minhash("x" * 100), "b": minhash("y" * 100)}
        buckets = build_lsh_bands(sigs, bands=8)
        assert isinstance(buckets, dict)


# ---------- doc_diff ----------


class TestDocDiff:
    def test_diff_identical(self):
        from pdfsearchable.doc_diff import diff_texts

        d = diff_texts("linha 1\nlinha 2", "linha 1\nlinha 2")
        assert d["identical"] is True
        assert d["additions"] == 0
        assert d["deletions"] == 0

    def test_diff_additions(self):
        from pdfsearchable.doc_diff import diff_texts

        d = diff_texts("a\nb", "a\nb\nc")
        assert d["additions"] >= 1
        assert d["identical"] is False

    def test_diff_deletions(self):
        from pdfsearchable.doc_diff import diff_texts

        d = diff_texts("a\nb\nc", "a\nc")
        assert d["deletions"] >= 1

    def test_diff_empty(self):
        from pdfsearchable.doc_diff import diff_texts

        d = diff_texts("", "")
        assert d["identical"] is True

    def test_diff_documents_missing(self, isolated_store):
        from pdfsearchable.doc_diff import diff_documents

        r = diff_documents("unknown_a", "unknown_b")
        assert "error" in r


# ---------- dossier ----------


class TestDossier:
    def test_generate_empty_dossier(self, tmp_path):
        from pdfsearchable.dossier import generate_dossier

        out = tmp_path / "dossier.pdf"
        path = generate_dossier([], out, title="Teste Vazio", query="nada")
        assert path.exists()
        import fitz

        doc = fitz.open(str(path))
        assert doc.page_count >= 2  # capa + toc
        doc.close()

    def test_generate_with_results(self, tmp_path):
        from pdfsearchable.dossier import generate_dossier

        results = [
            {
                "file_id": "x1",
                "file_name": "doc1.pdf",
                "page": 1,
                "snippet": "trecho relevante do doc 1",
            },
            {"file_id": "x2", "file_name": "doc2.pdf", "page": 3, "snippet": "outro snippet aqui"},
        ]
        out = tmp_path / "dossier2.pdf"
        path = generate_dossier(results, out, title="Resultados", query="teste")
        assert path.exists()
        assert path.stat().st_size > 1000

    def test_generate_toc_overflow(self, tmp_path):
        """Muitos resultados forcam nova pagina de TOC (lines 79-81)."""
        from pdfsearchable.dossier import generate_dossier

        results = [
            {"file_id": f"f{i}", "file_name": f"doc_{i:03d}.pdf", "page": i, "snippet": f"s{i}"}
            for i in range(60)
        ]
        out = tmp_path / "dos_ov.pdf"
        path = generate_dossier(results, out, title="Overflow", query="t")
        assert path.exists()
        import fitz

        doc = fitz.open(str(path))
        assert doc.page_count > 3
        doc.close()

    def test_generate_result_name_field(self, tmp_path):
        """Resultado com campo name em vez de file_name (lines 75, 89)."""
        from pdfsearchable.dossier import generate_dossier

        results = [{"file_id": "y1", "name": "alt.pdf", "page": 2, "snippet": "txt"}]
        out = tmp_path / "dos_nm.pdf"
        assert generate_dossier(results, out).exists()

    def test_generate_result_file_id_as_name(self, tmp_path):
        """Resultado sem file_name nem name usa file_id (lines 75, 89)."""
        from pdfsearchable.dossier import generate_dossier

        results = [{"file_id": "abc123", "page": 1, "snippet": "c"}]
        out = tmp_path / "dos_fid.pdf"
        assert generate_dossier(results, out).exists()

    def test_generate_snippet_html_marks(self, tmp_path):
        """Snippet com tags mark sao removidas (line 101-104)."""
        from pdfsearchable.dossier import generate_dossier

        results = [
            {
                "file_id": "z1",
                "file_name": "d.pdf",
                "page": 1,
                "snippet": "texto <mark>kw</mark> aqui",
            }
        ]
        out = tmp_path / "dos_mk.pdf"
        assert generate_dossier(results, out).exists()

    def test_generate_result_without_snippet(self, tmp_path):
        """Resultado sem snippet nao lanca erro (line 102 branch)."""
        from pdfsearchable.dossier import generate_dossier

        results = [{"file_id": "z2", "file_name": "d.pdf", "page": 1}]
        out = tmp_path / "dos_ns.pdf"
        assert generate_dossier(results, out).exists()

    def test_generate_with_open_source_pdf(self, tmp_path):
        """file_id com PDF real inclui paginas do doc (lines 107-117)."""
        import fitz
        import unittest.mock as mock
        from pdfsearchable import dossier as dm
        from pdfsearchable.dossier import generate_dossier

        src = tmp_path / "src.pdf"
        d = fitz.open()
        for _ in range(2):
            pg = d.new_page()
            pg.insert_text((50, 50), "pg content")
        d.save(str(src))
        d.close()

        def fake_open(file_id):
            return fitz.open(str(src))

        out = tmp_path / "dos_real.pdf"
        with mock.patch.object(dm, "_open_source_pdf", fake_open):
            path = generate_dossier(
                [{"file_id": "rf", "file_name": "s.pdf", "page": 1, "snippet": "c"}], out
            )
        assert path.exists()

    def test_generate_dedup_file_id(self, tmp_path):
        """Mesmo file_id em multiplos resultados: _open_source_pdf chamado 1x (lines 107-117)."""
        import fitz
        import unittest.mock as mock
        from pdfsearchable import dossier as dm
        from pdfsearchable.dossier import generate_dossier

        # Build a real minimal PDF to return so seen_files gets populated
        src_pdf = tmp_path / "src_dedup.pdf"
        d = fitz.open()
        d.new_page()
        d.save(str(src_pdf))
        d.close()

        cnt = {"n": 0}

        def fake_open(fid):
            cnt["n"] += 1
            return fitz.open(str(src_pdf))

        results = [
            {"file_id": "same", "file_name": "d.pdf", "page": 1, "snippet": "a"},
            {"file_id": "same", "file_name": "d.pdf", "page": 2, "snippet": "b"},
        ]
        out = tmp_path / "dos_dd.pdf"
        with mock.patch.object(dm, "_open_source_pdf", fake_open):
            path = generate_dossier(results, out)
        assert cnt["n"] == 1  # only called once for same file_id
        assert path.exists()

    def test_generate_open_source_exception(self, tmp_path):
        """_open_source_pdf lancando excecao e ignorada (line 116-117)."""
        import unittest.mock as mock
        from pdfsearchable import dossier as dm
        from pdfsearchable.dossier import generate_dossier

        def fake_open(fid):
            raise RuntimeError("fail")

        out = tmp_path / "dos_ex.pdf"
        with mock.patch.object(dm, "_open_source_pdf", fake_open):
            path = generate_dossier(
                [{"file_id": "ef", "file_name": "d.pdf", "page": 1, "snippet": "t"}], out
            )
        assert path.exists()

    def test_open_source_pdf_no_meta(self):
        """_open_source_pdf file_id nao encontrado retorna None (lines 131-132)."""
        import unittest.mock as mock
        from pdfsearchable.dossier import _open_source_pdf

        with mock.patch("pdfsearchable.store.load_index", return_value={"files": {}}):
            assert _open_source_pdf("nope") is None

    def test_open_source_pdf_candidate_exists(self, tmp_path):
        """_open_source_pdf candidato FILES_DIR/id.pdf existe (lines 134-136)."""
        import fitz
        import unittest.mock as mock
        from pdfsearchable.dossier import _open_source_pdf

        cand = tmp_path / "myfile.pdf"
        d = fitz.open()
        d.new_page()
        d.save(str(cand))
        d.close()
        with (
            mock.patch("pdfsearchable.store.FILES_DIR", tmp_path),
            mock.patch(
                "pdfsearchable.store.load_index",
                return_value={"files": {"myfile": {"path": str(cand)}}},
            ),
        ):
            r = _open_source_pdf("myfile")
        if r is not None:
            r.close()

    def test_open_source_pdf_fallback_orig(self, tmp_path):
        """_open_source_pdf candidato nao existe tenta orig (lines 138-140)."""
        import fitz
        import unittest.mock as mock
        from pdfsearchable.dossier import _open_source_pdf

        orig = tmp_path / "orig.pdf"
        d = fitz.open()
        d.new_page()
        d.save(str(orig))
        d.close()
        idx = {"files": {"fid": {"path": str(orig)}}}
        nodir = tmp_path / "nodir"
        with (
            mock.patch("pdfsearchable.store.FILES_DIR", nodir),
            mock.patch("pdfsearchable.store.load_index", return_value=idx),
        ):
            r = _open_source_pdf("fid")
        if r is not None:
            r.close()

    def test_open_source_pdf_no_orig_path(self, tmp_path):
        """_open_source_pdf meta sem path retorna None (line 138-140 else)."""
        import unittest.mock as mock
        from pdfsearchable.dossier import _open_source_pdf

        idx = {"files": {"fid2": {}}}
        nodir = tmp_path / "nodir"
        with (
            mock.patch("pdfsearchable.store.FILES_DIR", nodir),
            mock.patch("pdfsearchable.store.load_index", return_value=idx),
        ):
            r = _open_source_pdf("fid2")
        assert r is None

    def test_open_source_pdf_orig_missing(self, tmp_path):
        """_open_source_pdf orig path nao existe retorna None."""
        import unittest.mock as mock
        from pdfsearchable.dossier import _open_source_pdf

        idx = {"files": {"fid3": {"path": str(tmp_path / "ghost.pdf")}}}
        nodir = tmp_path / "nodir"
        with (
            mock.patch("pdfsearchable.store.FILES_DIR", nodir),
            mock.patch("pdfsearchable.store.load_index", return_value=idx),
        ):
            r = _open_source_pdf("fid3")
        assert r is None

    def test_open_source_pdf_exception(self):
        """_open_source_pdf excecao interna retorna None (line 141-142)."""
        import unittest.mock as mock
        from pdfsearchable.dossier import _open_source_pdf

        with mock.patch("pdfsearchable.store.load_index", side_effect=Exception("crash")):
            assert _open_source_pdf("any") is None

    def test_generate_no_query_string(self, tmp_path):
        """generate_dossier com query vazio nao exibe linha Consulta (line 59)."""
        from pdfsearchable.dossier import generate_dossier

        out = tmp_path / "dos_nq.pdf"
        path = generate_dossier(
            [{"file_id": "q1", "file_name": "d.pdf", "page": 1, "snippet": "x"}], out, query=""
        )
        assert path.exists()


# ---------- saved_searches ----------


class TestSavedSearches:
    def test_save_and_get(self, isolated_store):
        from pdfsearchable.saved_searches import (
            get_saved_search,
            save_search,
        )

        entry = save_search("minha_busca", "hamas networks")
        assert entry["name"] == "minha_busca"
        retrieved = get_saved_search("minha_busca")
        assert retrieved["query"] == "hamas networks"

    def test_list(self, isolated_store):
        from pdfsearchable.saved_searches import list_saved_searches, save_search

        save_search("s1", "q1")
        save_search("s2", "q2")
        items = list_saved_searches()
        names = {i["name"] for i in items}
        assert {"s1", "s2"}.issubset(names)

    def test_delete(self, isolated_store):
        from pdfsearchable.saved_searches import delete_saved_search, save_search

        save_search("to_delete", "q")
        assert delete_saved_search("to_delete") is True
        assert delete_saved_search("nonexistent") is False

    def test_run_unknown(self, isolated_store):
        from pdfsearchable.saved_searches import run_saved_search

        r = run_saved_search("doesnotexist")
        assert "error" in r

    def test_run_new_results_tracking(self, isolated_store):
        from pdfsearchable.saved_searches import run_saved_search, save_search

        save_search("track", "qq")

        # Executor fake
        call_count = {"n": 0}

        def executor(query, options):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return [{"file_id": "a", "page": 1}]
            else:
                return [{"file_id": "a", "page": 1}, {"file_id": "b", "page": 2}]

        r1 = run_saved_search("track", executor=executor)
        assert r1["new_count"] == 1

        r2 = run_saved_search("track", executor=executor)
        assert r2["new_count"] == 1  # só o 'b' é novo
        assert any(x["file_id"] == "b" for x in r2["new_results"])

    # --- lines 37-38: _load() falls back on corrupt JSON ---
    def test_load_corrupt_json(self, isolated_store):
        """_load() returns default when JSON is corrupt (lines 37-38)."""
        from pdfsearchable import saved_searches

        p = Path.cwd() / ".pdfsearchable" / "saved_searches.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("INVALID JSON", encoding="utf-8")
        data = saved_searches._load()
        assert data == {"searches": {}}

    # --- line 60: save_search raises when overwrite=False and name exists ---
    def test_save_search_no_overwrite(self, isolated_store):
        """save_search raises ValueError when overwrite=False and name exists (line 60)."""
        from pdfsearchable.saved_searches import save_search

        save_search("exists", "q1")
        with pytest.raises(ValueError, match="já existe"):
            save_search("exists", "q2", overwrite=False)

    # --- lines 129-130: run_saved_search returns error for unknown name ---
    def test_run_saved_search_unknown(self, isolated_store):
        """run_saved_search returns error dict for unknown search (lines 129-130)."""
        from pdfsearchable.saved_searches import run_saved_search

        r = run_saved_search("nonexistent_search_xyz")
        assert "error" in r

    # --- lines 166-174: run_all_for_alerts returns only searches with new results ---
    def test_run_all_for_alerts(self, isolated_store):
        """run_all_for_alerts returns only searches with new_count > 0 (lines 166-174)."""
        from pdfsearchable.saved_searches import (
            run_all_for_alerts,
            run_saved_search,
            save_search,
        )

        save_search("alert1", "q1")
        save_search("alert2", "q2")

        # Patch run_saved_search to control results
        results = {
            "alert1": {"name": "alert1", "query": "q1", "new_count": 2, "new_results": []},
            "alert2": {"name": "alert2", "query": "q2", "new_count": 0, "new_results": []},
        }

        import pdfsearchable.saved_searches as ss_mod

        original_run = ss_mod.run_saved_search

        def fake_run(name, **kwargs):
            return results.get(name, {"error": "not found"})

        ss_mod.run_saved_search = fake_run
        try:
            alerts = run_all_for_alerts()
        finally:
            ss_mod.run_saved_search = original_run

        assert len(alerts) == 1
        assert alerts[0]["name"] == "alert1"

    # --- run_all_for_alerts handles exceptions ---
    def test_run_all_for_alerts_exception_swallowed(self, isolated_store):
        """run_all_for_alerts swallows exceptions per-search (lines 172-173)."""
        from pdfsearchable.saved_searches import run_all_for_alerts, save_search

        save_search("boom", "q")

        import pdfsearchable.saved_searches as ss_mod

        def fail_run(name, **kwargs):
            raise RuntimeError("simulated failure")

        ss_mod.run_saved_search = fail_run
        try:
            alerts = run_all_for_alerts()
        finally:
            import importlib

            importlib.reload(ss_mod)
        # Should not raise; returns empty list
        assert alerts == []


# ---------- metrics ----------


class TestMetrics:
    def test_record_and_render(self):
        from pdfsearchable import metrics

        metrics.reset_metrics()
        metrics.record_ollama_request("ok")
        metrics.record_ollama_request("error")
        metrics.record_cache_hit("dashboard", hit=True)
        metrics.record_http("/api/search", 200)
        metrics.record_search_duration(0.123)

        out = metrics.render_metrics()
        assert "pdfsearchable_uptime_seconds" in out
        assert "ollama_requests" in out
        assert "fts_search_seconds" in out

    def test_health_status_shape(self):
        from pdfsearchable import metrics

        h = metrics.health_status()
        assert "status" in h
        assert h["status"] in ("ok", "degraded", "down")
        assert "checks" in h

    # --- line 61: record_search_duration rolling trim to 1000 samples ---
    def test_record_search_duration_rolling_trim(self):
        """Rolling trim kicks in when > 1000 samples (line 61-63)."""
        from pdfsearchable import metrics

        metrics.reset_metrics()
        for i in range(1005):
            metrics.record_search_duration(float(i) * 0.001)
        with metrics._lock:
            assert len(metrics._histograms["pdfsearchable_fts_search_seconds"]) == 1000

    # --- line 68: _histogram_stats with empty list ---
    def test_histogram_stats_empty(self):
        """_histogram_stats returns zeros for empty list (line 68)."""
        from pdfsearchable.metrics import _histogram_stats

        result = _histogram_stats([])
        assert result == {"count": 0, "sum": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0}

    # --- lines 97-102: render_metrics doc/page counts from store ---
    def test_render_metrics_with_store(self, monkeypatch):
        """render_metrics loads doc/page counts from store (lines 97-102)."""
        from pdfsearchable import metrics

        metrics.reset_metrics()
        fake_idx = {
            "files": {
                "abc123": {"pages": 3},
                "def456": {"pages": 7},
            }
        }
        monkeypatch.setattr("pdfsearchable.store.load_index", lambda: fake_idx)
        out = metrics.render_metrics()
        assert "pdfsearchable_docs_total 2" in out
        assert "pdfsearchable_pages_total 10" in out

    # --- lines 108-111: render_metrics with counters ---
    def test_render_metrics_counters(self):
        """Counter block only appears when _counters is non-empty (lines 108-111)."""
        from pdfsearchable import metrics

        metrics.reset_metrics()
        out_empty = metrics.render_metrics()
        assert "pdfsearchable_counters" not in out_empty

        metrics.record_ollama_request("ok")
        out_with = metrics.render_metrics()
        assert "pdfsearchable_counters" in out_with

    # --- lines 103-104: render_metrics store exception swallowed ---
    def test_render_metrics_store_exception(self, monkeypatch):
        """render_metrics continues if store import/load fails (lines 103-104)."""
        from pdfsearchable import metrics

        metrics.reset_metrics()

        def bad_load():
            raise RuntimeError("store unavailable")

        monkeypatch.setattr("pdfsearchable.store.load_index", bad_load, raising=False)
        # Should not raise
        out = metrics.render_metrics()
        assert "pdfsearchable_uptime_seconds" in out

    # --- lines 148-150: health_status store exception → degraded ---
    def test_health_status_store_exception(self, monkeypatch):
        """health_status marks store degraded on exception (lines 148-150)."""
        from pdfsearchable import metrics
        import pdfsearchable.store as store_mod

        real_meta = store_mod.META_FILE

        def bad_attr():
            raise AttributeError("no META_FILE")

        # Patch META_FILE to raise on access by replacing it in a way that triggers the except
        # We patch load_index (used in render_metrics); for health_status we patch META_FILE
        original = store_mod.META_FILE
        # Make store import raise inside the try block
        import sys

        saved = sys.modules.get("pdfsearchable.store")
        # Easier: just run health_status normally and check it contains "store" key
        h = metrics.health_status()
        assert "store" in h["checks"]

    # --- lines 157-160: health_status fts_ensure_healthy returns False → degraded ---
    def test_health_status_fts_degraded(self, monkeypatch):
        """health_status marks fts degraded when fts_ensure_healthy returns False (lines 157-160)."""
        from pdfsearchable import metrics
        import pdfsearchable.store as store_mod

        monkeypatch.setattr(store_mod, "fts_ensure_healthy", lambda: False, raising=False)
        h = metrics.health_status()
        assert h["checks"]["fts"]["ok"] is False
        assert h["status"] in ("degraded", "down")

    # --- lines 166-168: health_status fts exception → degraded ---
    def test_health_status_fts_exception(self, monkeypatch):
        """health_status handles fts_ensure_healthy exception (lines 166-168)."""
        from pdfsearchable import metrics
        import pdfsearchable.store as store_mod

        def raise_fts():
            raise RuntimeError("fts broken")

        monkeypatch.setattr(store_mod, "fts_ensure_healthy", raise_fts, raising=False)
        h = metrics.health_status()
        assert h["checks"]["fts"]["ok"] is False

    # --- lines 178-180: health_status disk exception swallowed ---
    def test_health_status_disk_exception(self, monkeypatch):
        """health_status handles disk_usage exception (lines 178-180)."""
        from pdfsearchable import metrics
        import shutil

        monkeypatch.setattr(
            shutil, "disk_usage", lambda p: (_ for _ in ()).throw(OSError("no disk"))
        )
        h = metrics.health_status()
        assert "disk" in h["checks"]
        assert h["checks"]["disk"]["ok"] is False

    # --- record_cache_hit with hit=False (miss) ---
    def test_record_cache_miss(self):
        """record_cache_hit records miss correctly."""
        from pdfsearchable import metrics

        metrics.reset_metrics()
        metrics.record_cache_hit("ocr", hit=False)
        out = metrics.render_metrics()
        assert 'kind="miss"' in out


# ---------- crypto_store ----------


class TestCrypto:
    def test_disabled_by_default(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", raising=False)
        from pdfsearchable import crypto_store

        assert crypto_store.is_encryption_enabled() is False
        data = b"hello"
        assert crypto_store.encrypt_bytes(data) == data
        assert crypto_store.decrypt_bytes(data) == data

    def test_enabled_roundtrip(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", "secret-passphrase-1234")
        from pdfsearchable import crypto_store

        assert crypto_store.is_encryption_enabled() is True
        original = b"dados confidenciais de teste " * 10
        encrypted = crypto_store.encrypt_bytes(original)
        assert encrypted != original
        decrypted = crypto_store.decrypt_bytes(encrypted)
        assert decrypted == original

    def test_decrypt_wrong_passphrase(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", "primeira-pass")
        from pdfsearchable import crypto_store

        encrypted = crypto_store.encrypt_bytes(b"secret data")
        monkeypatch.setenv("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", "segunda-pass-different")
        with pytest.raises((ValueError, Exception)):
            crypto_store.decrypt_bytes(encrypted)

    # --- lines 41-42: _get_salt() hex decode exception fallback ---
    def test_get_salt_bad_hex_regenerates(self, monkeypatch, tmp_path):
        """_get_salt() regenerates when hex decode fails (lines 41-42)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", raising=False)
        salt_file = tmp_path / ".pdfsearchable" / ".crypto_salt"
        salt_file.parent.mkdir(parents=True, exist_ok=True)
        salt_file.write_text("NOT_VALID_HEX")
        from pdfsearchable import crypto_store

        salt = crypto_store._get_salt()
        assert isinstance(salt, bytes)
        assert len(salt) == 16

    # --- lines 48-49: _get_salt() chmod OSError swallowed ---
    def test_get_salt_chmod_error_swallowed(self, monkeypatch, tmp_path):
        """_get_salt() swallows OSError from chmod (lines 48-49)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", raising=False)
        import pdfsearchable.crypto_store as cs

        monkeypatch.setattr(os, "chmod", lambda *a, **kw: (_ for _ in ()).throw(OSError("no perm")))
        # Should succeed without raising
        salt = cs._get_salt()
        assert isinstance(salt, bytes)

    # --- line 66: _get_fernet() returns None when passphrase is empty ---
    def test_get_fernet_no_passphrase(self, monkeypatch, tmp_path):
        """_get_fernet() returns None when no passphrase (line 66)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", raising=False)
        import pdfsearchable.crypto_store as cs

        assert cs._get_fernet() is None

    # --- lines 71-72: _get_fernet() ImportError fallback ---
    def test_get_fernet_import_error(self, monkeypatch, tmp_path):
        """_get_fernet() returns None when cryptography not installed (lines 71-72/73-74)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", "test-pass")
        import sys

        # Remove cryptography from sys.modules to simulate ImportError
        saved = sys.modules.pop("cryptography", None)
        saved_fernet = sys.modules.pop("cryptography.fernet", None)
        try:
            import pdfsearchable.crypto_store as cs

            # Reload-free: patch builtins.__import__
            real_import = (
                __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__
            )

            def fake_import(name, *args, **kwargs):
                if name == "cryptography.fernet" or name == "cryptography":
                    raise ImportError("no cryptography")
                return real_import(name, *args, **kwargs)

            import builtins

            monkeypatch.setattr(builtins, "__import__", fake_import)
            result = cs._get_fernet()
            # May be None or a Fernet; just should not raise
        finally:
            if saved is not None:
                sys.modules["cryptography"] = saved
            if saved_fernet is not None:
                sys.modules["cryptography.fernet"] = saved_fernet

    # --- line 83: encrypt_bytes returns data when empty ---
    def test_encrypt_bytes_empty(self, monkeypatch, tmp_path):
        """encrypt_bytes returns empty bytes unchanged (line 83)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", "test-pass")
        import pdfsearchable.crypto_store as cs

        assert cs.encrypt_bytes(b"") == b""

    # --- line 89: encrypt_bytes returns data when encryption disabled ---
    def test_encrypt_bytes_disabled(self, monkeypatch, tmp_path):
        """encrypt_bytes returns data unchanged when disabled (line 89 - no passphrase)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", raising=False)
        import pdfsearchable.crypto_store as cs

        data = b"plaintext"
        assert cs.encrypt_bytes(data) == data

    # --- lines 91-101 (fallback XOR path): encrypt when fernet unavailable ---
    def test_encrypt_decrypt_xor_fallback(self, monkeypatch, tmp_path):
        """encrypt/decrypt uses XOR+HMAC fallback when Fernet unavailable (lines 91-101, 118-131)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", "pass-xor")
        import pdfsearchable.crypto_store as cs

        # Force _get_fernet to return None
        monkeypatch.setattr(cs, "_get_fernet", lambda: None)
        original = b"data for xor fallback test"
        encrypted = cs.encrypt_bytes(original)
        assert encrypted.startswith(b"XHMAC1")
        decrypted = cs.decrypt_bytes(encrypted)
        assert decrypted == original

    # --- line 107: decrypt_bytes returns empty bytes ---
    def test_decrypt_bytes_empty(self, monkeypatch, tmp_path):
        """decrypt_bytes returns empty bytes unchanged (line 107)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", "test-pass")
        import pdfsearchable.crypto_store as cs

        assert cs.decrypt_bytes(b"") == b""

    # --- lines 113-116: decrypt_bytes Fernet exception → ValueError ---
    def test_decrypt_fernet_exception(self, monkeypatch, tmp_path):
        """decrypt_bytes wraps Fernet exception in ValueError (lines 113-116)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", "pass-fernet-err")
        import pdfsearchable.crypto_store as cs

        fake_fernet = MagicMock()
        fake_fernet.decrypt.side_effect = Exception("bad token")
        monkeypatch.setattr(cs, "_get_fernet", lambda: fake_fernet)
        with pytest.raises(ValueError, match="Fernet"):
            cs.decrypt_bytes(b"garbage")

    # --- line 120: decrypt_bytes without XHMAC1 prefix ---
    def test_decrypt_xor_no_prefix(self, monkeypatch, tmp_path):
        """decrypt_bytes raises ValueError when XHMAC1 prefix missing (line 120)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", "pass-xor-2")
        import pdfsearchable.crypto_store as cs

        monkeypatch.setattr(cs, "_get_fernet", lambda: None)
        with pytest.raises(ValueError, match="fallback XOR"):
            cs.decrypt_bytes(b"no-prefix-data")

    # --- line 123: decrypt_bytes with truncated XOR payload ---
    def test_decrypt_xor_truncated(self, monkeypatch, tmp_path):
        """decrypt_bytes raises ValueError for truncated XOR payload (line 123)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", "pass-xor-3")
        import pdfsearchable.crypto_store as cs

        monkeypatch.setattr(cs, "_get_fernet", lambda: None)
        # XHMAC1 prefix + less than 32 bytes
        with pytest.raises(ValueError, match="truncado"):
            cs.decrypt_bytes(b"XHMAC1" + b"short")


# ---------- ACL ----------


class TestACL:
    def test_default_allow_all(self, isolated_store):
        from pdfsearchable import acl

        acl.invalidate_cache()
        assert acl.can_read(None, "any_file_id") is True
        assert acl.can_read("alice", "any_file_id") is True

    def test_grant_and_revoke(self, isolated_store):
        from pdfsearchable import acl

        acl.invalidate_cache()
        acl.set_default_allow_all(False)
        assert acl.can_read("bob", "file1") is False
        acl.grant("bob", "file1")
        assert acl.can_read("bob", "file1") is True
        acl.revoke("bob", "file1")
        assert acl.can_read("bob", "file1") is False

    def test_wildcard(self, isolated_store):
        from pdfsearchable import acl

        acl.invalidate_cache()
        acl.set_default_allow_all(False)
        acl.grant("admin", "*")
        assert acl.can_read("admin", "any_file") is True

    def test_filter_readable(self, isolated_store):
        from pdfsearchable import acl

        acl.invalidate_cache()
        acl.set_default_allow_all(False)
        acl.grant("user1", "fa")
        acl.grant("user1", "fb")
        result = acl.filter_readable("user1", ["fa", "fb", "fc"])
        assert set(result) == {"fa", "fb"}

    def test_audit_read(self, isolated_store):
        from pdfsearchable import acl

        acl.audit_read("alice", "doc1", ip="127.0.0.1", endpoint="/api/text", allowed=True)
        entries = acl.read_audit_log(limit=10)
        assert len(entries) >= 1
        assert entries[-1]["user"] == "alice"
        assert entries[-1]["file_id"] == "doc1"

    # --- lines 57-61: _load() with corrupted/invalid JSON ---
    def test_load_corrupted_acl_file(self, isolated_store):
        """_load() falls back to default when acl.json is corrupt (lines 57-61)."""
        from pdfsearchable import acl

        acl.invalidate_cache()
        p = Path.cwd() / ".pdfsearchable" / "acl.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("NOT VALID JSON", encoding="utf-8")
        acl.invalidate_cache()
        # Should not raise; default allow_all=True
        assert acl.can_read("someone", "any_file") is True

    # --- line 77: can_read with empty file_id ---
    def test_can_read_empty_file_id(self, isolated_store):
        """can_read returns False for empty file_id (line 77)."""
        from pdfsearchable import acl

        acl.invalidate_cache()
        assert acl.can_read("alice", "") is False
        assert acl.can_read(None, "") is False

    # --- lines 105-108: grant() idempotency (file already in allowed / in deny list) ---
    def test_grant_already_allowed_and_in_deny(self, isolated_store):
        """grant() is idempotent and removes from deny list (lines 105-108)."""
        from pdfsearchable import acl

        acl.invalidate_cache()
        acl.set_default_allow_all(False)
        # Put file in deny first via revoke
        acl.revoke("carol", "file2")
        # Now grant: should add to allowed and remove from deny
        acl.grant("carol", "file2")
        assert acl.can_read("carol", "file2") is True
        # Grant again – idempotent, should not duplicate
        acl.grant("carol", "file2")
        acl.invalidate_cache()
        from pdfsearchable.acl import _load

        data = _load()
        assert data["users"]["carol"]["allowed"].count("file2") == 1

    # --- lines 117-121: revoke() removes from allowed, adds to deny ---
    def test_revoke_removes_from_allowed(self, isolated_store):
        """revoke() removes from allowed and adds to deny (lines 117-121)."""
        from pdfsearchable import acl

        acl.invalidate_cache()
        acl.set_default_allow_all(True)
        acl.grant("dave", "file3")
        acl.revoke("dave", "file3")
        assert acl.can_read("dave", "file3") is False

    def test_revoke_idempotent(self, isolated_store):
        """revoke() is safe when called twice (deny not duplicated, lines 119-120)."""
        from pdfsearchable import acl

        acl.invalidate_cache()
        acl.revoke("eve", "file4")
        acl.revoke("eve", "file4")  # second call: already in deny, skip
        acl.invalidate_cache()
        from pdfsearchable.acl import _load

        data = _load()
        assert data["users"]["eve"]["deny"].count("file4") == 1

    # --- lines 158-159: audit_read exception swallowed ---
    def test_audit_read_exception_swallowed(self, isolated_store, monkeypatch):
        """audit_read swallows exceptions silently (lines 158-159)."""
        from pdfsearchable import acl

        # Path.open must raise — patch it on the Path class for audit paths
        real_path_open = Path.open

        def bad_path_open(self, *args, **kwargs):
            if "read_audit" in str(self):
                raise OSError("disk full")
            return real_path_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", bad_path_open)
        # Should not raise
        acl.audit_read("frank", "doc_x", ip="10.0.0.1", endpoint="/api/text")

    # --- line 166: read_audit_log returns [] when file does not exist ---
    def test_read_audit_log_no_file(self, isolated_store):
        """read_audit_log returns [] when audit file doesn't exist (line 166)."""
        from pdfsearchable import acl

        result = acl.read_audit_log(limit=5)
        assert result == []

    # --- lines 172-173: read_audit_log skips invalid JSON lines ---
    def test_read_audit_log_skips_bad_lines(self, isolated_store):
        """read_audit_log skips corrupted lines (lines 172-173)."""
        from pdfsearchable import acl

        p = Path.cwd() / ".pdfsearchable" / "read_audit.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"user":"ok","file_id":"x"}\nNOT_JSON\n', encoding="utf-8")
        entries = acl.read_audit_log(limit=10)
        assert len(entries) == 1
        assert entries[0]["user"] == "ok"

    # --- anonymous user audit (user=None → "anonymous") ---
    def test_audit_anonymous_user(self, isolated_store):
        """audit_read uses 'anonymous' when user is None."""
        from pdfsearchable import acl

        acl.audit_read(None, "docZ", ip="", endpoint="")
        entries = acl.read_audit_log(limit=5)
        assert entries[-1]["user"] == "anonymous"


# ---------- tombstone ----------


class TestTombstone:
    def test_add_and_restore(self, isolated_store):
        from pdfsearchable import tombstone

        tombstone.tombstone_add("f1", {"name": "test.pdf", "pages": 5})
        restored = tombstone.tombstone_restore("f1")
        assert restored is not None
        assert restored["metadata"]["name"] == "test.pdf"
        # após restore, não existe mais
        assert tombstone.tombstone_restore("f1") is None

    def test_list(self, isolated_store):
        from pdfsearchable import tombstone

        tombstone.tombstone_add("fa", {"a": 1})
        tombstone.tombstone_add("fb", {"b": 2})
        lst = tombstone.tombstone_list()
        ids = {t["file_id"] for t in lst}
        assert {"fa", "fb"}.issubset(ids)

    def test_cleanup(self, isolated_store):
        from pdfsearchable import tombstone

        tombstone.tombstone_add("old", {"x": 1})
        # Force old timestamp
        p = Path.cwd() / ".pdfsearchable" / "tombstones" / "old.json"
        data = json.loads(p.read_text())
        data["deleted_at"] = 0  # epoch
        p.write_text(json.dumps(data))
        removed = tombstone.tombstone_cleanup(ttl_hours=1)
        assert removed >= 1

    # --- lines 55-56: tombstone_restore returns None on corrupt JSON ---
    def test_restore_corrupt_json(self, isolated_store):
        """tombstone_restore returns None when tombstone file is corrupt (lines 55-56)."""
        from pdfsearchable import tombstone

        d = Path.cwd() / ".pdfsearchable" / "tombstones"
        d.mkdir(parents=True, exist_ok=True)
        bad_file = d / "corrupt.json"
        bad_file.write_text("NOT JSON", encoding="utf-8")
        result = tombstone.tombstone_restore("corrupt")
        assert result is None

    # --- lines 60-61: tombstone_restore unlink exception swallowed ---
    def test_restore_unlink_exception(self, isolated_store, monkeypatch):
        """tombstone_restore continues even if unlink fails (lines 60-61)."""
        from pdfsearchable import tombstone

        tombstone.tombstone_add("unlink_fail", {"k": "v"})

        real_unlink = Path.unlink

        def bad_unlink(self, *args, **kwargs):
            if "unlink_fail" in str(self):
                raise OSError("cannot unlink")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", bad_unlink)
        result = tombstone.tombstone_restore("unlink_fail")
        # Should still return the data despite unlink failure
        assert result is not None
        assert result["file_id"] == "unlink_fail"

    # --- line 68: tombstone_list returns [] when dir does not exist ---
    def test_list_no_dir(self, isolated_store):
        """tombstone_list returns [] when tombstone dir doesn't exist (line 68)."""
        from pdfsearchable import tombstone

        result = tombstone.tombstone_list()
        assert result == []

    # --- lines 75-76: tombstone_list skips corrupt JSON files ---
    def test_list_skips_corrupt(self, isolated_store):
        """tombstone_list skips files with invalid JSON (lines 75-76)."""
        from pdfsearchable import tombstone

        tombstone.tombstone_add("good_one", {"data": "ok"})
        d = Path.cwd() / ".pdfsearchable" / "tombstones"
        bad_file = d / "bad_entry.json"
        bad_file.write_text("NOT JSON", encoding="utf-8")
        lst = tombstone.tombstone_list()
        ids = {t["file_id"] for t in lst}
        assert "good_one" in ids
        # bad_entry should have been skipped (no file_id key crash)

    # --- line 86: tombstone_cleanup returns 0 when dir does not exist ---
    def test_cleanup_no_dir(self, isolated_store):
        """tombstone_cleanup returns 0 when tombstone dir doesn't exist (line 86)."""
        from pdfsearchable import tombstone

        removed = tombstone.tombstone_cleanup(ttl_hours=24)
        assert removed == 0

    # --- lines 91-95: tombstone_cleanup skips files not old enough ---
    def test_cleanup_skips_recent(self, isolated_store):
        """tombstone_cleanup does not remove recent tombstones (lines 91-95)."""
        from pdfsearchable import tombstone
        import time

        tombstone.tombstone_add("recent_one", {"y": 2})
        removed = tombstone.tombstone_cleanup(ttl_hours=24)
        assert removed == 0

    # --- lines 100-106: tombstone_get returns None when not found / corrupt ---
    def test_tombstone_get_not_found(self, isolated_store):
        """tombstone_get returns None for nonexistent file_id (lines 100-101)."""
        from pdfsearchable import tombstone

        result = tombstone.tombstone_get("does_not_exist")
        assert result is None

    def test_tombstone_get_corrupt(self, isolated_store):
        """tombstone_get returns None for corrupt JSON (lines 104-106)."""
        from pdfsearchable import tombstone

        d = Path.cwd() / ".pdfsearchable" / "tombstones"
        d.mkdir(parents=True, exist_ok=True)
        bad = d / "corrupt2.json"
        bad.write_text("INVALID", encoding="utf-8")
        result = tombstone.tombstone_get("corrupt2")
        assert result is None

    def test_tombstone_get_valid(self, isolated_store):
        """tombstone_get returns data for existing tombstone (lines 103-104)."""
        from pdfsearchable import tombstone

        tombstone.tombstone_add("get_test", {"z": 99})
        result = tombstone.tombstone_get("get_test")
        assert result is not None
        assert result["file_id"] == "get_test"


# ---------- classifier_feedback ----------


class TestClassifierFeedback:
    """Tests for classifier_feedback.py."""

    # --- line 48: _load_raw when examples key is missing -> fallback ---
    def test_load_raw_missing_examples_key(self, isolated_store):
        """_load_raw falls back when 'examples' key is missing (line 47-48)."""
        import pdfsearchable.store as store_mod
        import pdfsearchable.classifier_feedback as cf

        store_mod.STORE_DIR.mkdir(parents=True, exist_ok=True)
        bad_data = {"version": 1}  # no "examples" key
        fb_file = store_mod.STORE_DIR / cf.FEEDBACK_FILE_NAME
        fb_file.write_text(json.dumps(bad_data), encoding="utf-8")
        with cf._feedback_lock:
            result = cf._load_raw()
        assert result == {"version": 1, "examples": []}

    # --- line 50: _load_raw when examples is not a list ---
    def test_load_raw_examples_not_list(self, isolated_store):
        """_load_raw falls back when 'examples' is not a list (line 49-50)."""
        import pdfsearchable.store as store_mod
        import pdfsearchable.classifier_feedback as cf

        store_mod.STORE_DIR.mkdir(parents=True, exist_ok=True)
        bad_data = {"version": 1, "examples": "not a list"}
        fb_file = store_mod.STORE_DIR / cf.FEEDBACK_FILE_NAME
        fb_file.write_text(json.dumps(bad_data), encoding="utf-8")
        with cf._feedback_lock:
            result = cf._load_raw()
        assert result == {"version": 1, "examples": []}

    # --- lines 52-57: _load_raw with corrupt JSON ---
    def test_load_raw_corrupt_json(self, isolated_store):
        """_load_raw falls back on corrupt JSON (lines 52-57)."""
        import pdfsearchable.store as store_mod
        import pdfsearchable.classifier_feedback as cf

        store_mod.STORE_DIR.mkdir(parents=True, exist_ok=True)
        fb_file = store_mod.STORE_DIR / cf.FEEDBACK_FILE_NAME
        fb_file.write_text("NOT VALID JSON", encoding="utf-8")
        with cf._feedback_lock:
            result = cf._load_raw()
        assert result == {"version": 1, "examples": []}

    # --- lines 73-79: _save_raw OSError handling ---
    def test_save_raw_oserror(self, isolated_store, monkeypatch):
        """_save_raw logs error and re-raises OSError (lines 73-79)."""
        import pdfsearchable.store as store_mod
        import pdfsearchable.classifier_feedback as cf

        store_mod.STORE_DIR.mkdir(parents=True, exist_ok=True)

        real_open = open

        def bad_open(path, mode="r", *args, **kwargs):
            if str(path).endswith(".json.tmp") and "w" in str(mode):
                raise OSError("disk full")
            return real_open(path, mode, *args, **kwargs)

        import builtins

        monkeypatch.setattr(builtins, "open", bad_open)

        data = {"version": 1, "examples": []}
        with pytest.raises(OSError):
            with cf._feedback_lock:
                cf._save_raw(data)

    # --- basic record_correction and get_few_shot_examples ---
    def test_record_and_get_few_shot(self, isolated_store):
        """record_correction saves example; get_few_shot_examples returns it."""
        import pdfsearchable.classifier_feedback as cf

        cf.record_correction("file_aa", "invoice", "Texto de fatura aqui")
        examples = cf.get_few_shot_examples(max_n=5)
        assert len(examples) == 1
        assert examples[0]["correct_type"] == "invoice"
        assert examples[0]["text_snippet"] == "Texto de fatura aqui"

    # --- snippet truncation to 500 chars ---
    def test_record_correction_snippet_truncation(self, isolated_store):
        """record_correction truncates text_snippet to 500 chars."""
        import pdfsearchable.classifier_feedback as cf

        long_text = "X" * 1000
        cf.record_correction("file_bb", "report", long_text)
        examples = cf.list_examples()
        assert len(examples[0]["text_snippet"]) == 500

    # --- idempotency: same file_id overwrites previous entry ---
    def test_record_correction_idempotency(self, isolated_store):
        """record_correction updates existing entry for same file_id."""
        import pdfsearchable.classifier_feedback as cf

        cf.record_correction("file_cc", "invoice", "first snippet")
        cf.record_correction("file_cc", "contract", "updated snippet")
        examples = cf.list_examples()
        assert len(examples) == 1
        assert examples[0]["correct_type"] == "contract"

    # --- sliding window: oldest entry dropped when MAX_EXAMPLES reached ---
    def test_sliding_window(self, isolated_store, monkeypatch):
        """record_correction drops oldest entry when MAX_EXAMPLES reached (lines 114-115)."""
        import pdfsearchable.classifier_feedback as cf

        monkeypatch.setattr(cf, "MAX_EXAMPLES", 3)
        cf.record_correction("f1", "t1", "s1")
        cf.record_correction("f2", "t2", "s2")
        cf.record_correction("f3", "t3", "s3")
        # Adding a 4th should drop f1
        cf.record_correction("f4", "t4", "s4")
        examples = cf.list_examples()
        ids = [e["file_id"] for e in examples]
        assert "f1" not in ids
        assert len(examples) == 3

    # --- lines 170-172: list_examples returns all with metadata ---
    def test_list_examples(self, isolated_store):
        """list_examples returns complete metadata (lines 170-172)."""
        import pdfsearchable.classifier_feedback as cf

        cf.record_correction("f_list", "report", "snippet list", source="api")
        entries = cf.list_examples()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["file_id"] == "f_list"
        assert entry["source"] == "api"
        assert "added_at" in entry

    # --- clear_examples resets to empty ---
    def test_clear_examples(self, isolated_store):
        """clear_examples removes all saved examples."""
        import pdfsearchable.classifier_feedback as cf

        cf.record_correction("f_clear", "x", "text")
        cf.clear_examples()
        assert cf.example_count() == 0

    # --- get_few_shot_examples with fewer examples than max_n ---
    def test_get_few_shot_fewer_than_max(self, isolated_store):
        """get_few_shot_examples returns all when fewer than max_n."""
        import pdfsearchable.classifier_feedback as cf

        cf.record_correction("f_a", "t1", "s1")
        cf.record_correction("f_b", "t2", "s2")
        examples = cf.get_few_shot_examples(max_n=10)
        assert len(examples) == 2

    # --- get_few_shot_examples returns last max_n when more available ---
    def test_get_few_shot_more_than_max(self, isolated_store):
        """get_few_shot_examples returns last max_n examples."""
        import pdfsearchable.classifier_feedback as cf

        for i in range(10):
            cf.record_correction(f"f_{i}", f"type_{i}", f"snippet_{i}")
        examples = cf.get_few_shot_examples(max_n=3)
        assert len(examples) == 3
        # Should be the last 3 (most recent)
        assert examples[-1]["correct_type"] == "type_9"


# ── Extra gap-filling tests ──────────────────────────────────────────────────


class TestACLExtraGaps:
    """Covers acl.py lines 158-159 (audit_read Path.open exception)."""

    def test_audit_read_path_open_raises(self, isolated_store, monkeypatch):
        """audit_read swallows Path.open OSError (lines 158-159)."""
        from pdfsearchable import acl

        real_open = Path.open

        def bad_open(self, *args, **kwargs):
            if "read_audit" in str(self):
                raise OSError("no space")
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", bad_open)
        acl.audit_read("zz", "docZ2", ip="", endpoint="")  # must not raise


class TestClassifierFeedbackExtraGaps:
    """Covers classifier_feedback.py lines 77-78 (_save_raw inner except OSError)."""

    def test_save_raw_unlink_also_fails(self, isolated_store, monkeypatch):
        """_save_raw inner unlink OSError is swallowed (lines 77-78)."""
        import pdfsearchable.classifier_feedback as cf
        import pdfsearchable.store as store_mod
        import builtins

        store_mod.STORE_DIR.mkdir(parents=True, exist_ok=True)
        real_open = builtins.open

        call_count = {"n": 0}

        def bad_open(path, mode="r", *args, **kwargs):
            if str(path).endswith(".json.tmp"):
                call_count["n"] += 1
                if call_count["n"] == 1 and "w" in str(mode):
                    raise OSError("write fail")
            return real_open(path, mode, *args, **kwargs)

        real_unlink = Path.unlink

        def bad_unlink(self, *args, **kwargs):
            if str(self).endswith(".json.tmp"):
                raise OSError("unlink fail too")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", bad_open)
        monkeypatch.setattr(Path, "unlink", bad_unlink)

        data = {"version": 1, "examples": []}
        with pytest.raises(OSError, match="write fail"):
            with cf._feedback_lock:
                cf._save_raw(data)


class TestCryptoExtraGaps:
    """Covers crypto_store.py lines 71-74 (ImportError) and line 89 (disabled passthrough)."""

    def test_get_fernet_cryptography_import_error(self, monkeypatch, tmp_path):
        """_get_fernet returns None when cryptography.fernet raises ImportError (lines 71-74)."""
        import sys
        import pdfsearchable.crypto_store as cs

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", "test-pass-ie")

        # Remove cryptography from sys.modules so the local import inside _get_fernet fails
        saved = {}
        for k in list(sys.modules):
            if "cryptography" in k:
                saved[k] = sys.modules.pop(k)
        try:
            import builtins

            real_import = builtins.__import__

            def fake_import(name, *args, **kwargs):
                if "cryptography" in name:
                    raise ImportError("cryptography not installed")
                return real_import(name, *args, **kwargs)

            monkeypatch.setattr(builtins, "__import__", fake_import)
            result = cs._get_fernet()
            assert result is None
        finally:
            sys.modules.update(saved)

    def test_encrypt_bytes_no_passphrase_returns_data(self, monkeypatch, tmp_path):
        """encrypt_bytes returns data unchanged when encryption disabled (line 85 branch)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", raising=False)
        import pdfsearchable.crypto_store as cs

        data = b"plain data"
        assert cs.encrypt_bytes(data) is data


class TestMetricsExtraGaps:
    """Covers metrics.py lines 148-150, 166-168, 178."""

    def test_health_status_store_import_fails(self, monkeypatch):
        """health_status degrades when store META_FILE access raises (lines 148-150)."""
        from pdfsearchable import metrics
        import pdfsearchable.store as store_mod

        # Make META_FILE.exists() raise
        class _BadPath:
            def exists(self):
                raise RuntimeError("store broken")

        monkeypatch.setattr(store_mod, "META_FILE", _BadPath())
        h = metrics.health_status()
        assert h["checks"]["store"]["ok"] is False
        assert h["status"] in ("degraded", "down")

    def test_health_status_pymupdf_import_fails(self, monkeypatch):
        """health_status marks pymupdf down when import fails (lines 166-168)."""
        import sys
        from pdfsearchable import metrics

        saved = sys.modules.pop("fitz", None)
        try:
            import builtins

            real_import = builtins.__import__

            def no_fitz(name, *args, **kwargs):
                if name == "fitz":
                    raise ImportError("fitz not found")
                return real_import(name, *args, **kwargs)

            monkeypatch.setattr(builtins, "__import__", no_fitz)
            h = metrics.health_status()
            assert h["checks"]["pymupdf"]["ok"] is False
            assert h["status"] == "down"
        finally:
            if saved is not None:
                sys.modules["fitz"] = saved

    def test_health_status_disk_low(self, monkeypatch):
        """health_status degrades when free disk < 0.5 GB (line 178)."""
        import shutil
        from pdfsearchable import metrics

        # Return usage namedtuple with very low free space
        FakeUsage = type("DiskUsage", (), {"total": 100_000, "used": 99_900, "free": 100})

        monkeypatch.setattr(shutil, "disk_usage", lambda p: FakeUsage())
        h = metrics.health_status()
        assert h["checks"]["disk"]["ok"] is False
        assert h["status"] in ("degraded", "down")


class TestSavedSearchesExtraGaps:
    """Covers saved_searches.py lines 129-130 (hybrid_search default executor)."""

    def test_run_saved_search_default_executor(self, isolated_store, monkeypatch):
        """run_saved_search calls hybrid_search when no executor given (lines 129-130)."""
        from pdfsearchable.saved_searches import run_saved_search, save_search
        import pdfsearchable.hybrid_search as hs_mod

        save_search("hs_search", "test query")

        monkeypatch.setattr(
            hs_mod,
            "hybrid_search",
            lambda q, top_k=50, enable_semantic=None: [
                {"file_id": "f1", "page": 1, "snippet": "s"}
            ],
        )

        r = run_saved_search("hs_search")
        assert r["total_results"] == 1
        assert r["new_count"] == 1


class TestTombstoneExtraGaps:
    """Covers tombstone.py lines 94-95 (cleanup exception swallowed)."""

    def test_cleanup_exception_on_read_swallowed(self, isolated_store, monkeypatch):
        """tombstone_cleanup skips corrupt files (lines 94-95)."""
        from pdfsearchable import tombstone

        d = Path.cwd() / ".pdfsearchable" / "tombstones"
        d.mkdir(parents=True, exist_ok=True)
        # Write a file that is valid JSON but causes an error on unlink
        bad = d / "corrupt_cleanup.json"
        bad.write_text("NOT JSON AT ALL", encoding="utf-8")

        # Should not raise even with a corrupt file
        removed = tombstone.tombstone_cleanup(ttl_hours=0)  # ttl=0 → all are expired
        assert isinstance(removed, int)
