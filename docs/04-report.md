# 4. Report

Documentação do **report HTML estático** e da **interface web SPA** do pdfsearchable (v0.4.0).

---

## Interface SPA vs Report Estático

A partir da v0.4.0, o pdfsearchable distingue dois modos de interface web:

| Aspecto | SPA (`app.html`) | Report estático (`report.html`) |
|---------|------------------|---------------------------------|
| **Gerado por** | `pdfsearchable serve` (copia templates para `.pdfsearchable/`) | `pdfsearchable report` (geração Jinja2) |
| **Atualização de dados** | Em tempo real via chamadas REST à API do servidor | Snapshot do índice no momento da geração |
| **Requer servidor ativo** | Sim — depende dos endpoints `/api/*` | Não — arquivo HTML autónomo, abrível offline |
| **Busca** | FTS + busca semântica via `/api/search` | JavaScript no cliente sobre `search_data` embutido |
| **Q&A com IA** | Sim — via `/api/ask` (Ollama/OpenAI) | Não disponível |
| **Anotações e metadados editáveis** | Sim — persistidos via API | Não — somente leitura |
| **Vistas adicionais** | wordcloud, map, graph, timeline | Nuvem e mapa embutidos na página única |
| **Modo escuro/claro** | Toggle dinâmico com CSS custom properties | Modo escuro via media query (estático) |

**Regra prática:**
- Use `pdfsearchable serve` para trabalho do dia-a-dia: interface viva, pesquisa em tempo real, IA e anotações.
- Use `pdfsearchable report` para criar um snapshot portátil e autónomo do índice (enviar por e-mail, arquivar, abrir sem servidor).

---

## Objetivo do Report Estático

O `report.html` é uma página HTML **totalmente autónoma** (sem dependência de servidor) que consolida o estado do índice num momento específico: estatísticas, lista de documentos, busca no navegador (com filtros avançados e sinônimos), nuvem de palavras **interativa**, **mapa de referências a locais**, atividade recente e duplicatas. Visual inspirado em estilo **Apple-like** (limpo, cards, tipografia Inter). Ao clicar no nome de um documento, abre-se a **visualização do documento** (PDF + metadados + hash; resumo e tags quando disponíveis com Ollama).

---

## Localização

- **Report (home):** `.pdfsearchable/report.html`
- **Visualização de documento:** `.pdfsearchable/document-view.html?id=<id>`
- **PDFs e texto extraído:** `arquivos-processados/` (na raiz do projeto)

**Geração do report estático:** o report (e o `document-view.html`) é gerado **apenas pelo comando `pdfsearchable report`**, que invoca `generate_report()` e grava os ficheiros em `.pdfsearchable/`. O comando imprime o caminho do ficheiro gerado ao concluir.

**Serve não gera mais o report:** a partir da v0.4.0, o comando `pdfsearchable serve` **não** gera `report.html`. Em vez disso, copia os templates da SPA (via `_setup_spa()`) para `.pdfsearchable/` e arranca o servidor HTTP. A interface principal do servidor é `app.html`.

**Cache:** se o índice não tiver mudado (hash em `.pdfsearchable/report_hash.txt`), `pdfsearchable report` não regera o report. Este ficheiro de hash só é relevante para o comando `report`.

**Configuração:** variáveis como `PDFSEARCHABLE_LIST_PAGE_SIZE` (tamanho da lista) e `PDFSEARCHABLE_SEARCH_SNIPPET_MAX_CHARS` (limite de texto por documento no search_data) afetam o tamanho do HTML; ViaCEP e IP-API em [10-config.md](10-config.md).

---

## O que a SPA (app.html) oferece que o report.html não tem

O `app.html` servido pelo `pdfsearchable serve` vai além do snapshot estático:

- **Atualizações em tempo real:** os dados refletem o índice atual sem necessidade de regerar o ficheiro.
- **Busca FTS + semântica via API:** resultados instantâneos via `/api/search` e `/api/search?mode=semantic`.
- **IA (Q&A):** painel "IA" no detalhe do documento com perguntas via `/api/ask` (Ollama/OpenAI).
- **Anotações:** painel "Anotações" persistido via API; notas por documento.
- **Metadados editáveis:** título, tags e outros campos editáveis diretamente na interface.
- **Cinco vistas:** `app.html`, `wordcloud.html`, `map.html`, `graph.html`, `timeline.html` — cada uma com propósito específico (ver [06-UX-UI.md](06-UX-UI.md)).
- **Modo escuro/claro dinâmico:** toggle com CSS custom properties e `localStorage`.
- **Navegação cross-view:** qualquer vista pode iniciar uma pesquisa no `app.html` via `localStorage`.

