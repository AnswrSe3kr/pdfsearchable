"""
Testes unitários para os 9 módulos inovadores adicionados na v0.4.0:
  1. contracts        — extracção de datas e prazos contratuais
  2. annotations      — CRUD de anotações por documento
  3. classifier_feedback — loop de aprendizagem few-shot
  4. knowledge_graph  — grafo de entidades entre documentos
  5. timeline         — linha do tempo automática
  6. semantic_search  — cosine similarity e serialização de vectores
  7. redaction        — detecção de ocultações em PDFs
  8. forensics        — análise forense de metadados PDF
  9. table_extractor  — extracção de tabelas e exportação CSV/JSON
"""

import json
import struct
from pathlib import Path

import pytest


# ===========================================================================
# 1. contracts
# ===========================================================================


@pytest.mark.unit
class TestContracts:
    def test_vigencia_extraida(self) -> None:
        from pdfsearchable.contracts import extract_contract_dates

        text = "Vigência: de 01/03/2025 a 28/02/2026"
        cd = extract_contract_dates(text)
        assert cd.start_date == "2025-03-01"
        assert cd.end_date == "2026-02-28"
        assert cd.confidence > 0

    def test_vencimento_extraido(self) -> None:
        from pdfsearchable.contracts import extract_contract_dates

        text = "Vencimento: 31/12/2025"
        cd = extract_contract_dates(text)
        assert cd.end_date == "2025-12-31"
        assert cd.confidence > 0

    def test_validade_extraida(self) -> None:
        from pdfsearchable.contracts import extract_contract_dates

        text = "Válido até 15/06/2026"
        cd = extract_contract_dates(text)
        assert cd.end_date == "2026-06-15"

    def test_prazo_meses(self) -> None:
        from pdfsearchable.contracts import extract_contract_dates

        text = "Prazo: 12 meses. Data de início: 01/01/2025"
        cd = extract_contract_dates(text)
        assert cd.duration_months == 12

    def test_prazo_anos_convertido_para_meses(self) -> None:
        from pdfsearchable.contracts import extract_contract_dates

        text = "Prazo: 2 anos"
        cd = extract_contract_dates(text)
        assert cd.duration_months == 24

    def test_renovacao_automatica(self) -> None:
        from pdfsearchable.contracts import extract_contract_dates

        text = "Cláusula 5ª — Renovação automática salvo aviso contrário."
        cd = extract_contract_dates(text)
        assert cd.auto_renewal is True

    def test_texto_sem_datas(self) -> None:
        from pdfsearchable.contracts import extract_contract_dates

        cd = extract_contract_dates("Contrato de prestação de serviços sem datas.")
        assert cd.confidence == 0.0
        assert cd.end_date is None

    def test_parse_date_iso(self) -> None:
        from pdfsearchable.contracts import _parse_date

        assert _parse_date("2025-06-15") == "2025-06-15"

    def test_parse_date_br(self) -> None:
        from pdfsearchable.contracts import _parse_date

        assert _parse_date("15/06/2025") == "2025-06-15"

    def test_parse_date_extenso(self) -> None:
        from pdfsearchable.contracts import _parse_date

        assert _parse_date("15 de junho de 2025") == "2025-06-15"

    def test_parse_date_invalida(self) -> None:
        from pdfsearchable.contracts import _parse_date

        assert _parse_date("não é data") is None

    def test_termino_extraido(self) -> None:
        from pdfsearchable.contracts import extract_contract_dates

        text = "Término: 31/03/2026"
        cd = extract_contract_dates(text)
        assert cd.end_date == "2026-03-31"


# ===========================================================================
# 2. annotations
# ===========================================================================


