# pdfsearchable

[![CI](https://github.com/AnswrSe3kr/pdfsearchable/actions/workflows/ci.yml/badge.svg)](https://github.com/AnswrSe3kr/pdfsearchable/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Testes](https://img.shields.io/badge/testes-901%20passando-brightgreen.svg)](tests/)
[![Licença: MIT](https://img.shields.io/badge/licen%C3%A7a-MIT-blue.svg)](LICENSE)
[![segurança: bandit](https://img.shields.io/badge/seguran%C3%A7a-bandit-yellow.svg)](https://github.com/PyCQA/bandit)
[![Estilo: ruff](https://img.shields.io/badge/estilo-ruff-000000.svg)](https://github.com/astral-sh/ruff)

**Indexe, pesquise e explore a sua coleção de PDFs — totalmente offline.**

Ferramenta de linha de comando que transforma uma pasta de PDFs numa base de conhecimento pesquisável e navegável, com uma aplicação web interativa de estética Apple. Sem necessidade de cloud. OCR incorporado. Enriquecimento por IA via Ollama local.

---

## Funcionalidades

| Funcionalidade | Detalhes |
|---|---|
| **Indexação** | Extração de texto via PyMuPDF + OCR Tesseract em todas as páginas |
| **Pipeline OCR** | Orientação OSD, binarização Otsu, deskew automático, retry por confiança; modo histórico com CLAHE, Sauvola e limpeza morfológica |
| **Pesquisa full-text** | SQLite FTS5 com `AND / OR / NOT` e suporte a frase exata |
| **Pesquisa semântica** | Embeddings Ollama (`nomic-embed-text`), similaridade cosseno em Python puro |
| **SPA interativa** | Aplicação web de 3 colunas ao estilo Apple: sidebar, lista de documentos, painel de detalhe |
| **Visualizador** | Visualizador de página completa com PDF inline, sidebar de metadados, anotações e chat RAG |
| **Nuvem de palavras** | Canvas interativo de frequência de palavras por tipo ou coleção inteira |
| **Mapa de localizações** | Mapa Leaflet de localizações extraídas com geocodificação opcional via Nominatim |
| **Grafo de conhecimento** | Grafo D3 de relações entre entidades, com layout force-directed em canvas |
| **Linha do tempo** | Vista cronológica dos documentos com filtro por intervalo de datas |
| **Enriquecimento por IA** | Classificação de tipo de documento, resumo, tags, partes, valores — via Ollama |
| **Chat RAG** | Pergunta-resposta multi-documento no terminal (`pdfsearchable chat`) |
| **Anotações** | Adicionar, listar e gerir anotações por página nos documentos indexados |
| **Contratos** | Análise de contratos com alertas de expiração e dashboard-resumo |
| **Forense** | Análise forense de PDF: metadados, texto oculto, ficheiros embutidos |
| **Deteção de redações** | Identifica conteúdo redigido/ocultado em PDFs |
| **Extração de tabelas** | Extrai tabelas estruturadas para CSV ou JSON |
| **Servidor MCP** | Expõe a sua biblioteca ao Claude Desktop, Cursor, Zed via stdio MCP |
| **Modo watch** | Auto-indexação de PDFs novos e modificados à medida que surgem |
| **Exportação** | JSON (índice completo), JSONL (pipelines LLM/RAG), CSV (tabular), Markdown (um ficheiro por doc), Obsidian/Logseq com frontmatter YAML |
| **Backup / Verificação** | Arquivo `.tar.gz` + verificação de integridade por drift de hash |
| **Atualizações em tempo real** | Push via SSE — SPA mostra banner quando o índice muda |
| **Offline-first** | Leaflet, wordcloud2 transferidos localmente; sem dependência de CDN em runtime |
| **Autenticação** | Token Bearer opcional para o servidor HTTP (`PDFSEARCHABLE_AUTH_TOKEN`) |
| **CORS** | Cabeçalhos CORS configuráveis para acesso cross-origin à API |
| **Log de auditoria** | Todas as ações são anexadas a `.pdfsearchable/audit.jsonl` |
| **Feedback do classificador** | Registo de correções para melhorar classificações futuras |
| **Snapshots automáticos** | Snapshots rotativos do índice antes de cada gravação (`PDFSEARCHABLE_AUTO_SNAPSHOT=1`) |
| **Migração de esquema** | `pdfsearchable migrate` força v1/v2 → v3 com pré-visualização em dry-run |
| **Indexação dry-run** | `pdfsearchable add --dry-run` mostra o que seria indexado sem escrever nada |
| **Embeddings incrementais** | `pdfsearchable add --embed` gera embeddings apenas de docs novos/alterados |
| **Dedup semântico** | `pdfsearchable dedup-semantic` encontra quase-duplicados (traduções, versões) por similaridade cosseno ≥ limiar |
| **Comando inspect** | `pdfsearchable inspect` mostra 5 painéis Rich ou JSON para qualquer documento |
| **Ollama keep-alive** | Modelo mantém-se carregado entre chamadas (`PDFSEARCHABLE_OLLAMA_KEEP_ALIVE=30m`) — poupa 2–5 s/doc |
| **Preservação de fórmulas** | Deteta LaTeX (`$…$`, `$$…$$`, `\[…\]`, ambientes `equation/align`) **e** clusters Unicode matemáticos (∫ ∑ ∏ √ ∂ π θ …). Ativar com `PDFSEARCHABLE_DETECT_FORMULAS=1`. Guardado em `metadata.formulas` e re-emitido na exportação Markdown como blocos `$$ … $$`. |
| **Benchmark Markdown** | `pdfsearchable benchmark-markdown <ficheiro>` mede o speedup de PDF→Markdown face a re-parse ingénuo do PyMuPDF. Medido: **7,7× mais rápido** num PDF sintético de 20 páginas (10 iterações, macOS arm64); o ganho vem de ler o texto já extraído em cache em vez de re-parsear. |

---

## Instalação

**Requisitos:** Python 3.11+, [Tesseract](https://github.com/tesseract-ocr/tesseract) (para OCR), [Ollama](https://ollama.com) (opcional, para funcionalidades de IA)

```bash
pip install pdfsearchable
```

Ou a partir do código-fonte:

```bash
git clone https://github.com/AnswrSe3kr/pdfsearchable.git
cd pdfsearchable
pip install -e .
```

Extras opcionais:

```bash
pip install "pdfsearchable[ai]"            # Classificação OpenAI
pip install "pdfsearchable[htr]"           # HTR (TrOCR) para texto manuscrito
pip install "pdfsearchable[tables-ocr]"    # Extração de tabelas em PDFs digitalizados
pip install "pdfsearchable[dev]"           # pytest, ruff
```

---

## Arranque rápido

```bash
# 1. Indexar uma pasta de PDFs
pdfsearchable add ~/Documentos/contratos/

# 2. Abrir a aplicação web interativa
pdfsearchable serve
# → Abre http://127.0.0.1:8000/app.html

# 3. Pesquisar a partir do terminal
pdfsearchable search "rescisão contratual"

# 4. Ver detalhes de um documento
pdfsearchable info contrato-aluguel
```

A aplicação web (`app.html`) é uma SPA que carrega todos os dados dinamicamente via API REST. Inclui:

- **Sidebar** — filtros por tipo de documento, tags e estatísticas
- **Lista de documentos** — lista pesquisável e ordenável de todos os PDFs indexados
- **Painel de detalhe** — metadados, texto extraído, anotações e chat RAG inline
- **Nuvem de palavras** — `/wordcloud.html` — canvas interativo de frequência de palavras
- **Mapa** — `/map.html` — mapa Leaflet com localizações geocodificadas
- **Grafo de conhecimento** — `/graph.html` — grafo force-directed de relações entre entidades
- **Linha do tempo** — `/timeline.html` — navegador cronológico de documentos

O `report.html` estático pode ser gerado em separado com `pdfsearchable report` — é um snapshot standalone, independente do servidor.

---

## Referência de comandos

### Indexação

```bash
pdfsearchable add FICHEIRO_OU_PASTA [OPÇÕES]

  --workers N                Workers paralelos (0=auto até 16; 1=sequencial; 2+=multiprocessing)
  --batch-size N             Processa em lotes de N ficheiros (gc entre lotes)
  --order-by size|mtime|name Ordena ficheiros antes do processamento
  --recursive / --no-recursive  Inclui PDFs em subpastas (padrão: recursivo)
  --resume                   Retoma execução interrompida (Ctrl+C)
  --continue                 Ignora já indexados e continua em caso de erro
  --reprocess                Re-indexa mesmo se o hash do conteúdo coincidir
  --extract-mode text|blocks|dict   Modo de extração PyMuPDF
  --compress                 Comprime texto armazenado (gzip)
  --password TEXTO           Password do PDF (ou env PDF_PASSWORD)
  --confirm-type             Confirma interativamente o tipo classificado pela IA
  --embed                    Gera embeddings semânticos (incrementais) após indexação
  --dry-run                  Pré-visualiza o que seria indexado sem escrever nada
```

OCR é configurado via variáveis de ambiente: `PDFSEARCHABLE_OCR_LANG` (padrão `por`), `PDFSEARCHABLE_OCR_DPI` (padrão `300`), `PDFSEARCHABLE_OCR_HISTORICAL` (`off` / `auto` / `on`).

```bash
pdfsearchable remove FICHEIRO_OU_ID [--yes]   # Remove do índice
pdfsearchable status                          # Estado do projeto (ficheiros indexados, totais)
pdfsearchable info ID_OU_NOME                 # Metadados detalhados de um documento
pdfsearchable inspect ID_OU_NOME [--json]     # Vista de 5 painéis (identificação, enriquecimento, entidades, deteções, metadados PDF)
pdfsearchable migrate [--dry-run]             # Força migração de esquema do índice (v1/v2 → v3)
pdfsearchable dedup-semantic [--threshold 0.98] [--model nomic-embed-text]   # Quase-duplicados por similaridade cosseno
```

### Pesquisa

```bash
pdfsearchable search QUERY [OPÇÕES]

  --type TIPO       Filtra por tipo de documento
  --language LANG   Filtra por idioma (ex.: pt-BR, en)
  --date-from DATA  Indexado desde (AAAA-MM-DD)
  --date-to DATA    Indexado até (AAAA-MM-DD)
  --semantic        Pesquisa semântica via embeddings (corra `pdfsearchable embed` primeiro)
  --ollama          Expande termos da query via Ollama
```

Operadores FTS: `"frase exacta"`, `termo1 AND termo2`, `termo1 OR termo2`, `NOT termo`

### Servidor web

```bash
pdfsearchable serve [OPÇÕES]

  --host HOST     Endereço de bind (padrão: 127.0.0.1)
  --port PORT     Porta (padrão: 8000)
  --open          Abre o browser automaticamente
```

`serve` copia todos os ficheiros-template da SPA para `.pdfsearchable/` e arranca o servidor HTTP. Abrir `http://host:port/` redireciona para `/app.html`.

**Endpoints da API expostos pelo servidor:**

| Método | Caminho | Descrição |
|--------|---------|-----------|
| GET | `/` | Redireciona para `/app.html` |
| GET | `/api/health` | Health check (estado do índice + Ollama) |
| GET | `/api/index` | JSON completo do índice para bootstrap da SPA |
| GET | `/api/search` | Pesquisa FTS + semântica (`?q=&type=`) |
| GET | `/api/text` | Texto completo extraído de um documento (`?id=`) |
| GET | `/api/page` | Texto de uma página específica (`?id=&page=`) |
| GET | `/api/annotations` | Lista anotações de um documento (`?id=`) |
| GET | `/api/wordcloud` | Frequências de palavras (`?type=&limit=`) |
| GET | `/api/locations` | Localizações com geocodificação opcional (`?geocode=0\|1`) |
| GET | `/api/graph` | Nós e arestas do grafo de conhecimento |
| GET | `/api/timeline` | Entradas e estatísticas da linha do tempo |
| GET | `/api/events` | Stream SSE de atualizações do índice em tempo real |
| GET | `/arquivos-processados/<file>` | Serve ficheiros PDF |
| POST | `/api/meta/update` | Atualiza doc_type, tags, subject |
| POST | `/api/annotations` | Adiciona nova anotação |
| POST | `/api/ask` | Pergunta-resposta RAG via Ollama (rate limited) |

**Funcionalidades do servidor:** auth Bearer/Basic (`PDFSEARCHABLE_AUTH_TOKEN`), CORS com caching `Max-Age` no preflight, rate limiting em `/api/ask`, compressão gzip para respostas grandes, timeout SSE (5 min), cache de geocodificação, respostas de erro em JSON em todos os endpoints, listagem de diretórios desativada.

### Relatório (estático, standalone)

```bash
pdfsearchable report [--output CAMINHO]
```

Gera um `report.html` self-contained com snapshot do índice atual. Pode ser aberto em qualquer browser sem servidor a correr. É independente de `serve` e da SPA ao vivo.

### Funcionalidades de IA (Ollama)

```bash
# Enriquecer todos os documentos indexados com metadados de IA
PDFSEARCHABLE_AI=ollama pdfsearchable add documentos/

# Gerar embeddings semânticos para pesquisa vetorial
pdfsearchable embed

# Conversar com a sua coleção de documentos
pdfsearchable chat
pdfsearchable chat --doc ID_DOCUMENTO    # Foco num único documento

# Pesquisa semântica
pdfsearchable search --semantic "quem assinou o contrato"
```

### Análise de documentos

```bash
# Deteta conteúdo redigido num PDF
pdfsearchable redactions [PDF]

# Análise forense (metadados, texto oculto, ficheiros embutidos)
pdfsearchable forensics [PDF]

# Extrai tabelas para CSV ou JSON
pdfsearchable tables [PDF]

# Resumo de contratos e alertas de expiração
pdfsearchable contracts

# Linha do tempo dos documentos no terminal
pdfsearchable timeline

# Gera HTML do grafo de conhecimento
pdfsearchable knowledge-graph
```

### Anotações e feedback

```bash
# Gere anotações dos documentos (listar, adicionar, apagar)
pdfsearchable annotations [OPÇÕES]

# Regista correções do classificador para treino futuro
pdfsearchable feedback
```

### Servidor MCP (Claude Desktop / Cursor / Zed)

```bash
pdfsearchable mcp
```

Adicionar ao `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pdfsearchable": {
      "command": "pdfsearchable",
      "args": ["mcp"],
      "cwd": "/caminho/para/a/sua/pasta/pdf"
    }
  }
}
```

Ferramentas expostas: `list_documents`, `search_documents`, `get_document_text`, `ask_document`, `ask_all_documents`, `index_document`, `get_redaction_report`, `get_forensics_summary`

### Automação

```bash
pdfsearchable watch [PASTA]        # Auto-indexa PDFs novos/modificados
pdfsearchable watch --interval 5   # Verifica a cada 5 segundos

pdfsearchable backup [OUTPUT]      # Cria .tar.gz do índice
pdfsearchable verify               # Verifica integridade por hash de todos os PDFs

pdfsearchable export --format jsonl --output colecao.jsonl
pdfsearchable export --format markdown --output ./docs_md/
pdfsearchable export --format csv --output metadados.csv
pdfsearchable export --format obsidian --output-dir ~/vault/PDFs

pdfsearchable doctor               # Diagnostica o ambiente (PyMuPDF, Tesseract, Ollama, disco…)
```

---

## Configuração

Todas as definições são variáveis de ambiente com prefixo `PDFSEARCHABLE_`, ou guardadas em `.pdfsearchable/config.json` (criado por `pdfsearchable init`).

| Variável | Padrão | Descrição |
|---|---|---|
| `PDFSEARCHABLE_AI` | `heuristic` | `heuristic` / `ollama` / `openai` |
| `PDFSEARCHABLE_OLLAMA_URL` | `http://localhost:11434` | URL base do Ollama |
| `PDFSEARCHABLE_OLLAMA_MODEL` | `llama3.2` | Modelo de chat/classificação |
| `PDFSEARCHABLE_OLLAMA_CLASSIFY_MODEL` | _(nenhum)_ | Modelo menor usado apenas para classificação (ex.: `llama3.2:1b`) |
| `PDFSEARCHABLE_OLLAMA_KEEP_ALIVE` | `5m` | Mantém modelo carregado entre chamadas (`-1`=permanente, `0`=descarrega) |
| `PDFSEARCHABLE_AUTO_SNAPSHOT` | `0` | `1` para snapshot do índice antes de cada gravação |
| `PDFSEARCHABLE_SNAPSHOT_KEEP` | `5` | Número de snapshots rotativos mantidos |
| `PDFSEARCHABLE_DETECT_REDACTIONS` | `0` | `1` para detetar zonas redigidas durante indexação |
| `PDFSEARCHABLE_FORENSICS` | `0` | `1` para executar análise forense durante indexação |
| `PDFSEARCHABLE_CONTRACTS` | `0` | `1` para detetar cláusulas contratuais durante indexação |
| `PDFSEARCHABLE_OCR_WORKERS` | auto (3·cpu/4, 2–8) | Workers OCR paralelos por página |
| `PDFSEARCHABLE_DETECT_FORMULAS` | `0` | `1` para detetar fórmulas LaTeX / Unicode matemáticas durante indexação |
| `PDFSEARCHABLE_AUTH_TOKEN` | _(nenhum)_ | Token Bearer para o servidor HTTP |
| `PDFSEARCHABLE_CORS` | `0` | `1` para ativar cabeçalhos CORS |
| `PDFSEARCHABLE_CORS_ORIGIN` | `*` | Origem CORS permitida |
| `PDFSEARCHABLE_ASK_RATE_LIMIT` | _(nenhum)_ | Pedidos/min para `/api/ask` (0 = ilimitado) |
| `PDFSEARCHABLE_HTTP_LOG` | `0` | `1` para registar pedidos HTTP |
| `PDFSEARCHABLE_WEBHOOK_URL` | _(nenhum)_ | POST de JSON após cada indexação |
| `PDFSEARCHABLE_OCR_LANG` | `por` | Código(s) de idioma Tesseract |
| `PDFSEARCHABLE_OCR_DPI` | `300` | DPI de renderização para OCR |
| `PDFSEARCHABLE_OCR_CORRECT` | `0` | `1` para corrigir erros de OCR via LLM |
| `PDFSEARCHABLE_OCR_HISTORICAL` | `off` | `off` / `auto` / `on` — pipeline para documentos históricos (CLAHE + Sauvola + morfologia + HTR históricos) |
| `OPENAI_API_KEY` | _(nenhum)_ | Obrigatório quando `PDFSEARCHABLE_AI=openai` |

---

## Estrutura de armazenamento

```
seu-projeto/
├── .pdfsearchable/
│   ├── index.json          # Registo de documentos
│   ├── fts.sqlite          # Índice full-text (FTS5)
│   ├── embeddings.sqlite   # Embeddings semânticos (opcional)
│   ├── audit.jsonl         # Log de ações
│   ├── assets/             # Assets JS/CSS offline
│   ├── config.json         # Configuração do projeto
│   ├── app.html            # SPA principal (copiada por serve)
│   ├── document-view.html  # Visualizador de página completa (copiado por serve)
│   ├── wordcloud.html      # Vista nuvem de palavras (copiada por serve)
│   ├── map.html            # Vista mapa de localizações (copiada por serve)
│   ├── graph.html          # Vista grafo de conhecimento (copiada por serve)
│   ├── timeline.html       # Vista linha do tempo (copiada por serve)
│   └── report.html         # Relatório estático (gerado por pdfsearchable report)
└── arquivos-processados/
    ├── <id>.pdf            # Symlink / cópia do original
    └── <id>/               # Texto extraído por página
        ├── page_001.txt
        └── …
```

---

## Desenvolvimento

```bash
git clone https://github.com/AnswrSe3kr/pdfsearchable.git
cd pdfsearchable
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Pre-commit hooks (segurança, lint, format)
pip install pre-commit && pre-commit install

# Pirâmide completa de testes
pytest tests/unit/        -q   # unitários (caixa-branca)
pytest tests/integration/ -q   # integração (caixa-cinzenta)
pytest tests/system/      -q   # sistema / E2E (caixa-preta)
pytest tests/acceptance/  -q   # UAT
pytest tests/security/    -q   # segurança (OWASP)
pytest tests/regression/  -q   # regressão (guardas contra bugs)
pytest tests/performance/ -q   # carga e throughput

# Tudo de uma vez com coverage
pytest tests/ --cov=pdfsearchable --cov-report=term-missing

# Lint + format
ruff check src/ && ruff format --check src/

# SAST security scan
bandit -r src/ -c pyproject.toml

# Scan de vulnerabilidades em dependências
pip-audit
```

---

## Licença

MIT
