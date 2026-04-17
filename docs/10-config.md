# 10. Arquivo de configuração

O pdfsearchable pode usar um **arquivo de configuração** opcional para definir padrões sem depender só de variáveis de ambiente.

---

## Localização e formato

- **Arquivo:** `.pdfsearchable/config.toml` ou `.pdfsearchable/config.json` (na pasta do projeto).
- **Precedência:** TOML é lido primeiro se existir; senão, JSON. As **variáveis de ambiente** têm prioridade sobre o arquivo (o arquivo define defaults).

---

## config.toml (recomendado)

Requer Python 3.11+ (stdlib `tomllib`) ou o pacote opcional `tomli` em Python 3.10.

```toml
[pdfsearchable]
ai = "ollama"
ocr_lang = "por+eng"
log_level = "INFO"

[pdfsearchable.search_synonyms]
nfe = "nota fiscal"
"nf-e" = "nota fiscal"
contrato = "acordo"
```

Chaves na seção `[pdfsearchable]` são convertidas para UPPER com prefixo `PDFSEARCHABLE_` (ex.: `ai` → `PDFSEARCHABLE_AI`). A seção `search_synonyms` vira o dicionário de sinônimos da busca no report.

---

## config.json

```json
{
  "pdfsearchable": {
    "ai": "ollama",
    "ocr_lang": "por+eng",
    "log_level": "INFO",
    "search_synonyms": {
      "nfe": "nota fiscal",
      "nf-e": "nota fiscal"
    }
  }
}
```

Ou chaves em UPPER no nível raiz:

```json
{
  "PDFSEARCHABLE_AI": "ollama",
  "PDFSEARCHABLE_OCR_LANG": "por+eng+spa+fra+ita+rus+deu+heb"
}
```

---

## Quando o config é aplicado

O config é carregado e aplicado ao `os.environ` no **início da CLI** (comando `main`). Assim, qualquer comando (`add`, `report`, `serve`, `search`, `ask`, `export`, etc.) usa os valores do arquivo quando a variável de ambiente correspondente não estiver definida.

---

## Sinônimos de busca

A chave **search_synonyms** (ou `PDFSEARCHABLE_SEARCH_SYNONYMS` no env em JSON) define pares **termo → equivalente**. Na busca do report, o usuário pode marcar **"Buscar por sinônimos"**; cada termo digitado é então expandido: se existir sinônimo, a busca considera o termo e todos os sinônimos (equivalente a OR). Valores podem ser **vários sinônimos separados por vírgula** (ex.: `"cadeira": "assento, poltrona, cátedra"`).

