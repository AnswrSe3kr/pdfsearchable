# 6. UX-UI

Documentação da experiência do usuário (UX) e da interface (UI) do **pdfsearchable** (v0.4.0).

---

## Princípios

O projeto segue as diretrizes **Apple HIG** para a interface web, com UX/UI **similar ou superior** a arquivos de documentos de referência como o [Epstein search](https://epsteinsearch.info/):

- **Apple HIG:** tipografia SF Pro (com fallback `-apple-system`, BlinkMacSystemFont); toolbar com efeito frosted-glass (`backdrop-filter: blur(20px) saturate(180%)`); cores de sistema (#007AFF no modo claro / #0A84FF no modo escuro).
- **Layout 3 colunas:** sidebar fixa 240px | área de conteúdo `flex-1` | painel de detalhe slide-in 360px.
- **Dark/light mode:** toggle dinâmico com CSS custom properties (`--bg`, `--bg-elevated`, `--bg-secondary`, `--text-primary`, `--text-secondary`, `--accent`, etc.); segue `prefers-color-scheme` na primeira visita; preferência persistida em `localStorage` (`pdfsearchable-theme`).
- **Navegação por teclado:** `⌘K` foca a barra de pesquisa; `⌘/` abre o modal de atalhos; teclas de seta navegam pela lista de documentos.
- **Busca em primeiro plano:** campo de pesquisa com sugestões "Experimentar:" (termos mais frequentes clicáveis) para descoberta rápida.
- **Conteúdo organizado em cards:** estatísticas, mapa, nuvem, lista de documentos; transições e sombras suaves.
- **Acessibilidade e responsividade:** labels, foco visível, `prefers-reduced-motion` respeitado; mobile-friendly.
- **Mensagens em português, tom direto** ("você", "seu"); sem emojis na SPA (reservados para o report estático).

---

## CLI (terminal)

### Rich

- **Tabelas:** resultados de busca, status, logs, duplicatas, arquivos indexados.
- **Painéis:** mensagens de estado vazio (nenhum arquivo, nenhum documento), resumo executivo da busca.
- **Progresso:** barra com spinner e descrição "Processando (N/total) nome.pdf".
- **Cores:** cyan para arquivos/comandos, green para sucesso, yellow para avisos, red para erros, dim para dicas.

### Mensagens

- **Estado vazio:** orientam o próximo passo (ex.: "Que tal adicionar seu primeiro PDF?", "Use pdfsearchable add pasta/").
- **Erros:** explicam o problema e, quando útil, sugerem ação (ex.: listar arquivos rejeitados quando não são PDF).
- **Confirmação:** em TTY, `remove` pergunta "Remover 'nome' do índice?"; `--yes` evita interação para scripts.

### Consistência

- Comandos e caminhos em destaque (cyan) para fácil leitura.
- Resumos ao final de operações (quantos indexados, throughput, "Report gerado em …").

---

## Interface Web SPA

A partir da v0.4.0, a interface principal servida por `pdfsearchable serve` é uma SPA (Single Page Application) com design Apple HIG. Os templates são copiados para `.pdfsearchable/` pelo `_setup_spa()` ao arrancar o servidor.

### Vistas disponíveis

| Vista | Ficheiro | Endpoint de dados | Descrição |
|-------|----------|-------------------|-----------|
| **Principal** | `app.html` | `/api/search`, `/api/ask`, `/api/document` | SPA 3 colunas: sidebar, lista de documentos, painel de detalhe |
| **Nuvem de palavras** | `wordcloud.html` | `/api/wordcloud` | Nuvem canvas interativa; clique numa palavra navega para `app.html` com a pesquisa pré-preenchida |
| **Mapa de locais** | `map.html` | `/api/locations?geocode=1` | Mapa Leaflet com marcadores geocodificados em tempo real |
| **Grafo de conhecimento** | `graph.html` | `/api/graph` | Grafo de entidades force-directed em canvas; nós e arestas clicáveis |
| **Linha do tempo** | `timeline.html` | `/api/timeline` | Documentos agrupados cronologicamente por ano; clique navega para `app.html` |

Todas as vistas são páginas HTML autónomas servidas pelo servidor HTTP. Não há framework JavaScript — JavaScript vanilla com CSS custom properties.

### Navegação cross-view

Qualquer vista pode iniciar uma pesquisa no `app.html` sem recarregar a SPA:

1. A vista define `localStorage.setItem('pdfs-search-init', termo)`.
2. A vista navega para `app.html`.
3. O `app.html` lê `pdfs-search-init` no `init()`, pré-preenche a barra de pesquisa e remove a chave do `localStorage`.

Isto permite, por exemplo, clicar numa palavra na nuvem e aterrar no `app.html` com os resultados da pesquisa já visíveis.

---

## Layout detalhado do app.html

### Sidebar (240px, fixa)

- **Strip de estatísticas:** contagem de documentos, páginas, palavras e tamanho total; valores atualizados via API ao carregar.
- **Filtros por tipo:** pills clicáveis para filtrar a lista de documentos por `doc_type`; pill "Todos" seleccionado por omissão.
- **FERRAMENTAS:** links para as vistas adicionais (Nuvem, Mapa, Grafo, Linha do tempo) e para o report estático.
- **Toggle dark/light:** ícone de lua/sol no rodapé da sidebar; alterna entre os temas e persiste em `localStorage`.

### Área de conteúdo (flex-1)

- **Toolbar frosted-glass:** título da coleção e botão de perfil/configurações.
- **Barra de pesquisa:** campo com placeholder "Pesquisar… (⌘K)"; modo de pesquisa (FTS / semântica); sugestões "Experimentar:" com os termos mais frequentes como links clicáveis.
- **Controlos de ordenação e filtro:** ordenar por data, nome ou relevância; filtro de idioma; toggle de filtros avançados.
- **Lista de documentos:** cards com skeleton loaders durante o carregamento; por card: nome do documento, badges (tipo, idioma), excerto do resultado com highlight, número de páginas e data de indexação. Navegação com teclas de seta; card selecionado abre o painel de detalhe.

### Painel de detalhe (360px, slide-in)

O painel desliza da direita ao selecionar um documento na lista. Fecha com `Esc` ou clicando fora.

- **Aba Info:** nome, tipo, idioma, páginas, tamanho, hash de conteúdo, datas de indexação/atualização, metadados do PDF (título, autor, producer, creator, keywords); campos título e tags editáveis inline (persistidos via PATCH `/api/document/<id>`); tags com gestão (adicionar/remover).
- **Aba Texto:** texto extraído do documento paginado; campo de pesquisa no texto com highlight dos termos.
- **Aba IA:** campo de pergunta livre; resposta RAG via `/api/ask` (Ollama/OpenAI); histórico de perguntas e respostas na sessão.
- **Aba Anotações:** lista de anotações do documento; campo para adicionar nova anotação; eliminação de anotações; persistidas via API.

---

## Tema e CSS custom properties

O sistema de temas usa CSS custom properties definidas no `:root` para o modo claro e sobrepostas em `[data-theme="dark"]`:

| Variável | Modo claro | Modo escuro |
|----------|-----------|-------------|
| `--bg` | `#f5f5f7` | `#000000` |
| `--bg-elevated` | `#ffffff` | `#1c1c1e` |
| `--bg-secondary` | `#f2f2f7` | `#2c2c2e` |
| `--text-primary` | `#1d1d1f` | `#f5f5f7` |
| `--text-secondary` | `#86868b` | `#98989d` |
| `--accent` | `#007AFF` | `#0A84FF` |
| `--border` | `rgba(0,0,0,.08)` | `rgba(255,255,255,.08)` |

Na primeira visita, o tema segue `prefers-color-scheme`. Depois, a preferência é lida de `localStorage` (`pdfsearchable-theme`).

---

## Report Estático (report.html)

O `report.html` gerado por `pdfsearchable report` mantém um design Apple-like independente da SPA.

### Estilo visual

- **Cores:** fundo `#fbfbfd` com gradiente suave até `#f5f5f7`; cards brancos; texto primário `#1d1d1f`, secundário `#86868b`; azul sistema `#007AFF` (accent) e hover `#0051d5`; cinzas `#e8e8ed`, `#d2d2d7`, `#86868b`; bordas `rgba(0,0,0,.06)`; sombras em camadas suaves (2px + 16px).
- **Modo escuro:** fundo `#000`, card `#1c1c1e`, texto `#f5f5f7`, azul `#0a84ff`, bordas `rgba(255,255,255,.08)`.
- **Tipografia:** Inter (Google Fonts) com fallback para `-apple-system`, BlinkMacSystemFont.
- **Espaçamento:** padding e margens generosos; cantos arredondados (12px / 18px); sombra leve nos cards.
- **Design system:** variáveis de transição (`--ease-out`, `--ease-in-out`, `--duration-*`) para animações consistentes.
- **Skeleton loaders:** durante a busca e ao trocar aba na nuvem de palavras.
- **Animações:** fade-in nos resultados da busca; `prefers-reduced-motion` respeitado.

### Componentes

- **Navegação:** nome do documento na lista é link para a visualização (document-view); breadcrumb e botão "Voltar ao home do report" na tela de documento.
- **Interatividade:** nuvem de palavras (wordcloud2.js) com clique para buscar; mapa de locais (Leaflet) com zoom e marcadores.
- **Busca:** placeholder "Pesquisar… (Ctrl+K)"; linha **"Experimentar:"** com os 8 termos mais frequentes; checkboxes "Ignorar acentos" e "Buscar por sinônimos"; operadores AND/OR/NEAR.
- **Uso de emojis** em títulos e destaques (📄, 📂, 📊, 🔍, 📋).

### Responsividade

- **Até 768px:** container e cards com menos padding; título menor; stats mais compactos; lista de documentos em coluna; botão "Copiar comando para remover" em largura total; tabela "Por tipo" com scroll horizontal; abas da nuvem em linha; rodapé menor.

---

## Fluxo do usuário

1. **Primeiro uso:** `pdfsearchable add arquivo.pdf` ou `pasta/` (recursivo por padrão) → feedback de progresso → tabela de indexados.
2. **Interface viva:** `pdfsearchable serve` → abre `http://host:port/app.html` no navegador — pesquisa em tempo real, IA, anotações.
3. **Snapshot portátil:** `pdfsearchable report` → gera `.pdfsearchable/report.html` — abrível offline, sem servidor.
4. **Vistas especializadas:** aceder a `wordcloud.html`, `map.html`, `graph.html` ou `timeline.html` via links FERRAMENTAS na sidebar.
5. **Remover:** no report ou na SPA, copiar o comando e rodar no terminal; ou `remove "nome"` (com confirmação em TTY).
6. **Duplicatas:** `pdfsearchable duplicates` ou card "Duplicatas" no report.

---

## Documentação de referência

- **Funcionalidades:** `docs/02-funcionalidades.md`
- **CLI:** `docs/03-CLI.md`
- **Report:** `docs/04-report.md`
- **Servidor e API:** `docs/11-servidor.md`
