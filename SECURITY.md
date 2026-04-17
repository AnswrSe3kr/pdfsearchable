# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.4.x   | ✅ Active  |
| 0.3.x   | ✅ Security fixes only |
| < 0.3   | ❌ End of life |

## Reporting a Vulnerability

**Please do NOT open a public GitHub issue for security vulnerabilities.**

### How to Report

1. **Email**: Send a detailed report to the maintainers (see `pyproject.toml` authors).
2. **GitHub Private Advisory**: Use [GitHub Security Advisories](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability) to report privately.

### What to Include

- Description of the vulnerability
- Steps to reproduce (proof of concept)
- Affected versions
- Potential impact
- Suggested fix (if any)

### Response Timeline

| Stage | Time |
|-------|------|
| Acknowledgement | ≤ 48 hours |
| Initial assessment | ≤ 5 business days |
| Fix / mitigation | ≤ 30 days (critical: ≤ 7 days) |
| Public disclosure | After fix is released |

## Security Design Principles

### What pdfsearchable does with your data

- **Entirely offline**: no data is sent to external servers (except Ollama if you configure it — local by default)
- **Local storage only**: all indexes, text, and reports are stored in `.pdfsearchable/` in your project directory
- **No telemetry**: zero usage tracking or analytics
- **File permissions**: sensitive files (index, audit log, FTS database) are created without world-write permissions

### Authentication

When using `pdfsearchable serve`, set `PDFSEARCHABLE_AUTH_TOKEN` to protect the HTTP interface:

```bash
export PDFSEARCHABLE_AUTH_TOKEN="your-secret-token"
pdfsearchable serve
```

The server accepts:
- `Authorization: Bearer <token>`
- `Authorization: Basic base64(anyuser:<token>)`

**Never expose the serve port to untrusted networks without authentication.**

### Audit Logging

All indexing operations are logged to `.pdfsearchable/audit.jsonl`. This file:
- Contains file paths and operation timestamps
- Does **not** store file passwords or authentication tokens
- Should be protected with appropriate filesystem permissions

### Dependency Security

We use `pip-audit` in CI to detect known vulnerabilities in dependencies. To check your local installation:

```bash
pip install pip-audit
pip-audit
```

### OCR Security

Tesseract OCR processes PDF images locally. Maliciously crafted PDFs are handled by PyMuPDF (libmupdf) before reaching Tesseract. We recommend keeping both Tesseract and PyMuPDF up to date.

## Known Limitations

- The HTTP server (`serve`) is single-process and not hardened for production exposure. Use a reverse proxy (nginx) with TLS if exposing externally.
- PDF passwords are passed via `--password` CLI argument, which may appear in shell history. Use `PDF_PASSWORD` environment variable instead.
- Audit logs are plaintext. If your threat model requires encrypted audit trails, mount `.pdfsearchable/` on an encrypted filesystem.
- Rate limiting in `/api/ask` is per-process (in-memory), not per-IP. Multiple simultaneous server processes would each have independent counters.

## Recent Security Improvements (0.4.x)

- **POST body size limits**: `/api/meta/update` and `/api/annotations` are capped at 1 MB; `/api/ask` is capped at 64 KB, preventing memory exhaustion via large `Content-Length`.
- **`file_id` length enforcement**: `/api/text` now requires exactly 16 hex characters (previously only checked that characters were hex, not length).
- **`/api/page` input validation**: the `page` parameter is now validated with `try/except` before `int()`, returning HTTP 400 instead of an unhandled 500.
- **Store cache thread-safety**: `load_index()` now returns a deep copy of the in-memory cache, preventing concurrent HTTP handler threads from observing partial mutations by writer threads.
- **MCP server version**: no longer hardcoded — reads from the installed package version.
- **SSRF protection strengthened**: `synonyms_api` URL validation now blocks private (RFC 1918), loopback, link-local (169.254.x.x), and reserved IP addresses in addition to non-HTTP schemes.
- **Embedding integrity checks**: `semantic_search` cosine similarity now uses `strict=True` zip (raises on dimension mismatch) and `_blob_to_vec` validates blob alignment before `struct.unpack`.
- **Unicode NFC normalization**: `_file_id()` now normalizes paths to NFC before hashing, preventing duplicate index entries on macOS HFS+/APFS (which returns NFD paths).
- **API error responses in JSON**: all `/api/*` error responses now return `{"error": "..."}` in JSON instead of HTML — prevents client-side parsing failures.
- **CORS on 429 rate limit**: the 429 rate-limit response now includes full CORS headers, allowing cross-origin SPAs to handle the error.
- **`_ask_timeout` enforced**: `PDFSEARCHABLE_ASK_TIMEOUT` (30–300s, default 90s) is now passed to the Ollama call, preventing indefinite hangs.
- **SSE connection timeout**: `/api/events` connections are capped at 5 minutes (300s), preventing thread exhaustion from long-lived idle connections.
- **Directory listing disabled**: the static file handler no longer exposes directory listings (returns 403).
- **`Access-Control-Max-Age`**: OPTIONS preflight responses include `Max-Age: 86400` (24h), reducing redundant preflight requests.
- **Gzip compression**: large JSON responses are automatically gzip-compressed when the client supports it (> 1KB threshold).
- **Geocoding cache**: Nominatim API results are cached in-memory per process, reducing external API calls.

## Security Checklist for Deployment

- [ ] Set `PDFSEARCHABLE_AUTH_TOKEN` before running `serve`
- [ ] Restrict `.pdfsearchable/` directory permissions (`chmod 700`)
- [ ] Use `PDF_PASSWORD` env var instead of `--password` flag
- [ ] Keep Tesseract and PyMuPDF updated
- [ ] Run `pip-audit` periodically
- [ ] Do not expose `serve` port publicly without a TLS reverse proxy
