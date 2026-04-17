# 11. HTR e modelos Hugging Face (opcionais)

O projeto suporta três backends de **HTR (Handwritten Text Recognition)** para manuscritos históricos, além de modelos Hugging Face opcionais para melhorar o pipeline. Abaixo estão detalhes de cada opção.

---

## Visão geral — HTR backends

| Backend | Quando usar | Configuração mínima |
|---------|-------------|---------------------|
| **`trocr`** (padrão) | Manuscrito moderno/semi-cursivo; execução 100% local | `pip install pdfsearchable[htr]` |
| **`transkribus`** | Manuscritos históricos (séc. XIV–XX), acervos luso-brasileiros | Conta + `PDFSEARCHABLE_TRANSKRIBUS_MODEL_ID` |
| **`escriptorium`** | Instância própria Kraken; total controlo dos modelos | Instância + token + `PDFSEARCHABLE_ESCRIPTORIUM_MODEL` |

Seleccione o backend com `PDFSEARCHABLE_HTR_BACKEND=<nome>`. Desactive HTR completamente com `PDFSEARCHABLE_HTR=0`.

## Visão geral — modelos Hugging Face

| Uso | Modelo sugerido | O que melhora | Onde integrar |
|-----|-----------------|---------------|----------------|
| **Detecção de idioma** | `papluca/xlm-roberta-base-language-detection` | ~99,6% acurácia, 20 idiomas; não precisa de Ollama | `language.py` (fallback após heurística/langdetect) |
| **NER (entidades)** | `lfcc/bert-portuguese-ner` ou `pierreguillou/ner-bert-large-cased-pt-lenerbr` | Pessoas, lugares, organizações, datas no texto em PT-BR | `content_extractors.py` (complementar regex/Ollama) |
| **Classificação de tipo** | `MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7` | Zero-shot: contrato, nota_fiscal, etc. sem API | `ai_classifier.py` (novo modo `hf` ou fallback) |
| **Resumo (PT-BR)** | `stjiris/t5-portuguese-legal-summarization` ou `unicamp-dl/ptt5-small-portuguese-vocab` | Resumo local, útil para documentos jurídicos ou gerais | `content_extractors.py` (alternativa ao Ollama para summary) |
| **HTR local** | `microsoft/trocr-base-handwritten` | Manuscrito/cursivo nas páginas | `htr.py` (backend `trocr`; variável `PDFSEARCHABLE_HTR_MODEL`) |

---

## 1. Detecção de idioma (integração opcional)

