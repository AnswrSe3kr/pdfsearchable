# 11. Servidor HTTP

Documentação completa do **servidor HTTP** iniciado por `pdfsearchable serve`. O servidor expõe a SPA interativa e uma REST API completa para acesso programático ao índice.

---

## Visão geral da arquitectura

A partir da versão 0.4.0, `pdfsearchable serve` não gera um relatório estático. Em vez disso:

1. **`_setup_spa()`** copia todos os ficheiros de template da SPA para `.pdfsearchable/` (sobrescrevendo versões anteriores para garantir que estão actualizados).
2. O servidor HTTP (`ThreadingHTTPServer`) é iniciado e serve `.pdfsearchable/` como raiz do documento.
3. A SPA (`app.html`) carrega os dados dinamicamente via REST API — não há pré-geração de HTML com dados embutidos.
4. Um observador SSE emite eventos `index_changed` sempre que o índice muda, permitindo actualizações em tempo real na SPA sem recarregar a página.

O comando `pdfsearchable report` é independente: gera apenas `report.html` estático (snapshot do índice), sem iniciar nenhum servidor.

---

## Uso

```bash
pdfsearchable serve
pdfsearchable serve --host 127.0.0.1 --port 8080
pdfsearchable serve --no-open   # Não abrir o browser
```

| Opção | Descrição | Padrão |
|-------|-----------|--------|
| `--host` | Endereço de escuta. | `127.0.0.1` |
| `--port` | Porta TCP. | `8000` |
| `--open` / `--no-open` | Abrir `app.html` no browser após iniciar. | ativo |

Ao iniciar, o servidor exibe:

```
Servidor em http://127.0.0.1:8000 — Ctrl+C para parar
Abrir: http://127.0.0.1:8000/app.html
```

---

## Pré-requisitos

- A pasta `.pdfsearchable/` deve existir (criada ao rodar `pdfsearchable add`). Se não existir, o servidor exibe uma mensagem orientando o uso de `pdfsearchable add` e encerra.
- Os ficheiros de template da SPA devem existir no pacote Python instalado (pasta `templates/`).

---

## Ficheiros de template servidos

`_setup_spa()` copia os seguintes ficheiros para `.pdfsearchable/`:

| Ficheiro | Descrição |
|----------|-----------|
| `app.html` | SPA principal — 3 colunas: sidebar, lista de documentos, painel de detalhe |
| `document-view.html` | Visualizador de documento em página completa: PDF inline, metadados, anotações, RAG |
| `wordcloud.html` | Nuvem de palavras interactiva em canvas (`GET /api/wordcloud`) |
| `map.html` | Mapa Leaflet de locais (`GET /api/locations?geocode=1`) |
| `graph.html` | Grafo de conhecimento em canvas com força D3 (`GET /api/graph`) |
| `timeline.html` | Linha cronológica de documentos (`GET /api/timeline`) |

O ficheiro `report.html` **não** é copiado pelo `serve` — é gerado exclusivamente por `pdfsearchable report`.

---

## Design da SPA (app.html)

A SPA segue as directrizes Apple HIG:

- **CSS custom properties** para todo o sistema de cores e espaçamento.
- **Modo claro/escuro** com `prefers-color-scheme` como padrão e persistência em `localStorage` (chave `pdfsearchable-theme`). O utilizador pode alternar manualmente.
- **Toolbar com backdrop-filter blur** — efeito de vidro fosco na barra superior.
- **Cross-view search** — clicar em palavras, locais ou entidades em qualquer vista (wordcloud, map, graph, timeline) grava `pdfs-search-init` em `localStorage` e navega para `app.html`, que lê esse valor e pré-preenche a caixa de busca.

**Atalhos de teclado em `app.html`:**

| Atalho | Acção |
|--------|-------|
| `⌘K` / `Ctrl+K` | Focar a caixa de pesquisa |
| `⌘/` / `Ctrl+/` | Abrir modal de atalhos |
| `Escape` | Fechar painel de detalhe / modal / limpar pesquisa |
| `↑` / `↓` | Navegar pela lista de documentos |

---

## Endpoints REST — GET

### GET /

Redireccionamento **302** para `/app.html`. Abrir `http://host:port/` leva directamente à SPA.

---

### GET /api/health

Verifica se o sistema está operacional.

**Resposta 200:**
```json
{
  "status": "ok",
  "index_ok": true,
  "ollama_ok": false
}
```

- `index_ok`: `true` se o índice JSON for carregável.
- `ollama_ok`: `true` se Ollama estiver acessível e `PDFSEARCHABLE_AI=ollama`. Caso contrário `false` (não é um erro — apenas indica disponibilidade).

