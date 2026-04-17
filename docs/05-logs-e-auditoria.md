# 5. Sistema de Logs, Auditoria e Tratamento de Erros

Documentação do **log**, da **auditoria** e das **exceções** do pdfsearchable.

---

## Visão geral

| Sistema | Arquivo | Formato | Uso |
|--------|---------|--------|-----|
| **Auditoria** | `.pdfsearchable/audit.jsonl` | Uma linha JSON por evento | Rastrear ações e eventos (indexação, CLI, erros). |
| **Log** | `.pdfsearchable/pdfsearchable.log` | Texto com timestamp e nível (rotação opcional) | Detalhes de execução (indexer, exceções, etc.). |
| **Exceções** | `pdfsearchable.exceptions` | Hierarquia de classes | Tratamento consistente e mensagens claras na CLI. |

Ambos (audit e log) ficam no diretório do projeto, dentro de `.pdfsearchable/`.

---

## Tratamento de erros (exceções)

O projeto define uma hierarquia de exceções em `pdfsearchable.exceptions`:

| Exceção | Uso |
|---------|-----|
| **PdfSearchableError** | Base; mensagem e `details` opcional. |
| **ValidationError** | Arquivo não encontrado, não é PDF, PDF inválido/corrompido/senha. |
| **IndexingError** | Falha na indexação (extração, OCR, IA, gravação). |
| **StoreError** | Falha no armazenamento (índice, FTS, arquivos). |
| **ReportError** | Falha na geração do report HTML. |
| **OcrError** | Falha específica no pipeline OCR (Tesseract indisponível, erro de pré-processamento). |
| **ConfigError** | Configuração inválida (variável de ambiente com valor fora do intervalo, conflito de opções). |

Todas são exportadas publicamente por `pdfsearchable.exceptions` e por `pdfsearchable.__init__`.

A CLI captura `ValidationError`, `IndexingError`, `PdfSearchableError`, `StoreError` e `ReportError` e exibe a mensagem amigável (`e.message`) antes de encerrar. Antes de mostrar a mensagem ao utilizador, a CLI regista a exceção no log (logger `cli`, via `logger.exception` ou `logger.warning`), para diagnóstico em `.pdfsearchable/pdfsearchable.log`. Erros de indexação são registrados na auditoria com `action: index_error` e, quando aplicável, `details.code` (ex.: `ValidationError`, `IndexingError`).

---

## Auditoria (audit.jsonl)

### Formato de cada linha

Cada linha é um JSON com:

- **timestamp** — Data/hora em ISO UTC (ex.: `2024-01-15T12:00:00Z`).
- **action** — Nome da ação.
- **details** — Objeto com dados do evento (path, file_id, error, code, etc.).
- **level** — `info` ou `error`.

### Ações registradas

| Action | Quando | Detalhes típicos |
|--------|--------|-------------------|
| `index_start` | Início da indexação de um PDF | path, file_id |
| `index_done` | PDF indexado com sucesso | file_id, pages, words, doc_type, classification_source |
| `index_error` | Erro ao indexar | path, error, code (tipo da exceção) |
| `index_skipped_unchanged` | Arquivo já indexado (mesmo hash) | path, content_hash |
| `index_updated_path` | Path atualizado para mesmo content_hash | path, file_id |
| `index_large_file` | Arquivo grande (> `PDFSEARCHABLE_LARGE_FILE_MB`) | path, size_mb |
| `cli_add` | Comando `add` executado | count, paths, workers |
| `cli_remove` | Comando `remove` executado | file_id, name |
| `cli_search` | Comando `search` executado | query, hits |
| `cli_search_semantic` | Busca semântica executada | query, model, hits |
| `cli_ask` | Comando `ask` executado com sucesso | file_id, question (truncada a 120 chars) |
| `cli_chat_end` | Sessão `chat` encerrada | turns (nº de perguntas), doc_id ("multi" se sem foco) |
| `cli_report` | Comando `report` executado | path |
| `cli_backup` | Comando `backup` executado | output, size_mb |
| `cli_verify` | Comando `verify` executado | total, warnings |
| `cli_export` | Exportação concluída (json/jsonl/csv/markdown) | format, output, count |
| `cli_export_obsidian` | Exportação Obsidian concluída | count, output_dir |
| `cli_index_fts` | Comando `index-fts` executado | files_indexed |
| `cli_embed` | Comando `embed` executado | model, count |
| `cli_info` | Comando `info` executado | file_id |
| `fts_index_new` | Indexação FTS diferida concluída | files_indexed |
| `serve_start` | Servidor HTTP iniciado | host, port |
| `api_ask` | Requisição `/api/ask` respondida com sucesso | file_id, question (truncada a 120 chars) |
| `watch_indexed` | Arquivo adicionado pelo `watch` | path, reason |
| `cli_inspect` | Comando `inspect` executado | file_id, name, json (bool) |
| `cli_migrate` | Comando `migrate` executado | from_version, to_version, dry_run |
| `cli_dedup_semantic` | `dedup-semantic` executado | threshold, model, pairs |
| `redaction_detect` | Zonas redactadas detectadas (se `DETECT_REDACTIONS=1`) | file_id, name, zones |
| `forensics_scan` | Análise forense concluída (se `FORENSICS=1`) | file_id, name, revisions, producer |
| `contracts_extract` | Extração de cláusulas contratuais (se `CONTRACTS=1`) | file_id, name, parties |
| `classifier_feedback` | Amostra rotulada gravada (se `CLASSIFIER_FEEDBACK=1`) | file_id, label, length |
| `snapshot_created` | Snapshot rotativo criado (se `AUTO_SNAPSHOT=1`) | path, keep |
| `snapshot_rotated` | Snapshots antigos removidos | removed, kept |

