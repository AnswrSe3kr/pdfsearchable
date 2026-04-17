# 2. Funcionalidades

Lista das funcionalidades do **pdfsearchable** agrupadas por área.

---

## Entrada e processamento

| Funcionalidade | Descrição |
|----------------|-----------|
| **Aceitar apenas PDF** | Apenas arquivos com extensão `.pdf`; outros tipos são rejeitados com mensagem clara. |
| **Arquivos e pastas** | Aceita um ou mais arquivos ou um diretório (PDFs recursivos por padrão; use `--no-recursive` para um nível). |
| **Validação** | Verificação de PDF válido, não corrompido; suporte a senha (env ou `--password`). |
| **Extração de texto** | PyMuPDF em modo texto (ou blocks/dict); texto por página e metadados (título, autor, subject, creation_date, mod_date, producer, creator, keywords). |
| **Tabelas estruturadas** | Extração de tabelas via `find_tables()`; texto das células indexado e pesquisável. |
| **Formulários (AcroForm)** | Campos de formulário preenchidos (nome e valor) extraídos e armazenados em `metadata.extended.form_fields`. |
| **Anotações** | Comentários e anotações de texto extraídos e armazenados em `metadata.extended.annotations`. |
| **Metadados XMP** | Presença de XMP detectada; armazenada em `metadata.extended.xmp`. |
| **Correção de OCR com LLM** | Com `PDFSEARCHABLE_OCR_CORRECT=1` e Ollama ativo, texto OCR pode ser corrigido por IA (caracteres trocados, palavras fragmentadas). |
| **Datas legíveis** | Conversão de datas no formato PDF (D:YYYYMMDD...) para DD/MM/AAAA ou DD/MM/AAAA HH:MM. |
| **Extracção de datas do texto** | `extract_dates()` detecta datas em três formatos: DD/MM/AAAA (e variantes com `-` e `.`), ISO 8601 (AAAA-MM-DD), extenso PT/EN (ex.: "20 de março de 2024", "15 January 2022"). Normaliza para AAAA-MM-DD; deduplica; valida intervalo 1800–2100. Gravadas em `identified_dates` no índice; exibidas na sidebar do document-view e no `info`. |
| **Normalização** | Normalização de espaços, quebras de linha e hífens Unicode. |

---

## OCR

| Funcionalidade | Descrição |
|----------------|-----------|
| **OCR** | Sempre ativo (não pode ser desativado). **Todas as páginas** passam por Tesseract por padrão (para capturar todo o texto). Use `PDFSEARCHABLE_OCR_ALWAYS=0` para OCR só em páginas com pouco texto (&lt; 50 caracteres). |
| **Cache de OCR** | Resultado por `(file_id, page_num)` em `.pdfsearchable/ocr_cache/`. |
| **Idiomas** | Variável `PDFSEARCHABLE_OCR_LANG`. Padrão: português (BR/PT), inglês, espanhol, francês, italiano, russo, alemão, hebraico (`por+eng+spa+fra+ita+rus+deu+heb`). |
| **DPI** | Variável `PDFSEARCHABLE_OCR_DPI` (72–600; padrão 300). Valores maiores melhoram documentos históricos e texto pequeno. |
| **PSM** | Variável `PDFSEARCHABLE_OCR_PSM` (0–13; padrão 3). Para manuscrito só com Tesseract: 6 ou 7. |
| **OEM** | Variável `PDFSEARCHABLE_OCR_OEM` (0–3; padrão 3). LSTM (1) costuma ser mais preciso. |
| **Pipeline de pré-processamento** | **Padrão:** OSD (orientação), remoção de bordas, binarização Otsu (global), deskew automático, contraste/nitidez. **Histórico** (`PDFSEARCHABLE_OCR_HISTORICAL=on/auto`): CLAHE (contraste adaptativo local), binarização Sauvola (local, janela 31px), limpeza morfológica (remove ruído/bleed-through), deskew. Detecção automática de documento histórico (papel amarelado, variância de contraste, ruído). Retry por confiança: `PDFSEARCHABLE_OCR_CONFIDENCE_THRESHOLD` e `PDFSEARCHABLE_OCR_RETRY_PSM`. |
| **HTR (manuscrito)** | Três backends disponíveis via `PDFSEARCHABLE_HTR_BACKEND`: **`trocr`** (TrOCR local; requer `[htr]`), **`transkribus`** (cloud, ideal para acervos históricos), **`escriptorium`** (instância Kraken própria). Use `PDFSEARCHABLE_HTR=0` para desativar e usar só Tesseract. Cache separado por backend e página. |
| **HTR multilíngue** | O backend TrOCR suporta **40+ idiomas** com 7 modelos dedicados (en, de, fr, ru/uk/bg/sr/be/mk, sv, ar, th) + fallback latino para pt, es, it, nl, pl, etc. Detecção automática de script via Tesseract OSD. Cache LRU de modelos (thread-safe). Configurável via `PDFSEARCHABLE_HTR_LANG` (forçar idioma) e `PDFSEARCHABLE_HTR_PRINTED` (texto impresso). |
| **HTR histórico** | Com `PDFSEARCHABLE_OCR_HISTORICAL=on/auto`, seleciona modelos especializados: **TRIDIS v2** para manuscritos medievais (pt/es/fr/it/de/la, séc. XI-XVI), **TrOCR-large** para maior capacidade em texto difícil, **Kansallisarkisto** para finlandês multi-century, **Riksarkivet** para sueco histórico. Segmentação de linhas adaptativa para documentos antigos. |