---

## Estrutura da página (report.html)

1. **Cabeçalho** — Título (configurável) e subtítulo.
2. **Estatísticas** — Cards com: documentos, páginas, palavras totais, MB indexados; tabela "Por tipo" (contagem por doc_type); tabelas "Por idioma" e "Por período" (por ano); linhas extras: documento médio (páginas), documentos com mais de N páginas, classificados por IA, páginas com OCR.
3. **Pesquisar** — Campo de busca; linha **"Experimentar:"** com os 8 termos mais frequentes como links (um clique para pesquisar, no estilo de arquivos de referência como [Epstein search](https://epsteinsearch.info/)); opções **"Ignorar acentos"** e **"Buscar por sinônimos"** (checkboxes); operadores AND, OR e NEAR; **filtros avançados** (painel expansível): tipo de documento, pessoa citada (partes/participantes), quantidade de páginas (faixas), data de indexação (de/até); resultados e lista de documentos respeitam os filtros aplicados; contagem de resultados; suporte a máscaras (IP, CPF, CNPJ, e-mail, etc.). **Skeleton loaders** durante "Buscando…"; resultados com animação **fade-in**.
4. **Nuvem de palavras** — Abas "Geral" e por tipo (doc_type); imagem da nuvem; **skeleton** ao trocar de aba; lista "Top palavras", "Bigramas" e **"Termos em destaque"** (snippets pré-computados para as top palavras).
5. **Referências a locais** — Card sempre visível; lista "X arquivo(s) citaram [local]" (clique para centralizar no mapa e abrir o popup; lista rola até o item); mapa interativo (Leaflet); base de cidades, estados, regiões e países (BR e internacional). **Enriquecimento:** CEPs encontrados no texto são consultados via **ViaCEP** (localidade/UF); IPs identificados nos documentos são geolocalizados via **IP-API** (cidade, país, coordenadas). Ativação/desativação: `PDFSEARCHABLE_VIA_CEP` e `PDFSEARCHABLE_IP_API` (detalhes em [10-config.md](10-config.md)). Estado vazio com mensagem amigável.
6. **Atividade recente** — Últimas entradas do audit (index_done, index_error, etc.) com timestamp e detalhes.
7. **Duplicatas** — Grupos de arquivos com mesmo content_hash (nome e path).
8. **Documentos processados** — Lista com filtros por tipo e idioma; por documento: nome, tipo, idioma, páginas, "X com OCR", datas (adicionado/atualizado), metadados (título, autor, datas do PDF, producer, creator, keywords); botão "Copiar comando para remover".
9. **Rodapé** — Data de geração do report, versão do índice, versão do pacote (pdfsearchable).

---

## Busca no report

- **Fonte de dados:** `search_data` com um item por documento: `name`, `id`, `text`, `pages: [{ n, text }]`, `doc_type`, `num_pages`, `indexed_at`, `parties` (para filtros avançados). **Sinônimos:** `search_synonyms` (mapa termo → equivalente ou vários separados por vírgula), do config/env e opcionalmente enriquecido por API (PT-BR ou EN-US) quando `PDFSEARCHABLE_SYNONYMS_API_ENABLED=1`.
- **Comportamento:** busca no cliente (JavaScript): para cada termo digitado, varre os `pages` de cada documento; exibe "página N" e snippet com highlight. Opção **"Ignorar acentos"** (marcada por padrão) normaliza termo e texto para matching. Opção **"Buscar por sinônimos"** expande cada termo com os equivalentes do mapa. **Operadores:** AND, OR e NEAR N (ex.: `termo1 OR termo2`, `x NEAR 5 y`).
- **Filtros avançados:** painel "Filtros avançados" com tipo de documento, pessoa citada (lista única de partes extraídas), quantidade de páginas (1–5, 6–20, 21–50, 51+), data de indexação (de/até). "Aplicar filtros" atualiza a lista de documentos e os resultados da busca. Integrados aos filtros já existentes (tipo e idioma) na lista de documentos.
- **Máscaras:** as mesmas da CLI (CPF, CNPJ, IP, e-mail, domínio, etc.) são aplicadas na lógica de matching no front-end quando relevante.

---

## Nuvem de palavras

- **Geral:** agregação do texto de todos os documentos; stopwords (PT/EN) + lista de exclusão (env `PDFSEARCHABLE_WORDCLOUD_STOP`, vírgulas).
- **Por tipo:** uma nuvem por `doc_type`; abas para alternar (com indicador de carregamento ao trocar).
- **Visual:** paleta estilo Apple (azuis, roxos, verdes, laranjas); hierarquia de tamanhos (min/max font); até 100 palavras na imagem estática.
- **Interatividade:** nuvem (aba Geral) com wordcloud2.js: clique em uma palavra para buscar; lista "Top palavras" com termos clicáveis; subtítulo orienta "palavras mais frequentes" e uso das abas.
- **IA (Ollama):** com `PDFSEARCHABLE_AI=ollama`, o report pode enriquecer a nuvem com palavras-chave extraídas por IA; o card exibe o badge "Enriquecido com IA" quando aplicável.
- **Top palavras, bigramas e termos em destaque:** listas clicáveis e snippets pré-computados no backend.

---

## Responsividade

- **Desktop:** layout em colunas, cards lado a lado quando fizer sentido.
- **Mobile (≤ 768px):** padding reduzido, cards empilhados, lista de documentos em coluna, botão de remover em largura total, tabela "Por tipo" com scroll horizontal (`.table-wrap`), abas da nuvem em linha.

---

## Visualização de documento (document-view.html)

- **Acesso:** link no nome do arquivo no report → `document-view.html?id=<file_id>`.
- **Layout:** painel esquerdo com PDF (iframe) e botão Download; painel direito (sidebar) com metadados: tipo, páginas, palavras, **hash de conteúdo**, datas de indexação/atualização, idioma, metadados do PDF (título, autor, producer, etc.).
- **Navegação:** breadcrumb "Home — [Nome do documento]"; botão "Voltar ao home do report" (report.html).
- **Dados:** metadados embutidos no HTML no momento da geração do report; PDF carregado de `../arquivos-processados/<id>.pdf`.

> **Nota:** no modo SPA (`serve`), a visualização de documento é feita no painel de detalhe do `app.html` (slide-in panel com abas Info, Texto, IA, Anotações), que é mais completo e atualizado em tempo real. O `document-view.html` é um template copiado para `.pdfsearchable/` pelo `_setup_spa()`.

---

## Design system e animações (Apple-like)

- **Variáveis de transição:** `--ease-out`, `--ease-in-out`, `--duration-fast`, `--duration-normal`, `--duration-slow`.
- **Transições:** cards (sombra e borda), abas da nuvem, painéis de filtros.
- **Skeleton loaders:** busca (blocos skeleton) e nuvem (retângulo skeleton ao trocar aba).
- **Animações:** `fade-in` nos resultados da busca; `prefers-reduced-motion` respeitado.

## Tecnologias

- **Templates:** Jinja2 (`report.html`, `document-view.html`).
- **Estilos:** CSS inline nos templates (variáveis CSS, design system, media queries).
- **Fonte:** Inter (Google Fonts) com fallback para `-apple-system`, BlinkMacSystemFont.
- **Scripts:** JavaScript vanilla para busca, filtros (tipo/idioma), abas da nuvem, cópia do comando de remoção; **wordcloud2.js** (nuvem interativa); **Leaflet** (mapa de locais).

---

## Variáveis passadas ao template

Principais: `title`, `total_files`, `total_pages`, `total_words`, `total_size_mb`, `count_by_type`, `doc_types`, `count_by_language`, `languages`, `count_by_year`, `years_sorted`, `avg_pages_per_doc`, `docs_large_threshold`, `docs_large_count`, `files` (ordenados por data), `index_version`, `app_version`, `report_generated_at`, `wordcloud_b64`, `wordcloud_by_type`, `wordcloud_words` (para nuvem interativa), `top_words`, `bigrams`, `highlight_snippets` (termos em destaque), `search_data`, `search_synonyms` (mapa termo → sinônimo(s)), `all_parties` (lista única de partes/participantes para filtro "pessoa citada"), `recent_activity`, `duplicate_groups`, `location_refs` (referências a locais), `files_base_url`, `report_home_url`, `document_view_url`.

---

## Sugestões de novas estatísticas (painel)

Ideias para enriquecer o painel de estatísticas, usando dados já disponíveis no índice ou fáceis de derivar.

| Sugestão | Descrição | Dados necessários | Prioridade |
|----------|-----------|-------------------|------------|
| **Por idioma** | Tabela "Por idioma" (pt-BR, en, etc.) com quantidade de documentos. | `language` por arquivo (já existe). | Alta |
| **Documentos com IA** | Card ou linha: "X documentos classificados por IA" (OpenAI/Ollama). | `classification_source` (openai, ollama) por arquivo. | Alta |
| **Páginas com OCR** | Total de páginas que passaram por OCR ou % sobre o total de páginas. | `pages[].has_ocr` ou `ocr_percentage` por arquivo. | Alta |
| **Média por documento** | "Média: N páginas/doc" e "Média: N palavras/doc" (sob os cards ou em texto). | `total_pages/total_files`, `total_words/total_files`. | ✅ Implementado |
| **Documentos grandes** | "X documentos com mais de N páginas" (configurável via `PDFSEARCHABLE_STATS_LARGE_DOC_PAGES`). | `num_pages` por arquivo. | ✅ Implementado |
| **Por período** | Tabela "Por período" por ano (indexed_at ou updated_at). | `indexed_at`, `updated_at` por arquivo. | ✅ Implementado |
| **Duplicatas (resumo)** | No painel de estatísticas: "X grupos de duplicatas" ou "Y arquivos em duplicata". | `duplicate_groups` (já calculado para o card Duplicatas). | Média |
| **Locais referenciados** | "X locais citados" (cidades, estados, países) com link para o card do mapa. | `location_refs` (já calculado). | Média |
| **Partes/participantes** | "X pessoas ou partes citadas" (partes, outorgantes, etc.). | `all_parties` (já calculado para filtro). | Média |
| **Valores detectados** | Total de menções a valores (BRL, USD, EUR) ou contagem por moeda. | Soma de `monetary_values` por arquivo (já existente no índice). | Média |
| **Documentos recentes** | "X documentos indexados nos últimos 30 dias". | `indexed_at` por arquivo. | Baixa |
| **Entidades (CPF, CNPJ, e-mail, IP)** | Totais agregados: "X CPFs", "X e-mails" etc. (opcional, pode ser pesado). | `identified_cpfs`, `identified_emails`, etc. por arquivo. | Baixa |

Implementação sugerida: começar por **Por idioma**, **Documentos com IA** e **Páginas com OCR** (dados já no índice; só expor no template e, se necessário, passar variáveis novas a partir de `generate_report`).

---

## Ver também

- [03-CLI.md](03-CLI.md) — Comandos `report` (gerar snapshot) e `serve` (SPA em HTTP).
- [06-UX-UI.md](06-UX-UI.md) — Design system da SPA (app.html) e das vistas adicionais.
- [11-servidor.md](11-servidor.md) — Servidor HTTP: rotas, `/api/ask`, `_setup_spa()`.
- [12-arquivos-gerados.md](12-arquivos-gerados.md) — Onde ficam report.html, templates SPA e arquivos-processados.
- [10-config.md](10-config.md) — ViaCEP e IP-API (locais), sinônimos, `PDFSEARCHABLE_LIST_PAGE_SIZE`, `PDFSEARCHABLE_SEARCH_SNIPPET_MAX_CHARS`, FTS diferido.
- [02-funcionalidades.md](02-funcionalidades.md) — Lista de funcionalidades por área (Report HTML, SPA, análise avançada).
- [05-logs-e-auditoria.md](05-logs-e-auditoria.md) — Log e exceções (StoreError, ReportError) quando a geração do report falha.
- [FAQ.md](FAQ.md) — Perguntas frequentes sobre report e busca.
