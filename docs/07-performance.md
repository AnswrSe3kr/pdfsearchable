# 7. Performance

Aspectos de **performance** do pdfsearchable: desenho, opções e limites.

---

## Processamento ágil (estilo IPED)

O projeto foi desenhado para **processamento e indexação ágeis**, em linha com ferramentas de alto throughput como o [IPED](https://github.com/sepinf-inc/IPED) (processador e indexador de evidências digitais da Polícia Federal): paralelismo por múltiplos núcleos, processamento em lote com controle de RAM, indexação FTS em modo diferido ou em segundo plano, e **integração de IA** (classificação, resumo, RAG, correção de OCR) sem bloquear o pipeline.

| Objetivo | Como obter |
|----------|------------|
| **Máximo throughput** | `--workers 0` (auto, até 16 processos; configurável com `PDFSEARCHABLE_MAX_WORKERS`) e `--batch-size` para controlar RAM. |
| **Lotes muito grandes** | `PDFSEARCHABLE_FTS_DEFERRED=1`: FTS é atualizado ao final do lote (ou com `pdfsearchable index-fts`). Com `PDFSEARCHABLE_FTS_BACKGROUND=1`, a indexação FTS roda em thread e o CLI retorna logo. |
| **Retomar sem parar** | `--continue` e `--skip-failed`: pula já indexados e não interrompe no primeiro erro. |
| **IA integrada** | Classificação por heurísticas (rápido), OpenAI ou Ollama; em modo Ollama: resumo, tags, valores, partes; busca com `--ollama` (expansão de termos); RAG no report (`/api/ask`); correção de OCR com `PDFSEARCHABLE_OCR_CORRECT=1`. |

---

## Processamento (add)

### Paralelismo

- **Workers:** `--workers N` (padrão **0** = automático até 16; 1 = sequencial; 2+ = multiprocessing). O limite do modo auto é configurável com `PDFSEARCHABLE_MAX_WORKERS` (1–32; padrão 16). Com 2 ou mais usa **multiprocessing** (um processo por PDF), evitando "Lock blocking" do PyMuPDF (que não é thread-safe).
- **Lotes:** `--batch-size N` processa em chunks e chama `gc.collect()` entre eles para reduzir pico de RAM quando há muitos PDFs grandes.
- **Throughput:** ao final do `add` são exibidos documentos/segundo e páginas/segundo.

### Memória

- Cada PDF é aberto, extraído e fechado no fluxo do indexador; não se mantêm vários documentos abertos além do paralelismo (workers).
- **Arquivos grandes (&gt; 20 MB):** compressão automática do texto e aviso; recomendado usar `--batch-size` em lotes grandes.
- **OCR:** renderização por página (DPI configurável, padrão 300, máx. 600); cache evita reprocessar a mesma página.
- **Pipeline histórico (`PDFSEARCHABLE_OCR_HISTORICAL`):** quando ativo (`on` ou `auto`), cada página passa por CLAHE, binarização Sauvola, limpeza morfológica e deskew antes do OCR, o que aumenta o tempo de processamento por página (~2–4× vs pipeline padrão). Em modo `auto`, a detecção heurística (amostragem de cantos, variância de contraste, ruído) acrescenta overhead mínimo. Modelos HTR históricos (TRIDIS, TrOCR-large) são maiores e mais lentos que o TrOCR-base padrão.

### Disco

- **Arquivos processados:** PDFs e texto em `arquivos-processados/` (raiz do projeto); opção `--compress` (ou automática para &gt; 20 MB) grava `full.txt.gz` e páginas em `.gz` em `arquivos-processados/{id}/`.
- **Índice:** `index.json` e FTS (fts.sqlite) em `.pdfsearchable/`. Para muitos arquivos, o tamanho do índice e do FTS cresce proporcionalmente.

---

## Busca

- **FTS (SQLite FTS5):** busca por termo indexada; retorno com snippet e número da página; limite padrão de resultados (ex.: 50 na CLI, 200 na função).
- **Fallback (sem FTS):** varredura no texto completo carregado em memória; adequado para conjuntos pequenos/médios.
- **Report:** busca no cliente sobre `search_data` (texto por página carregado no HTML). Para muitos documentos: **paginação** da lista (primeiros N com "Carregar mais"), **limite de snippet** por documento/página (`PDFSEARCHABLE_SEARCH_SNIPPET_MAX_CHARS`) e **tamanho da página** configurável (`PDFSEARCHABLE_LIST_PAGE_SIZE`) reduzem o tamanho do HTML e melhoram o carregamento. Com `PDFSEARCHABLE_SYNONYMS_API_ENABLED=1`, a geração do report pode fazer até 12 requisições HTTP às APIs de sinônimos (PT-BR ou EN-US), acrescentando latência.

---

## Report

- **Geração:** uma leitura do índice, leitura de texto por documento em `arquivos-processados/{id}/` (para nuvem, locais e search_data) e escrita de report.html e document-view.html. Para muitos arquivos, o tempo depende de I/O.
- **Paginação da lista:** os primeiros N documentos são exibidos (N = `PDFSEARCHABLE_LIST_PAGE_SIZE`, padrão 50); o botão "Carregar mais" carrega os próximos N sem recarregar a página, reduzindo DOM e tempo de renderização com 100+ documentos.
- **Limite de texto no search_data:** `PDFSEARCHABLE_SEARCH_SNIPPET_MAX_CHARS` (padrão 10000) trunca o texto de cada documento e de cada página embutido no HTML; evita que o report fique excessivamente grande e trave o navegador.
- **Nuvem:** construção em memória (tokenização, contagem, WordCloud por tipo); pode ser mais lenta com muito texto.
- **Cache:** se o índice não mudou (hash em `.pdfsearchable/report_hash.txt`), o report não é regerado.

---

## Testes de performance

Na suíte de testes há marcador **performance** para:

- Busca em texto grande (tempo &lt; 1s).
- `build_top_words` e `build_bigrams` em texto grande (tempo &lt; 2s).

Execução: `pytest -m performance -v`.

---

## Recomendações práticas

| Cenário | Sugestão |
|---------|----------|
| Muitos PDFs pequenos | `--workers 0` (auto) ou `--workers 4` ou mais; `--continue` para retomar. |
| PDFs grandes | `--batch-size 10` ou 20; `--compress`. |
| Pouca RAM | `--workers 1` e `--batch-size 5`. |
| Busca rápida | FTS e máscaras estão sempre ativos; a busca usa sempre o índice full-text. |
| Report lento / muitos docs | Aumentar `PDFSEARCHABLE_LIST_PAGE_SIZE` (ex.: 100) e reduzir `PDFSEARCHABLE_SEARCH_SNIPPET_MAX_CHARS` (ex.: 5000); usar "Carregar mais" na lista. |
| Lote muito grande no add | `PDFSEARCHABLE_FTS_DEFERRED=1` para não indexar FTS por arquivo; ao final do add o FTS é atualizado em lote. Com `PDFSEARCHABLE_FTS_BACKGROUND=1`, a indexação FTS roda em segundo plano e o CLI retorna logo. Comando `pdfsearchable index-fts` para reindexar FTS manualmente. |
| Aumentar teto de workers (auto) | `PDFSEARCHABLE_MAX_WORKERS=24` (1–32) para permitir mais processos quando `--workers 0`. |
| **OCR paralelo por página** | `PDFSEARCHABLE_OCR_WORKERS=0` (auto). O novo padrão usa **3/4 dos CPUs** (mín. 2, máx. 8) em vez de 1/2 — cerca de **30–50% mais rápido** em documentos com 20+ páginas de scan. |
| **Ollama keep-alive** | `PDFSEARCHABLE_OLLAMA_KEEP_ALIVE=30m` mantém o modelo carregado entre chamadas, evitando relançamento a cada página/classificação (ganho de 2–5 s por documento em lotes longos). Use `-1` para manter permanente; `0` para descarregar imediatamente. |
| **Classificação rápida** | `PDFSEARCHABLE_OLLAMA_CLASSIFY_MODEL=llama3.2:1b` para usar um modelo **menor** só para labels curtos, mantendo `OLLAMA_MODEL=llama3.2` para RAG/ask. Reduz o tempo de classificação em 3–10×. |
| **Snapshots rotativos** | `PDFSEARCHABLE_AUTO_SNAPSHOT=1` e `PDFSEARCHABLE_SNAPSHOT_KEEP=5`: antes de cada `save_index()` grava cópia em `.pdfsearchable/.snapshots/` e remove as mais antigas. Overhead &lt;50 ms por save; protege contra corrupção acidental. |
| **Dry-run antes de grandes lotes** | `pdfsearchable add pasta/ --dry-run` mostra quantos ficheiros seriam novos/reprocessados/ignorados **sem escrever nada**. Permite afinar `--workers`/`--batch-size` antes do run real. |
| **Embeddings incrementais** | `pdfsearchable add ... --embed` gera embeddings só dos docs novos/alterados. `pdfsearchable embed` (sem `--force`) também é incremental por omissão. |
| **Dedup semântico** | `pdfsearchable dedup-semantic --threshold 0.95` identifica documentos quase-iguais (traduções, versões) que `duplicates` (hash) não detecta. |
| **PDF → Markdown rápido** | `pdfsearchable benchmark-markdown <doc>` mede o speedup vs baseline PyMuPDF re-parse. **Medido: 7.7× mais rápido** (20 páginas, 10 iterações, macOS arm64). O ganho vem de usar o texto já extraído em `arquivos-processados/<id>/full.txt[.gz]` em vez de re-parsear o PDF. Ideal para pipelines RAG que reprocessam a colecção. |
| **Fórmulas matemáticas** | `PDFSEARCHABLE_DETECT_FORMULAS=1` adiciona custo O(n) sobre o texto já extraído (só regex — sem re-parse). ~5–20 ms por página para corpus com fórmulas. |

---

## Referência

- **Processamento e indexação:** [08-processamento-indexacao.md](08-processamento-indexacao.md)
- **UX e limites do report:** [06-UX-UI.md](06-UX-UI.md)
