# Exemplo de Utilização — pdfsearchable

Guia prático para usar o pdfsearchable do zero ao report. Todos os exemplos funcionam em **Windows, Linux e macOS**.

---

## 1. Verificar o ambiente

Antes de tudo, verifique se todas as dependências estão instaladas:

```bash
python setup.py --check     # Apenas verifica
python setup.py             # Verifica e instala o que faltar
```

O script verifica: Python >= 3.10, Tesseract (+ idiomas OCR), Ollama (opcional), dependências Python (core e opcionais) e espaço em disco.

---

## 2. Instalar o projeto

```bash
# Instalação básica (OCR, busca, report)
pip install -e .

# Com suporte a manuscritos (HTR multilíngue — 40+ idiomas)
pip install -e ".[htr]"

# Com classificação por IA (OpenAI)
pip install -e ".[ai]"

# Tudo junto
pip install -e ".[htr,ai]"
```

---

## 3. Inicializar e adicionar PDFs

```bash
# Criar a estrutura do projeto (opcional — o add cria automaticamente)
pdfsearchable init

# Adicionar um único PDF
pdfsearchable add contrato.pdf

# Adicionar uma pasta inteira (recursivo por padrão)
pdfsearchable add ~/Documentos/PDFs/

# Adicionar vários caminhos de uma vez
pdfsearchable add contrato.pdf ~/Documentos/PDFs/ relatorio.pdf
```

O `add` é o comando principal. Por padrão:
- Busca recursivamente em subpastas (`--recursive`)
- Ignora erros em arquivos individuais (`--skip-failed`)
- OCR em todas as páginas com DPI 300
- Detecta idioma automaticamente
- Classifica o tipo do documento (contrato, nota_fiscal, relatório, etc.)

### Opções comuns

```bash
# PDF protegido por senha
pdfsearchable add documento_protegido.pdf --password "minha_senha"

# Processamento paralelo (4 workers)
pdfsearchable add pasta/ --workers 4

# Lotes grandes (controlar uso de RAM)
pdfsearchable add pasta/ --batch-size 20

# Reprocessar documentos já indexados
pdfsearchable add pasta/ --reprocess

# Apenas um nível de pasta (sem recursão)
pdfsearchable add pasta/ --no-recursive

# Retomar após interrupção com Ctrl+C
pdfsearchable add pasta/ --resume
```

---

## 4. Pesquisar documentos

```bash
# Busca simples
pdfsearchable search "contrato de locação"

# Busca por CPF ou e-mail (máscaras automáticas)
pdfsearchable search "123.456.789-00"
pdfsearchable search "email@exemplo.com"

# Filtrar por tipo de documento
pdfsearchable search "cláusula" --type contrato

# Filtrar por idioma
pdfsearchable search "agreement" --language en

# Filtrar por data de indexação
pdfsearchable search "nota fiscal" --date-from 2024-01-01 --date-to 2024-12-31

# Busca expandida com Ollama (sinônimos e termos relacionados)
pdfsearchable search "rescisão" --ollama

# Não abrir o report no final
pdfsearchable search "termo" --no-open
```

---

## 5. Visualizar o report

### Interface interativa (SPA — recomendado)

```bash
# Iniciar o servidor (abre o browser automaticamente)
pdfsearchable serve

# Porta customizada
pdfsearchable serve --port 9000

# Sem abrir o browser
pdfsearchable serve --no-open
```

A SPA oferece busca em tempo real, mapa de locais, nuvem de palavras, grafo de conhecimento, linha do tempo, anotações e perguntas com IA (Ollama).

### Snapshot offline (report estático)

```bash
# Gerar report HTML (abrível sem servidor)
pdfsearchable report
```

O ficheiro `.pdfsearchable/report.html` é portátil — pode ser aberto em qualquer browser.

---

## 6. Perguntar sobre um documento (RAG)

Requer Ollama em execução (`ollama serve`) e `PDFSEARCHABLE_AI=ollama`.

