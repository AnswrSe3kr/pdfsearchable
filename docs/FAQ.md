# FAQ — pdfsearchable

Perguntas frequentes sobre instalação, uso, report e configuração.

---

## Instalação e uso básico

### Como instalo o pdfsearchable?

Na pasta do projeto:

```bash
pip install -e .
```

Ou use o script de verificação e instalação:

```bash
python scripts/check_env.py         # Verifica e instala dependências (Windows, Linux, macOS)
python scripts/check_env.py --check # Apenas verifica, sem instalar
```

OCR (Tesseract) e TrOCR estão incluídos nas dependências principais. Para classificação por OpenAI: `pip install -e ".[ai]"`.

### Onde devo executar os comandos?

Execute **na pasta onde estão os PDFs** (ou onde quer o índice). O projeto cria ali `.pdfsearchable/` e `arquivos-processados/`.

### Como adiciono PDFs?

```bash
pdfsearchable add documento.pdf
pdfsearchable add pasta/
```

OCR está sempre ativo (não pode ser desativado). Use `--password SENHA` para PDF protegido, `--batch-size 10` para controlar uso de memória.

---

## Report e busca

### Onde fica o report?

O report pode ser gerado de duas formas:

- **Linha de comando (sem servidor):** `pdfsearchable report` — gera imediatamente `.pdfsearchable/report.html` e `document-view.html`. Útil para CI/CD ou exportação offline.
- **Modo servidor:** execute `pdfsearchable serve` e aceda a http://127.0.0.1:8000/app.html (SPA interativa com busca, mapa, nuvem de palavras, timeline e grafo). O `serve` **não** gera `report.html` — use `pdfsearchable report` para isso.

### A busca encontra palavras com acento?

Sim. Use a opção **"Ignorar acentos"** (marcada por padrão): assim "contrato" encontra "contrató" e o trecho é destacado com o texto original.

### A busca aceita operadores (AND, OR, NEAR)?

Sim. No report, no campo de pesquisa você pode usar:
- **OR** — ex.: `contrato OR acordo` (qualquer um dos termos).
- **AND** — ex.: `nota AND fiscal` (ambos devem aparecer).
- **NEAR** — ex.: `cliente NEAR 5 contrato` (os dois termos dentro de até 5 palavras). O número é opcional (padrão 5).
Os operadores podem ser combinados; sinônimos (config) são aplicados a cada termo.

### O que são os "Filtros avançados"?

Na área de pesquisa do report você pode restringir por:

- **Tipo de documento** — classificação (contrato, nota_fiscal, relatório, etc.).
- **Pessoa citada** — partes/participantes extraídas (ex.: Outorgante, Partes).
- **Quantidade de páginas** — faixas (1–5, 6–20, 21–50, 51+).
- **Data de / Data até** — intervalo pela data de indexação.

Clique em **"Aplicar filtros"** para aplicar; os resultados da busca e a lista de documentos passam a respeitar esses critérios.

### O report não atualiza. O que fazer?

O report usa cache: se o índice não mudou, o HTML não é regerado ao arrancar o servidor. Ao adicionar ou remover documentos, reinicie `pdfsearchable serve` para que o report seja atualizado no próximo arranque.

### "Arquivo não encontrado" ao abrir um documento no report

O visualizador abre o PDF a partir da pasta **arquivos-processados** (cópia feita na indexação). Abra o report **a partir da pasta do projeto** (onde está `.pdfsearchable/` e `arquivos-processados/`), por exemplo abrindo `.pdfsearchable/report.html` pelo explorador de arquivos na raiz do projeto. Se os PDFs foram indexados antes da cópia automática existir, execute de novo `pdfsearchable add .` (ou `add` nos arquivos desejados) para que as cópias sejam criadas em `arquivos-processados/`.

---

## Classificação e IA

### Como o tipo do documento é definido?

Por **heurísticas** (palavras-chave e metadados do PDF) ou por **IA** (OpenAI ou Ollama). Modo definido por `PDFSEARCHABLE_AI`: `auto`, `heuristics`, `openai` ou `ollama`.

### Como uso o Ollama para classificação?

1. Instale e inicie o Ollama (`ollama serve`, modelo ex.: `ollama pull llama3.2`).
2. Defina `PDFSEARCHABLE_AI=ollama`.
3. Ao indexar, o tipo será classificado pelo modelo local; em modo ollama também é gerado um **resumo** e extraídos **valores** e **partes** quando disponíveis.

Variáveis opcionais: `PDFSEARCHABLE_OLLAMA_URL` (padrão `http://localhost:11434`), `PDFSEARCHABLE_OLLAMA_MODEL` (padrão `llama3.2`).

### Os metadados do PDF influenciam a classificação?

