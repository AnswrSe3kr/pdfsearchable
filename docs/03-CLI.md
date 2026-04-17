# 3. CLI

Documentação da interface de linha de comando do **pdfsearchable**.

---

## Entrada principal

```bash
pdfsearchable [COMANDO] [OPÇÕES] [ARGUMENTOS]
```

Comandos disponíveis: `init`, `add`, `remove`, `report`, `serve`, `search`, `status`, `stats`, `verify`, `duplicates`, `logs`, `index-fts`, `ask`, `export`, `watch`, `backup`, `embed`, `chat`, `mcp`, `doctor`, `info`, `inspect`, `migrate`, `dedup-semantic`, `benchmark-markdown`.

**Opção global:** `--config` / `-c` — Caminho para `config.toml` ou `config.json` (sobrepõe `.pdfsearchable/config`). Ex.: `pdfsearchable --config /path/to/config.toml add pasta/`.

---

## Comandos

### `init` — Inicializar estrutura

Cria apenas a pasta `.pdfsearchable/` e `arquivos-processados/` (e índice vazio se não existir), sem indexar PDFs.

```bash
pdfsearchable init
```

---

### `add` — Adicionar PDFs

Adiciona um ou mais PDFs ao índice (ou todos os PDFs de uma pasta, recursivo por padrão).

```bash
pdfsearchable add arquivo.pdf
pdfsearchable add pasta/
pdfsearchable add a.pdf b.pdf pasta/
```

| Opção | Descrição |
|-------|-----------|
| `--password`, `-p` | Senha do PDF (ou use env `PDF_PASSWORD`). |
| `--skip-failed` / `--no-skip-failed` | Continuar mesmo quando um arquivo falhar (padrão: ativo). |
| `--extract-mode` | Modo PyMuPDF: `text` (padrão), `blocks`, `dict`. |
| `--compress` | Comprimir texto armazenado (gzip). |
| `--workers`, `-w N` | Processamento paralelo (0 = auto até `PDFSEARCHABLE_MAX_WORKERS`, padrão máx. 16; 1 = sequencial; 2+ = multiprocessing). Com 2+ usa multiprocessing (evita Lock do PyMuPDF). |
| `--batch-size`, `-b N` | Processar em lotes de N arquivos (gc entre lotes). |
| `--continue` | Modo contínuo: pular já indexados e não interromper no primeiro erro. |
| `--recursive` / `--no-recursive`, `-r` | Incluir PDFs em subpastas (padrão: ativo). Use `--no-recursive` para um nível apenas. |
| `--reprocess` | Reprocessar arquivos já adicionados ao índice (reindexar mesmo que o conteúdo não tenha mudado). |
| `--resume` | Retomar processamento a partir da lista pendente (gravada ao interromper com Ctrl+C). |
| `--order-by` | Ordenar PDFs antes de processar: `size` (menores primeiro, padrão), `mtime` (mais recentes), `name` (alfabético). |
| `--verbose`, `-v` | Em modo sequencial (workers=1), mostrar o nome de cada ficheiro ao iniciar o processamento. |
| `--confirm-type` | Após classificação por IA, pedir confirmação do tipo sugerido (só em TTY). |
| `--embed` | Após indexar, gerar embeddings semânticos (Ollama `nomic-embed-text`) de forma **incremental** apenas para os documentos novos/reprocessados. Equivalente a correr `pdfsearchable embed` no fim. |
| `--dry-run` | Não escreve nada no índice. Exibe uma tabela Rich com status por ficheiro (`novo`, `reprocessar`, `ignorar`) e um resumo por categoria. Útil para prever o efeito antes de indexar grandes lotes. |