- **Modelo:** [papluca/xlm-roberta-base-language-detection](https://huggingface.co/papluca/xlm-roberta-base-language-detection)  
  Detecta 20 idiomas (incluindo português, inglês, espanhol, francês, etc.) com alta acurácia.

- **Ativação:** `PDFSEARCHABLE_HF_LANG=1` (ou `true`/`yes`). Requer `transformers` e `torch` (instalar via `pip install pdfsearchable[htr]`).

- **Fluxo:** Em `detect_language()`, após falha do langdetect e da heurística, se HF estiver ativo, usa o pipeline de classificação de texto para obter o idioma; evita chamar Ollama quando não for necessário.

- **Vantagem:** Rápido após o primeiro carregamento (modelo em disco), sem rede nem servidor local.

---

## 2. NER (Named Entity Recognition) em português

- **Modelos:**
  - [lfcc/bert-portuguese-ner](https://huggingface.co/lfcc/bert-portuguese-ner): PER, ORG, LOC, DATE, PROFESSION; F1 ~0,93; base BERTimbau.
  - [pierreguillou/ner-bert-large-cased-pt-lenerbr](https://huggingface.co/pierreguillou/ner-bert-large-cased-pt-lenerbr): domínio jurídico (LeNER-Br), PT-BR.

- **Integração sugerida:** Módulo `ner_hf.py` que, quando `PDFSEARCHABLE_HF_NER=1`, processa o texto com um pipeline `token-classification` e retorna entidades. O indexador pode **mesclar** essas entidades com as do regex e do Ollama (ex.: adicionar nomes de pessoas e organizações às partes, locais a uma lista de referências).

- **Exemplo de uso:**  
  `entities_ner = extract_entities_ner_hf(full_text)` → `{"PER": [...], "ORG": [...], "LOC": [...]}` e combinar com `parties` / `locations` no índice.

---

## 3. Classificação zero-shot (tipo de documento)

- **Modelo:** [MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7](https://huggingface.co/MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7) (NLI multilingue, 100+ idiomas).

- **Uso:** Pipeline `zero-shot-classification` com as mesmas etiquetas que o projeto já usa (`KNOWN_TYPES`: contrato, nota_fiscal, petição, ata, etc.). Assim é possível classificar o tipo do documento **sem** Ollama nem OpenAI.

- **Integração sugerida:** Em `ai_classifier.py`, novo modo `PDFSEARCHABLE_AI=hf` (ou fallback quando Ollama/OpenAI falham) que chama o modelo com `candidate_labels=list(KNOWN_TYPES)` e usa o label com maior score, normalizado para `KNOWN_TYPES`.

---

## 4. Resumo com T5 (português)

- **Modelos:**
  - [stjiris/t5-portuguese-legal-summarization](https://huggingface.co/stjiris/t5-portuguese-legal-summarization): focado em documentos jurídicos em PT-BR.
  - [unicamp-dl/ptt5-small-portuguese-vocab](https://huggingface.co/unicamp-dl/ptt5-small-portuguese-vocab): T5 pequeno com vocabulário em português (resumo geral).

- **Integração sugerida:** Em `content_extractors.py`, função opcional `extract_summary_t5_pt(text, max_length=150)` ativada por `PDFSEARCHABLE_HF_SUMMARY=1`. Se ativa e o modelo estiver disponível, o indexador pode usá-la como **fallback** quando Ollama não estiver disponível ou como alternativa leve para gerar `summary`.

---

## 5. HTR backend: TrOCR local (multilíngue)

- **Modelo padrão:** `microsoft/trocr-base-handwritten` (configurável via `PDFSEARCHABLE_HTR_MODEL`)
- **Modelo large:** `microsoft/trocr-large-handwritten` (usado automaticamente no modo histórico para maior capacidade)
- **Instalação:** `pip install pdfsearchable[htr]` — instala `transformers>=4.40` e `torch>=2.0`
- **Activação:** `PDFSEARCHABLE_HTR_BACKEND=trocr` (padrão quando HTR ativo)
- **Pipeline:** detecção de script via Tesseract OSD → selecção de modelo por idioma → segmentação em linhas → TrOCR por linha → texto reunido
- **Suporte multilíngue (40+ idiomas):** 7 modelos dedicados com cache LRU thread-safe:

| Idioma(s) | Modelo | Descrição |
|-----------|--------|-----------|
| **en** | `microsoft/trocr-base-handwritten` | Inglês (modelo padrão, IAM dataset) |
| **de** | `fhswf/TrOCR_german_handwritten` | Alemão manuscrito |
| **fr** | `agomberto/trocr-large-handwritten-fr` | Francês manuscrito |
| **ru, uk, bg, sr, be, mk** | `cyrillic-trocr/trocr-handwritten-cyrillic` | Cirílico (russo, ucraniano, etc.) |
| **sv** | `Riksarkivet/trocr-base-handwritten-hist-swe-2` | Sueco histórico |
| **ar** | `RayR1/trocr-base-arabic-handwritten` | Árabe manuscrito |
| **th** | `openthaigpt/thai-trocr` | Tailandês manuscrito |
| **pt, es, it, nl, pl, +25** | (fallback → en) | Script latino — usa modelo inglês como generalista |

- **Detecção automática:** o pipeline detecta o script da imagem via Tesseract OSD e selecciona o modelo adequado. Alternativamente, o idioma pode ser detectado a partir do texto nativo do PDF e passado como hint ao HTR.
- **Override manual:** `PDFSEARCHABLE_HTR_LANG=de` força um idioma; `PDFSEARCHABLE_HTR_PRINTED=1` usa modelo de texto impresso.
- **Cache LRU:** até `PDFSEARCHABLE_HTR_MAX_MODELS` modelos (padrão 3) mantidos em memória; evicção do modelo menos usado quando excede o limite.

### 5.1 Modo histórico (PDFSEARCHABLE_OCR_HISTORICAL)

Quando `PDFSEARCHABLE_OCR_HISTORICAL=on` ou `auto`, o sistema seleciona modelos especializados para documentos antigos e aplica um pipeline de pré-processamento optimizado:

**Modelos históricos por idioma:**

| Idioma(s) | Modelo | Período/Descrição |
|-----------|--------|-------------------|
| **pt, es, fr, it, de, ca, gl, ro, la** | `magistermilitum/tridis_v2_HTR_historical_manuscripts` | TRIDIS v2 — medieval/early-modern multilíngue (séc. XI-XVI), treinado em manuscritos ibéricos, franceses e germânicos; CER ~6-12% |
| **en, nl, pl, hr, cs, sk, hu, ...** | `microsoft/trocr-large-handwritten` | TrOCR-large — maior capacidade para texto difícil/degradado |
| **fi** | `Kansallisarkisto/multicentury-htr-model` | Arquivo Nacional finlandês — multi-century (913k+ amostras) |
| **sv** | `Riksarkivet/trocr-base-handwritten-hist-swe-2` | Arquivo Nacional sueco (séc. XVII-XX) |
| **ru, uk, bg, sr** | `cyrillic-trocr/trocr-handwritten-cyrillic` | Cirílico histórico (eslavo eclesiástico + moderno) |
| **ar** | `RayR1/trocr-base-arabic-handwritten` | Árabe histórico |

**Pipeline de pré-processamento histórico:**
1. **CLAHE** — Contrast Limited Adaptive Histogram Equalization (tiles 8×8, clip 2.5): melhora texto desbotado e iluminação irregular
2. **Sauvola** — Binarização local adaptativa (janela 31px, k=0.2): superior a Otsu para papel envelhecido, manchas e bleed-through
3. **Limpeza morfológica** — Opening + closing (kernel 2px): remove pontos de ruído, manchas de tinta e artefactos de bleed-through
4. **Deskew** — Correção de inclinação com precisão refinada

**Segmentação de linhas adaptativa:**
- Limiar de detecção mais baixo (3% vs 5%) para texto desbotado
- Merge de linhas próximas (gap < 5px) para palavras fragmentadas em baselines irregulares
- Margem maior (4px vs 2px) para preservar ascendentes/descendentes

**Detecção automática de documento histórico** (`auto`):
Heurística baseada em 3 indicadores (2+ = histórico):
1. **Papel amarelado** — R > 160, G > 140, B significativamente menor (cantos da imagem)
2. **Variância de contraste** — Blocos 8×8 com std > 40 (scans irregulares)
3. **Nível de ruído** — > 25% de pixels na zona cinzenta (manchas, desgaste)

**Uso:**
```bash
# Forçar pipeline histórico
PDFSEARCHABLE_OCR_HISTORICAL=on pdfsearchable add /acervo/manuscritos/

# Auto-detectar documentos históricos
PDFSEARCHABLE_OCR_HISTORICAL=auto pdfsearchable add /acervo/misto/

# Ou no config.toml
[pdfsearchable]
ocr_historical = "auto"
```

---

## 6. HTR backend: Transkribus Cloud API

- **Ideal para:** manuscritos históricos (séc. XIV–XX), incluindo português antigo, latim e hebraico
- **Activação:** `PDFSEARCHABLE_HTR_BACKEND=transkribus`
- **Modelos públicos recomendados para acervos luso-brasileiros:**
  - `39995` — *Portuguese Handwriting* (séc. XIX–XX)
  - `48152` — *Generic Handwriting* (multilíngue, séc. XIX–XX)
  - `13442` — *Handwritten Text Recognition* (inglês)
- **Workflow:** login → colecção → upload imagem → job HTR → polling → PAGE XML → texto
- **Autenticação:** cookie `JSESSIONID` (gerido automaticamente por sessão)
- **Sem dependências extras:** usa apenas stdlib Python (`urllib.request`, `http.cookiejar`, `xml.etree.ElementTree`)
- **Configuração mínima:**
  ```
  PDFSEARCHABLE_HTR_BACKEND=transkribus
  PDFSEARCHABLE_TRANSKRIBUS_USER=email@exemplo.com
  PDFSEARCHABLE_TRANSKRIBUS_PW=senha
  PDFSEARCHABLE_TRANSKRIBUS_MODEL_ID=39995
  ```

---

## 7. HTR backend: eScriptorium REST API

- **Ideal para:** instâncias próprias com modelos Kraken personalizados; total controlo dos dados
- **Activação:** `PDFSEARCHABLE_HTR_BACKEND=escriptorium`
- **Workflow:** projecto → documento → upload parte → aguardar conversão → transcrição → polling → linhas → texto
- **Autenticação:** `Token <api_key>` no cabeçalho `Authorization`
- **Sem dependências extras:** usa apenas stdlib Python
- **Modelos Kraken:** pesquisar em [zenodo.org/communities/ocr_models](https://zenodo.org/communities/ocr_models); importar na instância via Interface → Modelos → Importar URL
- **Configuração mínima:**
  ```
  PDFSEARCHABLE_HTR_BACKEND=escriptorium
  PDFSEARCHABLE_ESCRIPTORIUM_URL=https://escriptorium.example.org
  PDFSEARCHABLE_ESCRIPTORIUM_TOKEN=<api_key>
  PDFSEARCHABLE_ESCRIPTORIUM_MODEL=<pk_ou_nome>
  ```

---

## 8. Dependências e extras

- **HTR local (TrOCR):** `pip install pdfsearchable[htr]` instala `transformers>=4.40` e `torch>=2.0`. **Não** incluídos nas dependências principais.
- **Transkribus / eScriptorium:** stdlib apenas — sem instalação adicional.
- **NER/classificação/resumo HF:** usam `transformers` + `torch` (instalar via `[htr]` ou separadamente).

- **Variáveis de ambiente — HTR:**

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `PDFSEARCHABLE_HTR` | `0`/`false`/`no` desativa HTR completamente. | ativo |
| `PDFSEARCHABLE_HTR_BACKEND` | `trocr` \| `transkribus` \| `escriptorium` | `trocr` |
| `PDFSEARCHABLE_HTR_MODEL` | Modelo TrOCR (backend `trocr`). | `microsoft/trocr-base-handwritten` |
| `PDFSEARCHABLE_HTR_TIMEOUT` | Timeout polling backends cloud (s). | `120` |
| `PDFSEARCHABLE_HTR_LANG` | Forçar idioma HTR (ex.: `de`, `fr`, `ru`). Auto se omitido. | — |
| `PDFSEARCHABLE_HTR_PRINTED` | `1` para texto impresso (modelo `trocr-base-printed`). | `0` |
| `PDFSEARCHABLE_HTR_MAX_MODELS` | Máximo de modelos em cache LRU (1–10). | `3` |

- **Variáveis de ambiente — outros modelos HF:**

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `PDFSEARCHABLE_HF_LANG` | Usar modelo HF para detecção de idioma (fallback). | `0` |
| `PDFSEARCHABLE_HF_LANG_MODEL` | Modelo de detecção de idioma. | `papluca/xlm-roberta-base-language-detection` |
| `PDFSEARCHABLE_HF_NER` | Usar NER Hugging Face para entidades (PER, ORG, LOC). | `0` |
| `PDFSEARCHABLE_HF_NER_MODEL` | Modelo NER (ex.: `lfcc/bert-portuguese-ner`). | `lfcc/bert-portuguese-ner` |
| `PDFSEARCHABLE_AI` | Pode ganhar valor `hf` para classificação zero-shot. | (existente) |
| `PDFSEARCHABLE_HF_SUMMARY` | Resumo via T5 PT-BR quando Ollama indisponível. | `0` |

---

## 9. Estado da implementação

| Recurso | Módulo | Activação |
|---------|--------|-----------|
| HTR TrOCR local | `htr.py` + `[htr]` extra | `PDFSEARCHABLE_HTR_BACKEND=trocr` |
| HTR Transkribus | `htr_transkribus.py` | `PDFSEARCHABLE_HTR_BACKEND=transkribus` + credenciais |
| HTR eScriptorium | `htr_escriptorium.py` | `PDFSEARCHABLE_HTR_BACKEND=escriptorium` + credenciais |
| Detecção de idioma | `language.py` | `PDFSEARCHABLE_HF_LANG=1` |
| NER | `ner_hf.py` | `PDFSEARCHABLE_HF_NER=1` |
| Classificação zero-shot | `ai_classifier.py` | `PDFSEARCHABLE_AI=hf` |
| Resumo T5 | `content_extractors.py` | `PDFSEARCHABLE_HF_SUMMARY=1` |

Cada recurso usa carregamento lazy do modelo e não impacta quem não activa a variável correspondente.