Sim (ID4). Título, assunto (subject) e palavras-chave (keywords) do PDF são usados como hint na classificação, tanto nas heurísticas quanto no prompt da IA.

---

## OCR e idioma

### Quando o OCR é usado?

Por padrão o Tesseract é executado em **todas as páginas** de cada PDF para garantir que todo o texto seja capturado. OCR não pode ser desativado. Para usar OCR apenas em páginas com pouco texto nativo (&lt; 50 caracteres), defina `PDFSEARCHABLE_OCR_ALWAYS=0`. O pipeline inclui detecção de orientação (OSD), remoção de bordas, binarização Otsu, deskew e retry por confiança; variáveis em [10-config.md](10-config.md). Idiomas: `PDFSEARCHABLE_OCR_LANG` (padrão: `por+eng+spa+fra+ita+rus+deu+heb`). DPI: `PDFSEARCHABLE_OCR_DPI` (padrão 300).

### O que significa "X% texto OCR" no report?

Indica a percentagem de páginas do documento em que o texto veio de OCR (reconhecimento óptico) em vez do texto nativo do PDF. Ajuda a saber a origem do conteúdo.

### Cursiva e manuscrito

O projeto oferece três backends HTR dedicados (além da opção de usar apenas Tesseract), seleccionados por `PDFSEARCHABLE_HTR_BACKEND`:

**1. TrOCR local (`trocr`, padrão)**

- Requer o extra `[htr]`: `pip install pdfsearchable[htr]` (instala `transformers` e `torch`, ~330 MB).
- Funciona offline; segmenta a imagem em linhas e aplica TrOCR por linha.
- Modelo padrão: `microsoft/trocr-base-handwritten`. Configurável via `PDFSEARCHABLE_HTR_MODEL`.
- **7 modelos dedicados** por idioma (en, de, fr, ru/cirílico, sv, ar, th) + fallback latino.
- **Modo histórico** (`PDFSEARCHABLE_OCR_HISTORICAL=on/auto`): seleciona automaticamente modelos especializados — **TRIDIS v2** para manuscritos medievais (pt/es/fr/it/de/la, séc. XI-XVI), **TrOCR-large** para texto difícil, **Kansallisarkisto** para finlandês, **Riksarkivet** para sueco. Pipeline de pré-processamento optimizado com CLAHE, Sauvola e limpeza morfológica.
- Adequado para manuscrito moderno; com modo histórico, melhora significativamente documentos antigos/degradados.

**2. Transkribus Cloud (`transkribus`) — recomendado para acervos históricos**