**OCR:** Por padrão o Tesseract é executado em **todas as páginas** (se disponível) para capturar todo o texto. OCR não pode ser desativado. Use `PDFSEARCHABLE_OCR_ALWAYS=0` para OCR só em páginas com pouco texto. Idiomas, DPI e PSM: `PDFSEARCHABLE_OCR_LANG`, `PDFSEARCHABLE_OCR_DPI`, `PDFSEARCHABLE_OCR_PSM` (ver tabela abaixo). Para cursiva/manuscrito, veja [FAQ — Cursiva e manuscrito](FAQ.md#cursiva-e-manuscrito).

**Comportamento:** Apenas arquivos `.pdf` são aceitos. Pastas são expandidas recursivamente por padrão (`--recursive`); use `--no-recursive` para um nível apenas. Erros em arquivos individuais são ignorados automaticamente (`--skip-failed`). O progresso mostra “Processando (N/total) nome.pdf”. Ao final, é exibida uma tabela dos arquivos indexados e do throughput.

---

### `remove` — Remover do índice

Remove um documento pelo ID (16 caracteres) ou pelo nome.

```bash
pdfsearchable remove "nome do arquivo.pdf"
pdfsearchable remove abc123def4567890
pdfsearchable remove --yes abc123def4567890
```

| Opção | Descrição |
|-------|-----------|
| `--yes`, `-y` | Não pedir confirmação (útil em scripts). |

Em terminal interativo (TTY), é exibida a pergunta “Remover ‘nome’ do índice?”. Sem TTY ou com `--yes`, a confirmação é omitida.

---

### `report` — Gerar report HTML

Gera (ou actualiza) imediatamente o report HTML em `.pdfsearchable/report.html` e `document-view.html`, sem iniciar o servidor HTTP. Útil para gerar o report em scripts CI/CD ou antes de servir os ficheiros estáticos com outro servidor.

```bash
pdfsearchable report
```

> **Nota:** para servir o report no navegador com suporte a RAG (`/api/ask`), use **`pdfsearchable serve`**, que também regenera o report ao arrancar.

---

### `serve` — Servir report em HTTP

Inicia um servidor HTTP local que **gera o report ao arrancar** e serve a pasta `.pdfsearchable` (report e visualização de documento). O report (`report.html` e `document-view.html`) é criado/atualizado automaticamente quando o servidor inicia. Permite ver o report em `http://host:port/report.html` e usar o endpoint **RAG** `/api/ask` (perguntas sobre um documento via Ollama). Detalhes das rotas e do endpoint: [11-servidor.md](11-servidor.md).

```bash
pdfsearchable serve
pdfsearchable serve --port 8080
pdfsearchable serve --no-open   # Não abrir o browser
```

| Opção | Descrição |
|-------|-----------|
| `--host` | Host do servidor (padrão: 127.0.0.1). |
| `--port` | Porta (padrão: 8000). |
| `--open` / `--no-open` | Abrir o report no navegador automaticamente ao iniciar (padrão: ativo). |

No report: busca com "Ignorar acentos" e "Buscar por sinônimos", filtros avançados, nuvem de palavras e mapa de locais. Ao clicar no nome de um documento, abre-se a visualização (PDF + metadados + hash + resumo/tags quando disponíveis).

---

### `search` — Pesquisar

Pesquisa um termo em todos os documentos indexados.

```bash
pdfsearchable search "termo"
pdfsearchable search "123.456.789-00"
pdfsearchable search "palavra" --no-open
pdfsearchable search "contrato" --ollama
```

| Opção | Descrição |
|-------|-----------|
| `--open` / `--no-open` | Abrir report no navegador ao final (padrão: abrir). |
| `--ollama` / `--no-ollama` | Expandir a consulta com Ollama (termos relacionados/sinônimos). Padrão: desativado. |
| `--type` | Filtrar resultados por tipo de documento (ex.: contrato, nota_fiscal). |
| `--language` | Filtrar por idioma (ex.: pt-BR, en). |
| `--date-from` | Data de indexação a partir de (YYYY-MM-DD). |
| `--date-to` | Data de indexação até (YYYY-MM-DD). |

A busca usa sempre o índice full-text (FTS) e as máscaras (IP, CPF, CNPJ, e-mail, etc.) estão sempre ativas. **Ollama:** com `--ollama`, a consulta é enviada ao Ollama para obter termos relacionados; a busca FTS usa o termo original **OU** esses termos. A linha "🤖 Expansão Ollama: …" indica os termos adicionados. Quando há resultados, é exibida uma tabela (arquivo, página, trecho) e um resumo executivo; com `--open` (padrão) é mostrada a mensagem para ver o report via `pdfsearchable serve`.

---

### `status` — Status do projeto

Lista os documentos indexados (ID, nome, páginas, tipo).

---

### `stats` — Estatísticas do índice

Mostra métricas resumidas: número de documentos, páginas, tamanho (MB), última indexação e versão do índice.

```bash
pdfsearchable stats
```

---

### `verify` — Verificar integridade

Confere se cada documento do índice tem o PDF e o texto em disco em `arquivos-processados/`. Reporta ficheiros em falta (PDF ou texto).

```bash
pdfsearchable verify
```

```bash
pdfsearchable status
```

Se não houver documentos, exibe uma mensagem orientando o uso de `pdfsearchable add`.

---

### `duplicates` — Duplicatas

Lista grupos de arquivos com o mesmo conteúdo (mesmo `content_hash`), em caminhos diferentes.

```bash
pdfsearchable duplicates
```

Saída: tabela com hash (resumido) e lista de arquivos (nome e path) por grupo.

---

### `logs` — Auditoria

Exibe as últimas entradas do arquivo de auditoria.

```bash
pdfsearchable logs
pdfsearchable logs -n 50
```

| Opção | Descrição |
|-------|-----------|
| `-n`, `--lines` | Número de entradas (padrão: 30). |

---

### `index-fts` — Reindexar FTS

Reindexa o índice full-text (FTS) para todos os documentos. Útil quando se usa `PDFSEARCHABLE_FTS_DEFERRED=1` no `add` (FTS é atualizado ao final do lote) ou para reconstruir o índice após alterações no store.

```bash
pdfsearchable index-fts
```

Sem opções. Saída: painel de sucesso com a quantidade de documentos indexados no FTS. Em falha ao ler o índice, é exibida mensagem de erro e sugestão (backup, reconstruir com `add`).

---

### `ask` — Perguntar sobre um documento (RAG)

Faz uma pergunta sobre o conteúdo de um documento usando Ollama (modelo local). Requer `PDFSEARCHABLE_AI=ollama` e Ollama em execução.

```bash
pdfsearchable ask 000ea928c570e56d "Quem são as partes do contrato?"
pdfsearchable ask "contrato.pdf" "Qual o valor total?"
```

| Argumento | Descrição |
|-----------|-----------|
| `file_id_or_name` | ID (16 caracteres hex) ou nome (ou parte do nome) do documento. |
| `question` | Pergunta em texto livre. |

Se o Ollama não estiver acessível ou o documento não tiver texto suficiente, a CLI exibe mensagem de erro em português.

---

### `export` — Exportar documentos indexados

Exporta documentos indexados para cinco formatos. Delegação centralizada via `export.py`.

```bash
pdfsearchable export --format jsonl --output colecao.jsonl
pdfsearchable export --format markdown --output ./docs_md/
pdfsearchable export --format csv --output metadados.csv
pdfsearchable export --format json --output indice.json
pdfsearchable export --format obsidian --output-dir ~/vault/PDFs
```

| Opção | Descrição |
|-------|-----------|
| `--format` | `jsonl` (padrão), `json`, `csv`, `markdown`, `obsidian`. |
| `--output`, `-o` | Ficheiro de saída (json/jsonl/csv) ou directório (markdown). Padrão automático com timestamp. |
| `--output-dir`, `-d` | Pasta de destino para `--format obsidian`. |
| `--no-text` | Omitir texto completo (aplicável a `jsonl`). Útil para exportações apenas de metadados. |

**Formatos:**

| Formato | Conteúdo | Caso de uso |
|---------|----------|-------------|
| `jsonl` | 1 linha JSON por documento (id, name, text, tags, datas…) | Fine-tuning de LLMs, pipelines RAG |
| `json` | Dump completo do índice (estrutura interna) | Integração, backup de metadados |
| `csv` | Metadados tabulares (sem texto) | Análise em Excel / pandas |
| `markdown` | Um `.md` por documento com texto e cabeçalho de metadados | RAG externo (LlamaIndex, LangChain) |
| `obsidian` | Notas `.md` com YAML frontmatter (título, tipo, tags, partes…) | Obsidian, Logseq |

---

### `watch` — Monitorizar pasta

Monitoriza uma pasta e indexa automaticamente PDFs novos ou modificados.

```bash
pdfsearchable watch
pdfsearchable watch ~/Downloads --interval 5
pdfsearchable watch /dados/pdfs --no-recursive
```

| Opção | Descrição |
|-------|-----------|
| `directory` | Pasta a monitorizar (padrão: `.`). |
| `--interval`, `-i` | Intervalo de verificação em segundos (padrão: 10). |
| `--recursive/--no-recursive` | Incluir subpastas (padrão: recursivo). |

---

### `backup` — Cópia de segurança

Cria um arquivo `.tar.gz` de todo o conteúdo de `.pdfsearchable/` (índice, FTS, textos, cache, auditoria).

```bash
pdfsearchable backup
pdfsearchable backup --output ~/backups/meu-projeto.tar.gz
```

| Opção | Descrição |
|-------|-----------|
| `--output`, `-o` | Caminho do arquivo de backup (padrão: `.pdfsearchable-backup-YYYYMMDD-HHMMSS.tar.gz`). |

---

### `embed` — Gerar embeddings semânticos

Gera embeddings via Ollama (`nomic-embed-text`) e guarda em `.pdfsearchable/embeddings.sqlite`. Necessário para usar `pdfsearchable search --semantic`.

```bash
pdfsearchable embed
pdfsearchable embed --model nomic-embed-text --force
```

| Opção | Descrição |
|-------|-----------|
| `--model` | Modelo de embeddings Ollama (padrão: `nomic-embed-text`). |
| `--force` | Regenerar embeddings mesmo para documentos já processados. |

---

### `chat` — Chat com a colecção (RAG)

Conversa em linguagem natural sobre todos os documentos indexados via Ollama. Usa RAG (retrieval-augmented generation) com os textos extraídos.

```bash
pdfsearchable chat
pdfsearchable chat --doc DOCUMENT_ID
```

| Opção | Descrição |
|-------|-----------|
| `--doc` | Focar a conversa num único documento (ID ou nome). |

Requer `PDFSEARCHABLE_AI=ollama` e Ollama em execução.

---

### `mcp` — Servidor MCP (Claude Desktop / Cursor / Zed)

Inicia o servidor MCP em modo stdio. Expõe ferramentas para consulta e indexação da colecção a partir de IDEs e assistentes de IA compatíveis.

```bash
pdfsearchable mcp
```

**Ferramentas expostas:**

| Ferramenta | Descrição |
|------------|-----------|
| `list_documents` | Lista todos os documentos indexados com metadados. |
| `search_documents` | Pesquisa por termo (FTS) na colecção. |
| `get_document_text` | Devolve o texto completo de um documento. |
| `ask_document` | Pergunta sobre um documento via Ollama. |
| `ask_all_documents` | Pergunta sobre todos os documentos (RAG global). |
| `index_document` | Indexa um novo PDF dado o caminho no sistema de ficheiros. |
| `get_redaction_report` | Devolve as zonas potencialmente redactadas (`metadata.redactions`) de um documento: página, bbox, área. Requer que o documento tenha sido indexado com `PDFSEARCHABLE_DETECT_REDACTIONS=1`. |
| `get_forensics_summary` | Devolve a análise forense (`metadata.forensics`) de um documento: revisões incrementais, produtor, anomalias de stream. Requer `PDFSEARCHABLE_FORENSICS=1`. |

Configuração em `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pdfsearchable": {
      "command": "pdfsearchable",
      "args": ["mcp"],
      "cwd": "/pasta/dos/pdfs"
    }
  }
}
```

---

### `doctor` — Diagnóstico do ambiente

Verifica o ambiente e reporta o estado de todos os componentes numa tabela Rich.

```bash
pdfsearchable doctor
```

Verifica: versão Python, PyMuPDF, Tesseract, HTR backend (trocr/transkribus/escriptorium e disponibilidade), Ollama (acessibilidade e modelo configurado), armazenamento `.pdfsearchable/` com tamanho em MB, índice JSON (nº documentos e versão de schema), FTS SQLite (nº entradas e tamanho), ficheiro de config (toml/json), espaço livre em disco.

---

### `info` — Metadados detalhados de um documento

Mostra todos os metadados de um documento pelo ID (16 hex) ou nome (ou parte do nome).

```bash
pdfsearchable info a1b2c3d4e5f6a1b2
pdfsearchable info contrato
```

Exibe: tipo, páginas, palavras, idioma, hash de conteúdo, datas de indexação/actualização, resumo, tags, datas identificadas no texto, partes, valores monetários e percentagem de OCR.

---

### `inspect` — Inspecção detalhada (5 painéis)

Mostra uma vista estruturada de **todos** os campos de um documento em 5 painéis Rich: Identificação, Enriquecimento (IA), Entidades, Detecções Opcionais (redactions/forensics/contratos), Metadados PDF.

```bash
pdfsearchable inspect a1b2c3d4e5f6a1b2
pdfsearchable inspect contrato
pdfsearchable inspect epstein --json
```

| Opção | Descrição |
|-------|-----------|
| `--json` | Imprime o registo completo em JSON (para pipelines, grep, jq). |

Requer que o documento exista no índice. Sem `--json`, pinta painéis distintos por categoria; os painéis de detecções só aparecem se houver dados (ou seja, se indexado com as env vars correspondentes activas).

---

### `migrate` — Migração forçada do schema do índice

Força a migração do `index.json` para a versão actual (INDEX_VERSION=3). Aplica migrações v1 e v2 de forma idempotente. Útil quando o índice foi criado por uma versão anterior e se quer garantir o schema mais recente sem correr `add`.

```bash
pdfsearchable migrate
pdfsearchable migrate --dry-run
```

| Opção | Descrição |
|-------|-----------|
| `--dry-run` | Só mostra o que seria alterado (versão actual vs. alvo) sem escrever. |

Antes de gravar, o comando força `PDFSEARCHABLE_AUTO_SNAPSHOT=1` para criar um snapshot rotativo do índice em `.pdfsearchable/.snapshots/`.

---

### `dedup-semantic` — Duplicatas semânticas por embeddings

Detecta documentos **semanticamente similares** (mesmo conteúdo expresso de formas diferentes — ex.: versões, traduções, scans re-OCR) comparando o cosseno dos embeddings.

```bash
pdfsearchable dedup-semantic
pdfsearchable dedup-semantic --threshold 0.95
pdfsearchable dedup-semantic --threshold 0.92 --model nomic-embed-text
```

| Opção | Descrição |
|-------|-----------|
| `--threshold` | Limiar de cosseno para considerar duplicata semântica (0,0–1,0). Padrão: `0.98`. Valores mais baixos (0,90–0,95) capturam similaridade mais fraca (traduções, paráfrases). |
| `--model` | Modelo Ollama de embeddings. Padrão: `nomic-embed-text`. |

Requer embeddings pré-calculados (`pdfsearchable embed` ou `add --embed`). A saída é uma tabela de pares `(a, b, score)`. Complementa `duplicates` (que só detecta igualdade byte-a-byte via `content_hash`).

---

### `benchmark-markdown` — Benchmark reprodutível PDF → Markdown

Mede o tempo médio da conversão PDF → Markdown usando duas estratégias sobre o **mesmo PDF** e imprime o speedup:

1. **baseline**: abre o PDF do zero com PyMuPDF e extrai cada página (custo típico de uma pipeline sem cache, ex.: `marker`, `pdf2md` ingênuo).
2. **pdfsearchable**: usa o texto já extraído e cacheado em `arquivos-processados/<id>/full.txt[.gz]` e aplica o template do módulo `export`.

```bash
pdfsearchable benchmark-markdown                    # primeiro doc, 5 iterações
pdfsearchable benchmark-markdown contrato -n 10
pdfsearchable benchmark-markdown a1b2c3d4e5f6a1b2 --json
```

| Opção | Descrição |
|-------|-----------|
| `FILE_ID_OR_NAME` | (opcional) ID ou nome do documento. Omitido → primeiro indexado. |
| `--iterations`, `-n` | Número de iterações por estratégia (padrão: 5). Um warm-up extra é corrido e descartado. |
| `--json` | Imprime o resultado em JSON (inclui tempos individuais). |

Em bancada local (macOS arm64, PDF sintético de 20 páginas, 10 iterações): **7.7× mais rápido**. O ganho cresce com o número de páginas e com a necessidade de correr a exportação múltiplas vezes — cenários típicos de pipelines RAG que reprocessam a colecção (LlamaIndex/LangChain reindex, Obsidian sync).

---

## Variáveis de ambiente relevantes

| Variável | Descrição | Padrão / uso |
|----------|-----------|----------------|
| **Geral** | | |
| `PDF_PASSWORD` | Senha para PDFs protegidos (ou use `--password`). | — |
| **OCR** | | |
| `PDFSEARCHABLE_OCR_LANG` | Idiomas do Tesseract, separados por `+` (ex.: `por+eng+spa+fra+ita+rus+deu+heb`). | `por+eng+spa+fra+ita+rus+deu+heb` |
| `PDFSEARCHABLE_OCR_DPI` | DPI da renderização para OCR (72–600). | `300` |
| `PDFSEARCHABLE_OCR_PSM` | Modo de segmentação Tesseract (0–13). Para manuscrito com Tesseract: `6` ou `7`. | `3` |
| `PDFSEARCHABLE_OCR_OEM` | Motor Tesseract: 0=legado, 1=LSTM, 2=ambos, 3=padrão. LSTM costuma ser mais preciso. | `3` |
| `PDFSEARCHABLE_OCR_OSD` | `1` = detectar/corrigir orientação da página (Tesseract OSD) antes do OCR. | `1` |
| `PDFSEARCHABLE_OCR_BINARIZE` | `1` = binarização Otsu antes do OCR. | `1` |
| `PDFSEARCHABLE_OCR_DESKEW` | `1` = correção de inclinação de scan. | `1` |
| `PDFSEARCHABLE_OCR_BORDER_REMOVE` | `1` = remover bordas de scanner. | `1` |
| `PDFSEARCHABLE_OCR_PREPROCESS` | `1` para grayscale, contraste e nitidez antes do OCR (ignorado se Otsu ativo). | `1` |
| `PDFSEARCHABLE_OCR_ALWAYS` | `1` = OCR em todas as páginas; `0` = só em páginas com pouco texto. | `1` |
| `PDFSEARCHABLE_OCR_HISTORICAL` | Pipeline para documentos históricos: `on` (forçar CLAHE + Sauvola + limpeza morfológica + modelos HTR especializados), `auto` (detectar automaticamente documentos antigos), `off` (pipeline padrão). | `off` |
| `PDFSEARCHABLE_LARGE_FILE_MB` | Limiar (MB) acima do qual o `add` avisa sobre ficheiros grandes e activa compressão automática. | `20` |
| `PDFSEARCHABLE_HTR` | Ativar HTR (manuscrito). Use `0`/`false`/`no` para desativar e usar só Tesseract. | — (ativo se backend disponível) |
| `PDFSEARCHABLE_HTR_BACKEND` | Backend HTR: `trocr` (local, requer `[htr]`), `transkribus` (cloud), `escriptorium` (instância própria). | `trocr` |
| `PDFSEARCHABLE_HTR_MODEL` | Modelo Hugging Face para HTR (backend `trocr`). | `microsoft/trocr-base-handwritten` |
| `PDFSEARCHABLE_HTR_LANG` | Forçar idioma HTR (ex.: `de`, `fr`, `ru`). Sem esta variável, o idioma é detectado automaticamente. | — (auto) |
| `PDFSEARCHABLE_HTR_PRINTED` | `1` para usar modelo de texto impresso em vez de manuscrito. | `0` |
| `PDFSEARCHABLE_HTR_MAX_MODELS` | Número máximo de modelos TrOCR em cache LRU (1–10). | `3` |
| **IA (classificação e resumo)** | | |
| `PDFSEARCHABLE_AI` | Modo: `auto`, `heuristics`, `openai`, `ollama`. | `auto` |
| `OPENAI_API_KEY` | Chave da API OpenAI (ativa classificação/resumo quando em modo auto/openai). | — |
| `PDFSEARCHABLE_OPENAI_MODEL` | Modelo OpenAI. | `gpt-4o-mini` |
| `PDFSEARCHABLE_OLLAMA_URL` | URL da API Ollama (modelo local). | `http://localhost:11434` |
| `PDFSEARCHABLE_OLLAMA_MODEL` | Modelo Ollama (ex.: llama3.2). | `llama3.2` |
| **Report** | | |
| `PDFSEARCHABLE_WORDCLOUD_STOP` | Palavras extras a excluir da nuvem (separadas por vírgula). | — |
| `PDFSEARCHABLE_VIA_CEP` | Ativar/desativar consulta ViaCEP para CEPs no texto (mapa de locais). Ver [10-config.md](10-config.md). | `1` |
| `PDFSEARCHABLE_IP_API` | Ativar/desativar geolocalização de IPs via IP-API no report. | `1` |
| **Sinônimos** | | |
| `PDFSEARCHABLE_SEARCH_SYNONYMS` | Mapa de sinônimos para busca (JSON: termo → equivalente ou vários separados por vírgula). | — |
| `PDFSEARCHABLE_SYNONYMS_API_ENABLED` | `1`/`true`/`yes` para enriquecer sinônimos com API (até 12 top palavras) ao gerar o report. | — |
| `PDFSEARCHABLE_SYNONYMS_LANG` | Idioma da API de sinônimos: `pt-BR` ou `en-US`. | `pt-BR` |
| `PDFSEARCHABLE_API_DICIONARIO_PTBR` | URL base da API de dicionário PT-BR (ex.: api-dicionario-ptbr). | padrão herokuapp |
| `API_NINJAS_KEY` / `PDFSEARCHABLE_API_NINJAS_KEY` | Chave para API Ninjas Thesaurus (inglês EUA). | — |

---

## Saída e erros

- **Rich:** tabelas, painéis e cores no terminal.
- **Emojis:** usados em títulos e mensagens (📄, 📂, 📊, 🔍, etc.).
- **Erros:** mensagens em português; em caso de rejeição de não-PDF, lista dos arquivos rejeitados. Falhas ao ler o índice, ao gerar o report ou ao remover são registradas em `.pdfsearchable/pdfsearchable.log` (logger `cli`); em erros genéricos a CLI sugere consultar esse ficheiro.
- **Versão:** `pdfsearchable --version` exibe a versão do pacote.