---

## Indexação

| Funcionalidade | Descrição |
|----------------|-----------|
| **Índice incremental** | Skip por `content_hash`; atualização de path quando o mesmo conteúdo é adicionado em outro caminho. |
| **Texto por página** | Armazenamento em `arquivos-processados/{id}/pages/` para busca com número de página. |
| **FTS (SQLite FTS5)** | Índice full-text para buscas rápidas por termo com snippet e página. |
| **Metadados por documento** | id, name, path, num_pages, doc_type, word_count, file_size, content_hash, metadata, pages (com has_ocr), indexed_at, updated_at, language, classification_source, identified_dates, summary, tags, ocr_percentage, ocr_avg_confidence. |
| **Versionamento do índice** | `index.json` com `version`; migração automática ao abrir (v1 → v2 → v3). |
| **Compressão** | Opção de salvar texto em gzip (automática para arquivos &gt; 20 MB). |
| **Arquivos no projeto** | PDFs e texto em `arquivos-processados/` (raiz do projeto); migração automática de `.pdfsearchable/files/` se existir. |

---

## Classificação (tipo do documento)

| Funcionalidade | Descrição |
|----------------|-----------|
| **Heurísticas** | Palavras-chave com peso por posição (início do texto pesa mais); tipos: contrato, nota_fiscal, relatório, procuração, petição, recibo, ata, certidão, etc. |
| **OpenAI (opcional)** | Classificação por IA quando `OPENAI_API_KEY` está definida; modo `PDFSEARCHABLE_AI` (auto/heuristics/openai). |
| **Indicador (IA)** | Documentos classificados pela IA aparecem com “(IA)” no report e no status. |

---

## Busca

| Funcionalidade | Descrição |
|----------------|-----------|
| **Busca por termo** | FTS e máscaras sempre ativos; busca por termo com resultado por página. |
| **Operadores** | No report: **AND**, **OR** e **NEAR N** (ex.: `termo1 OR termo2`, `a AND b`, `x NEAR 5 y`). |
| **Sinônimos** | Config (config.json/env) com mapa termo → equivalente(s); checkbox **"Buscar por sinônimos"** no report; enriquecimento opcional via API (PT-BR: api-dicionario-ptbr, EN-US: API Ninjas Thesaurus) para top palavras. |
| **Máscaras** | CPF, CNPJ (numérico e alfanumérico 2026+), IPv4, IPv6, e-mail, domínio (FQDN), URL e redes sociais. |
| **Resultado por página** | Na CLI (FTS) e no report: exibição do número da página e snippet. |

---

## Report HTML

