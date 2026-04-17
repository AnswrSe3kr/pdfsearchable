# 8. Processamento e Indexação

Detalhes do **processamento** de PDFs e da **indexação** no pdfsearchable.

---

## Pipeline por documento

Para cada PDF aceito no comando `add`:

1. **Identificação:** `file_id` (hash do path absoluto, 16 caracteres), `content_hash` (hash do conteúdo do arquivo, 32 caracteres), `file_size`.
2. **Skip incremental:** se já existir um arquivo no índice com o mesmo `content_hash`, o arquivo é ignorado ou apenas o path é atualizado (quando o path mudou).
3. **Validação:** abertura do PDF (PyMuPDF); se protegido por senha, uso de senha (env ou `--password`); detecção de corrupção.
4. **Extração de texto:** PyMuPDF em modo configurável (`text`, `blocks`, `dict`); uma string por página; concatenação em texto completo; normalização (espaços, hífens Unicode).
5. **Metadados:** título, autor, subject, creation_date, mod_date, producer, creator, keywords; datas convertidas para formato legível (DD/MM/AAAA ou DD/MM/AAAA HH:MM).
6. **OCR:** por padrão em **todas as páginas** (`PDFSEARCHABLE_OCR_ALWAYS=1`); com `0`, apenas em páginas com menos de 50 caracteres (após strip). Pipeline: detecção de orientação (OSD), remoção de bordas, binarização Otsu, deskew automático, pré-processamento (contraste/nitidez), Tesseract com retry por confiança; resultado em cache por (file_id, página); flag `has_ocr` por página. Opcional: correção de OCR com LLM (`PDFSEARCHABLE_OCR_CORRECT=1` e Ollama).
7. **Detecção de idioma:** heurística (palavras comuns PT/EN) ou langdetect; campo `language` (pt-BR, en, etc.).
8. **Classificação de tipo:** heurísticas, Ollama ou OpenAI; campo `doc_type` e opcionalmente `classification_source`.
   - **Extracção de datas do texto:** `extract_dates(full_text)` (em `content_extractors`) detecta datas nos formatos DD/MM/AAAA (e variantes `-`/`.`), ISO 8601 e extenso PT/EN; normaliza para AAAA-MM-DD; deduplica; valida intervalo 1800–2100; guarda em `identified_dates` no índice.
9. **Persistência:**  
   - **Índice:** `add_file_meta` (index.json em `.pdfsearchable/`) com todos os metadados, `indexed_at` e `updated_at` (ISO UTC).  
   - **PDF e texto:** `copy_pdf_to_store` (cópia do PDF em `arquivos-processados/{id}.pdf`), `save_file_text` (full.txt ou full.txt.gz e pasta pages/ em `arquivos-processados/{id}/`).  
   - **FTS:** `fts_index_file` (SQLite FTS5 em `.pdfsearchable/fts.sqlite`, conteúdo por página).  
   - **Cópia do PDF:** `copy_pdf_to_store` grava o PDF em `arquivos-processados/{file_id}.pdf`.
10. **Auditoria:** evento `index_done` (ou `index_error`). Para ver o report em HTTP use **`pdfsearchable serve`** (o report é gerado ao arrancar o servidor).

---

## Estrutura do índice (index.json)

- **version:** versão do esquema (ex.: 3).
- **files:** lista de objetos, um por documento:
  - id, name, original_path, num_pages, doc_type, word_count, file_size, content_hash
  - metadata (title, author, subject, creation_date, mod_date, producer, creator, keywords)
  - pages: [{ n, char_count, has_ocr }, ...]
  - indexed_at, updated_at, language
  - classification_source (quando aplicável)
  - identified_dates (lista de datas no formato AAAA-MM-DD encontradas no texto)
  - summary, tags (quando AI=ollama ou openai)
  - ocr_percentage, ocr_avg_confidence (estatísticas de OCR)

Migração automática ao abrir: v1 → v2 (file_size, content_hash, metadata, pages) e v2 → v3 (indexed_at, updated_at, language, has_ocr por página).

---

## Armazenamento de texto e PDFs

