# 1. Fluxo de funcionamento

Visão geral do fluxo do **pdfsearchable** desde a entrada do usuário até a pesquisa e o report.

---

## Visão de alto nível

```
┌─────────────┐     add      ┌──────────────┐     ┌─────────────┐
│  Arquivos   │ ──────────►  │ Processamento│ ──► │   Índice    │
│  PDF / pasta│              │ + Indexação  │     │ .pdfsearchable
└─────────────┘              └──────────────┘     └──────┬──────┘
                                                         │
        ┌────────────────────────────────────────────────┼────────────────────────────────┐
        │                    │                            │                                │
        ▼                    ▼                            ▼                                ▼
   pdfsearchable         pdfsearchable              pdfsearchable                   pdfsearchable
   status                report                    search                           duplicates
   (lista índice)        (gera HTML;                (busca FTS/máscaras,             (grupos por hash)
                          serve para HTTP)           resultado por página)
```

---

## Fluxo detalhado: adicionar PDFs (`add`)

1. **Entrada:** o usuário informa um ou mais caminhos (arquivos `.pdf` ou diretório).
2. **Validação:** cada caminho é verificado; diretórios são expandidos para `*.pdf` recursivamente (padrão; use `--no-recursive` para um nível); arquivos não-PDF são rejeitados.
3. **Para cada PDF:**
   - Cálculo de **content_hash** e **file_size**; verificação se já existe no índice (skip por hash).
   - **Validação do PDF** (abertura, senha se necessário).
   - **Extração de texto** (PyMuPDF): texto nativo por página e metadados (título, autor, datas, producer, creator, keywords).
   - **Extração estendida** (opcional): tabelas (`find_tables`), formulários (AcroForm), anotações e XMP; texto das tabelas é indexado; dados em `metadata.extended`.
   - **OCR:** em **todas as páginas** por padrão (pipeline: OSD, binarização Otsu, deskew, remoção de bordas, Tesseract com retry por confiança; resultado em cache por `(file_id, página)`). **Pipeline histórico** (`PDFSEARCHABLE_OCR_HISTORICAL=on/auto`): CLAHE (contraste adaptativo local), binarização Sauvola (local), limpeza morfológica e modelos HTR especializados (TRIDIS medieval, TrOCR-large). Use `PDFSEARCHABLE_OCR_ALWAYS=0` para OCR só em páginas com pouco texto (&lt; 50 caracteres). Com `PDFSEARCHABLE_OCR_CORRECT=1` e Ollama, texto OCR pode ser corrigido por IA.
   - **Detecção de idioma** (heurística ou langdetect).
   - **Classificação do tipo** (heurísticas, OpenAI ou Ollama): contrato, nota_fiscal, relatório, etc.
   - **Extracção de datas:** `extract_dates()` detecta datas em texto (DD/MM/AAAA, ISO, extenso PT/EN); normaliza para AAAA-MM-DD; guarda em `identified_dates`.
   - **Persistência:** `store.add_file_meta` (metadados no `index.json`), `store.save_file_text` (texto em `arquivos-processados/{id}/`), `store.copy_pdf_to_store` (PDF em `arquivos-processados/{id}.pdf`), `store.fts_index_file` (SQLite FTS5).
   - **Auditoria:** evento `index_done` (ou `index_error` / `index_skipped_unchanged`) em `audit.jsonl`.
4. **Ao final:** exibição de resumo (tabela de indexados, throughput). Para ver a interface no navegador use **`pdfsearchable serve`** (SPA interativa). Para gerar um snapshot HTML offline use **`pdfsearchable report`**.

---

## Fluxo: pesquisa (`search`)

1. **Entrada:** termo ou máscara (ex.: `cpf`, `ip`, texto livre).
2. **Motor:** preferência por **FTS** (SQLite FTS5): busca por página, retorno de `(file_id, page_num, snippet)`.
3. **Fallback:** se não usar FTS ou para máscaras, varredura no texto completo com **search_with_masks** (regex para CPF, CNPJ, e-mail, IP, domínio, redes sociais).
4. **Saída:** tabela de resultados (arquivo, página, trecho) e resumo executivo; opcionalmente abertura do report no navegador.

---

## Fluxo: report (`report`)

O comando **`pdfsearchable report`** gera (ou atualiza) imediatamente o ficheiro `.pdfsearchable/report.html` e `document-view.html` **sem iniciar o servidor HTTP**. Útil para criar um snapshot portátil ou para CI/CD. Para servir o report em HTTP com a SPA interativa e o endpoint RAG (`/api/ask`), use **`pdfsearchable serve`** (que copia os templates da SPA e não usa o `report.html`).

1. **Leitura do índice:** `load_index()` (com migração de versão se necessário).
2. **Agregados:** total de arquivos, páginas, palavras, tamanho em disco; contagem por tipo; ordenação por data (updated_at/indexed_at).
3. **Dados por documento:** metadados, idioma, páginas com OCR, datas legíveis.
4. **Busca no HTML:** `build_search_data()` com texto por página para highlight e “página N”; mapa de sinônimos (config + enriquecimento opcional via API para top palavras).
5. **Nuvem de palavras:** interativa (wordcloud2.js) e por tipo; top palavras e bigramas; exclusões via env.
6. **Referências a locais:** detecção de cidades/estados/países no texto; lista e mapa (Leaflet).
7. **Atividade recente:** leitura de `audit.jsonl` (últimas indexações/erros).
8. **Duplicatas:** grupos por `content_hash` com 2+ arquivos.
9. **Renderização:** templates Jinja2 → `report.html` e `document-view.html` em `.pdfsearchable/`.

---

## Dados persistidos

| Onde | O quê |
|------|--------|
| `.pdfsearchable/index.json` | Índice (versão, lista de arquivos com metadados). |
| `arquivos-processados/{id}.pdf` | Cópia do PDF indexado. |
| `arquivos-processados/{id}/` | Texto completo (`full.txt` ou `.gz`) e pasta `pages/` (texto por página). |
| `.pdfsearchable/fts.sqlite` | Índice full-text (FTS5) por página. |
| `.pdfsearchable/ocr_cache/` | Cache de texto OCR por `(file_id, página)`. |
| `.pdfsearchable/audit.jsonl` | Trilha de auditoria (eventos em JSONL). |
| `.pdfsearchable/pdfsearchable.log` | Log do indexador (e outros que usam `get_logger`). |
| `.pdfsearchable/report.html` | Report HTML (home). |
| `.pdfsearchable/document-view.html` | Visualização de documento (PDF + metadados + hash). |

---

## Dependências entre módulos

- **cli** → indexer, report, store, audit, search, export
- **indexer** → pdf_processor, pdf_extended, ocr, store, ai_classifier, language, audit, content_extractors (inclui `extract_dates`)
- **report** → store, audit (read_audit_trail), config (get_search_synonyms), synonyms_api (enriquecimento opcional)
- **export** → store (load_index, load_file_text); formatos: json, jsonl, csv, markdown
- **search** → usado pela CLI e pelo report (busca no front-end é em memória com `search_data`).