| Funcionalidade | Descrição |
|----------------|-----------|
| **Estatísticas** | Total de documentos, páginas, palavras e MB indexados; tabela "Por tipo" (doc_type). |
| **Busca no navegador** | Campo de pesquisa com resultados por documento e página; highlight do termo; opções **"Ignorar acentos"** e **"Buscar por sinônimos"**; operadores AND/OR/NEAR; máscaras (CPF, CNPJ, IP, e-mail, etc.); indicador "Buscando…" durante a busca. |
| **Filtros avançados** | Painel expansível: tipo de documento, **pessoa citada** (partes/participantes), faixas de páginas (1–5, 6–20, 21–50, 51+), data de indexação (de/até); lista de documentos e resultados respeitam os filtros. |
| **Nuvem de palavras** | **Interativa** (wordcloud2.js): abas "Geral" e por tipo; clique na palavra dispara busca; **Top palavras**, **Bigramas** e **Termos em destaque** (snippets pré-computados); com Ollama, badge "Enriquecido com IA"; exclusões (env `PDFSEARCHABLE_WORDCLOUD_STOP`). |
| **Referências a locais** | Card sempre visível; lista "X arquivo(s) citaram [local]" (clique centraliza no mapa); **mapa interativo** (Leaflet). **Enriquecimento:** CEPs no texto → **ViaCEP** (localidade/UF); IPs identificados → **IP-API** (geolocalização). Ativação: `PDFSEARCHABLE_VIA_CEP` e `PDFSEARCHABLE_IP_API` (ver [10-config.md](10-config.md)). Estado vazio com mensagem amigável. |
| **Lista de documentos** | Filtro por tipo e idioma; **nome do arquivo é link** para a visualização do documento; metadados (título, autor, datas do PDF, producer, creator, keywords); “X páginas com OCR”; datas de adição/atualização; botão "Copiar comando para remover". |
| **Atividade recente** | Últimas entradas do audit (index_done, index_error, etc.) com timestamp e detalhes. |
| **Duplicatas** | Grupos de arquivos com mesmo content_hash (nome e path). |
| **Cache do report** | Se o índice não mudou (hash em `.pdfsearchable/report_hash.txt`), o report e o document-view não são regerados. |
| **Responsividade** | Desktop: layout em colunas; mobile (≤ 768px): cards empilhados, tabela "Por tipo" com scroll horizontal, abas da nuvem em linha. |
| **Rodapé** | Data de geração do report, versão do índice e versão do pacote. |

---

## Visualização de documento

| Funcionalidade | Descrição |
|----------------|------------|
| **Acesso** | Clicar no nome do arquivo no report abre `document-view.html?id=&lt;id&gt;`. |
| **Layout** | Painel esquerdo: PDF (iframe) e Download; painel direito (sidebar): metadados e **hash de conteúdo**. |
| **Metadados** | Tipo, páginas, palavras, hash de conteúdo, datas de indexação/atualização, idioma, metadados do PDF (título, autor, producer, creator, etc.). |
| **Navegação** | Breadcrumb "Home — [Nome do documento]"; botão "Voltar ao home do report" (report.html). PDF carregado de `arquivos-processados/&lt;id&gt;.pdf`. |

---

## CLI

| Funcionalidade | Descrição |
|----------------|-----------|
| **add** | Adicionar PDFs ou pasta (recursivo e skip-failed por padrão); opções: senha, workers, batch-size, continue, compress, **--reprocess** (reindexar já indexados). OCR sempre ativo. |
| **remove** | Remover por ID ou nome; confirmação em TTY; `--yes` para scripts. |
| **report** | Gerar/atualizar report HTML (apenas gera; use `serve` para servir em HTTP). |
| **serve** | Servidor HTTP com SPA interativa e REST API completa (17 endpoints: índice, busca FTS, wordcloud, mapa, grafo, timeline, SSE, anotações, RAG). Respostas JSON com gzip, CORS, auth Bearer/Basic, rate limiting, timeout SSE (5 min). |
| **search** | Pesquisar termo; máscaras; FTS; `--open` para abrir report ao final. |
| **status** | Listar documentos indexados (ID, nome, páginas, tipo; documentos classificados por IA com indicador "(IA)"). |
| **duplicates** | Listar grupos de duplicatas (mesmo content_hash). |
| **logs** | Exibir últimas entradas da auditoria (`-n` para quantidade). |
| **index-fts** | Reindexar o índice full-text para todos os documentos (útil com `PDFSEARCHABLE_FTS_DEFERRED=1`). |
| **ask** | Perguntar sobre um documento via Ollama (RAG); requer `PDFSEARCHABLE_AI=ollama`. |
| **export** | Exportar em `json`, `jsonl`, `csv`, `markdown` (via `export.py`) ou `obsidian` (YAML frontmatter); `--no-text` para metadados só. |
| **watch** | Monitorizar pasta e indexar automaticamente PDFs novos/modificados. |
| **backup** | Criar cópia de segurança `.tar.gz` de `.pdfsearchable/`. |
| **embed** | Gerar embeddings semânticos via Ollama para `search --semantic`. |
| **chat** | Conversa RAG com a colecção inteira (ou um único documento). |
| **mcp** | Servidor MCP stdio com 6 ferramentas (inclui `index_document`). |
| **doctor** | Diagnóstico do ambiente (PyMuPDF, Tesseract, Ollama, índice, disco). |
| **info** | Metadados detalhados de um documento (ID ou nome). |
| **inspect** | Vista em 5 painéis Rich (Identificação, Enriquecimento, Entidades, Detecções opcionais, Metadados PDF) ou `--json` para pipelines. |
| **migrate** | Força migração do schema do índice (v1/v2 → v3) com `--dry-run`. |
| **dedup-semantic** | Duplicatas **semânticas** por cosseno de embeddings; `--threshold` (0.0–1.0, padrão 0.98). |
| **benchmark-markdown** | Benchmark reprodutível PDF → Markdown. Mede o speedup vs baseline PyMuPDF re-parse (medido: **7.7× mais rápido** em 20 páginas). |
| **add --embed** | Gera embeddings (incremental) logo após indexar. |
| **add --dry-run** | Pré-visualiza novos/reprocessar/ignorar **sem escrever nada**. |

