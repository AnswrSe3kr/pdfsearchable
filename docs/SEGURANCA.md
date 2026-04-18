# Segurança — pdfsearchable

Resumo da análise de segurança do projeto e das medidas aplicadas.

## Credenciais e segredos

- **Senha de PDF:** obtida via opção `--password` ou variável de ambiente `PDF_PASSWORD`. Não é gravada em disco nem registrada em `audit.jsonl` ou logs.
- **API keys (OpenAI, API Ninjas):** lidas de variáveis de ambiente (`OPENAI_API_KEY`, `API_NINJAS_KEY` / `PDFSEARCHABLE_API_NINJAS_KEY`). Não há hardcode de chaves no código.
- **Auditoria:** os eventos em `audit.jsonl` e no log não incluem senhas nem chaves de API.

## Permissões do directório de dados

- **`.pdfsearchable/`** é criado com permissões **`700` (`rwx------`)**, evitando leitura por outros utilizadores em servidores partilhados. Protege o índice, textos extraídos, audit log e embeddings independentemente do umask do processo.

## Path traversal

- **`file_id`:** o identificador de arquivo é sempre 16 caracteres hex (derivado de SHA-256 do path). As funções que montam caminhos a partir de `file_id` (`_file_dir`, `load_file_text`, `load_page_text`, `copy_pdf_to_store`, `remove_file_meta`) validam o formato (`^[a-f0-9]{16}$`) e rejeitam valores inválidos, evitando escape do diretório `arquivos-processados/`.
- **Paths de entrada (CLI):** o comando `add` recebe caminhos de arquivos/pastas; o índice usa apenas `file_id` gerado internamente. O comando `remove` identifica o documento por `id` ou nome no índice, não por path arbitrário.
- **`export --output`:** quando o caminho de destino está fora do directório de trabalho, a CLI exibe um aviso antes de prosseguir.

## Chamadas externas (APIs e redes)

- **Sinônimos PT-BR:** a URL base da API de dicionário pode ser configurada por `PDFSEARCHABLE_API_DICIONARIO_PTBR`. Foi adicionada validação para aceitar apenas URLs `http://` ou `https://`, evitando uso de esquemas como `file://` (SSRF).
- **API Ninjas (EN):** URL fixa; chave só via parâmetro ou env.
- **Ollama / OpenAI:** URLs configuráveis por env (`PDFSEARCHABLE_OLLAMA_URL`, etc.); chamadas com timeout (60–90 s). Em ambientes multi-tenant, restringir essas URLs a destinos permitidos.

Recomendação: não exponha o processo que gera o report a variáveis de ambiente controladas por terceiros; em servidores compartilhados, fixe as URLs de API no código ou em config restrito.

## Templates e XSS

- **Jinja2:** o ambiente de templates usa `autoescape=select_autoescape(["html", "xml"])`, garantindo escape de saída em HTML.
- **Report / document-view:** dados dinâmicos (nomes de arquivos, snippets, termos de busca, metadados) são passados via `tojson` (Jinja2) e, no JavaScript, exibidos com `escapeHtml()` antes de inserir no DOM.
- **Sanitização de nomes:** no report, nomes de ficheiros exibidos são sanitizados (`_sanitize_display_name`: apenas nome do ficheiro, sem path, sem caracteres de controlo) para evitar reflexão de path ou conteúdo perigoso.
- **CSP:** o template do report inclui `Content-Security-Policy` (meta) para mitigar XSS (default-src 'self'; script-src/style-src com origens permitidas para fonts e Leaflet).

## Dependências

- As dependências principais estão em `pyproject.toml` (click, pymupdf, rich, wordcloud, jinja2; opcionais: pytest, openai, pytesseract, Pillow).
- **Recomendação:** executar periodicamente `pip install --upgrade pip` e depois `pip audit` (ou `safety check` com o pacote `safety`) no ambiente onde o projeto é instalado, e corrigir CVEs reportados.

## Uso perigoso de funções

- Não há uso de `os.system`, `subprocess`, `eval`, `exec` ou `pickle.loads` no código da aplicação em `src/pdfsearchable/*.py`.

## Resumo das alterações de segurança

### Sessão 2026-03-03
1. **Jinja2:** ativação explícita de autoescape nos templates do report.
2. **synonyms_api:** validação da URL base da API PT-BR para permitir apenas `http://` e `https://`.
3. **store:** validação de `file_id` (formato 16 hex) em `_file_dir`, `load_file_text`, `load_page_text`, `copy_pdf_to_store` e `remove_file_meta` para prevenir path traversal.