- Sem dependências extras — usa apenas stdlib Python.
- Excelente para manuscritos do séc. XIV–XX em várias línguas.
- Modelos públicos: `39995` (Portuguese Handwriting), `48152` (Generic Handwriting multilíngue).
- Requer conta em [app.transkribus.eu](https://app.transkribus.eu) e configurar:
  `PDFSEARCHABLE_TRANSKRIBUS_USER`, `PDFSEARCHABLE_TRANSKRIBUS_PW`, `PDFSEARCHABLE_TRANSKRIBUS_MODEL_ID`.

**3. eScriptorium (`escriptorium`) — instância Kraken própria**

- Sem dependências extras — usa apenas stdlib Python.
- Total controlo dos modelos e dados; adequado para instituições com instância própria.
- Requer: `PDFSEARCHABLE_ESCRIPTORIUM_URL`, `PDFSEARCHABLE_ESCRIPTORIUM_TOKEN`, `PDFSEARCHABLE_ESCRIPTORIUM_MODEL`.

**4. Apenas Tesseract (HTR desativado)**

- O Tesseract foi feito para **texto impresso** e tem suporte limitado a cursiva.
- Para desativar HTR: `PDFSEARCHABLE_HTR=0`.
- Para tentar melhorar: `PDFSEARCHABLE_OCR_PSM=6` (bloco) ou `7` (linha), e `PDFSEARCHABLE_OCR_DPI=400` ou `600`.
- Confira se `PDFSEARCHABLE_OCR_LANG` inclui o idioma do texto.

---

## Log e auditoria

### Onde ficam os logs e a auditoria?

- **Auditoria:** `.pdfsearchable/audit.jsonl` — uma linha JSON por evento (indexação, add, remove, search, erros).
- **Log:** `.pdfsearchable/pdfsearchable.log` — mensagens de execução (indexer, exceções). O arquivo usa rotação por tamanho (padrão 2 MB, 3 backups).

Use `pdfsearchable logs` para ver as últimas entradas da auditoria.

### Como ativar log no console ou mudar o nível?

- **PDFSEARCHABLE_LOG_CONSOLE=1** — envia o log também para o console.
- **PDFSEARCHABLE_LOG_LEVEL=DEBUG** — nível do log (DEBUG, INFO, WARNING, ERROR). Padrão: INFO.

### A auditoria pode encher o disco?

A escrita em `audit.jsonl` é segura: se falhar (disco cheio, permissão), o app não quebra e o evento vai para o stderr. Opcionalmente, defina **PDFSEARCHABLE_AUDIT_MAX_BYTES** (ex.: `5242880` para 5 MB): quando o arquivo ultrapassar esse tamanho, são mantidas apenas as últimas **PDFSEARCHABLE_AUDIT_MAX_LINES** linhas (padrão 50000).

---

## Erros e remoção

### "PDF protegido por senha"

Use `--password SENHA` ou a variável de ambiente `PDF_PASSWORD`.

### Como removo um documento do índice?

No report, use o botão **"Copiar comando para remover"** e execute o comando no terminal. Ou: `pdfsearchable remove <id>` (confirme quando solicitado; use `--yes` em scripts).

### Onde estão os arquivos processados?

PDFs e texto extraído ficam em `arquivos-processados/` na raiz do projeto. O índice e o cache ficam em `.pdfsearchable/`.

---

## Configuração por arquivo

Você pode usar um arquivo em vez de (ou além de) variáveis de ambiente:

- **`.pdfsearchable/config.toml`** ou **`.pdfsearchable/config.json`** — define padrões (ex.: `ai = "ollama"`, `ocr_lang = "por+eng"`). A env sempre prevalece sobre o arquivo.
- **Sinônimos de busca:** no config, chave `search_synonyms` (objeto termo → equivalente; pode ser vários sinônimos separados por vírgula). Ex.: `nfe` → `nota fiscal`. Opcionalmente, com `PDFSEARCHABLE_SYNONYMS_API_ENABLED=1`, o report enriquece sinônimos via API (PT-BR ou EN-US) para as top palavras.
- Detalhes: [10-config.md](10-config.md).

---

## Referência rápida de variáveis de ambiente

| Variável | Uso |
|----------|-----|
| `PDFSEARCHABLE_AI` | Modo de classificação: `auto`, `heuristics`, `openai`, `ollama` |
| `OPENAI_API_KEY` | Chave para classificação/resumo com OpenAI |
| `PDFSEARCHABLE_OPENAI_MODEL` | Modelo OpenAI (padrão: `gpt-4o-mini`) |
| `PDFSEARCHABLE_OLLAMA_URL` | URL do Ollama (padrão: `http://localhost:11434`) |
| `PDFSEARCHABLE_OLLAMA_MODEL` | Modelo Ollama (padrão: `llama3.2`) |
| `PDFSEARCHABLE_OCR_LANG` | Idiomas do Tesseract (padrão: `por+eng+spa+fra+ita+rus+deu+heb`) |
| `PDFSEARCHABLE_OCR_DPI` | DPI para OCR (72–600, padrão 300). Valores maiores melhoram texto pequeno e docs históricos. |
| `PDFSEARCHABLE_OCR_PREPROCESS` | `1` (padrão) = grayscale + contraste + nitidez antes do OCR; melhora scans. |
| `PDFSEARCHABLE_OCR_HISTORICAL` | `on`/`auto`/`off` — pipeline para docs históricos (CLAHE + Sauvola + limpeza morfológica + modelos HTR especializados). |
| `PDFSEARCHABLE_OCR_OEM` | Motor Tesseract (0–3; padrão 3). 1 = só LSTM (geralmente mais preciso). |
| `PDFSEARCHABLE_WORDCLOUD_STOP` | Palavras extras a excluir da nuvem (vírgulas) |
| `PDFSEARCHABLE_SEARCH_SYNONYMS` | Sinônimos de busca (JSON: termo → equivalente ou vários separados por vírgula) |
| `PDFSEARCHABLE_SYNONYMS_API_ENABLED` | `1`/`true`/`yes` para enriquecer sinônimos com API (PT-BR ou EN-US) ao gerar o report |
| `PDFSEARCHABLE_SYNONYMS_LANG` | Idioma da API de sinônimos: `pt-BR` ou `en-US` |
| `PDF_PASSWORD` | Senha padrão para PDFs protegidos |
| `PDFSEARCHABLE_LOG_LEVEL` | Nível do log: DEBUG, INFO, WARNING, ERROR |
| `PDFSEARCHABLE_LOG_CONSOLE` | 1/true/yes para enviar log ao console |
| `PDFSEARCHABLE_AUDIT_MAX_BYTES` | Rotação do audit: tamanho em bytes (0 = desativado) |
| `PDFSEARCHABLE_AUDIT_MAX_LINES` | Linhas a manter após rotação do audit |

Mais detalhes: [03-CLI.md](03-CLI.md), [05-logs-e-auditoria.md](05-logs-e-auditoria.md), [09-IA.md](09-IA.md), [04-report.md](04-report.md).