Útil para health checks em ambientes de CI/CD ou monitorização externa.

---

### GET /api/index

Retorna o índice completo em JSON para bootstrap da SPA. Chamado uma vez no carregamento inicial de `app.html`.

**Resposta 200** — estrutura interna do índice:
```json
{
  "version": 3,
  "files": [
    {
      "id": "0a1b2c3d4e5f6789",
      "name": "contrato.pdf",
      "original_path": "/pasta/contrato.pdf",
      "doc_type": "contrato",
      "language": "pt",
      "num_pages": 5,
      "word_count": 1200,
      "summary": "Contrato de prestação de serviços...",
      "tags": ["serviço", "trabalhista"],
      "indexed_at": "2026-04-01T10:00:00Z",
      "ocr_percentage": 80,
      "metadata": { "title": "...", "author": "..." }
    }
  ]
}
```

O array `files` contém todos os documentos indexados com metadados completos. A SPA usa este endpoint para bootstrap inicial — carrega uma vez ao arrancar e mantém estado local.

---

### GET /api/search

Pesquisa full-text (FTS5) e/ou semântica.

**Parâmetros de query:**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `q` | string | Expressão de pesquisa (obrigatório) |
| `type` | string | Filtrar por `doc_type` (opcional) |

Suporta operadores FTS: `"frase exacta"`, `termo1 AND termo2`, `termo1 OR termo2`, `NOT termo`.

Quando `PDFSEARCHABLE_AI=ollama` e embeddings disponíveis, os resultados FTS são complementados por resultados semânticos por similaridade de cosseno.

**Resposta 200** — array JSON de tuplos `[file_id, page_num, snippet]`:
```json
[
  ["0a1b2c3d4e5f6789", 2, "...rescisão contratual por justa <mark>causa</mark>..."],
  ["1c2d3e4f5a6b7c8d", 1, "...incidência de <mark>causa</mark> contratual..."]
]
```

Os termos pesquisados aparecem entre `<mark>…</mark>` no snippet. A SPA usa este formato directamente para renderizar os resultados.

**Erros:** 400 se `q` estiver ausente.

---

### GET /api/text

Retorna o texto extraído completo de um documento.

**Parâmetros de query:**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `id` | string | `file_id` do documento (obrigatório) |

**Resposta 200:** texto extraído completo em **`text/plain`** (não JSON). As páginas são separadas por `---\n`.

**Erros:** 400 se `id` ausente ou inválido (não-hex de 16 caracteres); 404 se documento não encontrado ou sem texto extraído.

---

### GET /api/page

Retorna o texto de uma página específica de um documento.

**Parâmetros de query:**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `id` | string | `file_id` do documento (obrigatório) |
| `page` | inteiro | Número da página, base 1 (obrigatório) |

**Resposta 200:**
```json
{
  "file_id": "0a1b2c3d4e5f6789",
  "page": 3,
  "text": "Conteúdo da página 3..."
}
```

**Erros:** 400 se `id` ou `page` ausentes ou `page` não for inteiro positivo; 404 se página não encontrada.

---

### GET /api/annotations

Lista as anotações de um documento.

**Parâmetros de query:**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `id` | string | `file_id` do documento (obrigatório) |

**Resposta 200:** array JSON de anotações:
```json
[
  {
    "id": "54d0f608c31148e4ae85147e2e0dd7f8",
    "type": "highlight",
    "page": 2,
    "text": "cláusula de rescisão",
    "color": "#FFD700",
    "created_at": "2026-03-15T10:30:00Z",
    "updated_at": "2026-03-15T10:30:00Z"
  }
]
```

Retorna array vazio `[]` se o documento não tiver anotações.

**Erros:** 400 se `id` ausente ou inválido (não-hex de 16 caracteres); 404 se documento não encontrado.

---

### GET /api/wordcloud

Retorna frequências de palavras para a nuvem de palavras.

**Parâmetros de query:**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `type` | string | Filtrar por `doc_type` (opcional; omitir para todos os documentos) |
| `limit` | inteiro | Número máximo de palavras (padrão: 100) |

**Resposta 200:**
```json
{
  "words": [
    {"text": "contrato", "weight": 142},
    {"text": "rescisão", "weight": 87}
  ],
  "total_words": 4523
}
```

Stop words e termos com menos de 3 caracteres são filtrados. O campo `weight` é a contagem de ocorrências normalizada.

---

### GET /api/locations