@pytest.mark.unit
class TestAnnotationStore:
    def _store(self, tmp_path: Path):
        from pdfsearchable.annotations import AnnotationStore
        return AnnotationStore(tmp_path)

    def test_get_vazio(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        result = store.get("abcdef1234567890")
        assert result == []

    def test_add_e_get(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        fid = "abcdef1234567890"
        ann_id = store.add(fid, {"type": "note", "page": 1, "text": "Texto importante"})
        assert ann_id is not None
        anns = store.get(fid)
        assert len(anns) == 1
        assert anns[0]["text"] == "Texto importante"
        assert anns[0]["type"] == "note"

    def test_add_highlight(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        fid = "abcdef1234567890"
        store.add(fid, {"type": "highlight", "page": 2, "text": "Destaque", "color": "#FF0000"})
        anns = store.get(fid)
        assert anns[0]["color"] == "#FF0000"

    def test_update(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        fid = "abcdef1234567890"
        ann_id = store.add(fid, {"type": "note", "page": 1, "text": "Original"})
        ok = store.update(fid, ann_id, {"note": "Nota actualizada"})
        assert ok is True
        anns = store.get(fid)
        assert anns[0]["note"] == "Nota actualizada"

    def test_delete(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        fid = "abcdef1234567890"
        ann_id = store.add(fid, {"type": "note", "page": 1, "text": "Para apagar"})
        ok = store.delete(fid, ann_id)
        assert ok is True
        anns = store.get(fid)
        assert anns == []

    def test_delete_inexistente_retorna_false(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        ok = store.delete("abcdef1234567890", "nao-existe")
        assert ok is False

    def test_file_id_invalido_rejeitado(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        # file_id inválido → retorna lista vazia
        result = store.get("id_invalido")
        assert result == []

    def test_count(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        fid = "abcdef1234567890"
        store.add(fid, {"type": "note", "page": 1, "text": "Um"})
        store.add(fid, {"type": "note", "page": 2, "text": "Dois"})
        assert store.count(fid) == 2

    def test_persistencia(self, tmp_path: Path) -> None:
        """Anotação persiste após recriar o store."""
        from pdfsearchable.annotations import AnnotationStore
        fid = "abcdef1234567890"
        s1 = AnnotationStore(tmp_path)
        s1.add(fid, {"type": "note", "page": 1, "text": "Persistente"})
        s2 = AnnotationStore(tmp_path)
        anns = s2.get(fid)
        assert len(anns) == 1
        assert anns[0]["text"] == "Persistente"

    def test_escrita_atomica(self, tmp_path: Path) -> None:
        """Não deve deixar ficheiro .tmp após operação normal."""
        store = self._store(tmp_path)
        fid = "abcdef1234567890"
        store.add(fid, {"type": "note", "page": 1, "text": "Teste"})
        tmp_files = list((tmp_path / "annotations").glob("*.tmp"))
        assert tmp_files == []


# ===========================================================================
# 3. classifier_feedback
# ===========================================================================


@pytest.mark.unit
class TestClassifierFeedback:
    def test_record_e_get(self, tmp_path: Path, monkeypatch) -> None:
        import pdfsearchable.classifier_feedback as fb
        monkeypatch.setattr(
            "pdfsearchable.classifier_feedback._feedback_file",
            lambda: tmp_path / "examples.json",
        )
        # Reiniciar dados
        fb.clear_examples()
        fb.record_correction("abcdef1234567890", "contrato", "Texto do contrato")
        examples = fb.get_few_shot_examples()
        assert len(examples) == 1
        assert examples[0]["correct_type"] == "contrato"

    def test_idempotente_mesmo_file_id(self, tmp_path: Path, monkeypatch) -> None:
        import pdfsearchable.classifier_feedback as fb
        monkeypatch.setattr(
            "pdfsearchable.classifier_feedback._feedback_file",
            lambda: tmp_path / "examples.json",
        )
        fb.clear_examples()
        fb.record_correction("abcdef1234567890", "contrato", "Texto A")
        fb.record_correction("abcdef1234567890", "fatura", "Texto B")
        examples = fb.get_few_shot_examples()
        # Deve manter apenas o mais recente para o mesmo file_id
        assert len(examples) == 1
        assert examples[0]["correct_type"] == "fatura"

    def test_janela_deslizante(self, tmp_path: Path, monkeypatch) -> None:
        import pdfsearchable.classifier_feedback as fb
        monkeypatch.setattr(
            "pdfsearchable.classifier_feedback._feedback_file",
            lambda: tmp_path / "examples.json",
        )
        monkeypatch.setattr(fb, "MAX_EXAMPLES", 3)
        fb.clear_examples()
        for i in range(5):
            fid = f"{i:016x}"
            fb.record_correction(fid, "documento", f"Texto {i}")
        assert fb.example_count() <= 3

    def test_clear(self, tmp_path: Path, monkeypatch) -> None:
        import pdfsearchable.classifier_feedback as fb
        monkeypatch.setattr(
            "pdfsearchable.classifier_feedback._feedback_file",
            lambda: tmp_path / "examples.json",
        )
        fb.record_correction("abcdef1234567890", "contrato", "X")
        fb.clear_examples()
        assert fb.example_count() == 0

    def test_max_n_respeitado(self, tmp_path: Path, monkeypatch) -> None:
        import pdfsearchable.classifier_feedback as fb
        monkeypatch.setattr(
            "pdfsearchable.classifier_feedback._feedback_file",
            lambda: tmp_path / "examples.json",
        )
        fb.clear_examples()
        for i in range(10):
            fb.record_correction(f"{i:016x}", "contrato", f"T{i}")
        examples = fb.get_few_shot_examples(max_n=3)
        assert len(examples) <= 3


# ===========================================================================
# 4. knowledge_graph
# ===========================================================================


@pytest.mark.unit
class TestKnowledgeGraph:
    def _sample_files(self) -> list[dict]:
        return [
            {
                "id": "aabbccdd11223344",
                "name": "contrato_a.pdf",
                "doc_type": "contrato",
                "metadata": {
                    "identified_emails": ["alice@example.com"],
                    "identified_cpfs": ["111.444.777-35"],
                    "parties": ["Empresa Alpha Lda"],
                    "monetary_values": [{"value_str": "R$ 10.000,00"}],
                },
            },
            {
                "id": "1122334455667788",
                "name": "contrato_b.pdf",
                "doc_type": "contrato",
                "metadata": {
                    "identified_emails": ["alice@example.com"],
                    "parties": ["Empresa Beta SA"],
                },
            },
        ]

    def test_nos_e_arestas_criados(self) -> None:
        from pdfsearchable.knowledge_graph import build_graph

        g = build_graph(self._sample_files())
        assert len(g["nodes"]) > 0
        assert len(g["edges"]) > 0

    def test_email_partilhado_gera_dois_nos_doc(self) -> None:
        from pdfsearchable.knowledge_graph import build_graph

        g = build_graph(self._sample_files())
        doc_nodes = [n for n in g["nodes"] if n["type"] == "document"]
        assert len(doc_nodes) == 2

    def test_email_partilhado_presente_nos_nos(self) -> None:
        from pdfsearchable.knowledge_graph import build_graph

        g = build_graph(self._sample_files())
        email_nodes = [n for n in g["nodes"] if n["type"] == "email"]
        assert any("alice@example.com" in n["label"] for n in email_nodes)

    def test_grafo_vazio_sem_ficheiros(self) -> None:
        from pdfsearchable.knowledge_graph import build_graph

        g = build_graph([])
        assert g["nodes"] == []
        assert g["edges"] == []

    def test_get_graph_stats(self) -> None:
        from pdfsearchable.knowledge_graph import get_graph_stats

        stats = get_graph_stats(self._sample_files())
        assert stats["nodes"] > 0
        assert stats["edges"] >= 0
        assert "document" in stats["entity_types"]

    def test_generate_graph_html(self, tmp_path: Path) -> None:
        from pdfsearchable.knowledge_graph import generate_graph_html

        out = generate_graph_html(self._sample_files(), tmp_path / "graph.html")
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "d3" in content.lower() or "D3" in content

    def test_nos_tem_campos_obrigatorios(self) -> None:
        from pdfsearchable.knowledge_graph import build_graph

        g = build_graph(self._sample_files())
        for node in g["nodes"]:
            assert "id" in node
            assert "label" in node
            assert "type" in node

    def test_arestas_tem_campos_obrigatorios(self) -> None:
        from pdfsearchable.knowledge_graph import build_graph

        g = build_graph(self._sample_files())
        for edge in g["edges"]:
            assert "source" in edge
            assert "target" in edge
            assert "weight" in edge


# ===========================================================================
# 5. timeline
# ===========================================================================


@pytest.mark.unit
class TestTimeline:
    def _sample_files(self) -> list[dict]:
        return [
            {
                "id": "aabbccdd11223344",
                "name": "doc2023.pdf",
                "doc_type": "contrato",
                "metadata": {"creation_date": "D:20230615120000"},
                "indexed_at": "2023-06-15T10:00:00Z",
            },
            {
                "id": "1122334455667788",
                "name": "doc2024.pdf",
                "doc_type": "fatura",
                "metadata": {"creation_date": "D:20240301000000"},
                "indexed_at": "2024-03-01T08:00:00Z",
            },
        ]

    def test_build_timeline_retorna_entradas(self) -> None:
        from pdfsearchable.timeline import build_timeline

        entries = build_timeline(self._sample_files())
        assert len(entries) == 2

    def test_entradas_ordenadas_por_data(self) -> None:
        from pdfsearchable.timeline import build_timeline

        entries = build_timeline(self._sample_files())
        years = [e.year for e in entries]
        assert years == sorted(years)

    def test_entry_tem_campos_obrigatorios(self) -> None:
        from pdfsearchable.timeline import build_timeline

        entries = build_timeline(self._sample_files())
        for e in entries:
            assert e.year > 0
            assert e.file_id
            assert e.name

    def test_parse_pdf_date(self) -> None:
        from pdfsearchable.timeline import _parse_pdf_date

        result = _parse_pdf_date("D:20230615120000")
        assert result == (2023, 6, 15)

    def test_parse_iso_date(self) -> None:
        from pdfsearchable.timeline import _parse_iso_date

        result = _parse_iso_date("2024-03-01")
        assert result == (2024, 3, 1)

    def test_parse_br_date(self) -> None:
        from pdfsearchable.timeline import _parse_br_date

        result = _parse_br_date("15/06/2023")
        assert result == (2023, 6, 15)

    def test_parse_pt_date(self) -> None:
        from pdfsearchable.timeline import _parse_pt_date

        result = _parse_pt_date("15 de março de 2024")
        assert result == (2024, 3, 15)

    def test_parse_date_invalida_retorna_none(self) -> None:
        from pdfsearchable.timeline import _parse_any_date

        assert _parse_any_date("sem data aqui") is None

    def test_group_by_year(self) -> None:
        from pdfsearchable.timeline import build_timeline, group_by_year

        entries = build_timeline(self._sample_files())
        grouped = group_by_year(entries)
        assert isinstance(grouped, dict)
        assert all(isinstance(k, int) for k in grouped)

    def test_timeline_stats(self) -> None:
        from pdfsearchable.timeline import build_timeline, timeline_stats

        entries = build_timeline(self._sample_files())
        stats = timeline_stats(entries)
        assert stats["total"] == 2
        assert stats["span_years"] >= 0

    def test_ficheiro_sem_data_usa_indexed_at(self) -> None:
        from pdfsearchable.timeline import build_timeline

        files = [
            {
                "id": "aabbccdd11223344",
                "name": "sem_data.pdf",
                "doc_type": "documento",
                "metadata": {},
                "indexed_at": "2022-01-10T00:00:00Z",
            }
        ]
        entries = build_timeline(files)
        assert len(entries) == 1
        assert entries[0].year == 2022


# ===========================================================================
# 6. semantic_search — funções puras (sem Ollama)
# ===========================================================================


@pytest.mark.unit
class TestSemanticSearch:
    def test_cosine_identico(self) -> None:
        from pdfsearchable.semantic_search import _cosine

        v = [1.0, 0.0, 0.0]
        assert abs(_cosine(v, v) - 1.0) < 1e-6

    def test_cosine_ortogonal(self) -> None:
        from pdfsearchable.semantic_search import _cosine

        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine(a, b)) < 1e-6

    def test_cosine_oposto(self) -> None:
        from pdfsearchable.semantic_search import _cosine

        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(_cosine(a, b) + 1.0) < 1e-6

    def test_cosine_vector_nulo(self) -> None:
        from pdfsearchable.semantic_search import _cosine

        assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_vec_to_blob_e_volta(self) -> None:
        from pdfsearchable.semantic_search import _vec_to_blob, _blob_to_vec

        vec = [0.1, 0.2, 0.3, 0.4]
        blob = _vec_to_blob(vec)
        assert isinstance(blob, bytes)
        assert len(blob) == 4 * 4  # 4 floats × 4 bytes
        recovered = _blob_to_vec(blob)
        for a, b in zip(vec, recovered):
            assert abs(a - b) < 1e-6

    def test_blob_e_struct_pack_compativeis(self) -> None:
        """Garantir que _vec_to_blob usa format float32 (4 bytes por valor)."""
        from pdfsearchable.semantic_search import _vec_to_blob

        vec = [1.0, 2.0]
        blob = _vec_to_blob(vec)
        expected = struct.pack("2f", 1.0, 2.0)
        assert blob == expected


# ===========================================================================
# 7. redaction — teste com PDF limpo (sem ocultações)
# ===========================================================================


@pytest.mark.unit
class TestRedaction:
    def test_pdf_limpo_sem_redaccoes(self, tmp_path: Path) -> None:
        fitz = pytest.importorskip("fitz")
        from pdfsearchable.redaction import detect_redactions

        pdf_path = tmp_path / "limpo.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), "Texto normal sem ocultações.")
        doc.save(str(pdf_path))
        doc.close()

        report = detect_redactions(pdf_path)
        assert report.has_redactions is False
        assert report.total_redacted_zones == 0

    def test_report_tem_campos_obrigatorios(self, tmp_path: Path) -> None:
        fitz = pytest.importorskip("fitz")
        from pdfsearchable.redaction import detect_redactions

        pdf_path = tmp_path / "test.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        report = detect_redactions(pdf_path)
        assert hasattr(report, "has_redactions")
        assert hasattr(report, "suspicious")
        assert hasattr(report, "total_redacted_zones")
        assert hasattr(report, "summary")
        assert hasattr(report, "pages")

    def test_pdf_inexistente_retorna_report_vazio(self, tmp_path: Path) -> None:
        from pdfsearchable.redaction import detect_redactions

        report = detect_redactions(tmp_path / "nao_existe.pdf")
        assert report.has_redactions is False


# ===========================================================================
# 8. forensics — teste com PDF limpo
# ===========================================================================


@pytest.mark.unit
class TestForensics:
    def test_pdf_limpo_sem_anomalias_criticas(self, tmp_path: Path) -> None:
        fitz = pytest.importorskip("fitz")
        from pdfsearchable.forensics import analyse_forensics

        pdf_path = tmp_path / "limpo.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        report = analyse_forensics(pdf_path)
        # PDF sem metadados suspeitos não deve ter risco elevado
        assert report.risk_score < 80

    def test_report_tem_campos_obrigatorios(self, tmp_path: Path) -> None:
        fitz = pytest.importorskip("fitz")
        from pdfsearchable.forensics import analyse_forensics

        pdf_path = tmp_path / "test.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        report = analyse_forensics(pdf_path)
        assert hasattr(report, "risk_score")
        assert hasattr(report, "suspicious")
        assert hasattr(report, "anomalies")
        assert hasattr(report, "summary")
        assert isinstance(report.anomalies, list)

    def test_risk_score_no_intervalo(self, tmp_path: Path) -> None:
        fitz = pytest.importorskip("fitz")
        from pdfsearchable.forensics import analyse_forensics

        pdf_path = tmp_path / "test.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        report = analyse_forensics(pdf_path)
        assert 0 <= report.risk_score <= 100

    def test_pdf_inexistente_retorna_report_vazio(self, tmp_path: Path) -> None:
        from pdfsearchable.forensics import analyse_forensics

        report = analyse_forensics(tmp_path / "nao_existe.pdf")
        assert report.risk_score == 0


# ===========================================================================
# 9. table_extractor
# ===========================================================================


@pytest.mark.unit
class TestTableExtractor:
    def _make_pdf_with_table(self, tmp_path: Path) -> Path:
        """Cria um PDF simples com texto tabelar (PyMuPDF find_tables)."""
        fitz = pytest.importorskip("fitz")
        pdf_path = tmp_path / "tabela.pdf"
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        # Inserir linhas de texto que formam uma tabela simples
        page.insert_text((50, 100), "Nome        Valor       Data")
        page.insert_text((50, 120), "Alpha       1000        2024-01-01")
        page.insert_text((50, 140), "Beta        2000        2024-02-01")
        doc.save(str(pdf_path))
        doc.close()
        return pdf_path

    def test_extract_tables_retorna_lista(self, tmp_path: Path) -> None:
        pytest.importorskip("fitz")
        from pdfsearchable.table_extractor import extract_tables

        pdf_path = self._make_pdf_with_table(tmp_path)
        tables = extract_tables(pdf_path)
        assert isinstance(tables, list)
        # PDF sem tabelas nativas reais → lista pode ser vazia, mas não deve falhar

    def test_extracted_table_campos(self, tmp_path: Path) -> None:
        """ExtractedTable tem os campos correctos."""
        from pdfsearchable.table_extractor import ExtractedTable

        t = ExtractedTable(
            page=1,
            table_index=0,
            headers=["Nome", "Valor"],
            rows=[["Alpha", "1000"], ["Beta", "2000"]],
            bbox=(0.0, 0.0, 200.0, 100.0),
            confidence=1.0,
            source="pymupdf",
        )
        assert t.page == 1
        assert t.headers == ["Nome", "Valor"]
        assert len(t.rows) == 2
        assert t.confidence == 1.0

    def test_tables_to_json(self, tmp_path: Path) -> None:
        from pdfsearchable.table_extractor import ExtractedTable, tables_to_json

        tables = [
            ExtractedTable(
                page=1,
                table_index=0,
                headers=["A", "B"],
                rows=[["1", "2"]],
                bbox=(0.0, 0.0, 100.0, 50.0),
                confidence=1.0,
                source="pymupdf",
            )
        ]
        out = tables_to_json(tables, tmp_path, "test")
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "tables" in data
        assert data["tables"][0]["headers"] == ["A", "B"]

    def test_tables_to_csv(self, tmp_path: Path) -> None:
        from pdfsearchable.table_extractor import ExtractedTable, tables_to_csv

        tables = [
            ExtractedTable(
                page=1,
                table_index=0,
                headers=["Nome", "Valor"],
                rows=[["Alpha", "1000"]],
                bbox=(0.0, 0.0, 100.0, 50.0),
                confidence=1.0,
                source="pymupdf",
            )
        ]
        paths = tables_to_csv(tables, tmp_path, "test")
        assert len(paths) == 1
        assert paths[0].exists()
        content = paths[0].read_text(encoding="utf-8-sig")
        assert "Nome" in content
        assert "Alpha" in content

    def test_tables_to_csv_lista_vazia(self, tmp_path: Path) -> None:
        from pdfsearchable.table_extractor import tables_to_csv

        paths = tables_to_csv([], tmp_path, "vazio")
        assert paths == []
