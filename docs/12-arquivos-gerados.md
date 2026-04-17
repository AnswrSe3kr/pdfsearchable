# 12. Arquivos gerados e persistidos

Referência dos **ficheiros e diretórios** criados ou atualizados pelo pdfsearchable, com indicação de quando e por que comando são gerados.

---

## Visão geral

| Origem / comando | O que gera ou altera |
|------------------|------------------------|
| **add**          | Índice, FTS, arquivos-processados (PDF + texto), cache OCR, auditoria. |
| **report**       | report.html, document-view.html (static), report_hash.txt, geocode_cache.json. |
| **serve**        | Copia templates da SPA (app.html, wordcloud.html, map.html, graph.html, timeline.html, document-view.html) para `.pdfsearchable/`. Não gera `report.html`. |
| **remove**       | Remove entrada do índice, FTS, ficheiros do documento em arquivos-processados e cache OCR do documento. |
| **index-fts**    | Atualiza o conteúdo do FTS (fts.sqlite). |
| **Configuração** | config.toml / config.json (opcionais, criados pelo utilizador em .pdfsearchable). |

Todos os caminhos são relativos à **pasta do projeto** (directório de trabalho onde os comandos são executados).

---

## Diretório `.pdfsearchable/`

Criado no primeiro `add` (ou ao garantir o store) com **permissões `700`** (`rwx------`) para impedir leitura por outros utilizadores em servidores partilhados. Contém o índice, FTS, cache, auditoria, log e — após `serve` — o report.

