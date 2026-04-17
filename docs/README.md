# Documentação — pdfsearchable

Índice da documentação do projeto.

---

## Documentos principais

| # | Documento | Conteúdo |
|---|-----------|----------|
| 1 | [01-fluxo-funcionamento.md](01-fluxo-funcionamento.md) | Fluxo de funcionamento (add, search, report) e dados persistidos |
| 2 | [02-funcionalidades.md](02-funcionalidades.md) | Lista de funcionalidades por área (OCR, HTR multilíngue, IA com 20 categorias) |
| 3 | [03-CLI.md](03-CLI.md) | Comandos CLI (30+), flags simplificadas (recursivo e skip-failed por padrão), variáveis de ambiente |
| 4 | [04-report.md](04-report.md) | Report HTML: estrutura, busca (Ignorar acentos, Buscar por sinônimos, AND/OR/NEAR), nuvem interativa, mapa de locais, document-view, responsividade |
| 5 | [05-logs-e-auditoria.md](05-logs-e-auditoria.md) | Sistema de logs e auditoria (audit.jsonl, pdfsearchable.log) |
| 6 | [06-UX-UI.md](06-UX-UI.md) | UX e UI (CLI e report, Apple-like, responsividade) |
| 7 | [07-performance.md](07-performance.md) | Performance: processamento, busca, report e recomendações |
| 8 | [08-processamento-indexacao.md](08-processamento-indexacao.md) | Processamento e indexação (pipeline padrão + histórico, índice, OCR DPI 300, lotes) |
| 9 | [09-IA.md](09-IA.md) | IA: classificação de tipo, Ollama com 20 categorias de entidades, RAG |
| 10 | [10-config.md](10-config.md) | Config (config.toml / config.json), OCR, HTR multilíngue, ViaCEP, IP-API, sinônimos, performance |
| 11 | [11-servidor.md](11-servidor.md) | Servidor HTTP (serve): SPA interativa, REST API completa (17 endpoints), autenticação, CORS, SSE |
| 11b | [11-HuggingFace.md](11-HuggingFace.md) | HTR multilíngue (40+ idiomas, 7 modelos dedicados + modelos históricos TRIDIS/large/Kansallisarkisto), pipeline OCR histórico (CLAHE, Sauvola, morfológica), modelos HF opcionais |
| 12 | [12-arquivos-gerados.md](12-arquivos-gerados.md) | Arquivos gerados e persistidos: .pdfsearchable/, arquivos-processados/ |

---

## Guias

- [exemplo_utilizacao.md](exemplo_utilizacao.md) — Guia prático com exemplos de uso completos (do zero ao report)

---

## Outros

- [FAQ.md](FAQ.md) — Perguntas frequentes (instalação, report, busca, filtros, IA, OCR)
- [SEGURANCA.md](SEGURANCA.md) — Segurança: credenciais, path traversal, XSS, chamadas externas, dependências