Retorna locais extraídos dos documentos, com geocodificação opcional.

**Parâmetros de query:**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `geocode` | `0` ou `1` | `1` para geocodificar via Nominatim (padrão: `0`) |

**Resposta 200:**
```json
{
  "locations": [
    {
      "name": "São Paulo",
      "lat": -23.5505,
      "lng": -46.6333,
      "doc_count": 15,
      "docs": ["0a1b2c3d...", "1c2d3e4f..."]
    }
  ],
  "total": 23
}
```

Com `geocode=0`, `lat` e `lng` podem ser `null` para locais não previamente geocodificados. Com `geocode=1`, o servidor chama a API Nominatim (OpenStreetMap) em tempo real — pode introduzir latência; respeita os limites de uso (1 req/s). Os resultados são cacheados em memória por processo, evitando chamadas repetidas à mesma localidade.

---

### GET /api/graph

Retorna o grafo de conhecimento de relações entre entidades.

**Resposta 200:**
```json
{
  "nodes": [
    {"id": "João Silva", "type": "person", "doc_count": 8},
    {"id": "Empresa XYZ Lda", "type": "organization", "doc_count": 5}
  ],
  "edges": [
    {"source": "João Silva", "target": "Empresa XYZ Lda", "weight": 4, "label": "assinou"}
  ]
}
```

Tipos de nó: `person`, `organization`, `location`, `date`, `document`. As arestas representam co-ocorrências ou relações explícitas extraídas pelo pipeline de IA.

---

### GET /api/timeline

Retorna entradas para a vista de linha cronológica.

**Resposta 200:**
```json
{
  "entries": [
    {
      "file_id": "0a1b2c3d4e5f6789",
      "filename": "contrato_2023.pdf",
      "date": "2023-05-15",
      "doc_type": "contrato",
      "summary": "Contrato de arrendamento..."
    }
  ],
  "stats": {
    "total": 42,
    "span_years": 12,
    "earliest": "2011-03-01",
    "latest": "2026-01-20"
  }
}
```

Apenas documentos com datas extraídas aparecem na timeline. Documentos sem data são omitidos.

---

### GET /api/events

Stream SSE (Server-Sent Events) para actualizações em tempo real.

O servidor observa o ficheiro `index.json` (via `mtime`) e emite um evento quando detecta alterações:

```
data: {"event": "index_changed", "count": 42}
```

- `count` — número actual de documentos no índice após a mudança.
- Heartbeats (`: heartbeat`) são enviados a cada 3 s quando não há alteração, mantendo a ligação viva.

A SPA mantém uma conexão permanente a este endpoint. Ao receber `index_changed`, recarrega `/api/index` e actualiza a interface sem recarregar a página.

Cada conexão SSE tem um **tempo máximo de 5 minutos** (300 s), após o qual é encerrada pelo servidor — prevenindo exaustão de threads em `ThreadingHTTPServer`. O cliente deve reconectar automaticamente quando a ligação é encerrada (comportamento padrão dos browsers com `EventSource`).

---

### GET /arquivos-processados/`<filename>`

Serve os ficheiros PDF indexados.

- `<filename>` deve ser um único segmento de caminho (sem `/` nem `..`), por exemplo `0a1b2c3d4e5f6789.pdf`.
- O `Content-Type` enviado é `application/pdf`.
- Protecção contra path traversal: o caminho resolvido deve ser relativo a `arquivos-processados/` — caso contrário, 403.

**Erros:** 400 se `<filename>` contiver `/` ou `..`; 403 se o caminho resolver fora do directório; 404 se o ficheiro não existir.

---

## Endpoints REST — POST

### POST /api/meta/update

Actualiza metadados editáveis de um documento.

**Corpo (JSON):**
```json
{
  "id": "0a1b2c3d4e5f6789",
  "doc_type": "contrato",
  "tags": ["rescisão", "2023"],
  "subject": "Contrato de arrendamento comercial"
}
```

Todos os campos excepto `id` são opcionais. Apenas os campos presentes são actualizados.

**Limite do corpo:** máximo 1 MB — pedidos maiores são truncados antes de serem lidos.

**Resposta 200:**
```json
{"ok": true}
```

**Erros:** 400 se `id` ausente, inválido (não-hex de 16 caracteres) ou JSON inválido; 500 em caso de erro ao gravar o índice.

---

### POST /api/annotations

Adiciona uma nova anotação a um documento.

**Corpo (JSON):**
```json
{
  "file_id": "0a1b2c3d4e5f6789",
  "type": "highlight",
  "page": 2,
  "text": "cláusula de rescisão",
  "color": "#FFD700"
}
```