- **Diretório de arquivos processados:** `arquivos-processados/` (na raiz do projeto).
- **Por documento:** `arquivos-processados/{file_id}.pdf` (cópia do PDF); `arquivos-processados/{file_id}/full.txt` ou `.gz` (texto completo); `arquivos-processados/{file_id}/pages/NNNN.txt` ou `.gz` (texto da página N, 1-based).
- **FTS:** `.pdfsearchable/fts.sqlite` — tabela virtual FTS5 com (content, file_id, page_num).
- **Migração:** se existir `.pdfsearchable/files/`, o conteúdo é copiado uma vez para `arquivos-processados/` ao garantir o store.

---

## OCR

- **Condição:** com `PDFSEARCHABLE_OCR_ALWAYS=1` (padrão), OCR em **todas** as páginas; com `0`, apenas em páginas com menos de 50 caracteres (após strip). Dependências pytesseract e Pillow (incluídas).
- **Renderização:** PyMuPDF (página → pixmap em DPI configurável via `PDFSEARCHABLE_OCR_DPI`, padrão 300, máx. 600) → PNG em memória.
- **Pipeline de pré-processamento padrão:** (1) detecção de orientação via OSD (`PDFSEARCHABLE_OCR_OSD`, padrão ativo); (2) remoção de bordas de scan (`PDFSEARCHABLE_OCR_BORDER_REMOVE`); (3) binarização Otsu adaptativa (`PDFSEARCHABLE_OCR_BINARIZE`); (4) deskew automático (`PDFSEARCHABLE_OCR_DESKEW`); (5) contraste e nitidez (`PDFSEARCHABLE_OCR_PREPROCESS`; ignorado se Otsu ativo). Melhora precisão em scans e imagens fracas.
- **Pipeline histórico** (`PDFSEARCHABLE_OCR_HISTORICAL=on/auto`): (1) remoção de bordas; (2) **CLAHE** — Contrast Limited Adaptive Histogram Equalization (tiles 8×8, clip 2.5) para texto desbotado; (3) **binarização Sauvola** (limiar local adaptativo, janela 31px, k=0.2) em vez de Otsu — superior para papel envelhecido, manchas, iluminação desigual; (4) **limpeza morfológica** (opening + closing) para remover ruído, bleed-through e manchas de tinta; (5) deskew automático. Detecção automática (`auto`): heurística baseada em cor do papel (amarelado), variância de contraste e nível de ruído.
- **Tesseract:** `image_to_string` com idiomas (`PDFSEARCHABLE_OCR_LANG`), PSM (`PDFSEARCHABLE_OCR_PSM`) e OEM (`PDFSEARCHABLE_OCR_OEM`). Retry adaptativo: quando a confiança fica abaixo de `PDFSEARCHABLE_OCR_CONFIDENCE_THRESHOLD` (padrão 40), são tentados PSMs alternativos em `PDFSEARCHABLE_OCR_RETRY_PSM` (ex.: `6,4`). O texto de saída é normalizado (espaços e quebras colapsados).
- **Correção com LLM:** com `PDFSEARCHABLE_OCR_CORRECT=1` e Ollama ativo, o texto OCR pode ser corrigido por IA (caracteres trocados, palavras fragmentadas).
- **Cache:** arquivo de texto por (file_id, page_num) em `.pdfsearchable/ocr_cache/`.

---

## Processamento em lote

- **Workers:** `index_pdfs(..., workers=N)` usa ThreadPoolExecutor; N=1 é sequencial; N&gt;1 paralelo.
- **Batch size:** quando `batch_size` é definido, a lista de paths é fatiada em chunks; cada chunk é processado (com workers) e em seguida é chamado `gc.collect()` antes do próximo chunk.
- **Callback de progresso:** `on_file_progress(path, current, total)` permite à CLI atualizar a descrição da barra de progresso.

---

## Retry e resiliência

- **Retry:** até 3 tentativas com backoff exponencial em caso de exceção durante extração/OCR.
- **Skip failed:** com `--skip-failed` ou `--continue`, exceção em um arquivo não interrompe o lote; o erro é registrado na auditoria e no log.

---

## Referência no código

- **indexer:** `index_pdf`, `index_pdfs`, `_extract_with_ocr`.
- **store:** `add_file_meta`, `save_file_text`, `fts_index_file`, `load_index`, `load_file_text`, `load_page_text`.
- **pdf_processor:** `validate_pdf`, `extract_text_from_pdf`, `content_hash`, `file_size`, `format_pdf_date`, `normalize_text`.
- **ocr:** `ocr_page`, `render_page_to_image`, `get_ocr_lang`, `get_ocr_dpi`.