| Ficheiro ou pasta | Descrição | Gerado/alterado por |
|-------------------|-----------|----------------------|
| **index.json** | Índice principal: versão do esquema e lista de documentos (id, name, path, num_pages, doc_type, word_count, file_size, content_hash, metadata, pages, indexed_at, updated_at, language, classification_source, etc.). Migração automática de versão ao abrir. | **add** (cria/atualiza); **remove** (remove entrada). |
| **fts.sqlite** | Base SQLite com índice full-text (FTS5) por documento e página para buscas rápidas. WAL mode activado. | **add** (indexa cada documento; ou em lote ao final se FTS_DEFERRED=1); **remove** (remove entradas do documento); **index-fts** (reindexa todos). |
| **embeddings.sqlite** | Base SQLite com embeddings semânticos por documento (blob de floats, modelo e data de indexação). Opcional — gerado pelo comando `embed`. | **embed** (cria/actualiza); **remove** não limpa automaticamente (executar `embed --force` após remoções). |
| **ocr_cache/** | Cache de texto OCR por (file_id, página). Um ficheiro de texto por combinação, ex.: `0a1b2c3d4e5f6789_p0001.txt`. | **add** (ao executar OCR numa página). **remove** (apaga entradas do documento removido). |
| **audit.jsonl** | Trilha de auditoria: uma linha JSON por evento (index_start, index_done, index_error, cli_add, cli_remove, etc.). | **add**, **remove**, **search**, **index-fts** e outros comandos que registam eventos. |
| **pdfsearchable.log** | Log de execução (indexer, cli, etc.) com rotação por tamanho. | Qualquer comando que use o logger (add, serve, remove, etc.). |
| **report.html** | Snapshot HTML estático (estatísticas, busca, nuvem, mapa, lista de documentos, duplicatas, atividade). Autónomo — abrível sem servidor. | **report** (gerado pelo comando `pdfsearchable report`). |
| **document-view.html** (static) | Página de visualização de documento estática (PDF + metadados + hash; resumo/tags quando disponíveis). Dados embutidos no HTML. | **report** (gerado pelo comando `pdfsearchable report`). |
| **app.html**, **wordcloud.html**, **map.html**, **graph.html**, **timeline.html** | Templates da SPA interativa servida pelo `pdfsearchable serve`. Copiados dos templates do pacote. | **serve** (cópia dos templates ao arrancar). |
| **report_hash.txt** | Hash do estado do índice usado para decidir se o report estático deve ser regerado. | **report** (escrito após gerar o report). |
| **geocode_cache.json** | Cache de geocoding (Nominatim, etc.) para o mapa de referências a locais. | **report** (durante a geração do report, se houver locais sem coordenadas e GEOCODE ativo). |
| **config.toml** / **config.json** | Configuração opcional (sinônimos, AI, OCR, etc.). Não são criados automaticamente; o utilizador pode colocá-los em `.pdfsearchable/`. | Utilizador (opcional). |
| **.snapshots/index_{timestamp}.json** | Cópias rotativas do índice criadas antes de cada `save_index()` quando `PDFSEARCHABLE_AUTO_SNAPSHOT=1`. Rotação automática: mantém os últimos `PDFSEARCHABLE_SNAPSHOT_KEEP` (padrão 5). | **add**, **remove**, **migrate** — indirectamente via `save_index`. |
| **feedback.jsonl** | Amostras rotuladas (label + trecho de texto) gravadas quando `PDFSEARCHABLE_CLASSIFIER_FEEDBACK=1`. Base para fine-tuning futuro do classificador. | **add** (quando há classificação e o flag está activo). |

---

## Diretório `arquivos-processados/`

Fica na **raiz do projeto** (ao lado de `.pdfsearchable/`). Contém cópias dos PDFs indexados e o texto extraído por documento.

| Caminho | Descrição | Gerado por |
|---------|-----------|------------|
| **&lt;file_id&gt;.pdf** | Cópia do PDF indexado. O servidor serve este ficheiro em `/arquivos-processados/<file_id>.pdf` para o iframe da visualização. | **add** (`copy_pdf_to_store`). **remove** apaga. |
| **&lt;file_id&gt;/full.txt** | Texto completo do documento (extração + OCR concatenados). | **add** (`save_file_text`). **remove** apaga a pasta do documento. |
| **&lt;file_id&gt;/full.txt.gz** | Idem, comprimido (quando `--compress` ou ficheiro &gt; 20 MB). | **add**. **remove** apaga. |
| **&lt;file_id&gt;/pages/NNNN.txt** | Texto da página N (1-based), ex.: `0001.txt`, `0002.txt`. | **add** (quando se guarda texto por página). **remove** apaga. |
| **&lt;file_id&gt;/pages/NNNN.txt.gz** | Idem, comprimido. | **add** (com compress). **remove** apaga. |

**Legado:** se existir `.pdfsearchable/files/`, o código migra o conteúdo uma vez para `arquivos-processados/`. Também é suportada leitura de `<file_id>.txt` em `arquivos-processados/` (ficheiro único de texto) se existir.

- **file_id:** 16 caracteres hex (derivado do path absoluto do PDF). Ex.: `0a1b2c3d4e5f6789`.

---

## Ordem de criação típica

1. **Primeiro uso:** `pdfsearchable add documento.pdf`  
   - Cria `.pdfsearchable/` (index.json, fts.sqlite, ocr_cache/, audit.jsonl, pdfsearchable.log).  
   - Cria `arquivos-processados/<id>.pdf`, `arquivos-processados/<id>/full.txt` e opcionalmente `pages/`.

2. **Gerar snapshot offline:** `pdfsearchable report`
   - Cria ou actualiza `.pdfsearchable/report.html`, `document-view.html` (static) e `report_hash.txt`.
   - Opcionalmente escreve/actualiza `geocode_cache.json`.

   **Interface viva:** `pdfsearchable serve`
   - Copia os templates da SPA (`app.html`, etc.) para `.pdfsearchable/` e inicia o servidor HTTP.
   - Não gera `report.html` — a SPA carrega os dados via REST API em tempo real.

3. **Remover documento:** `pdfsearchable remove <id ou nome>`  
   - Remove a entrada em index.json, as linhas no FTS, os ficheiros em `arquivos-processados/<id>.pdf` e `arquivos-processados/<id>/`, e o cache OCR desse documento.

---

## Tamanhos e manutenção

- **index.json** e **fts.sqlite** crescem com o número de documentos. Para muitos ficheiros, considerar backup e, se necessário, reconstruir o índice com `add` após limpeza.
- **ocr_cache/** pode ocupar bastante espaço em muitos PDFs com muitas páginas; pode ser apagado manualmente para forçar novo OCR no próximo `add` (o conteúdo indexado já está em arquivos-processados).
- **audit.jsonl** e **pdfsearchable.log** têm rotação configurável (ver [05-logs-e-auditoria.md](05-logs-e-auditoria.md) e [10-config.md](10-config.md)).
- **report.html** e **document-view.html** podem ser grandes quando há muitos documentos (search_data embutido); use `PDFSEARCHABLE_LIST_PAGE_SIZE` e `PDFSEARCHABLE_SEARCH_SNIPPET_MAX_CHARS` para limitar (ver [07-performance.md](07-performance.md) e [10-config.md](10-config.md)).

---

## Ver também

- [01-fluxo-funcionamento.md](01-fluxo-funcionamento.md) — Fluxo e dados persistidos.
- [05-logs-e-auditoria.md](05-logs-e-auditoria.md) — Estrutura de .pdfsearchable e audit/log.
- [08-processamento-indexacao.md](08-processamento-indexacao.md) — Armazenamento de texto e PDFs.
- [11-servidor.md](11-servidor.md) — Quando e como o report é gerado pelo servidor.