---

## Log e auditoria

| Funcionalidade | Descrição |
|----------------|-----------|
| **Auditoria (audit.jsonl)** | Uma linha JSON por evento: timestamp, action, details, level. |
| **Log (pdfsearchable.log)** | Logger por módulo (ex.: indexer) com nível DEBUG no arquivo. |
| **Eventos auditados** | index_start, index_done, index_error, index_skipped_unchanged, index_updated_path, index_large_file, cli_add, cli_remove, cli_search, cli_export, cli_export_obsidian, fts_index_new, **redaction_detect**, **forensics_scan**, **contracts_extract**, **classifier_feedback**, **snapshot_created**, **snapshot_rotated**. |

---

## Detecções opcionais (indexação)

| Funcionalidade | Descrição |
|----------------|-----------|
| **Redaction detection** | Com `PDFSEARCHABLE_DETECT_REDACTIONS=1`, detecta zonas potencialmente redactadas (tarjas pretas). Resultado em `metadata.redactions` (lista `{page, bbox, area}`) e contagem em `metadata.redaction_zones`. Também funciona em `workers>1` (multiprocessing). |
| **Forensics** | Com `PDFSEARCHABLE_FORENSICS=1`, análise forense do PDF (revisões incrementais, produtor, anomalias de stream). Resultado em `metadata.forensics`. |
| **Contracts** | Com `PDFSEARCHABLE_CONTRACTS=1`, detecção heurística de cláusulas contratuais (partes, objecto, prazo, valor, foro). Resultado em `metadata.contract_summary`. |
| **Fórmulas matemáticas** | Com `PDFSEARCHABLE_DETECT_FORMULAS=1`, detecta fórmulas em notação **LaTeX** (`$…$`, `$$…$$`, `\[…\]`, `equation`/`align`/`gather`/`eqnarray`) e clusters de **símbolos Unicode** matemáticos (∫ ∑ ∏ √ ∂ π θ α β …). Cada hit tem `{page, raw, kind, latex}`; hits Unicode são convertidos best-effort para macros LaTeX (`∫` → `\int`, `α` → `\alpha`, etc.). Re-emitidas como blocos `$$ … $$` na exportação Markdown. |
| **Snapshots automáticos** | Com `PDFSEARCHABLE_AUTO_SNAPSHOT=1`, cada `save_index()` grava cópia rotativa em `.pdfsearchable/.snapshots/index_{timestamp}.json`; `PDFSEARCHABLE_SNAPSHOT_KEEP` define quantas manter (padrão 5). |
| **Classifier feedback** | Com `PDFSEARCHABLE_CLASSIFIER_FEEDBACK=1`, grava amostras rotuladas em `.pdfsearchable/feedback.jsonl` para fine-tuning futuro. |
| **Ollama keep-alive** | `PDFSEARCHABLE_OLLAMA_KEEP_ALIVE` mantém o modelo carregado entre chamadas (padrão `5m`; use `-1` permanente, `0` descarregar). |
| **Classificação com modelo menor** | `PDFSEARCHABLE_OLLAMA_CLASSIFY_MODEL` usa um modelo Ollama separado (e tipicamente menor) para a etapa de classificação. |

---

## Outros

| Funcionalidade | Descrição |
|----------------|-----------|
| **Detecção de idioma** | Heurística ou langdetect; campo `language` por documento (ex.: pt-BR, en). |
| **Processamento em lotes** | `--batch-size N` e `gc` entre lotes para controlar uso de RAM. |
| **Progresso por arquivo** | Durante `add`, exibição do nome do arquivo em processamento. |