### Escrita segura

Se a escrita em `audit.jsonl` falhar (disco cheio, permissão, etc.), o evento é enviado para o **logger de fallback** (stderr) e a exceção **não é propagada** — o fluxo do programa continua.

### Rotação opcional

Quando o arquivo de auditoria ultrapassa um tamanho máximo, o projeto pode manter apenas as últimas N linhas:

| Variável de ambiente | Descrição | Padrão |
|----------------------|-----------|--------|
| **PDFSEARCHABLE_AUDIT_MAX_BYTES** | Tamanho em bytes a partir do qual rotacionar (0 = desativado). | `0` |
| **PDFSEARCHABLE_AUDIT_MAX_LINES** | Número de linhas a manter após rotação. | `50000` |

### Uso no código

- **Registrar:** `audit("action_name", {"key": "value"}, level="info"|"error")`.
- **Ler:** `read_audit_trail(limit=N)` retorna as últimas N entradas (ordem reversa, mais recente primeiro).

### Uso no report

O card “Atividade recente” usa `read_audit_trail(50)` filtrado por ações de indexação e exibe até 25 entradas.

### Uso na CLI

O comando `pdfsearchable logs` exibe as últimas entradas (padrão 30) em tabela (Quando, Ação, Detalhes).

---

## Log (pdfsearchable.log)

### Formato

Linhas de texto com:

- **Timestamp** — `%(asctime)s`
- **Nível** — `%(levelname)s` (DEBUG, INFO, WARNING, ERROR)
- **Nome do logger** — `%(name)s` (ex.: indexer)
- **Mensagem** — `%(message)s`

Exemplo:  
`2024-01-15 12:00:00,123 | INFO | indexer | Indexado: documento.pdf (5 páginas)`

### Como obter um logger

- **Função:** `get_logger(name)` em `audit.py`.
- **Comportamento:** Cria (ou reutiliza) um logger com:
  - **Arquivo:** `RotatingFileHandler` em `pdfsearchable.log` (rotação por tamanho e número de backups).
  - **Console:** opcional, via variável de ambiente.
  - **Nível:** configurável por variável de ambiente.

### Variáveis de ambiente do log

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| **PDFSEARCHABLE_LOG_LEVEL** | Nível do log: DEBUG, INFO, WARNING, ERROR. | `INFO` |
| **PDFSEARCHABLE_LOG_CONSOLE** | Enviar log também para o console (1, true, yes). | desativado |
| **PDFSEARCHABLE_LOG_MAX_BYTES** | Tamanho máximo do arquivo de log em bytes antes de rotacionar. | `2097152` (2 MB) |
| **PDFSEARCHABLE_LOG_BACKUP_COUNT** | Quantidade de arquivos de backup (.log.1, .log.2, …). | `3` |

Se não for possível criar ou escrever no arquivo de log, o logger usa **stderr** como fallback.

### Quem usa

Todos os módulos usam `get_logger(nome)` de `audit.py`:

- **indexer:** mensagens de info (arquivo indexado, arquivo grande), retries e exceções.
- **cli:** exceções ao ler o índice, ao gerar o report, ao remover, ao chamar Ollama; erros em threads daemon (FTS background, embeddings).
- **store:** operações no índice (load, save, remove); falhas silenciosas de permissão ao remover páginas.
- **ocr:** falhas não críticas de pré-processamento de imagem (debug); fallback HTR.
- **language:** fallback de langdetect (debug).
- **ai_classifier:** falha de classificação OpenAI (warning).
- **content_extractors:** falhas de cache (debug).
- **pdf_extended:** falhas em extracção de tabelas (debug).
- **export:** resultado de cada exportação (info).

---

## Diretório do projeto

Estrutura relevante (na pasta onde os comandos são executados):

```
.pdfsearchable/
├── audit.jsonl        # Auditoria (eventos em JSONL)
├── pdfsearchable.log  # Log (texto, com rotação)
├── index.json
├── report.html
├── document-view.html
├── report_hash.txt
├── fts.sqlite
├── ocr_cache/
└── (opcional) config.toml / config.json

arquivos-processados/   # Na raiz do projeto (PDFs e texto extraído)
├── <id>.pdf
└── <id>/
    ├── full.txt (ou .gz)
    └── pages/
```

---

## Boas práticas

- **Exceções:** usar `ValidationError` / `IndexingError` (e demais) nos fluxos de indexação e CLI para mensagens claras e auditoria com `code`.
- **Auditoria:** usar para eventos de negócio e ações do usuário; manter `details` enxuto (evitar dados sensíveis em massa).
- **Log:** usar para diagnóstico (exceções, retries, throughput); não logar senhas.
- **Rotação:** ativar `PDFSEARCHABLE_AUDIT_MAX_BYTES` em ambientes com muito uso; o log já usa rotação por padrão.