```bash
# Perguntar por ID
pdfsearchable ask 0a1b2c3d4e5f6789 "Quem são as partes do contrato?"

# Perguntar por nome (ou parte do nome)
pdfsearchable ask "contrato.pdf" "Qual o valor total?"
pdfsearchable ask contrato "Qual a data de vencimento?"
```

### Chat com a colecção inteira

```bash
# Chat livre com todos os documentos
pdfsearchable chat

# Chat focado num documento
pdfsearchable chat --doc contrato.pdf
```

---

## 7. Exportar dados

```bash
# JSONL (ideal para RAG e fine-tuning)
pdfsearchable export --format jsonl --output colecao.jsonl

# CSV (para Excel / pandas)
pdfsearchable export --format csv --output metadados.csv

# Markdown (para LlamaIndex / LangChain)
pdfsearchable export --format markdown --output ./docs_md/

# Obsidian (notas com YAML frontmatter)
pdfsearchable export --format obsidian --output-dir ~/vault/PDFs

# Apenas metadados (sem texto)
pdfsearchable export --format jsonl --output meta.jsonl --no-text
```

---

## 8. Comandos úteis do dia-a-dia

```bash
# Ver status do índice
pdfsearchable status

# Estatísticas resumidas
pdfsearchable stats

# Listar duplicatas
pdfsearchable duplicates

# Verificar integridade (PDFs e textos em disco)
pdfsearchable verify

# Ver últimos eventos (auditoria)
pdfsearchable logs
pdfsearchable logs -n 50

# Diagnóstico completo do ambiente
pdfsearchable doctor

# Metadados detalhados de um documento
pdfsearchable info contrato
pdfsearchable info 0a1b2c3d4e5f6789

# Remover um documento
pdfsearchable remove "contrato.pdf"
pdfsearchable remove --yes 0a1b2c3d4e5f6789  # Sem confirmação

# Backup do índice e dados
pdfsearchable backup
pdfsearchable backup --output ~/backups/meu-projeto.tar.gz
```

---

## 9. Monitorizar pasta (indexação automática)

```bash
# Monitorizar a pasta atual (verifica a cada 10s)
pdfsearchable watch

# Monitorizar ~/Downloads a cada 5 segundos
pdfsearchable watch ~/Downloads --interval 5

# Sem recursão
pdfsearchable watch /dados/pdfs --no-recursive
```

O `watch` detecta PDFs novos ou modificados e indexa automaticamente.

---

## 10. Configuração com IA (Ollama)

Para activar todas as funcionalidades de IA:

```bash
# 1. Instalar e iniciar o Ollama
ollama serve
ollama pull llama3.2

# 2. Definir a variável
export PDFSEARCHABLE_AI=ollama

# 3. Indexar (classificação, resumo, tags, entidades, 20 categorias)
pdfsearchable add pasta/

# 4. Gerar embeddings para busca semântica
pdfsearchable embed

# 5. Buscar com semântica
pdfsearchable search "conceito de rescisão" --semantic
```

### Configuração por arquivo (em vez de variáveis de ambiente)

Crie `.pdfsearchable/config.toml`:

```toml
[pdfsearchable]
ai = "ollama"
ollama_model = "llama3.2"
ocr_lang = "por+eng"
log_level = "INFO"

[pdfsearchable.search_synonyms]
nfe = "nota fiscal"
"nf-e" = "nota fiscal"
contrato = "acordo, pacto"
```

---

## 11. HTR multilíngue (manuscritos)

O pdfsearchable suporta reconhecimento de manuscritos em 40+ idiomas:

```bash
# Instalar dependências HTR
pip install -e ".[htr]"

# Indexar (detecção automática de idioma e script)
pdfsearchable add manuscritos/

# Forçar um idioma específico
PDFSEARCHABLE_HTR_LANG=de pdfsearchable add manuscritos_alemao/

# Usar modelo de texto impresso
PDFSEARCHABLE_HTR_PRINTED=1 pdfsearchable add impressos/
```

