"""Fixtures compartilhadas para testes."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def tmp_project(tmp_path):
    """Diretório temporário como projeto (cwd)."""
    old = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(old)


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """
    Diretório temporário isolado para testes que usam o store/CLI.
    Define cwd e os paths do store e do report para este diretório.
    """
    monkeypatch.chdir(tmp_path)
    store_dir = tmp_path / ".pdfsearchable"
    # store usa Path.cwd() no import; forçar paths para o tmp
    import pdfsearchable.store as store_mod
    import pdfsearchable.report as report_mod

    monkeypatch.setattr(store_mod, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(store_mod, "STORE_DIR", store_dir)
    monkeypatch.setattr(store_mod, "FILES_DIR", tmp_path / store_mod.PROCESSED_DIR_NAME)
    monkeypatch.setattr(store_mod, "META_FILE", store_dir / "index.json")
    monkeypatch.setattr(store_mod, "OCR_CACHE_DIR", store_dir / "ocr_cache")
    monkeypatch.setattr(store_mod, "FTS_DB", store_dir / "fts.sqlite")
    # report importa STORE_DIR do store no load; forçar paths para o report escrever no tmp
    monkeypatch.setattr(report_mod, "STORE_DIR", store_dir)
    monkeypatch.setattr(report_mod, "REPORT_PATH", store_dir / "report.html")
    monkeypatch.setattr(report_mod, "DOCUMENT_VIEW_PATH", store_dir / "document-view.html")
    monkeypatch.setattr(report_mod, "REPORT_HASH_FILE", store_dir / "report_hash.txt")
    return tmp_path


@pytest.fixture
def minimal_pdf(isolated_store):
    """
    PDF mínimo com uma página e texto 'Sample PDF for testing.'
    Criado em isolated_store para uso em testes E2E/acceptance.
    """
    import fitz

    pdf_path = isolated_store / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Sample PDF for testing.")
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture
def mock_ollama_up(monkeypatch):
    """
    Simula Ollama UP: health check retorna True e _ollama_request retorna
    uma resposta JSON válida para extract_summary_and_subject_ollama.
    """
    import pdfsearchable.content_extractors as ce

    monkeypatch.setattr(ce, "ollama_health_check", lambda: True)

    def fake_request(prompt, max_tokens=150, cache_key=None, timeout=None, json_mode=False):
        if json_mode or "JSON" in prompt or "json" in prompt:
            return json.dumps({
                "summary": "Documento de teste sobre operações.",
                "subject": "Teste de operação",
                "entities": ["João Silva"],
                "themes": ["operações"],
            })
        return "Resumo mock do documento."

    monkeypatch.setattr(ce, "_ollama_request", fake_request)
    yield


@pytest.fixture
def mock_ollama_down(monkeypatch):
    """Simula Ollama DOWN: todas as chamadas retornam None/False."""
    import pdfsearchable.content_extractors as ce

    monkeypatch.setattr(ce, "ollama_health_check", lambda: False)
    monkeypatch.setattr(
        ce,
        "_ollama_request",
        lambda *a, **kw: None,
    )
    yield


@pytest.fixture
def mock_htr_stub(monkeypatch):
    """
    Stub para todas as rotas HTR: retorna string fixa, nunca carrega modelos reais.
    """
    try:
        import pdfsearchable.htr as htr_mod
        monkeypatch.setattr(
            htr_mod,
            "htr_transcribe",
            lambda *a, **kw: "texto htr simulado",
        )
    except Exception:
        pass
    try:
        import pdfsearchable.htr_transkribus as tk_mod
        monkeypatch.setattr(
            tk_mod,
            "transkribus_htr",
            lambda *a, **kw: "texto transkribus simulado",
        )
    except Exception:
        pass
    try:
        import pdfsearchable.htr_escriptorium as es_mod
        monkeypatch.setattr(
            es_mod,
            "escriptorium_htr",
            lambda *a, **kw: "texto escriptorium simulado",
        )
    except Exception:
        pass
    yield


@pytest.fixture
def mock_semantic_models(monkeypatch):
    """
    Evita carregar modelos sentence-transformers reais. Retorna embeddings
    determinísticos baseados em hash do texto.
    """
    try:
        import pdfsearchable.semantic_search as ss

        class FakeModel:
            def encode(self, texts, **kw):
                import hashlib
                import numpy as np

                if isinstance(texts, str):
                    texts = [texts]
                vecs = []
                for t in texts:
                    h = hashlib.sha256(t.encode()).digest()
                    v = np.frombuffer(h, dtype=np.uint8).astype(np.float32)[:32]
                    vecs.append(v / (np.linalg.norm(v) + 1e-9))
                return np.array(vecs)

        monkeypatch.setattr(ss, "_get_model", lambda: FakeModel(), raising=False)
    except Exception:
        pass
    yield


@pytest.fixture
def sample_text():
    """Texto de exemplo para extração de entidades."""
    return """
    Contrato entre João Silva (CPF 111.444.777-35) e Empresa XYZ Ltda (CNPJ 11.222.333/0001-81).
    Contato: joao@email.com e suporte@empresa.com.br.
    IP do servidor: 192.168.1.1 e 2001:0db8:85a3::8a2e:0370:7334.
    Valor: R$ 10.000,00 e USD $ 5.000.
    Parte A: João Silva. Parte B: Empresa XYZ.
    """