| Campo | Tipo | Obrigatório | Descrição |
|-------|------|-------------|-----------|
| `file_id` | string | sim | Identificador do documento |
| `type` | string | sim | `highlight`, `note`, `bookmark` |
| `page` | inteiro | sim | Número da página (base 1) |
| `text` | string | sim | Texto anotado ou conteúdo da nota |
| `color` | string | não | Cor em hex (padrão: `#FFD700`) |

**Resposta 201:**
```json
{"id": "54d0f608c31148e4ae85147e2e0dd7f8"}
```

**Erros:** 400 se `file_id` ausente ou inválido (não-hex de 16 caracteres), ou JSON inválido; 500 se falhar ao gravar a anotação.

---

### POST /api/ask

Responde a uma pergunta sobre o conteúdo de um documento (RAG) usando Ollama.

**Requisitos:**
- `PDFSEARCHABLE_AI=ollama` deve estar definido. Caso contrário, responde **400**.
- Ollama deve estar em execução e acessível. Se não estiver, responde **503**.

**Corpo (JSON) — limite 64 KB:**
```json
{
  "id": "0a1b2c3d4e5f6789",
  "question": "Qual é a data de término do contrato?"
}
```

**Resposta 200:**
```json
{"answer": "O contrato termina em 31 de Dezembro de 2025, conforme cláusula 8.º."}
```

**Códigos de erro:**

| Código | Situação |
|--------|----------|
| 400 | JSON inválido, campos em falta, ou `PDFSEARCHABLE_AI` ≠ `ollama` |
| 404 | Documento não encontrado ou sem texto extraído |
| 429 | Rate limit excedido (ver abaixo) |
| 500 | Erro ao ler o documento no store |
| 502 | Erro ao chamar Ollama ou resposta vazia |
| 503 | Ollama não acessível |

**Rate limiting:** configurável com `PDFSEARCHABLE_ASK_RATE_LIMIT` (número inteiro de requisições/minuto; `0` = sem limite). A contagem é **global por processo** (não por IP) e usa uma janela deslizante de 60 segundos. O contador é protegido por `threading.Lock` para segurança concorrente.

**Timeouts:** `PDFSEARCHABLE_ASK_TIMEOUT` controla o tempo máximo de espera pela resposta do Ollama (padrão: 90 s; intervalo aceite: 30–300 s). `PDFSEARCHABLE_OLLAMA_TIMEOUT` controla o timeout geral das chamadas HTTP ao Ollama.

O texto do documento é carregado via `load_file_text(file_id)` e enviado ao Ollama através de `ask_document_ollama(text, question)` em `content_extractors.py`.

---

## CORS

Com `PDFSEARCHABLE_CORS=1`, todos os endpoints incluem os cabeçalhos:

```
Access-Control-Allow-Origin: <PDFSEARCHABLE_CORS_ORIGIN>
Access-Control-Allow-Methods: GET, POST, OPTIONS
Access-Control-Allow-Headers: Authorization, Content-Type
```

O cabeçalho `Authorization` está incluído em `Access-Control-Allow-Headers` para permitir pedidos autenticados a partir de origens diferentes (ex.: SPA em domínio próprio com `PDFSEARCHABLE_AUTH_TOKEN` activo).

O valor de `PDFSEARCHABLE_CORS_ORIGIN` por omissão é `*`. Pedidos `OPTIONS` (preflight) recebem **204** com os cabeçalhos CORS, `Access-Control-Max-Age: 86400` (24 h de cache do preflight) e sem corpo.

Em produção, recomenda-se definir `PDFSEARCHABLE_CORS_ORIGIN` para o domínio específico em vez de `*`.

---

## Autenticação

Com `PDFSEARCHABLE_AUTH_TOKEN=<token>` definido, **todos os pedidos** (incluindo `GET /api/health` e `GET /api/events`) requerem o cabeçalho:

```
Authorization: Bearer <token>
```

Também é aceite Basic Auth — qualquer username com o token como password:
```
Authorization: Basic base64(username:token)
```

Pedidos sem o cabeçalho ou com token inválido recebem **401** com `WWW-Authenticate: Basic realm="pdfsearchable"`.

Se `PDFSEARCHABLE_AUTH_TOKEN` estiver vazio ou não definido, a autenticação está desactivada e todos os endpoints são públicos.

---

## Log de acesso HTTP

Com `PDFSEARCHABLE_HTTP_LOG=1`, cada pedido HTTP é registado no log do projecto (`pdfsearchable.log`) no formato:

```
2026-04-01 14:32:05 GET /api/search?q=contrato 200 12ms
```

Por omissão os logs de acesso estão desactivados para não poluir a saída standard.

---

## Implementação técnica

**Servidor:** `http.server.ThreadingHTTPServer` — um thread por pedido HTTP. Adequado para uso pessoal e pequenas equipas; não é um servidor de produção de alta carga.

**Handler:** subclasse de `SimpleHTTPRequestHandler` que:

- Implementa `do_GET` para todos os endpoints GET listados acima.
- Implementa `do_POST` para `/api/meta/update`, `/api/annotations` e `/api/ask`.
- Implementa `do_OPTIONS` para respostas CORS preflight.
- Pedidos para rotas não reconhecidas devolvem **404** em JSON.
- **Todas as respostas de erro** dos endpoints `/api/*` são em **JSON** (`{"error": "..."}`) — não HTML.
- `log_message` é substituído por uma implementação que respeita `PDFSEARCHABLE_HTTP_LOG`.
- **Listagem de directórios** está desabilitada (403) — `list_directory()` é sobrescrito.

**Raiz do documento:** `.pdfsearchable/` é a raiz; ficheiros estáticos (HTML, CSS, JS, assets) são servidos directamente a partir dessa pasta. Os PDFs em `arquivos-processados/` ficam na pasta pai e são servidos pelo handler especializado de `/arquivos-processados/<filename>`.

**SSE:** o endpoint `/api/events` mantém a conexão aberta num thread dedicado com timeout de 5 min (300 s), observando `meta.json` via polling de `mtime` a cada 3 s.

**Compressão:** endpoints que retornam JSON grande (índice, pesquisa, wordcloud, locations, graph, timeline) suportam **gzip** automaticamente quando o cliente envia `Accept-Encoding: gzip` e o corpo excede 1 KB.

**Setup da SPA:** `_setup_spa(base_dir)` copia os templates do pacote instalado (`importlib.resources` ou caminho relativo ao módulo) para `.pdfsearchable/`, verificando hashes para evitar cópias desnecessárias em arranques repetidos.

---

## Segurança

- **Manter `--host 127.0.0.1`** em ambientes partilhados. O endpoint `/api/ask` envia conteúdo dos documentos ao Ollama — não expor a redes não confiáveis sem autenticação.
- **Path traversal** em `/arquivos-processados/<filename>`: `suffix` não pode conter `/` ou `..`; adicionalmente `(arquivos_dir / suffix).resolve().is_relative_to(arquivos_dir.resolve())` rejeita qualquer caminho que escape do directório.
- **Validação de `file_id`**: em `/api/text`, `/api/page`, `/api/annotations` (GET e POST) e `/api/ask` o `id` é validado como exactamente 16 caracteres hex (`^[0-9a-fA-F]{16}$`), prevenindo path traversal e injecção. Todos os endpoints que aceitam um identificador de documento aplicam esta validação antes de qualquer acesso ao armazenamento.
- **Validação do parâmetro `page`** em `/api/page`: convertido com `int()` dentro de `try/except`; retorna 400 em inputs não numéricos.
- **Limite de corpo POST**: `/api/meta/update` e `/api/annotations` aceitam no máximo 1 MB; `/api/ask` aceita no máximo 64 KB — previne exaustão de memória via `Content-Length` arbitrário.
- **Rate limiting** em `/api/ask` previne uso abusivo do Ollama por parte de clientes mal-intencionados (padrão: 30 req/min; `PDFSEARCHABLE_ASK_RATE_LIMIT=0` desactiva).
- **Auth token** deve ser transmitido apenas sobre HTTPS em produção; sobre HTTP local é aceitável para uso pessoal.
- **CORS com `*`** não deve ser usado se o servidor estiver acessível a partir de origens não confiáveis.

Ver [SEGURANCA.md](SEGURANCA.md) para análise completa.

---

## Encerramento

**Ctrl+C** no terminal encerra o servidor graciosamente. É exibida a mensagem `Servidor encerrado.` Não há endpoint de shutdown remoto.

---

## Ver também

- [03-CLI.md](03-CLI.md) — Comandos `serve` e `report`.
- [06-UX-UI.md](06-UX-UI.md) — Design da SPA, Apple HIG, modo escuro/claro.
- [12-arquivos-gerados.md](12-arquivos-gerados.md) — Onde ficam os ficheiros da SPA e arquivos-processados.
- [SEGURANCA.md](SEGURANCA.md) — Exposição do servidor, autenticação, CORS, path traversal.