### Sessão 2026-04-06
4. **CLI `/api/text`:** validação de `file_id` reforçada — além de verificar que todos os caracteres são hex, agora exige exactamente 16 caracteres, prevenindo IDs arbitrariamente longos.
5. **CLI `/api/page`:** parâmetro `page` agora validado com `try/except` antes de `int()` — evita 500 em inputs não numéricos; normalizado para `max(1, page)`.
6. **CLI `/api/meta/update` e `/api/annotations`:** adicionado limite de 1 MB ao corpo dos pedidos POST, prevenindo ataques de exaustão de memória via `Content-Length` elevado.
7. **CLI `/api/ask`:** corpo do pedido POST limitado a 64 KB (suficiente para `id` + pergunta; o conteúdo do documento não é transmitido pelo cliente).
8. **CLI `/api/search`:** corrigido bug em que `type_filter` chamava `.get()` num tuple retornado por `fts_search`, causando `AttributeError`; filtro agora usa `r[0]` (índice do `file_id`).
9. **store.py cache:** substituídas cópias shallow por `copy.deepcopy` na devolução de cache, evitando corrupção do estado interno quando múltiplos threads modificam os dicts de metadados devolvidos por `load_index()`.
10. **mcp_server.py:** versão do servidor MCP agora derivada dinamicamente do pacote instalado (`__version__`) em vez de estar hardcodada.
11. **synonyms_api.py `_is_safe_http_url`:** validação SSRF reforçada — além de verificar `http://`/`https://`, agora bloqueia explicitamente IPs privados (RFC 1918), loopback (127.x, ::1), link-local (169.254.x.x) e reservados, prevenindo que uma `PDFSEARCHABLE_API_DICIONARIO_PTBR` mal configurada sirva como pivot para serviços internos.
12. **semantic_search.py `_cosine`:** alterado `zip(strict=False)` para `zip(strict=True)` — mismatch de dimensões entre vectores agora levanta `ValueError` em vez de truncar silenciosamente, detectando corrupção de embeddings.
13. **semantic_search.py `_blob_to_vec`:** adicionada validação de tamanho do blob antes de `struct.unpack` — blobs cujo comprimento não é múltiplo de 4 levantam `ValueError` explícito em vez de desserializar dados parciais.

### Sessão 2026-04-07
14. **store.py `_file_id`:** adicionada normalização Unicode NFC (`unicodedata.normalize("NFC", ...)`) antes de calcular o SHA-256 — garante que o mesmo ficheiro físico produz sempre o mesmo `file_id` independentemente da forma de normalização do path (NFD vs NFC), eliminando duplicados silenciosos em sistemas macOS HFS+/APFS.
15. **CLI `/api/meta/update`:** adicionada validação de formato de `id` (exactamente 16 caracteres hex) — consistente com os restantes endpoints que aceitam identificadores de documento.
16. **CLI `/api/ask`:** erros de pré-condição (Ollama não configurado, Ollama inacessível) agora retornam `application/json` em vez de HTML — evita que clientes que esperam JSON recebam respostas não estruturadas.
17. **store.py `update_file_tags`:** tags agora gravadas no campo `tags` de topo do documento (em vez de `metadata.tags`) — consistente com o campo lido em toda a base de código, eliminando silêncio de perda de dados na actualização via API.

### Sessão 2026-04-07 (continuação — melhorias ao servidor)
18. **CLI: respostas de erro em JSON:** todos os endpoints `/api/*` agora retornam `{"error": "..."}` em JSON em vez de páginas HTML geradas por `send_error()` — garante que clientes que esperam JSON nunca recebem HTML inesperado.
19. **CLI: CORS no rate limit 429:** a resposta 429 de `/api/ask` agora inclui cabeçalhos CORS completos (via `_send_cors()`), permitindo que SPAs cross-origin interpretem correctamente o erro.
20. **CLI: `_ask_timeout` agora aplicado:** o timeout configurável via `PDFSEARCHABLE_ASK_TIMEOUT` (30–300 s, padrão 90 s) é agora passado à chamada `ask_document_ollama()`, evitando que pedidos ao Ollama fiquem pendurados indefinidamente.
21. **CLI: SSE timeout de 5 min:** conexões ao `/api/events` têm agora um tempo máximo de 300 s, prevenindo exaustão de threads do `ThreadingHTTPServer` por clientes que mantêm ligações abertas indefinidamente.
22. **CLI: listagem de directórios desabilitada:** o método `list_directory()` foi sobrescrito para retornar 403 JSON, impedindo que o `SimpleHTTPRequestHandler` exponha a estrutura de ficheiros de `.pdfsearchable/`.
23. **CLI: `Access-Control-Max-Age` no preflight:** respostas `OPTIONS` incluem agora `Access-Control-Max-Age: 86400` (24 h), reduzindo o número de pedidos preflight repetidos por browsers.
24. **CLI: cache de geocodificação:** resultados da API Nominatim são cacheados em memória por processo, evitando chamadas repetidas à mesma localidade e reduzindo o risco de exceder limites de uso da API.
25. **CLI: compressão gzip:** endpoints que retornam JSON grande suportam compressão gzip automática quando o cliente envia `Accept-Encoding: gzip` e o corpo excede 1 KB, reduzindo significativamente a largura de banda.

### Sessão 2026-04-09
26. **OCR DPI:** padrão aumentado de 150 → 300 e máximo de 300 → 600 para melhor suporte a documentos históricos.
27. **CLI defaults simplificados:** `--recursive` e `--skip-failed` agora activos por padrão no `add`; `--open` activo por padrão no `serve`. Reduz necessidade de flags e erros de utilizador.
28. **scripts/check_env.py:** script cross-platform (Windows, Linux, macOS) para verificação e instalação de dependências. Não executa código arbitrário — apenas verifica binários do sistema e instala via pip.
29. **HTR multilíngue:** suporte a 40+ idiomas com 6 modelos dedicados; cache LRU thread-safe; detecção automática de script.

## Boas práticas para o operador

- Manter o ambiente (pip/venv) atualizado e rodar `pip audit` com frequência.
- Não versionar `.pdfsearchable/` (índice, audit, cache) nem `arquivos-processados/` se contiverem dados sensíveis.
- Em cenários de relatórios servidos por um servidor web, servir apenas como arquivos estáticos (sem executar código do usuário no mesmo processo que acessa o índice).
- O comando `pdfsearchable serve` por padrão escuta em `127.0.0.1` (apenas localhost); use `--host` com cuidado em redes compartilhadas. O endpoint `/api/ask` chama o Ollama com conteúdo do documento; não exponha o servidor a redes não confiáveis sem avaliar riscos.