### Manuscritos históricos — Pipeline local (recomendado)

Para documentos envelhecidos e manuscritos (séc. XI–XX), use o modo histórico:

```bash
# Auto-detectar documentos históricos (CLAHE + Sauvola + TRIDIS/TrOCR-large)
PDFSEARCHABLE_OCR_HISTORICAL=auto pdfsearchable add acervo_historico/

# Forçar pipeline histórico em toda a colecção
PDFSEARCHABLE_OCR_HISTORICAL=on pdfsearchable add manuscritos/

# Ou no config.toml
# [pdfsearchable]
# ocr_historical = "auto"
```

**Modelos usados automaticamente no modo histórico:**
- **Português, espanhol, francês, italiano, alemão, latim** → TRIDIS v2 (medieval, séc. XI-XVI)
- **Inglês, holandês, polaco, etc.** → TrOCR-large (maior capacidade)
- **Finlandês** → Kansallisarkisto multi-century
- **Sueco** → Riksarkivet histórico (séc. XVII-XX)
- **Russo/cirílico** → modelo cirílico (eslavo eclesiástico + moderno)

### Manuscritos históricos — Transkribus Cloud (alternativa)

Para documentos do séc. XIV–XX com conta Transkribus:

```bash
export PDFSEARCHABLE_HTR_BACKEND=transkribus
export PDFSEARCHABLE_TRANSKRIBUS_USER=email@exemplo.com
export PDFSEARCHABLE_TRANSKRIBUS_PW=senha
export PDFSEARCHABLE_TRANSKRIBUS_MODEL_ID=39995  # Portuguese Handwriting

pdfsearchable add acervo_historico/
```

---

## 12. Integração com editores (MCP)

O pdfsearchable expõe um servidor MCP para Claude Desktop, Cursor e Zed:

```bash
pdfsearchable mcp
```

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

Ferramentas disponíveis: `list_documents`, `search_documents`, `get_document_text`, `ask_document`, `ask_all_documents`, `index_document`.

---

## 13. Fluxo completo (exemplo real)

```bash
# 1. Verificar ambiente
python setup.py --check

# 2. Ir para a pasta dos PDFs
cd ~/Documentos/processos/

# 3. Indexar tudo (com Ollama)
export PDFSEARCHABLE_AI=ollama
pdfsearchable add .

# 4. Ver status
pdfsearchable stats

# 5. Pesquisar
pdfsearchable search "cláusula de rescisão"

# 6. Abrir a interface
pdfsearchable serve

# 7. Exportar para análise
pdfsearchable export --format csv --output analise.csv

# 8. Monitorizar novos PDFs
pdfsearchable watch ~/Downloads --interval 30
```

---

## Referência rápida

| Quer... | Comando |
|---------|---------|
| Indexar PDFs | `pdfsearchable add pasta/` |
| Indexar docs históricos | `PDFSEARCHABLE_OCR_HISTORICAL=auto pdfsearchable add pasta/` |
| Pesquisar | `pdfsearchable search "termo"` |
| Interface web | `pdfsearchable serve` |
| Report offline | `pdfsearchable report` |
| Perguntar (IA) | `pdfsearchable ask doc "pergunta"` |
| Chat | `pdfsearchable chat` |
| Exportar | `pdfsearchable export --format jsonl` |
| Status | `pdfsearchable status` |
| Diagnóstico | `pdfsearchable doctor` |
| Monitorizar | `pdfsearchable watch pasta/` |
| Backup | `pdfsearchable backup` |
| Verificar deps | `python setup.py --check` |

---

## Ver também

- [03-CLI.md](03-CLI.md) — Documentação completa de todos os comandos e flags
- [10-config.md](10-config.md) — Variáveis de ambiente e arquivo de configuração
- [09-IA.md](09-IA.md) — IA (Ollama, OpenAI, heurísticas)
- [11-HuggingFace.md](11-HuggingFace.md) — HTR multilíngue e modelos Hugging Face
- [FAQ.md](FAQ.md) — Perguntas frequentes