- **Estático:** config ou env, como abaixo.
- **Enriquecimento via API (opcional):** com `PDFSEARCHABLE_SYNONYMS_API_ENABLED=1` (ou `true`/`yes`), ao gerar o report o sistema chama a API de sinônimos para até 12 das palavras mais frequentes e mescla ao mapa.  
  - **PT-BR:** API [api-dicionario-ptbr](https://github.com/atrikx/api-dicionario-ptbr) (URL em `PDFSEARCHABLE_API_DICIONARIO_PTBR`).  
  - **EN-US:** [API Ninjas Thesaurus](https://api-ninjas.com/api/thesaurus) (chave em `API_NINJAS_KEY` ou `PDFSEARCHABLE_API_NINJAS_KEY`).  
  - Idioma da API: `PDFSEARCHABLE_SYNONYMS_LANG=pt-BR` ou `en-US` (padrão `pt-BR`).

Exemplo env (JSON):  
`PDFSEARCHABLE_SEARCH_SYNONYMS='{"nfe":"nota fiscal","nf-e":"nota fiscal","cadeira":"assento, poltrona"}'`

---

## ViaCEP e IP-API (locais no report)

O report pode enriquecer o **mapa de referências a locais** com:

- **ViaCEP** — CEPs encontrados no texto dos documentos são consultados em [ViaCEP](https://viacep.com.br/); o endereço (localidade, UF) vira um local no mapa (com coordenadas quando a cidade estiver na base interna).
- **IP-API** — IPs identificados nos documentos (campo `identified_ips`) são consultados em [IP-API](https://ip-api.com/); a geolocalização (cidade, país, lat/lon) é exibida no mapa.

**Variáveis de ambiente (opcional):**

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `PDFSEARCHABLE_VIA_CEP` | Ativar (1/true/yes) ou desativar (0/false/no) consulta ViaCEP para CEPs no texto. | `1` (ativo) |
| `PDFSEARCHABLE_IP_API` | Ativar ou desativar consulta IP-API para IPs identificados. | `1` (ativo) |

**Limites:** por geração de report são consultados no máximo 25 CEPs e 15 IPs únicos; entre cada chamada à IP-API é aplicado um delay (~1,5 s) para respeitar o limite gratuito de **45 requisições/minuto**. A API gratuita da IP-API é apenas para **uso não comercial**; para uso comercial, consulte o serviço Pro em [ip-api.com](https://ip-api.com/).

**Geocoding (Nominatim):** por padrão, locais sem coordenadas são consultados na API [Nominatim](https://nominatim.openstreetmap.org/) (OpenStreetMap). Use `PDFSEARCHABLE_GEOCODE=0` (ou `false`/`no`) para desativar. `PDFSEARCHABLE_GEOCODE_MAX` limita novas consultas por geração (5–100; padrão 25). Cache em `.pdfsearchable/geocode_cache.json`. Respeite 1 requisição/segundo (uso justo).

---

## OCR (Tesseract)

Melhorias de precisão e comportamento do OCR (Tesseract + PyMuPDF):

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `PDFSEARCHABLE_OCR_LANG` | Idiomas do Tesseract separados por `+` (ex.: `por+eng+spa+fra+ita+rus+deu+heb`). | `por+eng+spa+fra+ita+rus+deu+heb` |
| `PDFSEARCHABLE_OCR_DPI` | DPI da renderização da página para OCR (72–600). Valores maiores melhoram texto pequeno e documentos históricos, com mais uso de RAM e tempo. | `300` |
| `PDFSEARCHABLE_OCR_PSM` | Modo de segmentação de página (PSM) do Tesseract (0–13). 3 = automático; 6 ou 7 podem ajudar em manuscrito. | `3` |
| `PDFSEARCHABLE_OCR_OEM` | Motor do Tesseract: 0 = legado, 1 = LSTM apenas, 2 = legado+LSTM, 3 = padrão. LSTM costuma ser mais preciso. | `3` |
| `PDFSEARCHABLE_OCR_OSD` | `1` = detectar e corrigir orientação da página automaticamente (Tesseract OSD) antes do OCR. | `1` |
| `PDFSEARCHABLE_OCR_BINARIZE` | `1` = binarização Otsu adaptativa antes do OCR (melhora scans com fundo não uniforme). | `1` |
| `PDFSEARCHABLE_OCR_DESKEW` | `1` = correção automática de inclinação de scan (±10° em passos de 0,5°) por projeção horizontal. | `1` |
| `PDFSEARCHABLE_OCR_BORDER_REMOVE` | `1` = remover bordas escuras de scanner antes do OCR. | `1` |
| `PDFSEARCHABLE_OCR_PREPROCESS` | `1` = grayscale, contraste e nitidez antes do Tesseract (ignorado se Otsu ativo). Melhora resultados em scans. | `1` |
| `PDFSEARCHABLE_OCR_CONFIDENCE_THRESHOLD` | Limiar de confiança (0–100). Abaixo disto, tenta PSMs alternativos automaticamente. | `40` |
| `PDFSEARCHABLE_OCR_RETRY_PSM` | PSMs alternativos a tentar quando confiança &lt; limiar (ex.: `6,4`, `11`). | `6,4` |
| `PDFSEARCHABLE_OCR_ALWAYS` | `1` = OCR em todas as páginas; `0` = OCR só em páginas com pouco texto nativo (&lt; 50 caracteres). | `1` |
| `PDFSEARCHABLE_LARGE_FILE_MB` | Limiar em MB acima do qual o `add` avisa sobre ficheiros grandes e activa compressão automática. | `20` |
| `PDFSEARCHABLE_OCR_CORRECT` | `1`/`true`/`yes` para corrigir erros de OCR com LLM (Ollama). Corrige caracteres trocados e palavras fragmentadas. Requer Ollama ativo. | `0` (desativado) |
| `PDFSEARCHABLE_OCR_HISTORICAL` | Pipeline para documentos históricos/envelhecidos. `on` = forçar (CLAHE + Sauvola + limpeza morfológica + modelos HTR maiores); `auto` = detectar automaticamente (papel amarelado, variância, ruído); `off` = pipeline padrão (Otsu). | `off` |
| `PDFSEARCHABLE_HTR` | Ativar HTR (manuscrito). Use `0`/`false`/`no` para desativar e usar só Tesseract. Por defeito ativo se o backend estiver disponível. | — (ativo se backend disponível) |
| `PDFSEARCHABLE_HTR_BACKEND` | Backend HTR: `trocr` (local, requer `[htr]`), `transkribus` (cloud), `escriptorium` (instância própria). | `trocr` |
| `PDFSEARCHABLE_HTR_MODEL` | Modelo Hugging Face para HTR (backend `trocr`; ex.: `microsoft/trocr-base-handwritten`). | `microsoft/trocr-base-handwritten` |
| `PDFSEARCHABLE_HTR_TIMEOUT` | Timeout de polling em segundos para backends cloud (Transkribus, eScriptorium). | `120` |
| `PDFSEARCHABLE_HTR_LANG` | Forçar idioma HTR (ex.: `de`, `fr`, `ru`, `ar`, `sv`). Sem esta variável, o idioma é detectado automaticamente via Tesseract OSD. | — (auto) |
| `PDFSEARCHABLE_HTR_PRINTED` | `1` para usar modelo de texto impresso multilíngue (`microsoft/trocr-base-printed`) em vez de manuscrito. | `0` |
| `PDFSEARCHABLE_HTR_MAX_MODELS` | Número máximo de modelos TrOCR em cache LRU simultâneo (1–10). | `3` |
| `PDFSEARCHABLE_OCR_HTR_FALLBACK_THRESHOLD` | Confiança mínima (0–100) do Tesseract abaixo da qual se usa **fallback HTR** na mesma página. Só aplica quando HTR está ativo. | `25` |

### HTR backend: Transkribus Cloud API

Ideal para manuscritos históricos (séc. XIV–XX). Requer conta em [app.transkribus.eu](https://app.transkribus.eu).
Active com `PDFSEARCHABLE_HTR_BACKEND=transkribus`.

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `PDFSEARCHABLE_TRANSKRIBUS_USER` | E-mail da conta Transkribus. | — (obrigatório) |
| `PDFSEARCHABLE_TRANSKRIBUS_PW` | Senha da conta. | — (obrigatório) |
| `PDFSEARCHABLE_TRANSKRIBUS_MODEL_ID` | ID inteiro do modelo HTR (ex.: `39995` = Portuguese Handwriting séc. XIX–XX; `48152` = Generic Handwriting multilíngue). | — (obrigatório) |
| `PDFSEARCHABLE_TRANSKRIBUS_COL_ID` | ID da colecção de trabalho. Se omitido, cria colecção temporária por sessão. | — (opcional) |
| `PDFSEARCHABLE_TRANSKRIBUS_BASE_URL` | URL base da API REST Transkribus. | `https://transkribus.eu/TrpServer/rest` |
| `PDFSEARCHABLE_TRANSKRIBUS_CLEANUP` | `0` para manter documentos temporários após transcrição. | `1` (remove) |

### HTR backend: eScriptorium REST API

Plataforma open-source baseada em Kraken para manuscritos. Requer instância própria ou acesso a instância partilhada.
Active com `PDFSEARCHABLE_HTR_BACKEND=escriptorium`.

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `PDFSEARCHABLE_ESCRIPTORIUM_URL` | URL base da instância (ex.: `https://escriptorium.example.org`). | — (obrigatório) |
| `PDFSEARCHABLE_ESCRIPTORIUM_TOKEN` | API token (Perfil → API Key na interface). | — (obrigatório) |
| `PDFSEARCHABLE_ESCRIPTORIUM_MODEL` | PK (inteiro) ou nome do modelo HTR Kraken a usar. | — (obrigatório) |
| `PDFSEARCHABLE_ESCRIPTORIUM_PROJECT` | PK do projecto de trabalho. Se omitido, cria projecto temporário por sessão. | — (opcional) |
| `PDFSEARCHABLE_ESCRIPTORIUM_CLEANUP` | `0` para manter documentos temporários após transcrição. | `1` (remove) |
| `PDFSEARCHABLE_OCR_WORKERS` | Número de workers para **OCR paralelo por página** (0 = auto: **3/4 dos CPUs**, mín. 2, máx. 8; 1 = sequencial). O documento PDF é sempre fechado antes do OCR para evitar *lock blocking* do MuPDF. | auto (3·cpu/4, 2–8) |

O texto retornado pelo OCR é normalizado (espaços múltiplos e quebras de linha excessivas colapsados).

---

## Nuvem de palavras (report)

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `PDFSEARCHABLE_WORDCLOUD_MIN_COUNT` | Mínimo de ocorrências para um termo entrar na nuvem (reduz ruído). | `1` |
| `PDFSEARCHABLE_WORDCLOUD_MAX_WORD_LEN` | Máximo de caracteres por palavra (0 = sem limite). Palavras maiores (ex.: URLs) são excluídas. | `0` |
| `PDFSEARCHABLE_WORDCLOUD_LANG` | Idioma das stopwords: `pt-BR`, `en`, `es`. | `pt-BR` |
| `PDFSEARCHABLE_WORDCLOUD_STOP` | Stopwords extras separadas por vírgula (ex.: `termo1,termo2`). | — |
| `PDFSEARCHABLE_WORDCLOUD_STEMMING` | `1`/`true`/`yes` para agrupar contagens por radical (ex.: "contrato" e "contratos" → mesmo termo). Requer dependência opcional `[wordcloud-stemming]` (NLTK). | `0` (desativado) |

A nuvem inclui **bigramas** (ex.: "nota fiscal") como termos únicos; até 75 palavras + 20 bigramas são enviados ao front.

---

## Performance e escala (report e FTS)

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `PDFSEARCHABLE_LIST_PAGE_SIZE` | Quantidade de documentos exibidos inicialmente na lista do report; "Carregar mais" traz os próximos N. (10–500.) | `50` |
| `PDFSEARCHABLE_SEARCH_SNIPPET_MAX_CHARS` | Limite de caracteres por documento/página no `search_data` embutido no HTML; reduz tamanho do report com muitos docs. (1000–500000.) | `10000` |
| `PDFSEARCHABLE_FTS_DEFERRED` | Se `1`/`true`/`yes`: durante o `add` não indexa FTS por arquivo; ao final do lote (ou comando `pdfsearchable index-fts`) o FTS é atualizado para todos os documentos. Útil em lotes muito grandes. | `0` (desativado) |
| `PDFSEARCHABLE_FTS_BACKGROUND` | Com `FTS_DEFERRED=1`, se `FTS_BACKGROUND=1` a indexação FTS ao final do add roda em thread e o CLI retorna imediatamente. | `0` (desativado) |
| `PDFSEARCHABLE_MAX_WORKERS` | Limite máximo de workers quando `--workers 0` (auto). Valores entre 1 e 64; padrão 16. Aumenta o throughput em máquinas com muitos núcleos. | `16` |
| `PDFSEARCHABLE_BACKUP_INDEX` | Se `1`/`true`/`yes`, faz cópia do índice (`index.json.bak`) antes de operações destrutivas (ex.: remove). | `1` |
| `PDFSEARCHABLE_CORS` | Se `1`/`true`/`yes`, o servidor envia cabeçalhos CORS (Access-Control-Allow-Origin). | — |
| `PDFSEARCHABLE_CORS_ORIGIN` | Valor de Allow-Origin quando CORS está ativo (ex.: `*` ou `https://app.example.com`). | `*` |
| `PDFSEARCHABLE_ASK_RATE_LIMIT` | Número máximo de requisições a `/api/ask` por minuto (0 = sem limite). | `30` |
| `PDFSEARCHABLE_ASK_TIMEOUT` | Timeout em segundos para chamadas ao Ollama no `/api/ask` (30–300). | `90` |
| `PDFSEARCHABLE_HTTP_LOG` | Se `1`/`true`/`yes`, o servidor regista pedidos HTTP no log. | — |
| `PDFSEARCHABLE_STATS_LARGE_DOC_PAGES` | Limiar de páginas para considerar um documento "grande" na estatística do report (ex.: quantos docs têm mais de N páginas). | `50` |

## Detecções opcionais (redactions, forensics, contratos)

Detecções adicionais executadas durante a indexação. Geram campos em `metadata` e podem ser inspeccionadas com `pdfsearchable inspect <file>` ou pelo servidor MCP (`get_redaction_report`, `get_forensics_summary`).

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `PDFSEARCHABLE_DETECT_REDACTIONS` | `1`/`true`/`yes` = detecta zonas potencialmente redactadas (rectângulos pretos / tarjas). Resultado em `metadata.redactions` (lista de `{page, bbox, area}`) e contagem em `metadata.redaction_zones`. | `0` |
| `PDFSEARCHABLE_FORENSICS` | `1`/`true`/`yes` = análise forense básica do PDF (revisões incrementais, metadata do produtor, anomalias de stream). Resultado em `metadata.forensics`. | `0` |
| `PDFSEARCHABLE_CONTRACTS` | `1`/`true`/`yes` = detecção heurística de cláusulas contratuais (partes, objecto, prazo, valor, foro). Resultado em `metadata.contract_summary`. | `0` |
| `PDFSEARCHABLE_CLASSIFIER_FEEDBACK` | `1`/`true`/`yes` = grava amostras rotuladas (label + texto truncado) em `.pdfsearchable/feedback.jsonl` para fine-tuning futuro. | `0` |
| `PDFSEARCHABLE_DETECT_FORMULAS` | `1`/`true`/`yes` = detecta fórmulas matemáticas no texto: LaTeX (`$…$`, `$$…$$`, `\[…\]`, ambientes `equation`/`align`/`gather`) e clusters de símbolos Unicode (∫ ∑ ∏ √ ∂ π θ α β …). Resultado em `metadata.formulas = {total, by_kind, hits}`; cada hit tem `{page, raw, kind, latex}`. Re-emitidas como `$$ … $$` na exportação Markdown. | `0` |

## Snapshots automáticos do índice

Antes de cada `save_index()`, uma cópia rotativa pode ser gravada em `.pdfsearchable/.snapshots/index_{timestamp}.json`.

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `PDFSEARCHABLE_AUTO_SNAPSHOT` | `1`/`true`/`yes`/`on` = activa snapshots automáticos do índice antes de cada escrita. | `0` |
| `PDFSEARCHABLE_SNAPSHOT_KEEP` | Número máximo de snapshots mantidos; os mais antigos são removidos. | `5` |

## Ollama (RAG, ask, correção OCR)

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `PDFSEARCHABLE_OLLAMA_URL` | URL da API Ollama. | `http://localhost:11434` |
| `PDFSEARCHABLE_OLLAMA_MODEL` | Modelo Ollama (ex.: llama3.2). | `llama3.2` |
| `PDFSEARCHABLE_OLLAMA_TIMEOUT` | Timeout em segundos para chamadas ao Ollama (30–300). | `90` |
| `PDFSEARCHABLE_OLLAMA_RETRY` | Número de tentativas em falha (0–5). | `2` |
| `PDFSEARCHABLE_OLLAMA_CACHE` | `1` = usar cache por hash do prompt nas chamadas Ollama (resumo, RAG, etc.). | `1` |
| `PDFSEARCHABLE_OLLAMA_KEEP_ALIVE` | Tempo que o Ollama mantém o modelo carregado em memória entre chamadas. Valores: duração (`5m`, `30m`, `1h`), `0` para descarregar imediatamente, `-1` para manter permanente. Evita relançar o modelo em cada página/classificação. | `5m` |
| `PDFSEARCHABLE_OLLAMA_CLASSIFY_MODEL` | Modelo Ollama alternativo usado apenas para **classificação** (labels curtos). Pode ser menor/rápido (ex.: `llama3.2:1b`), enquanto `PDFSEARCHABLE_OLLAMA_MODEL` permanece para RAG/ask. | — (usa `OLLAMA_MODEL`) |
| `PDFSEARCHABLE_SUMMARY_SHORT` | `1`/`true`/`yes` = gerar resumo em uma única frase (menos tokens no Ollama). | `0` |

## Hugging Face (opcional)

É possível usar modelos HF adicionais para deixar o pipeline mais robusto. Requerem `transformers` e `torch`: `pip install pdfsearchable[htr]`. Detalhes em [11-HuggingFace.md](11-HuggingFace.md).

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `PDFSEARCHABLE_HF_LANG` | `1`/`true`/`yes` = usar modelo HF para detecção de idioma (fallback após heurística/langdetect). Requer `[htr]`. | `0` |
| `PDFSEARCHABLE_HF_LANG_MODEL` | Modelo de detecção de idioma. | `papluca/xlm-roberta-base-language-detection` |
| `PDFSEARCHABLE_HF_NER` | `1`/`true`/`yes` = extrair entidades (PER, ORG, LOC) com NER em PT-BR; mescla PER/ORG em partes e grava LOC em `identified_locations`. | `0` |
| `PDFSEARCHABLE_HF_NER_MODEL` | Modelo NER (ex.: `lfcc/bert-portuguese-ner`). | `lfcc/bert-portuguese-ner` |
| `PDFSEARCHABLE_AI` | Modos: `auto`, `heuristics`, `openai`, `ollama`, **`hf`** (classificação zero-shot com Hugging Face, sem API). | `auto` |
| `PDFSEARCHABLE_HF_CLASSIFY_MODEL` | Modelo zero-shot para classificação de tipo de documento (quando `PDFSEARCHABLE_AI=hf`). | `MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7` |
| `PDFSEARCHABLE_HF_SUMMARY` | `1`/`true`/`yes` = gerar resumo com T5 em PT-BR quando Ollama não produz resumo (fallback). | `0` |
| `PDFSEARCHABLE_HF_SUMMARY_MODEL` | Modelo T5 para resumo (ex.: `stjiris/t5-portuguese-legal-summarization`). | `stjiris/t5-portuguese-legal-summarization` |
