"""
CLI principal: amigável, humanizado, visual Apple-like.

Fluxo: pdfsearchable add (indexa PDFs) → pdfsearchable serve (serve SPA interativa em HTTP).
A SPA (app.html) é servida directamente; report.html pode ser gerado com 'pdfsearchable report'.
"""

import contextlib
import json as _json
import os
import re
import sys
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any

# Arquivo para retomar processamento interrompido (add --resume)
def _pending_add_file() -> Path:
    return Path.cwd() / ".pdfsearchable" / "pending_add.json"

import click
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, BarColumn, SpinnerColumn, TextColumn, TimeRemainingColumn

from pdfsearchable.audit import audit, get_logger, read_audit_trail
from pdfsearchable.config import apply_config_to_env, validate_config_env
from pdfsearchable.exceptions import (
    IndexingError,
    PdfSearchableError,
    ReportError,
    StoreError,
    ValidationError,
)
from pdfsearchable.indexer import index_pdfs, get_stats
from pdfsearchable.store import (
    load_index,
    save_index,
    ensure_store,
    load_file_text,
    remove_file_meta,
    fts_search,
    fts_index_all_files,
    fts_index_new_files,
    fts_ensure_healthy,
    fts_last_error,
    get_duplicate_groups,
    get_semantic_duplicate_groups,
    update_doc_type,
    update_file_tags,
    update_file_subject,
    STORE_DIR,
    FILES_DIR,
    META_FILE,
    INDEX_VERSION,
)
from pdfsearchable.search import search_with_masks
from pdfsearchable.content_extractors import ask_document_ollama, expand_search_query_ollama

try:
    from pdfsearchable import __version__
except ImportError:
    __version__ = "0.2.0"

console = Console()
logger = get_logger("cli")

# Mensagem padrão quando o utilizador deve consultar o log (erros inesperados ou de sistema)
LOG_HINT = "[dim]Consulte .pdfsearchable/pdfsearchable.log para detalhes.[/]"


def _abort_index_error(e: Exception) -> None:
    """
    Trata erros ao ler o índice (index.json ou store) de forma amigável.
    Regista a exceção no log e mostra mensagem ao utilizador.
    """
    logger.exception("Falha ao aceder ao índice")
    if isinstance(e, StoreError):
        console.print(f"[red]{e.message}[/]")
    else:
        console.print("[red]Falha ao ler o índice (.pdfsearchable/index.json).[/]")
        console.print(
            "[dim]Faça backup da pasta .pdfsearchable; se o arquivo index.json estiver corrompido, "
            "pode apagá-lo (ou renomeá-lo) e reconstruir o índice com [cyan]pdfsearchable add[/].[/]"
        )
    raise click.Abort() from None


def _run_deferred_fts() -> None:
    """
    Se PDFSEARCHABLE_FTS_DEFERRED=1, indexa FTS para todos os arquivos do índice
    (ao final do add). Com PDFSEARCHABLE_FTS_BACKGROUND=1, roda em thread e não bloqueia.
    """
    if (os.environ.get("PDFSEARCHABLE_FTS_DEFERRED") or "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return

    def do_fts() -> None:
        try:
            n = fts_index_new_files()
            if n > 0:
                audit("fts_index_new", {"files_indexed": n})
        except Exception as _fts_err:
            logger.error("Erro na indexação FTS diferida: %s", _fts_err)
            audit(
                "fts_index_error",
                {
                    "error": str(_fts_err),
                    "hint": "Execute 'pdfsearchable index-fts' manualmente para reconstruir o índice de busca.",
                },
            )

    if (os.environ.get("PDFSEARCHABLE_FTS_BACKGROUND") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        t = threading.Thread(target=do_fts, daemon=True)
        t.start()
        console.print("[dim]Índice FTS será atualizado em segundo plano.[/]")
    else:
        do_fts()


@click.group()
@click.option(
    "--config",
    "-c",
    type=click.Path(path_type=Path, exists=False),
    default=None,
    help="Caminho para config.toml ou config.json (sobrepõe .pdfsearchable/config).",
)
@click.version_option(version=__version__, prog_name="pdfsearchable")
def main(config: Path | None) -> None:
    """
    pdfsearchable — Processa, indexa e pesquisa em PDFs.

    Report visual (Apple-like), busca com filtros avançados, nuvem de palavras
    e mapa de locais (ViaCEP, IP-API). Visualização de documento (PDF + metadados + hash).
    Execute na pasta dos PDFs. Use --help em qualquer comando para ver opções disponíveis.
    """
    if config is not None:
        os.environ["PDFSEARCHABLE_CONFIG_FILE"] = str(config.resolve())
    apply_config_to_env()
    # Validar tipos e intervalos das variáveis de ambiente ao arrancar
    for _cfg_warn in validate_config_env():
        console.print(f"[yellow]⚠ Configuração: {_cfg_warn}[/]")


@main.command("init")
def init_cmd() -> None:
    """
    Cria apenas a estrutura .pdfsearchable/ e arquivos-processados/ (sem indexar PDFs).

    Útil para preparar o projeto antes de adicionar documentos ou para scripts
    que precisam da pasta existente.
    """
    try:
        ensure_store()
        if not META_FILE.exists():
            save_index({"version": INDEX_VERSION, "files": []})
        console.print(
            Panel(
                "[green]✓[/] Estrutura criada: [cyan].pdfsearchable/[/] e [cyan]arquivos-processados/[/]\n\n"
                "[dim]Use [cyan]pdfsearchable add pasta/[/] para indexar PDFs.[/]",
                title="[bold green]Inicializado[/]",
                border_style="green",
            )
        )
    except StoreError as e:
        console.print(f"[red]{e.message}[/]")
        raise click.Abort() from None


@main.command("stats")
def stats_cmd() -> None:
    """
    Mostra métricas resumidas do índice: documentos, páginas, tamanho, última indexação.
    """
    try:
        idx = load_index()
    except StoreError as e:
        _abort_index_error(e)
    files = idx.get("files", [])
    total_files = len(files)
    total_pages = sum(f.get("num_pages", 0) for f in files)
    total_bytes = sum(f.get("file_size") or 0 for f in files)
    total_mb = round(total_bytes / (1024 * 1024), 2)
    last_dates = [
        f.get("updated_at") or f.get("indexed_at") or ""
        for f in files
        if f.get("updated_at") or f.get("indexed_at")
    ]
    last_indexed = max(last_dates, default="")
    table = Table(title="📊 Estatísticas do índice")
    table.add_column("Métrica", style="cyan")
    table.add_column("Valor", style="green")
    table.add_row("Documentos", str(total_files))
    table.add_row("Páginas", str(total_pages))
    table.add_row("Tamanho (MB)", str(total_mb))
    table.add_row("Última indexação", last_indexed or "—")
    table.add_row("Versão do índice", str(idx.get("version", "?")))
    console.print(table)
    if total_files == 0:
        console.print(
            "[dim]Nenhum documento indexado. Use [cyan]pdfsearchable add[/] para adicionar PDFs.[/]"
        )


@main.command(
    "add",
    epilog=(
        "Exemplos:\n\n"
        "  pdfsearchable add relatorio.pdf\n\n"
        "  pdfsearchable add pasta/ -r --workers 4\n\n"
        "  pdfsearchable add pasta/ --resume\n\n"
        "  pdfsearchable add pasta/ --order-by size --batch-size 50"
    ),
)
@click.argument("files", type=click.Path(exists=True, path_type=Path), nargs=-1)
@click.option("--password", "-p", envvar="PDF_PASSWORD", help="Senha do PDF (ou env PDF_PASSWORD).")
@click.option("--skip-failed/--no-skip-failed", default=True, show_default=True, help="Continuar mesmo quando um arquivo falhar.")
@click.option(
    "--extract-mode",
    type=click.Choice(["text", "blocks", "dict"]),
    default="text",
    help="Modo de extração PyMuPDF: text, blocks ou dict.",
)
@click.option("--compress", is_flag=True, help="Comprimir texto armazenado (gzip).")
@click.option(
    "--workers",
    "-w",
    type=int,
    default=0,
    metavar="N",
    help="Processamento paralelo (0=auto até 16; 1=sequencial; 2+=multiprocessing). Com 2+ usa multiprocessing (evita Lock do PyMuPDF).",
)
@click.option(
    "--batch-size",
    "-b",
    type=int,
    default=None,
    metavar="N",
    help="Processar em lotes de N arquivos (gc entre lotes para controlar RAM).",
)
@click.option(
    "--continue",
    "continue_mode",
    is_flag=True,
    help="Modo contínuo: pular já indexados e continuar mesmo se um arquivo falhar.",
)
@click.option("--recursive/--no-recursive", "-r", default=True, show_default=True, help="Incluir PDFs em subpastas (recursivo).")
@click.option(
    "--reprocess",
    is_flag=True,
    help="Reprocessar arquivos já adicionados ao índice (reindexar mesmo que o conteúdo não tenha mudado).",
)
@click.option(
    "--resume",
    is_flag=True,
    help="Retomar processamento a partir da lista pendente (última interrupção com Ctrl+C).",
)
@click.option(
    "--order-by",
    type=click.Choice(["size", "mtime", "name"]),
    default="size",
    help="Ordenar PDFs antes de processar: size=menores primeiro (feedback rápido), mtime=mais recentes, name=alfabético.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Mostrar nome de cada arquivo ao iniciar o processamento (modo sequencial).",
)
@click.option(
    "--confirm-type",
    is_flag=True,
    help="Após classificação por IA, pedir confirmação do tipo sugerido (TTY apenas).",
)
@click.option(
    "--embed",
    "embed_after",
    is_flag=True,
    help="Gerar embeddings semânticos (Ollama nomic-embed-text) após indexar (incremental).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Simular: listar o que seria indexado (ou ignorado por hash) sem escrever no índice.",
)
def add(
    files: tuple[Path, ...],
    password: str | None,
    skip_failed: bool,
    extract_mode: str,
    compress: bool,
    workers: int,
    batch_size: int | None,
    continue_mode: bool,
    recursive: bool,
    reprocess: bool,
    resume: bool,
    order_by: str,
    verbose: bool,
    confirm_type: bool,
    embed_after: bool,
    dry_run: bool,
) -> None:
    """
    Adiciona um ou mais PDFs ao projeto.

    Aceita arquivos .pdf e também pastas: ao passar um diretório, todos os
    PDFs dentro dele são indexados (recursivo por padrão; use --no-recursive para um nível apenas).
    O projeto aceita apenas PDF. Você pode rodar quantas vezes quiser; o índice é incremental.
    Arquivos com erro são ignorados automaticamente (--skip-failed). Use --resume para retomar após Ctrl+C.
    """
    _paf = _pending_add_file()
    expanded: list[Path] = []
    non_pdfs: list[Path] = []

    if resume and _paf.exists():
        try:
            data = _json.loads(_paf.read_text(encoding="utf-8"))
            paths_raw = data.get("paths") or []
            expanded = [Path(p).resolve() for p in paths_raw if p]
            _paf.unlink()
        except (OSError, _json.JSONDecodeError):
            pass
        if not expanded:
            console.print("[dim]Nenhum caminho pendente encontrado ou lista já vazia.[/]")
            return
        console.print(f"[dim]Retomando {len(expanded)} arquivo(s) pendente(s).[/]")
    else:
        if not files:
            console.print(
                Panel(
                    "[bold]📂 Nenhum arquivo informado[/]\n\n"
                    "Use: [cyan]pdfsearchable add caminho/arquivo.pdf[/]\n"
                    "Ou uma pasta: [cyan]pdfsearchable add pasta/[/]\n"
                    "Ou vários: [cyan]pdfsearchable add a.pdf b.pdf pasta/[/]\n\n"
                    "[dim]Você pode adicionar mais PDFs a qualquer momento; o índice é incremental.[/]",
                    title="📄 pdfsearchable",
                    border_style="blue",
                )
            )
            return
        # Excluir pastas internas do projeto (evita reindexar cópias em arquivos-processados/
        # quando o utilizador faz add . ou add /caminho/que/contém/arquivos-processados).
        EXCLUDED_DIRS = {"arquivos-processados", ".pdfsearchable"}

        def _scan_dir(root: Path, recursive: bool) -> list[Path]:
            results: list[Path] = []
            if recursive:
                for child in root.iterdir():
                    if child.is_dir():
                        if child.name in EXCLUDED_DIRS:
                            continue
                        results.extend(_scan_dir(child, recursive=True))
                    elif child.suffix.lower() == ".pdf":
                        results.append(child)
            else:
                results.extend(p for p in root.glob("*.pdf") if p.is_file())
            return sorted(results)

        for p in files:
            p = p.resolve()
            if p.is_dir():
                expanded.extend(_scan_dir(p, recursive=recursive))
            else:
                if p.suffix.lower() != ".pdf":
                    non_pdfs.append(p)
                else:
                    expanded.append(p)
        if non_pdfs:
            console.print(
                "[red]O projeto aceita apenas arquivos PDF (ou pastas com PDFs).[/] "
                "Os seguintes não são PDF e foram rejeitados:"
            )
            for p in non_pdfs:
                console.print(f"  [red]•[/] {p}")
            raise click.Abort() from None
        if not expanded:
            console.print(
                "[yellow]Nenhum PDF encontrado.[/] "
                "Se passou uma pasta, verifique se há arquivos .pdf dentro dela."
            )
            console.print(
                "[dim]Use [cyan]pdfsearchable add pasta/ -r[/] para incluir subpastas.[/]"
            )
            return

    # Dirs de sistema a excluir do scan (contêm PDFs que não são documentos do utilizador)
    _EXCLUDED_DIR_PARTS = {
        ".venv", "venv", "env", ".env",  # ambientes virtuais Python
        "node_modules",                   # dependências JS
        ".git",                           # controlo de versão
        ".pdfsearchable",                 # própria store do projeto (inclui subpastas)
        "__pycache__", ".tox", ".pytest_cache",  # artefactos de build/test
        "site-packages",                  # pacotes instalados (pip, conda, etc.)
    }
    def _is_excluded(path: Path) -> bool:
        # Verificar todas as partes do path resolvido para apanhar subpastas
        # Ex.: /proj/.pdfsearchable/archive/file.pdf → partes incluem ".pdfsearchable"
        return any(part in _EXCLUDED_DIR_PARTS for part in path.resolve().parts)

    # Ignora arquivos de metadados do macOS (._*) e pastas de sistema
    pdfs = [p for p in expanded if not p.name.startswith("._") and not _is_excluded(p)]
    excluded_count = len(expanded) - len(pdfs) - sum(1 for p in expanded if p.name.startswith("._"))
    if excluded_count > 0:
        console.print(
            f"[dim]⚠ {excluded_count} PDF(s) em pastas de sistema (.venv/, node_modules/, etc.) foram ignorados.[/]"
        )

    # Ordenação: menores primeiro (feedback rápido), mtime ou nome
    if order_by == "size":
        pdfs = sorted(pdfs, key=lambda p: p.stat().st_size)
    elif order_by == "mtime":
        pdfs = sorted(pdfs, key=lambda p: p.stat().st_mtime, reverse=True)
    else:
        pdfs = sorted(pdfs, key=lambda p: p.name.lower())

    # ─── Dry-run: listar o que seria indexado e parar ──────────────────────
    if dry_run:
        from pdfsearchable.pdf_processor import content_hash as _ch, file_size as _fs
        from pdfsearchable.store import find_by_content_hash as _fh

        total_bytes = sum(p.stat().st_size for p in pdfs)
        new_count = 0
        dup_count = 0
        skip_count = 0
        table = Table(title=f"Dry-run: {len(pdfs)} PDF(s) · {total_bytes / 1024**2:.1f} MB")
        table.add_column("Status", style="cyan")
        table.add_column("Arquivo")
        table.add_column("Tamanho", justify="right")
        for p in pdfs[:200]:  # limite de exibição
            try:
                h = _ch(p)
                existing = _fh(h)
                if existing and not reprocess:
                    status = "[yellow]⇒ ignorar (hash igual)[/]"
                    skip_count += 1
                elif existing and reprocess:
                    status = "[magenta]↻ reprocessar[/]"
                    dup_count += 1
                else:
                    status = "[green]+ novo[/]"
                    new_count += 1
                table.add_row(status, p.name[:60], f"{_fs(p) / 1024:.0f} KB")
            except Exception as _e:
                table.add_row("[red]erro[/]", p.name[:60], str(_e)[:30])
        console.print(table)
        if len(pdfs) > 200:
            console.print(f"[dim]… e mais {len(pdfs) - 200} arquivo(s) (omitidos da tabela).[/]")
        console.print(
            f"[bold]Resumo:[/] [green]novos={new_count}[/] · "
            f"[magenta]reprocessar={dup_count}[/] · [yellow]ignorar={skip_count}[/]"
        )
        console.print("[dim]Nenhuma escrita foi feita. Remova --dry-run para indexar.[/]")
        return
    # ─────────────────────────────────────────────────────────────────────────

    # Aviso para arquivos grandes (configurável via PDFSEARCHABLE_LARGE_FILE_MB)
    try:
        large_mb = max(1, int(os.environ.get("PDFSEARCHABLE_LARGE_FILE_MB") or "20"))
    except ValueError:
        large_mb = 20
    large_threshold = large_mb * 1024 * 1024
    large_files = [p for p in pdfs if p.stat().st_size >= large_threshold]
    if large_files:
        console.print(
            f"[dim]📦 {len(large_files)} arquivo(s) com mais de {large_mb} MB: "
            f"será usada compressão automática.[/]"
        )

    # ─── Pre-flight checks (robustez) ─────────────────────────────────────────
    # 1. Verificar leitura de cada PDF (descartar arquivos ilegíveis cedo)
    unreadable: list[Path] = []
    total_size = 0
    for p in pdfs:
        try:
            with p.open("rb") as fh:
                fh.read(4)
            total_size += p.stat().st_size
        except OSError as e:
            unreadable.append(p)
            console.print(f"[red]✗ Ilegível:[/] {p.name} ({e})")
    if unreadable:
        console.print(
            f"[yellow]⚠ {len(unreadable)} arquivo(s) ilegíveis foram excluídos do processamento.[/]"
        )
        pdfs = [p for p in pdfs if p not in unreadable]
        total_pdfs = len(pdfs)
        if not pdfs:
            console.print("[red]Nenhum PDF legível para processar.[/]")
            return

    # 2. Verificar espaço em disco (~3x do tamanho da fonte: texto + OCR cache + cópias)
    try:
        from shutil import disk_usage
        store_parent = Path.cwd()
        store_parent.mkdir(parents=True, exist_ok=True)
        free = disk_usage(store_parent).free
        needed = max(total_size * 3, 50 * 1024 * 1024)  # mínimo 50 MB
        if free < needed:
            console.print(
                f"[red]✗ Espaço em disco insuficiente:[/] "
                f"{free / 1024**3:.2f} GB livres, ~{needed / 1024**3:.2f} GB necessários."
            )
            console.print(
                "[dim]Liberte espaço ou mude para outro disco antes de continuar.[/]"
            )
            raise click.Abort()
        if free < needed * 2:
            console.print(
                f"[yellow]⚠ Pouco espaço livre:[/] {free / 1024**3:.2f} GB "
                f"(estimado: {needed / 1024**3:.2f} GB)."
            )
    except (OSError, ImportError):
        pass  # disk_usage falha em alguns FS — não bloquear

    # 3. Verificar permissão de escrita no diretório do projeto
    try:
        test_dir = Path.cwd() / ".pdfsearchable"
        test_dir.mkdir(parents=True, exist_ok=True)
        test_file = test_dir / ".write_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
    except OSError as e:
        console.print(
            f"[red]✗ Sem permissão de escrita em {Path.cwd()}/.pdfsearchable:[/] {e}"
        )
        raise click.Abort() from e
    # ───────────────────────────────────────────────────────────────────────────

    import time as _time

    skip_fail = skip_failed or continue_mode
    total_pdfs = len(pdfs)
    last_completed: list[int] = [0]  # mutável para uso no callback e em KeyboardInterrupt

    def on_file_start(path: Path) -> None:
        if verbose:
            console.print(f"  [dim]Processando {path.name}…[/]")

    def on_progress(path: Path, current: int, total: int) -> None:
        last_completed[0] = current
        progress.update(
            task,
            description=f"Processando ({current}/{total}) {path.name}",
            completed=current,
        )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=24, complete_style="cyan", finished_style="green"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Processando e indexando PDFs…", total=total_pdfs)
        t0 = _time.perf_counter()
        try:
            results = index_pdfs(
                pdfs,
                mode=extract_mode,
                password=password,
                use_ocr=True,
                compress=compress,
                skip_existing=not reprocess,
                skip_failed=skip_fail,
                workers=workers,
                batch_size=batch_size,
                on_file_progress=on_progress,
                on_file_start=on_file_start if verbose and workers == 1 else None,
            )
            last_completed[0] = total_pdfs
            progress.update(task, completed=total_pdfs, description="Concluído")
        except KeyboardInterrupt:
            remaining = pdfs[last_completed[0] :]
            if remaining:
                _paf.parent.mkdir(parents=True, exist_ok=True)
                try:
                    # Escrita atômica: gravar em .tmp e renomear (evita JSON truncado em crash)
                    _paf_tmp = _paf.with_suffix(".json.tmp")
                    _paf_tmp.write_text(
                        _json.dumps({"paths": [str(p) for p in remaining]}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    _paf_tmp.replace(_paf)
                    console.print(
                        f"\n[dim]Interrompido. {len(remaining)} arquivo(s) pendente(s). Retome com [cyan]pdfsearchable add --resume[/].[/]"
                    )
                except OSError:
                    console.print("\n[dim]Interrompido. Não foi possível gravar lista pendente.[/]")
            raise click.Abort() from None
        except ValidationError as e:
            console.print(f"[red]Validação: {e.message}[/]")
            raise click.Abort() from None
        except IndexingError as e:
            console.print(f"[red]Indexação: {e.message}[/]")
            raise click.Abort() from None
        except StoreError as e:
            logger.exception("Erro de armazenamento durante indexação")
            console.print(f"[red]{e.message}[/]")
            console.print(LOG_HINT)
            raise click.Abort() from None
        except PdfSearchableError as e:
            console.print(f"[red]{e.message}[/]")
            raise click.Abort() from None
        except Exception as e:
            logger.exception("Erro inesperado durante indexação")
            _msg = str(e).strip() or type(e).__name__
            console.print(
                Panel(
                    f"[red]Erro inesperado durante a indexação:[/]\n"
                    f"  {_msg}\n\n"
                    "[dim]Possíveis causas: PDF muito grande, disco cheio, "
                    "permissão negada ou biblioteca PyMuPDF desatualizada.\n"
                    f"Detalhes técnicos no log. {LOG_HINT}[/]",
                    title="[bold red]Erro[/]",
                    border_style="red",
                )
            )
            raise click.Abort() from None
        elapsed = _time.perf_counter() - t0

    # FTS diferido: indexar ao final do lote (ou em background)
    _run_deferred_fts()

    # Webhook: notificar URL externa (PDFSEARCHABLE_WEBHOOK_URL) em background daemon
    # Disparado em thread separada para não atrasar a exibição de "Concluído".
    if results:
        threading.Thread(
            target=_fire_webhook,
            args=(results, "indexed"),
            daemon=True,
        ).start()

    # Detetar duplicados de conteúdo entre os PDFs submetidos que foram ignorados
    _warn_content_duplicates(pdfs, results)

    # Limpar lista pendente em caso de conclusão normal
    if _paf.exists():
        with contextlib.suppress(OSError):
            _paf.unlink()

    audit("cli_add", {"count": len(results), "paths": [str(p) for p in pdfs], "workers": workers})
    # Avisar sobre arquivos que falharam durante a indexação
    _failed_results = [r for r in results if r.get("error")]
    if _failed_results:
        for _fr in _failed_results:
            _ferr = str(_fr.get("error", "erro desconhecido")).strip()
            _fname = _fr.get("name") or _fr.get("path") or "arquivo desconhecido"
            _is_corrupt = any(
                kw in _ferr.lower()
                for kw in ("corrompido", "corrupt", "damaged", "invalid", "inválido")
            )
            _hint = (
                "O PDF parece estar corrompido ou incompleto. "
                "Tente obter uma cópia íntegra do arquivo."
                if _is_corrupt
                else "Verifique se o arquivo existe, não está protegido por senha e não está a ser usado por outra aplicação."
            )
            console.print(
                f"[yellow]⚠ {_fname}:[/] {_ferr}\n"
                f"  [dim]{_hint}[/]"
            )
        results = [r for r in results if not r.get("error")]

    if not results:
        console.print(
            Panel(
                "[dim]Nenhum arquivo novo indexado (conteúdo já existente ou inalterado).\n\n"
                "Você pode adicionar mais PDFs com [bold cyan]pdfsearchable add ...[/] ou ver o report com [bold cyan]pdfsearchable serve[/].[/]",
                title="[bold]Concluído[/]",
                border_style="dim",
            )
        )
    else:
        table = Table(title="Arquivos indexados")
        table.add_column("Documento", style="cyan")
        table.add_column("Páginas", justify="right", style="dim")
        table.add_column("Tipo", style="green")
        table.add_column("Avisos", style="yellow")
        for r in results:
            tipo = r.get("doc_type", "—")
            if r.get("classification_source") in ("openai", "ollama"):
                tipo = f"{tipo} [dim](IA)[/]"
            # Avisos inline: páginas com falha, OCR não disponível, enriquecimento parcial
            _row_warns: list[str] = []
            if r.get("failed_pages"):
                _fp_count = len(r["failed_pages"])
                _row_warns.append(f"{_fp_count} pág. com erro")
            if r.get("ocr_warnings"):
                _row_warns.append(r["ocr_warnings"])
            if r.get("enrichment_partial"):
                _row_warns.append("enriquecimento parcial")
            table.add_row(r["name"], str(r["num_pages"]), tipo, ", ".join(_row_warns) or "—")
        console.print(table)

        # Confirmação manual do tipo quando solicitado e em TTY
        if confirm_type and sys.stdin.isatty():
            for r in results:
                src = r.get("classification_source")
                if src not in ("openai", "ollama"):
                    continue
                fid = r.get("id")
                if not fid:
                    continue
                suggested = r.get("doc_type") or "documento"
                console.print(
                    f"[cyan]{r.get('name', fid)}[/] — tipo sugerido: [bold]{suggested}[/] "
                    "[dim](classificado por IA)[/]"
                )
                if click.confirm("Aceitar este tipo?", default=True):
                    update_doc_type(fid, suggested, source=src)
                    continue
                new_type = click.prompt(
                    "Informe o tipo correto (ex.: contrato, ata, documento)",
                    default=suggested,
                ).strip()
                if not new_type:
                    new_type = "documento"
                update_doc_type(fid, new_type, source="manual")
        total_pages = sum(r.get("num_pages", 0) for r in results)
        if elapsed and elapsed > 0:
            docs_s = len(results) / elapsed
            pages_s = total_pages / elapsed
            console.print(
                f"[dim]Throughput: [bold]{len(results)}[/] doc(s) em [bold]{elapsed:.1f}s[/] "
                f"([bold]{docs_s:.1f}[/] doc/s, [bold]{pages_s:.1f}[/] pág/s)[/]"
            )
        elapsed_min = elapsed / 60.0 if elapsed else 0
        one_liner = f"[dim]{len(results)} documento(s) indexado(s)"
        if elapsed_min >= 0.1:
            one_liner += f" em {elapsed_min:.1f} min."
        one_liner += ".[/]"
        console.print(one_liner)
        console.print(
            Panel(
                f"[green]✓[/] [bold]{len(results)}[/] documento(s) indexado(s).\n\n"
                "[dim]Explore os documentos com [cyan]pdfsearchable serve[/][/]",
                title="[bold green]Sucesso[/]",
                border_style="green",
            )
        )
    if results:
        console.print("[dim]No report, clique no nome do documento para ver o PDF e metadados.[/]")

    # ─── Embeddings integrados (--embed) ────────────────────────────────────
    if embed_after and results:
        console.print("\n[cyan]Gerando embeddings semânticos…[/]")
        try:
            from pdfsearchable.semantic_search import embed_all_documents
            ok_n, fail_n = embed_all_documents()
            if fail_n == 0:
                console.print(f"[green]✓[/] {ok_n} documento(s) embeddeds (incremental).")
            else:
                console.print(
                    f"[yellow]⚠[/] {ok_n} OK, {fail_n} falhou (Ollama indisponível?)."
                )
        except Exception as _e:
            logger.exception("Falha ao gerar embeddings pós-add")
            console.print(f"[yellow]⚠ Embeddings falharam:[/] {_e}")
    # ───────────────────────────────────────────────────────────────────────


@main.command(
    epilog=(
        "Exemplos:\n\n"
        "  pdfsearchable remove a1b2c3d4e5f6a1b2\n\n"
        "  pdfsearchable remove contrato_2024\n\n"
        "  pdfsearchable remove a1b2c3d4e5f6a1b2 --yes"
    ),
)
@click.argument("file_id_or_name", required=True)
@click.option(
    "--yes", "-y", "yes_confirm", is_flag=True, help="Não pedir confirmação (útil em scripts)."
)
def remove(file_id_or_name: str, yes_confirm: bool) -> None:
    """
    Remove um arquivo do índice pelo ID (16 caracteres) ou pelo nome.

    Pede confirmação quando o terminal é interativo (TTY), a menos que use --yes.
    """
    try:
        idx = load_index()
    except Exception as e:
        _abort_index_error(e)
    files = idx.get("files", [])
    fid = None
    name = "?"
    for f in files:
        if f.get("id") == file_id_or_name or (f.get("name") or "").strip() == file_id_or_name:
            fid = f.get("id")
            name = f.get("name", "?")
            break
    if not fid:
        console.print(
            "[yellow]Arquivo não encontrado no índice. Use [bold]pdfsearchable status[/] para listar.[/]"
        )
        return
    if not yes_confirm and sys.stdin.isatty() and not click.confirm(
        f'Remover "{name}" do índice? O arquivo PDF na pasta NÃO será apagado; apenas sairá do índice.'
    ):
        return
    # Backup opcional do índice antes de operação destrutiva
    backup_env = (os.environ.get("PDFSEARCHABLE_BACKUP_INDEX") or "1").strip().lower()
    if backup_env in ("1", "true", "yes") and META_FILE.exists():
        try:
            import shutil as _shutil

            bak = STORE_DIR / "index.json.bak"
            _shutil.copy2(META_FILE, bak)
        except OSError:
            pass
    try:
        if remove_file_meta(fid):
            audit("cli_remove", {"file_id": fid, "name": name})
            console.print(
                Panel(
                    f"[green]✓[/] Removido do índice: [cyan]{name}[/]",
                    title="[bold green]Sucesso[/]",
                    border_style="green",
                )
            )
        else:
            console.print(
                Panel(
                    "[red]Falha ao remover do índice (documento não encontrado).[/]",
                    title="[bold red]Erro[/]",
                    border_style="red",
                )
            )
    except StoreError as e:
        logger.exception("Erro ao remover do índice")
        console.print(
            Panel(
                f"[red]{e.message}[/]\n\n{LOG_HINT}", title="[bold red]Erro[/]", border_style="red"
            )
        )
        raise click.Abort() from None


@main.command("verify")
def verify_cmd() -> None:
    """
    Verifica integridade do índice: confere se cada documento tem PDF e texto em disco.

    Reporta arquivos em falta ou inacessíveis em arquivos-processados/.
    """
    try:
        idx = load_index()
    except StoreError as e:
        _abort_index_error(e)
    files = idx.get("files", [])
    if not files:
        console.print("[dim]Índice vazio. Nada a verificar.[/]")
        return
    missing_pdf: list[str] = []
    missing_text: list[str] = []
    for f in files:
        fid = f.get("id")
        name = f.get("name", "?")
        if not fid:
            continue
        pdf_path = FILES_DIR / f"{fid}.pdf"
        text_dir = FILES_DIR / fid
        if not pdf_path.is_file():
            missing_pdf.append(f"{name} ({fid})")
        has_text = (text_dir / "full.txt").exists() or (text_dir / "full.txt.gz").exists()
        if not has_text:
            missing_text.append(f"{name} ({fid})")
    if not missing_pdf and not missing_text:
        console.print(
            Panel(
                f"[green]✓[/] Integridade OK: [bold]{len(files)}[/] documento(s) com PDF e texto em disco.",
                title="[bold green]Verificação[/]",
                border_style="green",
            )
        )
        return
    table = Table(title="Problemas de integridade")
    table.add_column("Tipo", style="red")
    table.add_column("Documentos", style="cyan")
    if missing_pdf:
        table.add_row(
            "PDF em falta", "\n".join(missing_pdf[:20]) + ("\n…" if len(missing_pdf) > 20 else "")
        )
    if missing_text:
        table.add_row(
            "Texto em falta",
            "\n".join(missing_text[:20]) + ("\n…" if len(missing_text) > 20 else ""),
        )
    console.print(table)
    console.print(
        f"[dim]Total: {len(missing_pdf)} PDF(s) em falta, {len(missing_text)} texto(s) em falta.[/]"
    )
    console.print(
        "[dim]Sugestão: reindexar com [cyan]pdfsearchable add --reprocess[/] ou remover do índice com [cyan]pdfsearchable remove <id>[/].[/]"
    )


@main.command("duplicates")
def duplicates_cmd() -> None:
    """
    Lista possíveis duplicatas: arquivos com o mesmo conteúdo (content_hash) e paths diferentes.
    """
    groups = get_duplicate_groups()
    if not groups:
        console.print(
            Panel(
                "[dim]Nenhuma duplicata encontrada — cada arquivo tem conteúdo único.[/]",
                title="[bold]Duplicatas[/]",
                border_style="dim",
            )
        )
        return
    table = Table(title="Duplicatas (mesmo conteúdo, paths diferentes)")
    table.add_column("Hash", style="dim")
    table.add_column("Arquivos (nome / path)", style="cyan")
    for g in groups:
        h = (g[0].get("content_hash") or "?")[:12] + "…"
        names = "\n".join(
            f.get("name", "?") + "\n  " + (f.get("original_path", "?") or "?") for f in g
        )
        table.add_row(h, names)
    console.print(table)
    console.print(f"[dim]Total: {len(groups)} grupo(s) com duplicatas.[/]")


@main.command(
    "index-fts",
    epilog=(
        "Exemplos:\n\n"
        "  pdfsearchable index-fts\n\n"
        "  # Reconstruir FTS após add com FTS_DEFERRED:\n"
        "  PDFSEARCHABLE_FTS_DEFERRED=1 pdfsearchable add *.pdf && pdfsearchable index-fts"
    ),
)
def index_fts_cmd() -> None:
    """
    Reindexa o índice full-text (FTS) para todos os documentos.

    Útil quando se usa PDFSEARCHABLE_FTS_DEFERRED=1 no add e se quer
    garantir que o FTS está atualizado, ou para reconstruir o índice após
    alterações manuais no store.
    """
    try:
        n = fts_index_all_files()
    except StoreError as e:
        _abort_index_error(e)
    console.print(
        Panel(
            f"[green]✓[/] Índice FTS atualizado: [bold]{n}[/] documento(s) indexado(s).",
            title="[bold green]Sucesso[/]",
            border_style="green",
        )
    )
    audit("cli_index_fts", {"files_indexed": n})


def _fire_webhook(results: list[dict], action: str = "indexed") -> None:
    """
    Dispara um POST JSON para PDFSEARCHABLE_WEBHOOK_URL se configurado.
    Silencioso em caso de erro — não deve bloquear o fluxo principal.
    Payload: {"action": "indexed", "count": N, "files": [{name, id, doc_type, ...}, ...]}
    """
    url = (os.environ.get("PDFSEARCHABLE_WEBHOOK_URL") or "").strip()
    if not url or not results:
        return
    import json as _json_wh
    import urllib.request as _req

    payload = _json_wh.dumps(
        {
            "action": action,
            "count": len(results),
            "files": [
                {
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "doc_type": r.get("doc_type"),
                    "num_pages": r.get("num_pages"),
                    "language": r.get("language"),
                    "indexed_at": r.get("indexed_at") or r.get("updated_at"),
                }
                for r in results
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    try:
        req = _req.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with _req.urlopen(req, timeout=8):  # nosec
            pass
        logger.debug("Webhook disparado: %s (%d arquivo(s))", url, len(results))
    except Exception as exc:
        _wh_err = str(exc).strip()
        logger.warning("Webhook falhou (%s): %s", url, _wh_err)
        try:
            audit(
                "webhook_error",
                {
                    "url": url,
                    "error": _wh_err,
                    "hint": (
                        "Verifique se o endpoint está acessível e se a variável "
                        "PDFSEARCHABLE_WEBHOOK_URL está correta."
                    ),
                },
            )
        except Exception:
            pass


def _warn_content_duplicates(pdfs: list[Path], results: list[dict]) -> None:
    """
    Após indexar, avisa se algum PDF submetido foi ignorado por ser duplicado
    de conteúdo de um arquivo já existente com caminho diferente.
    Silencioso em caso de qualquer erro (não deve bloquear o fluxo principal).
    """
    if not pdfs:
        return
    try:
        import hashlib as _hl

        def _fid(p: Path) -> str:
            return _hl.sha256(str(p.resolve()).encode()).hexdigest()[:16]

        from pdfsearchable.indexer import content_hash as _ch

        indexed_orig_paths = {r.get("original_path") for r in results if r.get("original_path")}
        # Apenas PDFs que não foram indexados nesta execução
        skipped = [p for p in pdfs if str(p) not in indexed_orig_paths]
        if not skipped:
            return
        idx_now = load_index()
        fid_set = {f["id"] for f in idx_now.get("files", []) if f.get("id")}
        hash_to_name: dict[str, str] = {
            f["content_hash"]: f.get("name", "?")
            for f in idx_now.get("files", [])
            if f.get("content_hash")
        }
        for p in skipped:
            if _fid(p) in fid_set:
                continue  # mesmo caminho, já indexado — skip silencioso
            try:
                h = _ch(p)
            except Exception as _e:
                logger.debug("content_hash falhou para %s: %s — verificação de duplicados ignorada", p, _e)
                continue
            dup_name = hash_to_name.get(h)
            if dup_name and dup_name != p.name:
                console.print(
                    f"[yellow]⚠ Duplicado:[/] [bold]{p.name}[/] tem conteúdo idêntico a "
                    f"[bold]{dup_name}[/] (já indexado — arquivo ignorado)."
                )
    except Exception as _e:
        logger.debug("Verificação de duplicados falhou (não crítico): %s", _e)


def _semantic_search(
    query: str,
    doc_type_filter: str | None,
    language_filter: str | None,
    date_from: str | None,
    date_to: str | None,
    top_k: int = 10,
) -> None:
    """Busca semântica por cosine similarity contra embeddings armazenados."""
    import struct
    import math
    import sqlite3 as _sq

    from pdfsearchable.store import STORE_DIR, load_index

    emb_db = STORE_DIR / "embeddings.sqlite"
    if not emb_db.exists():
        console.print(
            "[yellow]Embeddings não encontrados.[/] Gere-os primeiro com:\n"
            "[cyan]pdfsearchable embed[/]"
        )
        return

    ollama_url = (os.environ.get("PDFSEARCHABLE_OLLAMA_URL") or "http://localhost:11434").rstrip("/")
    model = (os.environ.get("PDFSEARCHABLE_EMBED_MODEL") or "nomic-embed-text").strip()

    # Obter embedding da query
    import urllib.request as _req
    import json as _json_s

    payload = _json_s.dumps({"model": model, "prompt": query}).encode()
    req = _req.Request(
        f"{ollama_url}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with _req.urlopen(req, timeout=30) as resp:  # nosec
            q_vec: list[float] = _json_s.loads(resp.read()).get("embedding", [])
    except Exception as e:
        _emb_err = str(e).strip()
        console.print(
            Panel(
                f"[red]Não foi possível obter o embedding da pesquisa:[/] {_emb_err}\n\n"
                f"[dim]Verifique se o Ollama está a correr em [cyan]{ollama_url}[/] "
                f"e se o modelo [cyan]{model}[/] está disponível.\n"
                "Para verificar: [cyan]ollama list[/] e [cyan]pdfsearchable doctor[/].[/]",
                title="[bold red]Erro de embedding[/]",
                border_style="red",
            )
        )
        return

    if not q_vec:
        console.print("[red]Modelo de embedding não devolveu vector.[/]")
        return

    # Carregar embeddings do DB
    conn = _sq.connect(emb_db, timeout=15)
    rows = conn.execute("SELECT file_id, embedding FROM embeddings WHERE model=?", (model,)).fetchall()
    conn.close()
    if not rows:
        console.print(f"[yellow]Nenhum embedding gerado para o modelo '{model}'.[/]")
        return

    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0

    def _blob_to_vec(blob: bytes) -> list[float]:
        n = len(blob) // 4
        return list(struct.unpack(f"{n}f", blob))

    try:
        idx = load_index()
    except Exception as e:
        _abort_index_error(e)
    files = idx.get("files", [])
    files = _filter_files_by_filters(files, doc_type_filter, language_filter, date_from, date_to)
    allowed_ids = {f.get("id") for f in files}
    id_to_meta = {f.get("id", ""): f for f in files}

    scores: list[tuple[float, str]] = []
    for fid, blob in rows:
        if fid not in allowed_ids:
            continue
        try:
            vec = _blob_to_vec(blob)
            sim = _cosine(q_vec, vec)
            scores.append((sim, fid))
        except Exception as _vec_err:
            logger.debug("Embedding inválido para %s: %s", fid, _vec_err)
            continue

    if not scores:
        console.print("[yellow]Nenhum embedding correspondente aos filtros activos.[/]")
        return

    scores.sort(reverse=True)
    top = scores[:top_k]

    table = Table(title=f'Busca semântica: "{query}"')
    table.add_column("Similaridade", justify="right", style="cyan", width=12)
    table.add_column("Documento", style="bold")
    table.add_column("Tipo", style="green")
    table.add_column("Páginas", justify="right", style="dim")
    for sim, fid in top:
        meta = id_to_meta.get(fid, {})
        bar = "█" * int(sim * 10) + "░" * (10 - int(sim * 10))
        table.add_row(
            f"{sim:.3f} {bar}",
            meta.get("name", fid),
            meta.get("doc_type") or "—",
            str(meta.get("num_pages") or 0),
        )
    console.print(table)
    console.print(
        f"[dim]{len(top)} documento(s) mais semelhantes · modelo: {model}[/]"
    )
    audit("cli_search_semantic", {"query": query, "model": model, "hits": len(top)})


_LOCAL_ASSETS: list[tuple[str, str]] = [
    ("leaflet.css",              "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"),
    ("leaflet.js",               "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"),
    ("MarkerCluster.css",        "https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css"),
    ("MarkerCluster.Default.css","https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css"),
    ("leaflet.markercluster.js", "https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"),
    ("wordcloud2.min.js",        "https://cdnjs.cloudflare.com/ajax/libs/wordcloud2.js/1.0.2/wordcloud2.min.js"),
]


def _ensure_local_assets(base_dir: Path) -> None:
    """
    Baixa os assets de terceiros (Leaflet, MarkerCluster, wordcloud2) para
    .pdfsearchable/assets/ para uso offline. Silencioso em caso de falha de rede.
    """
    import urllib.request

    assets_dir = base_dir / "assets"
    assets_dir.mkdir(exist_ok=True)
    missing = [(fn, url) for fn, url in _LOCAL_ASSETS if not (assets_dir / fn).exists()]
    if not missing:
        return
    for filename, url in missing:
        dest = assets_dir / filename
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:  # nosec
                dest.write_bytes(resp.read())
        except Exception as _dl_err:
            logger.debug("Download de asset %s falhou: %s — usando fallback", filename, _dl_err)


def _setup_spa(base_dir: Path) -> None:
    """
    Copia todos os templates HTML de templates/ para .pdfsearchable/.
    Silencioso em caso de falha (o servidor continua sem os templates).
    """
    import shutil as _shutil
    templates_dir = Path(__file__).parent / "templates"
    for _tmpl in templates_dir.glob("*.html"):
        try:
            _shutil.copy2(_tmpl, base_dir / _tmpl.name)
            logger.debug("%s copiado para %s", _tmpl.name, base_dir)
        except OSError as _e:
            logger.warning("Não foi possível copiar %s: %s", _tmpl.name, _e)


def _run_http_server(host: str, port: int, open_browser: bool = False) -> None:
    """
    Inicia o servidor HTTP que serve .pdfsearchable como SPA interativa.
    Copia app.html ao arrancar; serve a pasta e os PDFs em arquivos-processados/.
    Endpoints JSON /api/* para dados e /api/ask para RAG (Ollama).
    """
    import json
    import urllib.parse

    from pdfsearchable.content_extractors import ollama_health_check

    base_dir = Path.cwd() / ".pdfsearchable"
    if not base_dir.exists():
        console.print(
            "[red]Pasta .pdfsearchable não encontrada.[/] Adicione documentos com [cyan]pdfsearchable add[/] e depois use [cyan]pdfsearchable serve[/]."
        )
        raise click.Abort() from None

    # Baixar assets de terceiros para uso offline (silencioso em caso de falha de rede)
    _ensure_local_assets(base_dir)

    # Verificar/reconstruir FTS ao arrancar (silencioso; loga se reconstruir)
    fts_ensure_healthy()

    # Copiar SPA (app.html) para .pdfsearchable/ — operação leve, sempre síncrona
    _setup_spa(base_dir)

    # PDFs ficam em <projeto>/arquivos-processados/; o report referencia ../arquivos-processados/<id>.pdf
    processed_dir = base_dir.parent / "arquivos-processados"
    os.chdir(str(base_dir))

    # CORS: se PDFSEARCHABLE_CORS=1, enviar Access-Control-Allow-Origin (ex.: * ou valor da env)
    cors_origin = os.environ.get("PDFSEARCHABLE_CORS", "").strip().lower()
    if cors_origin in ("1", "true", "yes"):
        cors_origin = os.environ.get("PDFSEARCHABLE_CORS_ORIGIN", "*").strip() or "*"

    # Rate limit /api/ask: máximo de requisições por minuto (por processo)
    ask_rate_limit = 0
    with contextlib.suppress(ValueError):
        ask_rate_limit = max(0, int(os.environ.get("PDFSEARCHABLE_ASK_RATE_LIMIT", "30").strip()))
    _ask_timeout = 90
    with contextlib.suppress(ValueError):
        _ask_timeout = max(
            30, min(300, int(os.environ.get("PDFSEARCHABLE_ASK_TIMEOUT", "90").strip()))
        )
    _ask_request_times: list[float] = []  # para rate limit
    _ask_request_times_lock = threading.Lock()

    # Cache de geocodificação (por processo, evita chamadas repetidas à Nominatim)
    _geocode_cache: dict[str, tuple[float | None, float | None]] = {}

    # Auth: token opcional via PDFSEARCHABLE_AUTH_TOKEN
    _auth_token = (os.environ.get("PDFSEARCHABLE_AUTH_TOKEN") or "").strip()

    class Handler(SimpleHTTPRequestHandler):
        def list_directory(self, path: Any) -> None:  # type: ignore[override]
            """Desabilita listagem de diretórios por segurança."""
            self._send_json_error(403, "Listagem de diretórios desabilitada")
            return None

        def log_message(self, format: str, *args: Any) -> None:  # type: ignore[override]  # noqa: A002
            if (os.environ.get("PDFSEARCHABLE_HTTP_LOG") or "").strip().lower() in (
                "1",
                "true",
                "yes",
            ):
                super().log_message(format, *args)

        def _check_csrf(self) -> bool:
            """
            Proteção CSRF: rejeita POSTs com header ``Origin`` cuja host parte
            não bate com o host do servidor. Requisições de mesma origem (Origin
            ausente ou igual) e clientes nativos (curl, scripts) são aceitos.
            Baseado em OWASP Cheat Sheet: "Verifying Origin With Standard Headers".
            """
            origin = self.headers.get("Origin", "").strip()
            if not origin:
                # Sem Origin → não é browser cross-site, permite
                return True
            # CORS configurado e permissivo → respeita a configuração
            if cors_origin and cors_origin == "*":
                return True
            if cors_origin and origin == cors_origin:
                return True
            # Comparar com host do request
            try:
                host = self.headers.get("Host", "").strip()
                if not host:
                    return False
                parsed_origin = urllib.parse.urlparse(origin)
                origin_host = parsed_origin.netloc or parsed_origin.path
                # Aceitar se o host da Origin bate com o Host do request
                return origin_host == host
            except Exception:
                return False

        def _check_auth(self) -> bool:
            """Verifica token Bearer ou Basic Auth. Retorna True se autorizado."""
            if not _auth_token:
                return True
            import base64 as _b64
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                return auth[7:].strip() == _auth_token
            if auth.startswith("Basic "):
                try:
                    decoded = _b64.b64decode(auth[6:]).decode("utf-8", errors="replace")
                    # aceita qualquer username: token como password
                    return decoded.split(":", 1)[-1] == _auth_token
                except Exception:
                    return False
            return False

        def _send_401(self) -> None:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="pdfsearchable"')
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Unauthorized")

        def _send_json_error(self, code: int, message: str) -> None:
            """Envia resposta de erro em JSON (em vez de HTML padrão)."""
            body = json.dumps({"error": message}).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._send_cors()
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, body: bytes, status: int = 200) -> None:
            """Envia JSON com compressão gzip se o cliente suportar e body > 1 KB."""
            import gzip as _gz
            accept_enc = self.headers.get("Accept-Encoding", "")
            if len(body) > 1024 and "gzip" in accept_enc:
                compressed = _gz.compress(body, compresslevel=6)
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(compressed)))
                self._send_cors()
                self.end_headers()
                self.wfile.write(compressed)
            else:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._send_cors()
                self.end_headers()
                self.wfile.write(body)

        def _send_cors(self) -> None:
            if cors_origin:
                self.send_header("Access-Control-Allow-Origin", cors_origin)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")

        def do_OPTIONS(self) -> None:  # type: ignore[override]
            self.send_response(204)
            self._send_cors()
            if cors_origin:
                self.send_header("Access-Control-Max-Age", "86400")
            self.end_headers()

        def do_GET(self) -> None:  # type: ignore[override]
            if not self._check_auth():
                self._send_401()
                return
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                self.send_response(302)
                self.send_header("Location", "/app.html")
                self._send_cors()
                self.end_headers()
                return
            if parsed.path == "/api/health":
                health = {"status": "ok", "index_ok": False, "ollama_ok": False}
                try:
                    load_index()
                    health["index_ok"] = True
                except Exception as _health_err:
                    logger.debug("Health check: índice falhou: %s", _health_err)
                if (os.environ.get("PDFSEARCHABLE_AI") or "").strip().lower() == "ollama":
                    try:
                        health["ollama_ok"] = ollama_health_check()
                    except Exception as _ollama_err:
                        logger.debug("Health check: Ollama falhou: %s", _ollama_err)
                body = json.dumps(health).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._send_cors()
                self.end_headers()
                self.wfile.write(body)
                return
            # Servir PDFs de arquivos-processados (fora de .pdfsearchable)
            if parsed.path.startswith("/arquivos-processados/") and processed_dir.exists():
                suffix = parsed.path[len("/arquivos-processados/") :].lstrip("/")
                if suffix and ".." not in suffix and "/" not in suffix:
                    file_path = (processed_dir / suffix).resolve()
                    if file_path.is_relative_to(processed_dir.resolve()) and file_path.is_file():
                        try:
                            with open(file_path, "rb") as f:
                                data = f.read()
                            self.send_response(200)
                            self.send_header("Content-Type", "application/pdf")
                            self.send_header("Content-Length", str(len(data)))
                            self._send_cors()
                            self.end_headers()
                            self.wfile.write(data)
                            return
                        except OSError as _pdf_err:
                            logger.warning("Falha ao servir PDF %s: %s", file_path, _pdf_err)
                            self._send_json_error(500, "Erro ao ler arquivo PDF")
                            return
            # /api/text?id=<file_id> — texto extraído do documento (para copiar)
            if parsed.path == "/api/text":
                qs = urllib.parse.parse_qs(parsed.query)
                file_id = (qs.get("id") or [""])[0].strip()
                if not file_id or len(file_id) != 16 or not all(c in "0123456789abcdefABCDEF" for c in file_id):
                    self._send_json_error(400, "ID inválido")
                    return
                try:
                    text = load_file_text(file_id)
                except Exception as _txt_err:
                    logger.warning("Falha ao carregar texto de %s: %s", file_id, _txt_err)
                    text = ""
                if not text:
                    self._send_json_error(404, "Texto não encontrado")
                    return
                body = text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._send_cors()
                self.end_headers()
                self.wfile.write(body)
                return

            # /api/stats — agregados pré-calculados para o dashboard
            if parsed.path == "/api/stats":
                try:
                    from pdfsearchable.store import compute_dashboard_stats
                    stats = compute_dashboard_stats()
                    body = json.dumps(stats, ensure_ascii=False).encode("utf-8")
                    self._send_json(body)
                except Exception as exc:
                    logger.exception("API /api/stats: falha")
                    self._send_json_error(500, str(exc)[:80])
                return

            # /api/index — índice completo para bootstrap da SPA
            if parsed.path == "/api/index":
                try:
                    idx = load_index()
                    body = json.dumps(idx, ensure_ascii=False).encode("utf-8")
                    self._send_json(body)
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:80])
                return

            # /api/search?q=<query>[&type=<doc_type>] — busca FTS via API
            if parsed.path == "/api/search":
                qs = urllib.parse.parse_qs(parsed.query)
                query = (qs.get("q") or [""])[0].strip()
                type_filter = (qs.get("type") or [""])[0].strip()
                if not query:
                    self._send_json(json.dumps([]).encode())
                    return
                try:
                    results = fts_search(query, limit=20)
                    if type_filter:
                        idx = load_index()
                        type_map = {f.get("id", ""): f.get("doc_type", "") for f in idx.get("files", [])}
                        # fts_search returns tuples (file_id, page_num, snippet)
                        results = [r for r in results if type_map.get(r[0], "") == type_filter]
                    self._send_json(json.dumps(results, ensure_ascii=False).encode("utf-8"))
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:80])
                return

            # /api/page?id=<file_id>&page=<n> — texto de uma página específica
            if parsed.path == "/api/page":
                qs = urllib.parse.parse_qs(parsed.query)
                file_id = (qs.get("id") or [""])[0].strip()
                try:
                    page_n = max(1, int((qs.get("page") or ["1"])[0]))
                except (ValueError, TypeError):
                    self._send_json_error(400, "page deve ser um inteiro")
                    return
                if not file_id or len(file_id) != 16 or not all(c in "0123456789abcdefABCDEF" for c in file_id):
                    self._send_json_error(400, "id inválido")
                    return
                try:
                    from pdfsearchable.store import load_page_text
                    text = load_page_text(file_id, page_n)
                except Exception as _pg_err:
                    logger.debug("Falha ao carregar página %d de %s: %s", page_n, file_id, _pg_err)
                    text = ""
                body = json.dumps({"text": text or "", "page": page_n}, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._send_cors()
                self.end_headers()
                self.wfile.write(body)
                return

            # /api/annotations?id=<file_id> — lista anotações de um documento
            if parsed.path == "/api/annotations":
                qs = urllib.parse.parse_qs(parsed.query)
                file_id = (qs.get("id") or [""])[0].strip()
                if not file_id or len(file_id) != 16 or not all(c in "0123456789abcdefABCDEF" for c in file_id):
                    self._send_json_error(400, "id inválido")
                    return
                try:
                    from pdfsearchable.annotations import AnnotationStore
                    _ann_store = AnnotationStore(STORE_DIR)
                    anns = _ann_store.get(file_id)
                    body = json.dumps(anns, ensure_ascii=False).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self._send_cors()
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:80])
                return

            # /api/wordcloud — frequências de palavras para visualização
            if parsed.path == "/api/wordcloud":
                qs = urllib.parse.parse_qs(parsed.query)
                wc_type = (qs.get("type") or [None])[0]
                try:
                    wc_limit = max(10, min(500, int((qs.get("limit") or ["200"])[0])))
                except (ValueError, IndexError):
                    wc_limit = 200
                _PT_STOP = frozenset([
                    "de","da","do","das","dos","em","no","na","nos","nas","a","o","as","os",
                    "e","que","para","com","por","se","um","uma","uns","umas","ao","aos","à",
                    "às","ou","mas","como","ser","ter","foi","são","este","esta","estes","estas",
                    "esse","essa","esses","essas","ele","ela","eles","elas","seu","sua","seus",
                    "suas","meu","minha","neste","nesta","nesse","nessa","pelo","pela","pelos",
                    "pelas","mais","nem","já","ainda","sobre","entre","até","após","ante","muito",
                    "também","quando","onde","qual","quais","que","não","sim","pois","logo",
                    "então","aqui","ali","lá","há","era","está","estão","deve","tem","têm",
                    "todo","toda","todos","todas","cada","entre","desde","durante","após",
                    "mediante","conforme","sendo","tendo","podendo","referente","relativo",
                    "presente","contrato","acordo","mediante","estabelecido","nos","nas","nos",
                ])
                try:
                    import re as _re2
                    idx2 = load_index()
                    files2 = idx2.get("files", [])
                    if wc_type:
                        files2 = [f for f in files2 if (f.get("doc_type") or "") == wc_type]
                    freq: dict[str, int] = {}
                    for f2 in files2:
                        txt2 = load_file_text(f2.get("id", ""))
                        for w2 in _re2.findall(r'\b[a-záéíóúàâãêôçA-ZÁÉÍÓÚÀÂÃÊÔÇ]{3,}\b', txt2):
                            lw = w2.lower()
                            if lw not in _PT_STOP:
                                freq[lw] = freq.get(lw, 0) + 1
                    top = sorted(freq.items(), key=lambda x: -x[1])[:wc_limit]
                    body_wc = json.dumps({"words": [{"text": t, "weight": c} for t, c in top],
                                          "total_words": len(freq)}).encode()
                    self._send_json(body_wc)
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:80])
                return

            # /api/locations — locais citados nos documentos com geocodificação opcional
            if parsed.path == "/api/locations":
                qs2 = urllib.parse.parse_qs(parsed.query)
                do_geo = (qs2.get("geocode") or ["0"])[0] in ("1", "true", "yes")
                try:
                    idx3 = load_index()
                    loc_map: dict[str, list[dict]] = {}
                    for f3 in idx3.get("files", []):
                        locs3 = (
                            (f3.get("identified_locations") or [])
                            + (f3.get("identified_addresses") or [])
                        )
                        for loc3 in locs3:
                            if loc3 and len(str(loc3).strip()) >= 3:
                                key3 = str(loc3).strip()
                                loc_map.setdefault(key3, []).append(
                                    {"id": f3.get("id", ""), "name": f3.get("name", "")}
                                )
                    result_locs = []
                    for loc_name, loc_docs in sorted(loc_map.items(), key=lambda x: -len(x[1])):
                        entry: dict = {"name": loc_name, "lat": None, "lng": None,
                                       "doc_count": len(loc_docs), "docs": loc_docs[:10]}
                        if do_geo:
                            if loc_name in _geocode_cache:
                                entry["lat"], entry["lng"] = _geocode_cache[loc_name]
                            else:
                                try:
                                    import urllib.request as _ur2
                                    _geo_q = urllib.parse.urlencode(
                                        {"q": loc_name, "format": "json", "limit": "1"}
                                    )
                                    _geo_url = f"https://nominatim.openstreetmap.org/search?{_geo_q}"
                                    if not _geo_url.startswith("https://nominatim.openstreetmap.org/"):
                                        raise ValueError("URL de geocodificação inválida")
                                    _geo_req = _ur2.Request(
                                        _geo_url, headers={"User-Agent": f"pdfsearchable/{__version__}"}
                                    )
                                    with _ur2.urlopen(_geo_req, timeout=3) as _gr:  # nosec B310
                                        _gd = json.loads(_gr.read())
                                    if _gd:
                                        entry["lat"] = float(_gd[0]["lat"])
                                        entry["lng"] = float(_gd[0]["lon"])
                                    _geocode_cache[loc_name] = (entry["lat"], entry["lng"])
                                except Exception as _geo_err:
                                    logger.debug("Geocodificação falhou para '%s': %s", loc_name, _geo_err)
                                    _geocode_cache[loc_name] = (None, None)
                        result_locs.append(entry)
                    body_loc = json.dumps({"locations": result_locs,
                                           "total": len(result_locs)}).encode()
                    self._send_json(body_loc)
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:80])
                return

            # /api/graph — grafo de conhecimento (nodes + edges JSON)
            if parsed.path == "/api/graph":
                try:
                    from pdfsearchable.store import load_index as _li_g
                    from pdfsearchable.knowledge_graph import build_graph as _build_g
                    _idx_g = _li_g()
                    _files_g = _idx_g.get("files", [])
                    _graph = _build_g(_files_g)
                    _body_g = json.dumps(_graph).encode()
                    self._send_json(_body_g)
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:80])
                return

            # /api/timeline — linha do tempo dos documentos (lista JSON ordenada por data)
            if parsed.path == "/api/timeline":
                try:
                    import dataclasses as _dc
                    from pdfsearchable.store import load_index as _li_t
                    from pdfsearchable.timeline import build_timeline as _btl, timeline_stats as _tls
                    _idx_t = _li_t()
                    _files_t = _idx_t.get("files", [])
                    _raw_entries = _btl(_files_t)
                    _stats = _tls(_raw_entries)
                    _entries_json = [_dc.asdict(e) for e in _raw_entries]
                    _body_t = json.dumps({"entries": _entries_json, "stats": _stats}).encode()
                    self._send_json(_body_t)
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:80])
                return

            # /api/events — SSE: notifica clientes quando o índice muda (watch → report ao vivo)
            if parsed.path == "/api/events":
                import time as _t

                _SSE_MAX_SECONDS = 300  # 5 min — evita exaustão de threads
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-store")
                self.send_header("X-Accel-Buffering", "no")
                self._send_cors()
                self.end_headers()
                last_mtime: float = META_FILE.stat().st_mtime if META_FILE.exists() else 0.0
                _sse_start = _t.monotonic()
                try:
                    while (_t.monotonic() - _sse_start) < _SSE_MAX_SECONDS:
                        _t.sleep(3)
                        try:
                            cur_mtime = META_FILE.stat().st_mtime if META_FILE.exists() else 0.0
                        except OSError:
                            cur_mtime = last_mtime
                        if cur_mtime != last_mtime:
                            last_mtime = cur_mtime
                            try:
                                _idx = load_index()
                                _count = len(_idx.get("files", []))
                            except Exception as _sse_idx_err:
                                logger.debug("SSE: falha ao carregar índice: %s", _sse_idx_err)
                                _count = 0
                            payload = json.dumps({"event": "index_changed", "count": _count})
                            self.wfile.write(f"data: {payload}\n\n".encode())
                        else:
                            self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError) as _sse_err:
                    logger.debug("SSE: cliente desconectou: %s", type(_sse_err).__name__)
                return

            # /api/metrics — Prometheus text-exposition format
            if parsed.path == "/api/metrics":
                try:
                    from pdfsearchable.metrics import render_metrics
                    body = render_metrics().encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self._send_cors()
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:80])
                return

            # /api/health — health check separado de /api/status
            if parsed.path == "/api/health":
                try:
                    from pdfsearchable.metrics import health_status
                    h = health_status()
                    status_code = 200 if h["status"] != "down" else 503
                    body = json.dumps(h, ensure_ascii=False).encode("utf-8")
                    self.send_response(status_code)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self._send_cors()
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:80])
                return

            # /api/hybrid_search?q=<query>&top_k=10 — busca híbrida BM25+dense+RRF
            if parsed.path == "/api/hybrid_search":
                qs = urllib.parse.parse_qs(parsed.query)
                query = (qs.get("q") or [""])[0].strip()
                try:
                    top_k = max(1, min(100, int((qs.get("top_k") or ["10"])[0])))
                except (ValueError, TypeError):
                    top_k = 10
                if not query:
                    self._send_json(b"[]")
                    return
                try:
                    from pdfsearchable.hybrid_search import hybrid_search
                    results = hybrid_search(query, top_k=top_k)
                    body = json.dumps(results, ensure_ascii=False).encode("utf-8")
                    self._send_json(body)
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:120])
                return

            # /api/profile?path=... — perfil estrutural de um PDF (debug)
            if parsed.path == "/api/profile":
                qs = urllib.parse.parse_qs(parsed.query)
                path_q = (qs.get("path") or [""])[0].strip()
                if not path_q:
                    self._send_json_error(400, "parâmetro 'path' obrigatório")
                    return
                try:
                    from pdfsearchable.pdf_profiler import profile_pdf, recommend_pipeline
                    pr = profile_pdf(path_q)
                    pr["recommendation"] = recommend_pipeline(pr)
                    body = json.dumps(pr, ensure_ascii=False).encode("utf-8")
                    self._send_json(body)
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:120])
                return

            # /api/duplicates — near-duplicates MinHash
            if parsed.path == "/api/duplicates":
                try:
                    from pdfsearchable.dedup import scan_store_for_near_duplicates
                    qs = urllib.parse.parse_qs(parsed.query)
                    try:
                        thr = float((qs.get("threshold") or ["0.8"])[0])
                    except (ValueError, TypeError):
                        thr = 0.8
                    pairs = scan_store_for_near_duplicates(threshold=thr)
                    body = json.dumps(pairs, ensure_ascii=False).encode("utf-8")
                    self._send_json(body)
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:120])
                return

            # /api/diff?a=<id>&b=<id> — diff entre duas versões
            if parsed.path == "/api/diff":
                qs = urllib.parse.parse_qs(parsed.query)
                a = (qs.get("a") or [""])[0].strip()
                b = (qs.get("b") or [""])[0].strip()
                if not a or not b:
                    self._send_json_error(400, "parâmetros a,b obrigatórios")
                    return
                try:
                    from pdfsearchable.doc_diff import diff_documents
                    d = diff_documents(a, b)
                    body = json.dumps(d, ensure_ascii=False).encode("utf-8")
                    self._send_json(body)
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:120])
                return

            # /api/saved_searches — list
            if parsed.path == "/api/saved_searches":
                try:
                    from pdfsearchable.saved_searches import list_saved_searches
                    body = json.dumps(list_saved_searches(), ensure_ascii=False).encode("utf-8")
                    self._send_json(body)
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:120])
                return

            # /api/tombstones — lista tombstones pendentes
            if parsed.path == "/api/tombstones":
                try:
                    from pdfsearchable.tombstone import tombstone_list
                    body = json.dumps(tombstone_list(), ensure_ascii=False).encode("utf-8")
                    self._send_json(body)
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:120])
                return

            if parsed.path.startswith("/api/"):
                self._send_json_error(404, "Endpoint não encontrado")
                return
            return super().do_GET()

        def do_POST(self) -> None:  # type: ignore[override]
            if not self._check_auth():
                self._send_401()
                return
            if not self._check_csrf():
                self._send_json_error(403, "Origem não autorizada (CSRF)")
                return
            import time as _time

            parsed = urllib.parse.urlparse(self.path)

            # /api/meta/update — actualizar metadados de um documento (tipo, tags, subject)
            if parsed.path == "/api/meta/update":
                _MAX_BODY = 1 * 1024 * 1024  # 1 MB
                try:
                    length = min(_MAX_BODY, max(0, int(self.headers.get("Content-Length", "0"))))
                except ValueError:
                    length = 0
                raw = self.rfile.read(length) if length > 0 else b""
                try:
                    data = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    self._send_json_error(400, "JSON inválido")
                    return
                file_id = data.get("id", "").strip()
                if not file_id or len(file_id) != 16 or not all(c in "0123456789abcdefABCDEF" for c in file_id):
                    self._send_json_error(400, "id inválido")
                    return
                changed = False
                if "doc_type" in data:
                    changed = update_doc_type(file_id, data["doc_type"], source="spa") or changed
                if "tags" in data and isinstance(data["tags"], list):
                    changed = update_file_tags(file_id, data["tags"]) or changed
                if "subject" in data:
                    changed = update_file_subject(file_id, data["subject"]) or changed
                body = json.dumps({"ok": changed}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._send_cors()
                self.end_headers()
                self.wfile.write(body)
                return

            # /api/annotations — adicionar anotação
            if parsed.path == "/api/annotations":
                _MAX_BODY = 1 * 1024 * 1024  # 1 MB
                try:
                    length = min(_MAX_BODY, max(0, int(self.headers.get("Content-Length", "0"))))
                except ValueError:
                    length = 0
                raw = self.rfile.read(length) if length > 0 else b""
                try:
                    data = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    self._send_json_error(400, "JSON inválido")
                    return
                file_id = data.pop("file_id", "").strip()
                if not file_id or len(file_id) != 16 or not all(c in "0123456789abcdefABCDEF" for c in file_id):
                    self._send_json_error(400, "file_id inválido")
                    return
                try:
                    from pdfsearchable.annotations import AnnotationStore
                    _ann_store = AnnotationStore(STORE_DIR)
                    ann_id = _ann_store.add(file_id, data)
                    body = json.dumps({"id": ann_id}).encode()
                    self.send_response(201)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self._send_cors()
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as exc:
                    self._send_json_error(400, str(exc)[:80])
                return

            if parsed.path != "/api/ask":
                self._send_json_error(404, "Not Found")
                return
            # Rate limit (thread-safe: ThreadingHTTPServer usa threads por requisição)
            if ask_rate_limit > 0:
                now = _time.time()
                with _ask_request_times_lock:
                    _ask_request_times.append(now)
                    # manter só últimos 60s
                    while _ask_request_times and now - _ask_request_times[0] > 60:
                        _ask_request_times.pop(0)
                    too_many = len(_ask_request_times) > ask_rate_limit
                if too_many:
                    self.send_response(429)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self._send_cors()
                    self.end_headers()
                    self.wfile.write(
                        json.dumps(
                            {"error": "Muitas requisições. Tente novamente em breve."}
                        ).encode("utf-8")
                    )
                    return
            _MAX_ASK_BODY = 64 * 1024  # 64 KB — question + id (generous limit)
            try:
                length = min(_MAX_ASK_BODY, max(0, int(self.headers.get("Content-Length", "0"))))
            except ValueError:
                length = 0
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send_json_error(400, "JSON inválido")
                return
            file_id = (payload.get("id") or "").strip()
            question = (payload.get("question") or "").strip()
            if not file_id or len(file_id) != 16 or not all(c in "0123456789abcdefABCDEF" for c in file_id):
                self._send_json_error(400, "id inválido")
                return
            if not question:
                self._send_json_error(400, "Campo 'question' é obrigatório.")
                return

            if (os.environ.get("PDFSEARCHABLE_AI") or "").strip().lower() != "ollama":
                _body = json.dumps({"error": "PDFSEARCHABLE_AI=ollama é obrigatório para /api/ask."}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(_body)))
                self._send_cors()
                self.end_headers()
                self.wfile.write(_body)
                return
            if not ollama_health_check():
                _body = json.dumps({"error": "Ollama não está acessível."}).encode("utf-8")
                self.send_response(503)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(_body)))
                self._send_cors()
                self.end_headers()
                self.wfile.write(_body)
                return

            try:
                text = load_file_text(file_id)
            except StoreError as e:
                logger.warning(
                    "API /api/ask: StoreError ao ler documento %s: %s (details: %s)",
                    file_id,
                    e.message,
                    e.details,
                )
                self._send_json_error(500, "Erro ao ler o documento (índice ou arquivo inacessível).")
                return
            except Exception:
                logger.exception("API /api/ask: falha ao ler documento %s", file_id)
                self._send_json_error(500, "Erro ao ler o documento (índice ou arquivo inacessível).")
                return
            if not text:
                self._send_json_error(404, "Documento não encontrado ou sem texto.")
                return
            try:
                answer = ask_document_ollama(text, question, timeout=_ask_timeout)
            except Exception as e:
                logger.exception("API /api/ask: falha ao chamar Ollama: %s", e)
                self._send_json_error(502, "Erro interno ao chamar Ollama.")
                return
            if not answer:
                self._send_json_error(502, "Falha ao obter resposta do Ollama.")
                return
            resp = {"answer": answer}
            data = json.dumps(resp).encode("utf-8")
            audit("api_ask", {"file_id": file_id, "question": question[:120]})
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self._send_cors()
            self.end_headers()
            self.wfile.write(data)

            # ===== Novos endpoints POST =====

            # /api/saved_searches/save — { name, query, options? }
            if parsed.path == "/api/saved_searches/save":
                try:
                    length = min(64 * 1024, max(0, int(self.headers.get("Content-Length", "0"))))
                    body_raw = self.rfile.read(length)
                    payload = json.loads(body_raw.decode("utf-8") or "{}")
                    name = (payload.get("name") or "").strip()
                    query = (payload.get("query") or "").strip()
                    if not name or not query:
                        self._send_json_error(400, "name e query obrigatórios")
                        return
                    from pdfsearchable.saved_searches import save_search
                    entry = save_search(name, query, options=payload.get("options"))
                    self._send_json(json.dumps(entry).encode("utf-8"))
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:120])
                return

            # /api/saved_searches/run — { name }
            if parsed.path == "/api/saved_searches/run":
                try:
                    length = min(4096, max(0, int(self.headers.get("Content-Length", "0"))))
                    payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                    name = (payload.get("name") or "").strip()
                    from pdfsearchable.saved_searches import run_saved_search
                    r = run_saved_search(name)
                    self._send_json(json.dumps(r, ensure_ascii=False).encode("utf-8"))
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:120])
                return

            # /api/saved_searches/delete — { name }
            if parsed.path == "/api/saved_searches/delete":
                try:
                    length = min(4096, max(0, int(self.headers.get("Content-Length", "0"))))
                    payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                    name = (payload.get("name") or "").strip()
                    from pdfsearchable.saved_searches import delete_saved_search
                    ok = delete_saved_search(name)
                    self._send_json(json.dumps({"ok": ok}).encode("utf-8"))
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:120])
                return

            # /api/tombstones/restore — { file_id }
            if parsed.path == "/api/tombstones/restore":
                try:
                    length = min(4096, max(0, int(self.headers.get("Content-Length", "0"))))
                    payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                    fid = (payload.get("file_id") or "").strip()
                    from pdfsearchable.tombstone import tombstone_restore
                    data = tombstone_restore(fid)
                    if data is None:
                        self._send_json_error(404, "tombstone não encontrado")
                        return
                    self._send_json(json.dumps(data, ensure_ascii=False).encode("utf-8"))
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:120])
                return

            # /api/dossier/generate — { query, results, title? } → retorna path do PDF
            if parsed.path == "/api/dossier/generate":
                try:
                    length = min(1 * 1024 * 1024, max(0, int(self.headers.get("Content-Length", "0"))))
                    payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                    results = payload.get("results") or []
                    query = payload.get("query") or ""
                    title = payload.get("title") or "Dossiê de Resultados"
                    import time as _time

                    from pdfsearchable.dossier import generate_dossier
                    out_path = STORE_DIR / f"dossier_{int(_time.time())}.pdf"
                    generate_dossier(results, out_path, title=title, query=query)
                    self._send_json(json.dumps({
                        "path": str(out_path),
                        "size": out_path.stat().st_size,
                    }).encode("utf-8"))
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:120])
                return

            # /api/classifier/feedback — { file_id, correct_label }
            if parsed.path == "/api/classifier/feedback":
                try:
                    length = min(4096, max(0, int(self.headers.get("Content-Length", "0"))))
                    payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                    fid = (payload.get("file_id") or "").strip()
                    label = (payload.get("correct_label") or "").strip()
                    if not fid or not label:
                        self._send_json_error(400, "file_id e correct_label obrigatórios")
                        return
                    try:
                        from pdfsearchable.classifier_feedback import record_correction
                        from pdfsearchable.store import read_page_text
                        snippet = ""
                        try:
                            snippet = (read_page_text(fid, 1) or "")[:500]
                        except Exception:
                            pass
                        record_correction(fid, label, snippet, source="ui")
                    except Exception:
                        pass
                    self._send_json(json.dumps({"ok": True}).encode("utf-8"))
                except Exception as exc:
                    self._send_json_error(500, str(exc)[:120])
                return

    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        console.print(f"[red]Não foi possível iniciar o servidor em {host}:{port}: {e}[/]")
        raise click.Abort() from None

    audit("serve_start", {"host": host, "port": port})
    app_url = f"http://{host}:{port}/app.html"
    auth_hint = (
        "\n[dim]Auth: [yellow]PDFSEARCHABLE_AUTH_TOKEN[/] configurado.[/]"
        if _auth_token
        else ""
    )
    console.print(
        Panel(
            f"[bold]App:[/]       [cyan]{app_url}[/]\n"
            f"[bold]API:[/]       [cyan]http://{host}:{port}/api/index[/]  ·  /api/search  ·  /api/ask\n\n"
            "[dim]Pressione Ctrl+C para parar o servidor.[/]" + auth_hint,
            title="[bold green]Servidor iniciado[/]",
            border_style="green",
        )
    )
    if open_browser:
        import webbrowser
        webbrowser.open(app_url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]Servidor encerrado.[/]")


@main.command()
def report() -> None:
    """
    Gera o report estático (report.html) sem iniciar o servidor.

    Use [cyan]pdfsearchable serve[/] para a interface interativa completa (SPA).
    """
    try:
        from pdfsearchable.report import generate_report
        generate_report()
    except (StoreError, ReportError) as e:
        logger.exception("Erro ao gerar report")
        console.print(f"[red]{e.message}[/]")
        console.print(LOG_HINT)
        raise click.Abort() from None
    except Exception as e:
        logger.exception("Erro inesperado ao gerar report")
        console.print(f"[red]Erro inesperado: {e}[/]")
        console.print(LOG_HINT)
        raise click.Abort() from None
    report_file = Path.cwd() / ".pdfsearchable" / "report.html"
    console.print(
        Panel(
            f"[green]✓[/] Report gerado: [cyan]{report_file}[/]\n\n"
            "[dim]Para interface interativa: [cyan]pdfsearchable serve[/][/]",
            title="[bold green]Report gerado[/]",
            border_style="green",
        )
    )
    audit("cli_report", {"path": str(report_file)})


@main.command()
@click.option("--benchmark", is_flag=True, default=False,
              help="Executa benchmarks sintéticos de indexação, FTS e MinHash.")
def doctor(benchmark: bool) -> None:
    """
    Verifica dependências, configuração e estado do projeto.

    Inclui: Python, PyMuPDF, Tesseract, Ollama, HTR backend, índice, FTS,
    armazenamento, arquivo de config, espaço em disco e tamanho do store.
    Com --benchmark, também executa cargas sintéticas e reporta latências.
    """
    import shutil
    import subprocess
    import sqlite3 as _sq
    import sys as _sys
    import time as _time

    ok = True
    table = Table(title="🩺 Diagnóstico")
    table.add_column("Componente", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Detalhe", style="dim")

    # Python
    py_ver = _sys.version.split()[0]
    py_color = "green" if tuple(int(x) for x in py_ver.split(".")[:2]) >= (3, 10) else "yellow"
    table.add_row("Python", f"[{py_color}]{py_ver}[/]", _sys.executable)

    # PyMuPDF
    try:
        import fitz as _fitz
        ver = getattr(_fitz, "version", ("?",))[0]
        table.add_row("PyMuPDF", "[green]OK[/]", f"v{ver}")
    except ImportError:
        table.add_row("PyMuPDF", "[red]FALTA[/]", "pip install pymupdf")
        ok = False

    # Tesseract
    tess_path = shutil.which("tesseract")
    if tess_path:
        try:
            res = subprocess.run(
                ["tesseract", "--version"], capture_output=True, text=True, timeout=5  # noqa: S607
            )
            out = res.stdout or res.stderr or ""
            ver_line = out.splitlines()[0] if out.strip() else tess_path
            table.add_row("Tesseract", "[green]OK[/]", ver_line.strip())
        except Exception:
            table.add_row("Tesseract", "[yellow]INSTALADO[/]", tess_path)
    else:
        table.add_row(
            "Tesseract",
            "[yellow]NÃO ENCONTRADO[/]",
            "brew install tesseract  /  apt install tesseract-ocr",
        )

    # HTR backend
    htr_raw = (os.environ.get("PDFSEARCHABLE_HTR") or "").strip().lower()
    if htr_raw in ("0", "false", "no"):
        table.add_row("HTR", "[dim]desativado[/]", "PDFSEARCHABLE_HTR=0")
    else:
        try:
            from pdfsearchable.htr import get_htr_backend, htr_available as _htr_avail, list_supported_languages

            backend = get_htr_backend()
            if _htr_avail():
                langs = list_supported_languages()
                dedicated = [k for k, v in langs.items() if k != "printed" and "fallback" not in v.lower() and "Latin" not in v]
                lang_info = f"backend={backend} · idiomas com modelo dedicado: {', '.join(sorted(dedicated))}"
                table.add_row("HTR", "[green]OK[/]", lang_info)
            else:
                hints = {
                    "trocr": "pip install pdfsearchable[htr]",
                    "transkribus": "configure PDFSEARCHABLE_TRANSKRIBUS_USER/PW/MODEL_ID",
                    "escriptorium": "configure PDFSEARCHABLE_ESCRIPTORIUM_URL/TOKEN/MODEL",
                }
                table.add_row(
                    "HTR",
                    "[yellow]NÃO DISPONÍVEL[/]",
                    f"backend={backend} · {hints.get(backend, '')}",
                )
        except Exception as _e:
            table.add_row("HTR", "[dim]?[/]", str(_e)[:60])

    # pymupdf_layout — melhoria opcional de análise de layout
    try:
        import importlib.util as _ilu
        if _ilu.find_spec("pymupdf.layout") is not None or _ilu.find_spec("pymupdf_layout") is not None:
            table.add_row("pymupdf_layout", "[green]instalado[/]", "layout aprimorado activo")
        else:
            table.add_row(
                "pymupdf_layout",
                "[dim]opcional[/]",
                "melhora análise de layout multicoluna · pip install pymupdf_layout",
            )
    except Exception:
        pass

    # Ollama
    ai_mode = (os.environ.get("PDFSEARCHABLE_AI") or "").strip().lower()
    if ai_mode == "ollama":
        try:
            from pdfsearchable.content_extractors import ollama_health_check as _ollama_hc
            if _ollama_hc():
                model = (os.environ.get("PDFSEARCHABLE_OLLAMA_MODEL") or "llama3.2").strip()
                table.add_row("Ollama", "[green]OK[/]", f"acessível · modelo={model}")
            else:
                table.add_row("Ollama", "[red]INACESSÍVEL[/]", "inicie com: ollama serve")
                ok = False
        except Exception:
            table.add_row("Ollama", "[red]ERRO[/]", "falha ao verificar")
            ok = False
    else:
        table.add_row(
            "Ollama",
            "[dim]desativado[/]",
            f"PDFSEARCHABLE_AI={ai_mode or 'não definido (use ollama para activar)'}",
        )

    # Armazenamento
    if STORE_DIR.exists():
        try:
            store_bytes = sum(
                f.stat().st_size for f in STORE_DIR.rglob("*") if f.is_file()
            )
            store_mb = store_bytes / (1024 * 1024)
            table.add_row(
                "Armazenamento (.pdfsearchable)",
                "[green]OK[/]",
                f"{store_mb:.1f} MB · {STORE_DIR}",
            )
        except Exception:
            table.add_row("Armazenamento (.pdfsearchable)", "[green]OK[/]", str(STORE_DIR))
    else:
        table.add_row(
            "Armazenamento (.pdfsearchable)",
            "[yellow]NÃO INICIALIZADO[/]",
            "use: pdfsearchable init",
        )

    # Índice JSON
    try:
        idx = load_index()
        n = len(idx.get("files", []))
        schema = idx.get("version", "?")
        table.add_row("Índice JSON", "[green]OK[/]", f"{n} documento(s) · schema v{schema}")
    except Exception as exc:
        table.add_row("Índice JSON", "[red]ERRO[/]", str(exc)[:60])
        ok = False

    # FTS SQLite
    fts_db = STORE_DIR / "fts.sqlite"
    if fts_db.exists():
        try:
            with _sq.connect(str(fts_db), timeout=5) as _conn:
                row = _conn.execute(
                    "SELECT COUNT(*) FROM fts_idx"
                ).fetchone()
                fts_n = row[0] if row else 0
            fts_size_kb = fts_db.stat().st_size // 1024
            table.add_row("FTS SQLite", "[green]OK[/]", f"{fts_n} entrada(s) · {fts_size_kb} KB")
        except Exception as _fe:
            table.add_row("FTS SQLite", "[yellow]ERRO[/]", str(_fe)[:60])
    else:
        table.add_row(
            "FTS SQLite",
            "[dim]não criado[/]",
            "criado ao indexar o primeiro documento",
        )

    # Config file
    config_toml = Path.cwd() / ".pdfsearchable" / "config.toml"
    config_json = Path.cwd() / ".pdfsearchable" / "config.json"
    if config_toml.exists():
        table.add_row("Config", "[green]config.toml[/]", str(config_toml))
    elif config_json.exists():
        table.add_row("Config", "[green]config.json[/]", str(config_json))
    else:
        table.add_row("Config", "[dim]não encontrado[/]", "configuração via env vars apenas")

    # Espaço em disco
    try:
        stat = shutil.disk_usage(STORE_DIR if STORE_DIR.exists() else Path.cwd())
        free_gb = stat.free / (1024**3)
        total_gb = stat.total / (1024**3)
        color = "green" if free_gb >= 1.0 else "yellow"
        table.add_row(
            "Disco livre",
            f"[{color}]{free_gb:.1f} GB[/]",
            f"de {total_gb:.0f} GB · {Path.cwd()}",
        )
    except Exception:
        table.add_row("Disco livre", "[dim]?[/]", "não foi possível verificar")

    console.print(table)
    if ok:
        console.print("[green]✓ Tudo pronto.[/]")
    else:
        console.print(
            "[yellow]Algumas verificações falharam.[/] Consulte os detalhes acima e "
            f"o log em [cyan]{STORE_DIR / 'pdfsearchable.log'}[/]."
        )

    if benchmark:
        console.print("\n[bold cyan]🏁 Benchmarks sintéticos[/]")
        bench_table = Table(show_header=True)
        bench_table.add_column("Operação", style="cyan")
        bench_table.add_column("Latência", style="bold")
        bench_table.add_column("Detalhe", style="dim")

        # 1. Criar PDF sintético
        import tempfile as _tmp
        import fitz as _fb
        with _tmp.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            tmp_pdf = Path(f.name)
        try:
            _d = _fb.open()
            for _i in range(5):
                _pg = _d.new_page()
                _pg.insert_textbox(
                    _fb.Rect(72, 72, 540, 770),
                    "Texto de benchmark " * 80,
                    fontsize=11,
                )
            t0 = _time.perf_counter()
            _d.save(str(tmp_pdf))
            _d.close()
            bench_table.add_row(
                "Criar PDF (5 páginas)",
                f"{(_time.perf_counter()-t0)*1000:.1f} ms",
                f"{tmp_pdf.stat().st_size//1024} KB",
            )

            # 2. Extração PyMuPDF
            t0 = _time.perf_counter()
            _d2 = _fb.open(str(tmp_pdf))
            total_chars = sum(len(p.get_text() or "") for p in _d2)
            _d2.close()
            bench_table.add_row(
                "Extrair texto (5 páginas)",
                f"{(_time.perf_counter()-t0)*1000:.1f} ms",
                f"{total_chars} chars",
            )

            # 3. PDF profiler
            t0 = _time.perf_counter()
            try:
                from pdfsearchable.pdf_profiler import profile_pdf
                _pr = profile_pdf(tmp_pdf)
                bench_table.add_row(
                    "PDF profiler",
                    f"{(_time.perf_counter()-t0)*1000:.1f} ms",
                    f"kind={_pr.get('kind')}",
                )
            except Exception as _e:
                bench_table.add_row("PDF profiler", "[red]ERRO[/]", str(_e)[:40])

            # 4. FTS search (se já existir índice)
            try:
                from pdfsearchable.store import fts_search as _fs
                t0 = _time.perf_counter()
                _r = _fs("teste", limit=20)
                bench_table.add_row(
                    "FTS search",
                    f"{(_time.perf_counter()-t0)*1000:.1f} ms",
                    f"{len(_r or [])} hits",
                )
            except Exception as _e:
                bench_table.add_row("FTS search", "[yellow]N/D[/]", str(_e)[:40])

            # 5. MinHash (1000 shingles ~ doc médio)
            try:
                from pdfsearchable.dedup import minhash as _mh
                sample = "palavra diversa contexto " * 200
                t0 = _time.perf_counter()
                _sig = _mh(sample)
                bench_table.add_row(
                    "MinHash 128 perm",
                    f"{(_time.perf_counter()-t0)*1000:.1f} ms",
                    f"{len(_sig)} hashes",
                )
            except Exception as _e:
                bench_table.add_row("MinHash", "[red]ERRO[/]", str(_e)[:40])

            # 6. Render metrics
            try:
                from pdfsearchable.metrics import render_metrics as _rm
                t0 = _time.perf_counter()
                _out = _rm()
                bench_table.add_row(
                    "render_metrics",
                    f"{(_time.perf_counter()-t0)*1000:.2f} ms",
                    f"{len(_out)} bytes",
                )
            except Exception as _e:
                bench_table.add_row("render_metrics", "[red]ERRO[/]", str(_e)[:40])

            # 7. Dashboard stats (cached)
            try:
                from pdfsearchable.store import compute_dashboard_stats as _cds
                t0 = _time.perf_counter()
                _s = _cds()
                d1 = (_time.perf_counter() - t0) * 1000
                t0 = _time.perf_counter()
                _s = _cds()
                d2 = (_time.perf_counter() - t0) * 1000
                bench_table.add_row(
                    "Dashboard stats",
                    f"{d1:.1f} → {d2:.2f} ms",
                    "1ª vs cache",
                )
            except Exception as _e:
                bench_table.add_row("Dashboard stats", "[yellow]N/D[/]", str(_e)[:40])

        finally:
            try:
                tmp_pdf.unlink()
            except Exception:
                pass

        console.print(bench_table)


def _fts_quote_term(term: str) -> str:
    """Escapa um termo para query FTS5: frases com espaço entre aspas."""
    t = (term or "").strip()
    if not t:
        return ""
    if " " in t or '"' in t:
        return '"' + t.replace('"', '""') + '"'
    return t


def _filter_files_by_filters(
    files: list[dict],
    doc_type: str | None,
    language: str | None,
    date_from: str | None,
    date_to: str | None,
) -> list[dict]:
    """Filtra lista de arquivos por tipo, idioma e intervalo de datas (indexação)."""
    out = list(files)
    if doc_type:
        doc_type_lower = doc_type.strip().lower()
        out = [f for f in out if (f.get("doc_type") or "documento").lower() == doc_type_lower]
    if language:
        lang_lower = language.strip().lower()
        out = [f for f in out if (f.get("language") or "").lower() == lang_lower]
    if date_from:
        date_from = date_from.strip()[:10]  # YYYY-MM-DD
        out = [
            f for f in out if (f.get("indexed_at") or f.get("updated_at") or "")[:10] >= date_from
        ]
    if date_to:
        date_to = date_to.strip()[:10]
        out = [f for f in out if (f.get("indexed_at") or f.get("updated_at") or "")[:10] <= date_to]
    return out


@main.command(
    epilog=(
        "Exemplos:\n\n"
        "  pdfsearchable search contrato\n\n"
        "  pdfsearchable search \"nota fiscal\" --type nota_fiscal\n\n"
        "  pdfsearchable search cpf --date-from 2024-01-01\n\n"
        "  pdfsearchable search empresa --ollama"
    ),
)
@click.argument("query", required=True)
@click.option(
    "--open/--no-open",
    "open_report",
    default=True,
    help="Mostrar mensagem para ver o report via serve (padrão: sim).",
)
@click.option(
    "--ollama/--no-ollama",
    "use_ollama",
    default=False,
    help="Expandir consulta com Ollama. Padrão: desativado.",
)
@click.option(
    "--semantic",
    "use_semantic",
    is_flag=True,
    default=False,
    help="Busca semântica por embeddings (requer pdfsearchable embed primeiro).",
)
@click.option(
    "--type",
    "doc_type_filter",
    default=None,
    help="Filtrar por tipo de documento (ex.: contrato, nota_fiscal).",
)
@click.option(
    "--language", "language_filter", default=None, help="Filtrar por idioma (ex.: pt-BR, en)."
)
@click.option(
    "--date-from", "date_from", default=None, help="Data de indexação a partir de (YYYY-MM-DD)."
)
@click.option("--date-to", "date_to", default=None, help="Data de indexação até (YYYY-MM-DD).")
def search(
    query: str,
    open_report: bool,
    use_ollama: bool | None,
    use_semantic: bool,
    doc_type_filter: str | None,
    language_filter: str | None,
    date_from: str | None,
    date_to: str | None,
) -> None:
    """
    Pesquisa um termo em todos os documentos indexados.

    Usa FTS e máscaras (IP, CPF, CNPJ, e-mail, URLs, redes sociais).
    Use --type e --language para filtrar; --ollama para expandir com IA;
    --semantic para busca por similaridade semântica (requer [cyan]pdfsearchable embed[/]).
    """
    # --- Busca semântica ---
    if use_semantic:
        _semantic_search(query, doc_type_filter, language_filter, date_from, date_to)
        return
    try:
        idx = load_index()
    except Exception as e:
        _abort_index_error(e)
    files = idx.get("files", [])
    if not files:
        console.print(
            "[yellow]Nenhum documento indexado. Use [bold]pdfsearchable add[/] primeiro.[/]"
        )
        return

    files = _filter_files_by_filters(files, doc_type_filter, language_filter, date_from, date_to)
    if not files:
        console.print(
            "[yellow]Nenhum documento corresponde aos filtros (--type, --language, --date-from, --date-to).[/]"
        )
        return

    id_to_name = {f.get("id"): f.get("name", "?") for f in files}
    allowed_ids = {f.get("id") for f in files}
    total_docs = len(files)

    # Expansão com Ollama (--ollama ativa)
    fts_query = query.strip()
    expanded_terms: list[str] = []
    if use_ollama and fts_query:
        expanded_terms = expand_search_query_ollama(fts_query, max_terms=5)
        if expanded_terms:
            parts = [_fts_quote_term(fts_query)] + [_fts_quote_term(t) for t in expanded_terms]
            fts_query = " OR ".join(parts)
            console.print("[dim]🤖 Expansão Ollama: " + ", ".join(expanded_terms) + "[/]")

    # FTS primeiro (retorna file_id, page_num, snippet); aplicar filtros
    fts_hits_raw = fts_search(fts_query, limit=200)
    # Verificar se o FTS falhou (índice corrompido ou não reconstruível)
    _fts_err = fts_last_error()
    if _fts_err and not fts_hits_raw:
        console.print(
            Panel(
                f"[yellow]⚠ O índice de busca teve um problema:[/]\n"
                f"  {_fts_err}\n\n"
                "[dim]A pesquisa vai prosseguir usando busca de texto simples (mais lenta).[/]",
                title="[bold yellow]Índice de busca indisponível[/]",
                border_style="yellow",
            )
        )
    fts_hits = [(fid, pnum, snip) for fid, pnum, snip in fts_hits_raw if fid in allowed_ids][:50]
    if fts_hits:
        audit(
            "cli_search",
            {
                "query": query.strip(),
                "hits": len(fts_hits),
                "engine": "fts",
                "ollama_expanded": bool(expanded_terms),
            },
        )
        table = Table(title=f"🔍 Resultados para “{query}” (por página)")
        table.add_column("Arquivo", style="cyan")
        table.add_column("Pág.", justify="right", style="dim")
        table.add_column("Trecho", style="white", max_width=60)
        for fid, pnum, snippet in fts_hits:
            table.add_row(id_to_name.get(fid, fid), str(pnum), (snippet or "").strip())
        console.print(table)
        if len(fts_hits) > 50:
            console.print(f"[dim]… e mais {len(fts_hits) - 50} resultado(s).[/]")
        # Resumo executivo
        unique_files = len({fid for fid, _, _ in fts_hits})
        console.print(
            Panel(
                f"[bold]Consulta:[/] “{query}”\n"
                f"[bold]Total de ocorrências:[/] {len(fts_hits)}\n"
                f"[bold]Documentos encontrados:[/] {unique_files} de {total_docs}\n"
                f"[bold]Resumo:[/] O termo foi encontrado em {unique_files} documento(s), "
                f"em {len(fts_hits)} trecho(s) no total.",
                title="📋 Resumo executivo",
                border_style="green",
            )
        )
        if open_report:
            console.print(
                "[dim]Explore no navegador: [cyan]pdfsearchable serve[/] → http://127.0.0.1:8000/app.html[/]"
            )
        return

    hits = []
    for f in files:  # já filtrado por tipo/idioma/datas
        fid = f.get("id")
        text = load_file_text(fid)
        name = f.get("name", "?")
        for mask_type, start, end, matched in search_with_masks(query, text, use_masks=True):
            snippet_start = max(0, start - 50)
            snippet_end = min(len(text), end + 50)
            snippet = text[snippet_start:snippet_end]
            if snippet_start > 0:
                snippet = "…" + snippet
            if snippet_end < len(text):
                snippet = snippet + "…"
            hits.append(
                {"file": name, "mask_type": mask_type, "matched": matched, "snippet": snippet}
            )

    if not hits:
        console.print(f"[yellow]Nenhum resultado para “[bold]{query}[/]”.[/]")
        console.print(
            "[dim]Tente outro termo ou use [cyan]pdfsearchable serve[/] para explorar os documentos no report.[/]"
        )
        if open_report:
            console.print(
                "[dim]Explore no navegador: [cyan]pdfsearchable serve[/] → http://127.0.0.1:8000/app.html[/]"
            )
        return

    audit("cli_search", {"query": query, "hits": len(hits)})
    table = Table(title=f"🔍 Resultados para “{query}”")
    table.add_column("Arquivo", style="cyan")
    table.add_column("Tipo", style="dim")
    table.add_column("Trecho", style="white", max_width=60)
    for h in hits[:50]:
        table.add_row(h["file"], h["mask_type"], h["snippet"].strip())
    console.print(table)
    if len(hits) > 50:
        console.print(f"[dim]… e mais {len(hits) - 50} resultado(s).[/]")

    # Resumo executivo
    unique_files = len({h["file"] for h in hits})
    console.print(
        Panel(
            f"[bold]Consulta:[/] “{query}”\n"
            f"[bold]Total de ocorrências:[/] {len(hits)}\n"
            f"[bold]Documentos encontrados:[/] {unique_files} de {total_docs}\n"
            f"[bold]Resumo:[/] O termo foi encontrado em {unique_files} documento(s), "
            f"em {len(hits)} trecho(s) no total.",
            title="📋 Resumo executivo",
            border_style="green",
        )
    )
    if open_report:
        console.print(
            "[dim]Explore no navegador: [cyan]pdfsearchable serve[/] → http://127.0.0.1:8000/app.html[/]"
        )


@main.command()
@click.argument("file_id_or_name", required=True)
@click.argument("question", required=True)
def ask(file_id_or_name: str, question: str) -> None:
    """
    Pergunta sobre um documento usando IA (Ollama). RAG: usa o texto do documento como contexto.

    Requer PDFSEARCHABLE_AI=ollama e Ollama em execução.
    Exemplo: pdfsearchable ask 000ea928c570e56d "Quem são os imigrantes listados?"
    """
    from pdfsearchable.content_extractors import ollama_health_check

    if (os.environ.get("PDFSEARCHABLE_AI") or "").strip().lower() != "ollama":
        console.print("[yellow]O comando [bold]ask[/] requer PDFSEARCHABLE_AI=ollama.[/]")
        console.print("[dim]Defina a variável e tenha o Ollama em execução.[/]")
        raise click.Abort() from None
    if not ollama_health_check():
        console.print("[red]Ollama não está acessível.[/] Inicie com [cyan]ollama serve[/].")
        raise click.Abort() from None

    try:
        idx = load_index()
    except Exception as e:
        _abort_index_error(e)
    files = idx.get("files", [])
    if not files:
        console.print("[yellow]Nenhum documento indexado.[/]")
        raise click.Abort() from None

    fid = None
    arg = (file_id_or_name or "").strip()
    if re.match(r"^[a-fA-F0-9]{16}$", arg):
        fid = next((f.get("id") for f in files if (f.get("id") or "")[:16] == arg[:16]), None)
    if not fid:
        arg_lower = arg.lower()
        for f in files:
            name = (f.get("name") or "").lower()
            if arg_lower in name or name.startswith(arg_lower):
                fid = f.get("id")
                break
    if not fid:
        console.print(f"[red]Documento não encontrado:[/] {arg}")
        console.print("[dim]Use o ID (ex: 000ea928c570e56d) ou parte do nome do arquivo.[/]")
        raise click.Abort() from None

    try:
        text = load_file_text(fid)
    except StoreError as e:
        logger.exception("Erro ao ler texto do documento %s", fid)
        console.print(f"[red]{e.message}[/]")
        console.print(LOG_HINT)
        raise click.Abort() from None
    if not text or len(text.strip()) < 50:
        console.print("[yellow]Documento sem texto suficiente para responder.[/]")
        raise click.Abort() from None

    name = next((f.get("name", fid) for f in files if f.get("id") == fid), fid)
    console.print(f"[dim]Perguntando sobre:[/] [cyan]{name}[/]")
    console.print(f"[dim]Pergunta:[/] {question}\n")
    answer = ask_document_ollama(text, question)
    if not answer:
        console.print("[red]Não foi possível obter resposta do Ollama.[/]")
        raise click.Abort() from None
    console.print(Panel(answer.strip(), title="🤖 Resposta", border_style="green"))
    audit("cli_ask", {"file_id": fid, "question": question[:120]})


@main.command()
def status() -> None:
    """
    Mostra status do projeto: arquivos indexados e totais.
    """
    stats = get_stats()
    files = stats.get("files", [])
    total_files = stats.get("total_files", 0)
    total_pages = stats.get("total_pages", 0)

    if total_files == 0:
        console.print(
            Panel(
                "[dim]Nenhum documento indexado ainda.[/]\n\n"
                "Que tal adicionar seu primeiro PDF?\n"
                "[cyan]pdfsearchable add arquivo.pdf[/] ou [cyan]pdfsearchable add pasta/[/]",
                title="📄 Status",
                border_style="blue",
            )
        )
        return

    table = Table(title="📊 Status do projeto")
    table.add_column("ID", style="dim")
    table.add_column("Documento", style="cyan")
    table.add_column("Páginas", justify="right")
    table.add_column("Tipo", style="green")
    for f in files:
        tipo = f.get("doc_type", "—")
        if f.get("classification_source") in ("openai", "ollama"):
            tipo = f"{tipo} [dim](IA)[/]"
        table.add_row(
            f.get("id", "—")[:12] + "…",
            f.get("name", "—"),
            str(f.get("num_pages", 0)),
            tipo,
        )
    console.print(table)
    console.print(f"[bold]Total:[/] {total_files} documento(s) · {total_pages} página(s)")
    console.print(
        "[dim]Report: [cyan]pdfsearchable serve[/] — no report, clique no nome do documento para abrir a visualização.[/]"
    )
    console.print("[dim]Para remover: [cyan]pdfsearchable remove <id ou nome>[/][/]")


@main.command(
    epilog=(
        "Exemplos:\n\n"
        "  pdfsearchable serve\n\n"
        "  pdfsearchable serve --open\n\n"
        "  pdfsearchable serve --port 9000\n\n"
        "  pdfsearchable serve --host 0.0.0.0 --port 8080"
    ),
)
@click.option("--host", default="127.0.0.1", show_default=True, help="Host para o servidor HTTP.")
@click.option(
    "--port", default=8000, show_default=True, type=int, help="Porta para o servidor HTTP."
)
@click.option(
    "--open/--no-open",
    "open_browser",
    default=True,
    show_default=True,
    help="Abrir o report no browser ao iniciar o servidor.",
)
def serve(host: str, port: int, open_browser: bool) -> None:
    """
    Serve a SPA interativa (app.html) e os endpoints /api/* em http://host:port.

    Raiz HTTP: .pdfsearchable; PDFs em arquivos-processados/ são servidos sob
    /arquivos-processados/. Endpoint /api/ask (RAG com Ollama) para perguntas
    sobre um documento: JSON {"id": "<file_id>", "question": "..."} → {"answer": "..."}.
    """
    _run_http_server(host=host, port=port, open_browser=open_browser)


@main.command("open")
@click.option("--port", default=8000, show_default=True, type=int, help="Porta do servidor serve.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Host do servidor serve.")
def open_cmd(host: str, port: int) -> None:
    """
    Abre a SPA no browser padrão (requer pdfsearchable serve em execução).
    """
    import webbrowser

    url = f"http://{host}:{port}/app.html"
    console.print(f"[dim]Abrindo [cyan]{url}[/] no browser…[/]")
    webbrowser.open(url)


@main.command(
    epilog=(
        "Exemplos:\n\n"
        "  pdfsearchable logs\n\n"
        "  pdfsearchable logs -n 100\n\n"
        "  pdfsearchable logs -n 10"
    ),
)
@click.option("-n", "--lines", default=30, show_default=True, help="Número de entradas de auditoria.")
def logs(lines: int) -> None:
    """
    Mostra as últimas entradas do log de auditoria.
    """
    trail = read_audit_trail(limit=lines)
    if not trail:
        console.print(
            "[dim]Ainda não há entradas de auditoria.[/]\n"
            "[dim]Use [cyan]pdfsearchable add[/] para começar a indexar.[/]"
        )
        return
    _ACTION_LABELS: dict[str, str] = {
        "index_done": "Indexação concluída",
        "index_file": "Arquivo indexado",
        "index_skip": "Arquivo ignorado (sem alterações)",
        "index_error": "Erro na indexação",
        "cli_add": "add iniciado",
        "cli_remove": "Documento removido",
        "cli_search": "Pesquisa realizada",
        "cli_report": "Report gerado",
        "cli_index_fts": "Índice FTS atualizado",
        "fts_index_all": "Índice FTS reconstruído",
    }
    table = Table(title="📋 Auditoria")
    table.add_column("Quando", style="dim")
    table.add_column("Ação", style="cyan")
    table.add_column("Detalhes", style="white")
    for e in trail:
        action_raw = e.get("action", "—")
        action_label = _ACTION_LABELS.get(action_raw, action_raw)
        details = e.get("details") or {}
        details_str = " ".join(f"{k}={v}" for k, v in list(details.items())[:4])
        table.add_row(
            e.get("timestamp", "—")[:19],
            action_label,
            details_str or "—",
        )
    console.print(table)


@main.command(
    epilog=(
        "Exemplos:\n\n"
        "  pdfsearchable export --format jsonl --output colecao.jsonl\n\n"
        "  pdfsearchable export --format markdown --output ./docs_md/\n\n"
        "  pdfsearchable export --format obsidian --output-dir ~/obsidian/vault/PDFs\n\n"
        "  pdfsearchable export --format csv --output metadados.csv"
    ),
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "jsonl", "csv", "markdown", "obsidian"], case_sensitive=False),
    default="jsonl",
    show_default=True,
    help=(
        "Formato: json (índice completo), jsonl (1 doc/linha para LLMs/RAG), "
        "csv (metadados tabulares), markdown (1 .md por doc com texto), "
        "obsidian (notas .md com YAML frontmatter para Obsidian/Logseq)."
    ),
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Arquivo de saída (json/jsonl/csv) ou directório (markdown). Padrão automático.",
)
@click.option(
    "--output-dir",
    "-d",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Pasta de saída para --format obsidian (padrão: ./pdfsearchable-obsidian/).",
)
@click.option(
    "--no-text",
    is_flag=True,
    default=False,
    help="Omitir texto completo dos documentos (jsonl). Útil para exportações de metadados.",
)
def export(
    fmt: str,
    output: Path | None,
    output_dir: Path | None,
    no_text: bool,
) -> None:
    """
    Exporta documentos indexados para json, jsonl, csv, markdown ou obsidian.

    \b
    json      — dump completo do índice (estrutura interna)
    jsonl     — um documento por linha (fine-tuning de LLMs, RAG externo)
    csv       — metadados tabulares, sem texto completo
    markdown  — um arquivo .md por documento com texto e metadados
    obsidian  — notas .md com YAML frontmatter para Obsidian/Logseq
    """
    import datetime as _dt

    fmt = fmt.lower()

    # --- Obsidian / Logseq (inline — não usa export.py) ---
    if fmt == "obsidian":
        try:
            idx = load_index()
        except Exception as e:
            _abort_index_error(e)
        files = idx.get("files", [])
        if not files:
            console.print("[yellow]Nenhum documento no índice para exportar.[/]")
            return
        import re as _re

        dest_dir = (output_dir or Path.cwd() / "pdfsearchable-obsidian").resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for f in files:
            name = f.get("name") or f.get("id", "documento")
            # Nome de arquivo seguro: remove caracteres problemáticos
            safe_name = _re.sub(r'[\\/*?:"<>|]', "_", name)
            safe_name = safe_name.removesuffix(".pdf") if safe_name.endswith(".pdf") else safe_name
            md_path = dest_dir / f"{safe_name}.md"
            tags_raw = f.get("tags") or []
            tags_yaml = (
                "\n".join(f"  - {t}" for t in tags_raw) if tags_raw else "  []"
            )
            parties_raw = f.get("parties") or []
            parties_yaml = (
                "\n".join(f'  - "{p}"' for p in parties_raw) if parties_raw else "  []"
            )
            summary = (f.get("summary") or "").replace('"', "'")
            subject = (f.get("subject") or "").replace('"', "'")
            indexed_date = (f.get("indexed_at") or "")[:10]
            updated_date = (f.get("updated_at") or "")[:10]
            lang = f.get("language") or ""
            ocr_pct = f.get("ocr_percentage")
            ocr_line = f"ocr_percentage: {ocr_pct}" if ocr_pct is not None else ""
            frontmatter_parts = [
                "---",
                f'title: "{safe_name}"',
                f"doc_type: {f.get('doc_type') or 'documento'}",
                f"language: {lang}" if lang else "",
                f"num_pages: {f.get('num_pages') or 0}",
                f"word_count: {f.get('word_count') or 0}",
                f"indexed_at: {indexed_date}" if indexed_date else "",
                f"updated_at: {updated_date}" if updated_date else "",
                f"file_id: {f.get('id', '')}",
                f"source: {f.get('original_path') or ''}",
                ocr_line,
                f"tags:\n{tags_yaml}",
                f"parties:\n{parties_yaml}",
                "---",
            ]
            frontmatter = "\n".join(p for p in frontmatter_parts if p)
            body_parts = [frontmatter, ""]
            if subject:
                body_parts += ["## Assunto", "", subject, ""]
            if summary:
                body_parts += ["## Sumário", "", summary, ""]
            if parties_raw:
                body_parts += ["## Partes", ""]
                body_parts += [f"- {p}" for p in parties_raw]
                body_parts.append("")
            body_parts += [
                "## Metadados",
                "",
                "| Campo | Valor |",
                "|-------|-------|",
                f"| Tipo | {f.get('doc_type') or '—'} |",
                f"| Páginas | {f.get('num_pages') or 0} |",
                f"| Palavras | {f.get('word_count') or 0} |",
                f"| Idioma | {lang or '—'} |",
                f"| Indexado em | {indexed_date or '—'} |",
            ]
            md_path.write_text("\n".join(body_parts), encoding="utf-8")
            count += 1
        console.print(
            Panel(
                f"[green]✓[/] {count} nota(s) .md gerada(s) em [cyan]{dest_dir}[/]\n"
                "[dim]Importe a pasta no Obsidian (Vault) ou Logseq.[/]",
                title="[bold green]Exportação Obsidian concluída[/]",
                border_style="green",
            )
        )
        audit("cli_export_obsidian", {"count": count, "output_dir": str(dest_dir)})
        return

    # --- json / jsonl / csv / markdown — delegam para export.py ---
    from pdfsearchable.export import export as _export

    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = output
    if out_path is None:
        defaults: dict[str, Path] = {
            "json":     Path(f"pdfsearchable-export-{ts}.json"),
            "jsonl":    Path(f"pdfsearchable-export-{ts}.jsonl"),
            "csv":      Path(f"pdfsearchable-export-{ts}.csv"),
            "markdown": Path(f"pdfsearchable-export-{ts}-md"),
        }
        out_path = defaults.get(fmt, Path(f"pdfsearchable-export-{ts}.out"))

    # Aviso de segurança: output fora do directório de trabalho
    try:
        resolved = out_path.resolve()
        cwd_resolved = Path.cwd().resolve()
        if not str(resolved).startswith(str(cwd_resolved)):
            console.print(
                f"[yellow]⚠ Atenção:[/] O destino [cyan]{resolved}[/] está fora do directório actual."
            )
    except Exception:
        pass

    try:
        n = _export(fmt, out_path, include_text=not no_text)
    except ValueError as e:
        console.print(f"[red]Formato inválido:[/] {e}")
        raise click.Abort() from None
    except OSError as e:
        logger.exception("Erro ao escrever arquivo de exportação")
        console.print(f"[red]Não foi possível escrever o arquivo:[/] {e}")
        console.print(LOG_HINT)
        raise click.Abort() from None
    except Exception as e:
        logger.exception("Erro inesperado na exportação")
        console.print(f"[red]Exportação falhou:[/] {e}")
        console.print(LOG_HINT)
        raise click.Abort() from None

    console.print(
        Panel(
            f"[green]✓[/] {n} documento(s) exportado(s) para [cyan]{out_path}[/]\n"
            f"[dim]Formato: {fmt.upper()}[/]",
            title="[bold green]Exportação concluída[/]",
            border_style="green",
        )
    )
    audit("cli_export", {"format": fmt, "output": str(out_path), "count": n})


@main.command(
    epilog=(
        "Exemplos:\n\n"
        "  pdfsearchable watch\n\n"
        "  pdfsearchable watch ~/Downloads --interval 5\n\n"
        "  pdfsearchable watch /dados/pdfs --no-recursive"
    ),
)
@click.argument(
    "directory",
    type=click.Path(exists=True, file_okay=False, readable=True, path_type=Path),
    default=".",
)
@click.option(
    "--interval",
    "-i",
    default=10,
    show_default=True,
    type=int,
    help="Intervalo de verificação em segundos.",
)
@click.option(
    "--recursive/--no-recursive",
    default=True,
    show_default=True,
    help="Verificar subpastas.",
)
def watch(directory: Path, interval: int, recursive: bool) -> None:
    """
    Monitoriza DIRECTORY e indexa automaticamente novos PDFs (e modificados).

    Usa polling (sem dependências externas). Pressione Ctrl+C para parar.
    Aguarda que o PDF termine de ser escrito (debounce por tamanho estável)
    antes de indexar.  Deteta também arquivos modificados (mtime alterado).
    """
    import time as _time

    from pdfsearchable.indexer import index_pdfs

    ensure_store()
    directory = directory.resolve()
    interval = max(2, interval)

    def _scan() -> dict[Path, float]:
        """Retorna {path: mtime} para todos os PDFs encontrados."""
        pattern = "**/*.pdf" if recursive else "*.pdf"
        result: dict[Path, float] = {}
        for p in directory.glob(pattern):
            if p.is_file() and not p.name.startswith("._"):
                with contextlib.suppress(OSError):
                    result[p.resolve()] = p.stat().st_mtime
        return result

    def _stable_size(p: Path, wait: float = 1.5) -> bool:
        """Verifica que o tamanho do arquivo não mudou em `wait` segundos (debounce)."""
        try:
            s1 = p.stat().st_size
            _time.sleep(wait)
            s2 = p.stat().st_size
            return s1 == s2 and s1 > 0
        except OSError:
            return False

    # Carregar paths/mtimes já no índice para não re-indexar ao arrancar
    try:
        idx0 = load_index()
        _known: dict[str, float] = {}  # original_path → indexed mtime (0 = desconhecido)
        for f in idx0.get("files", []):
            op = f.get("original_path")
            if op:
                _known[op] = 0.0  # mtime desconhecido; evita re-index ao arrancar
    except Exception:
        _known = {}

    # Estado inicial: path → mtime (files já conhecidos recebem mtime real)
    seen: dict[Path, float] = {}
    for p, mtime in _scan().items():
        if str(p) in _known:
            seen[p] = mtime  # marca como visto com mtime real → detecta futuras mudanças

    console.print(
        Panel(
            f"[bold]Diretório:[/] [cyan]{directory}[/]\n"
            f"[bold]Intervalo:[/] {interval}s  |  [bold]Recursivo:[/] {'sim' if recursive else 'não'}\n\n"
            f"[dim]{len(seen)} PDF(s) já no índice. Detecta novos e modificados. Ctrl+C para parar.[/]",
            title="[bold blue]watch — monitorando PDFs[/]",
            border_style="blue",
        )
    )

    def _index_one(p: Path, reason: str) -> None:
        console.print(f"[dim]{reason}:[/] [cyan]{p.name}[/] — aguardando arquivo estável…")
        if not _stable_size(p):
            console.print(f"[yellow]⚠[/] {p.name}: arquivo vazio ou em escrita; ignorado.")
            return
        try:
            results = index_pdfs([p], workers=1, skip_existing=False)
            ok = sum(1 for r in results if not r.get("error"))
            if ok:
                fts_index_new_files()
                threading.Thread(
                    target=_fire_webhook,
                    args=(results, "watch_indexed"),
                    daemon=True,
                ).start()
                console.print(f"[green]✓[/] Indexado: [bold]{p.name}[/]")
                audit("watch_indexed", {"path": str(p), "reason": reason})
            else:
                err = results[0].get("error", "?") if results else "sem resultado"
                console.print(f"[yellow]⚠[/] Falha ao indexar [bold]{p.name}[/]: {err}")
        except Exception as e:
            logger.exception("watch: erro ao indexar %s", p)
            console.print(f"[red]Erro ao indexar {p.name}:[/] {e}")

    try:
        while True:
            _time.sleep(interval)
            current = _scan()
            for p, mtime in sorted(current.items()):
                prev_mtime = seen.get(p)
                if prev_mtime is None:
                    # Arquivo novo
                    _index_one(p, "Novo PDF")
                    seen[p] = current[p]
                elif mtime > prev_mtime and prev_mtime > 0:
                    # Arquivo modificado (mtime aumentou; prev > 0 exclui arranque)
                    _index_one(p, "PDF modificado")
                    seen[p] = current[p]
                else:
                    seen[p] = mtime  # actualiza mtime mesmo que igual (garante consistência)
            # Remover paths apagados do disco
            for gone in set(seen) - set(current):
                del seen[gone]
    except KeyboardInterrupt:
        console.print("\n[dim]Watch encerrado.[/]")


@main.command(
    epilog=(
        "Exemplos:\n\n"
        "  pdfsearchable backup\n\n"
        "  pdfsearchable backup --output ~/backups/meu-projeto.tar.gz"
    ),
)
@click.option(
    "--output",
    "-o",
    type=click.Path(writable=True, dir_okay=False, path_type=Path),
    default=None,
    help="Caminho do arquivo de backup (padrão: .pdfsearchable-backup-YYYYMMDD-HHMMSS.tar.gz).",
)
def backup(output: Path | None) -> None:
    """
    Cria um backup compactado de .pdfsearchable/ (índice, textos, FTS).

    O arquivo gerado pode ser restaurado manualmente extraindo o conteúdo
    para a pasta .pdfsearchable/ do projeto.
    """
    import tarfile
    import datetime as _dt

    store_dir = Path.cwd() / ".pdfsearchable"
    if not store_dir.exists():
        console.print("[red]Pasta .pdfsearchable não encontrada.[/] Use [cyan]pdfsearchable add[/] primeiro.")
        raise click.Abort() from None

    if output is None:
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        output = Path.cwd() / f".pdfsearchable-backup-{ts}.tar.gz"

    try:
        with tarfile.open(output, "w:gz") as tar:
            tar.add(store_dir, arcname=".pdfsearchable")
        size_mb = round(output.stat().st_size / (1024 * 1024), 2)
        console.print(
            Panel(
                f"[green]✓[/] Backup criado em [cyan]{output}[/]\n"
                f"[dim]Tamanho: {size_mb} MB[/]",
                title="[bold green]Backup concluído[/]",
                border_style="green",
            )
        )
        audit("cli_backup", {"output": str(output), "size_mb": size_mb})
    except OSError as e:
        console.print(f"[red]Falha ao criar backup: {e}[/]")
        raise click.Abort() from None


@main.command()
def verify() -> None:
    """
    Verifica a integridade do índice: arquivos em falta, textos ilegíveis e hashes.

    Detecta entradas órfãs (arquivos .pdfsearchable/ em falta), PDFs originais
    inaccessíveis, e textos armazenados corrompidos (falha de leitura).
    """
    import hashlib as _hl

    try:
        idx = load_index()
    except StoreError as e:
        _abort_index_error(e)
    files = idx.get("files", [])
    if not files:
        console.print("[dim]Nenhum documento no índice.[/]")
        return

    ok_count = 0
    warn_count = 0
    issues: list[str] = []

    for f in files:
        fid = f.get("id", "?")
        name = f.get("name", fid)
        original = f.get("original_path", "")
        stored_hash = f.get("content_hash")

        # 1. PDF original acessível
        if original and not Path(original).exists():
            issues.append(f"[yellow]⚠ PDF original não encontrado:[/] {name} ([dim]{original}[/])")
            warn_count += 1

        # 2. Texto armazenado legível
        try:
            text = load_file_text(fid)
            if not text:
                issues.append(f"[yellow]⚠ Sem texto armazenado:[/] {name}")
                warn_count += 1
            else:
                ok_count += 1
        except StoreError:
            issues.append(f"[red]✗ Texto ilegível (possível corrupção):[/] {name}")
            warn_count += 1
            continue

        # 3. Content hash do texto vs. PDF original (se ambos acessíveis)
        if stored_hash and original and Path(original).exists():
            try:
                actual_hash = _hl.sha256(Path(original).read_bytes()).hexdigest()[:32]
                if actual_hash != stored_hash:
                    issues.append(
                        f"[cyan]ℹ PDF modificado desde indexação:[/] {name} "
                        f"[dim](re-indexe com [cyan]pdfsearchable add --reprocess[/])[/]"
                    )
            except OSError:
                pass

    if issues:
        for line in issues:
            console.print(f"  {line}")
        console.print()

    status = "[green]OK[/]" if warn_count == 0 else f"[yellow]{warn_count} aviso(s)[/]"
    console.print(
        Panel(
            f"[bold]{len(files)}[/] documento(s) verificado(s) · {status}\n"
            f"[dim]{ok_count} com texto acessível · {warn_count} com problema(s)[/]",
            title="[bold]Verificação de integridade[/]",
            border_style="green" if warn_count == 0 else "yellow",
        )
    )
    audit("cli_verify", {"total": len(files), "warnings": warn_count})


@main.command(
    epilog=(
        "Exemplos:\n\n"
        "  # Adicionar ao claude_desktop_config.json:\n"
        '  # {"mcpServers": {"pdfsearchable": {"command": "pdfsearchable", "args": ["mcp"], "cwd": "/pasta/projeto"}}}\n\n'
        "  pdfsearchable mcp   # iniciar servidor MCP (usado pelo cliente, não directamente)"
    ),
)
def mcp() -> None:
    """
    Inicia o servidor MCP (Model Context Protocol) via stdin/stdout.

    Integra pdfsearchable como ferramenta MCP em Claude Desktop, Cursor,
    Zed, Windsurf e outros clientes compatíveis. Expõe 5 tools:
    list_documents, search_documents, get_document_text, ask_document,
    ask_all_documents.

    Configure no claude_desktop_config.json com o caminho do projecto
    em 'cwd'. O servidor aplica a config do projecto automaticamente.
    """
    from pdfsearchable.mcp_server import run_stdio_server

    run_stdio_server()


@main.command(
    epilog=(
        "Exemplos:\n\n"
        "  pdfsearchable chat\n\n"
        "  pdfsearchable chat --doc a1b2c3d4e5f6\n\n"
        "  pdfsearchable chat --max-docs 10"
    ),
)
@click.option(
    "--doc",
    "doc_id",
    default=None,
    metavar="ID",
    help="Focar numa conversa sobre um único documento (ID ou parte do nome).",
)
@click.option(
    "--max-docs",
    default=5,
    show_default=True,
    type=int,
    help="Máximo de documentos a consultar por pergunta (RAG multi-documento).",
)
def chat(doc_id: str | None, max_docs: int) -> None:
    """
    Conversa em linguagem natural sobre os documentos indexados (RAG via Ollama).

    Requer Ollama em execução. Configure o modelo com PDFSEARCHABLE_OLLAMA_MODEL
    (padrão: llama3.2). Use 'sair', 'exit' ou Ctrl+C para terminar.
    Com --doc, foca a conversa num único documento.
    """
    from pdfsearchable.content_extractors import ollama_health_check, ollama_stream_chat
    from pdfsearchable.store import load_index, load_file_text, fts_search

    if (os.environ.get("PDFSEARCHABLE_AI") or "").strip().lower() != "ollama":
        console.print(
            "[yellow]⚠ Ollama não está activado.[/] Configure [cyan]PDFSEARCHABLE_AI=ollama[/] e tente novamente."
        )
        raise click.Abort() from None

    try:
        if not ollama_health_check():
            console.print("[red]Ollama não está acessível.[/] Inicie o Ollama e tente novamente.")
            raise click.Abort() from None
    except Exception:
        console.print("[red]Não foi possível verificar o estado do Ollama.[/]")
        raise click.Abort() from None

    try:
        idx = load_index()
    except Exception as e:
        _abort_index_error(e)
    files = idx.get("files", [])
    if not files:
        console.print("[yellow]Nenhum documento indexado. Use [cyan]pdfsearchable add[/] primeiro.[/]")
        return

    # Modo foco: um único documento
    focused_meta: dict | None = None
    if doc_id:
        focused_meta = next(
            (f for f in files if f.get("id", "").startswith(doc_id) or doc_id.lower() in (f.get("name") or "").lower()),
            None,
        )
        if not focused_meta:
            console.print(f"[red]Documento '{doc_id}' não encontrado.[/]")
            raise click.Abort() from None

    model_name = (os.environ.get("PDFSEARCHABLE_OLLAMA_MODEL") or "llama3.2").strip()
    id_to_meta = {f.get("id", ""): f for f in files}
    max_docs = max(1, min(20, max_docs))

    if focused_meta:
        mode_hint = f"[bold]Documento:[/] [cyan]{focused_meta.get('name', doc_id)}[/]"
    else:
        mode_hint = f"[bold]Modo:[/] RAG multi-documento (até {max_docs} doc(s) por pergunta)"

    console.print(
        Panel(
            f"{mode_hint}\n"
            f"[bold]Modelo:[/] [cyan]{model_name}[/]  |  [bold]Documentos:[/] {len(files)}\n\n"
            "[dim]'sair' ou Ctrl+C para terminar · 'limpar' para nova conversa[/]",
            title="[bold blue]pdfsearchable chat[/]",
            border_style="blue",
        )
    )

    history: list[dict] = []  # [{"role": "user"|"assistant", "text": ...}]

    def _build_context(question: str) -> tuple[str, list[str]]:
        """Selecciona documentos relevantes e monta contexto para o Ollama."""
        if focused_meta:
            fid = focused_meta.get("id", "")
            text = load_file_text(fid)
            return text[:40_000], [focused_meta.get("name", fid)]

        hits = fts_search(question, limit=max_docs * 4)
        seen: list[str] = []
        for fid, _, _ in hits:
            if fid not in seen:
                seen.append(fid)
            if len(seen) >= max_docs:
                break
        if not seen:
            seen = [f.get("id", "") for f in files[:max_docs] if f.get("id")]

        parts: list[str] = []
        names: list[str] = []
        for fid in seen:
            m = id_to_meta.get(fid, {})
            t = load_file_text(fid)
            if t and t.strip():
                parts.append(f"=== {m.get('name', fid)} ===\n{t[:6000]}")
                names.append(m.get("name", fid))
        return "\n\n".join(parts), names

    try:
        while True:
            try:
                user_input = console.input("[bold cyan]Você:[/] ").strip()
            except EOFError:
                break
            if not user_input:
                continue
            if user_input.lower() in ("sair", "exit", "quit", "q"):
                break
            if user_input.lower() in ("limpar", "clear"):
                history.clear()
                console.print("[dim]Conversa reiniciada.[/]")
                continue

            # Construir contexto + histórico resumido
            context, sources = _build_context(user_input)
            if not context:
                console.print("[yellow]Sem texto nos documentos seleccionados.[/]")
                continue

            # Histórico resumido (últimas 3 trocas)
            hist_text = ""
            if history:
                recent = history[-6:]
                hist_text = "\n".join(
                    f"{'Utilizador' if h['role'] == 'user' else 'Assistente'}: {h['text']}"
                    for h in recent
                )
                hist_text = f"Histórico recente:\n{hist_text}\n\n"

            full_question = hist_text + user_input if hist_text else user_input

            stream = ollama_stream_chat(context, full_question)
            if stream is None:
                console.print("[yellow]Ollama não devolveu resposta.[/]")
                continue

            console.print("\n[bold green]Assistente:[/] ", end="")
            tokens: list[str] = []
            try:
                for token in stream:
                    console.print(token, end="", highlight=False, markup=False)
                    tokens.append(token)
                console.print()  # newline after streaming
            except KeyboardInterrupt:
                console.print("\n[dim](interrompido)[/]")

            answer = "".join(tokens).strip()
            if not answer:
                console.print("[yellow]Sem resposta recebida.[/]")
                continue

            history.append({"role": "user", "text": user_input})
            history.append({"role": "assistant", "text": answer})

            if sources and not focused_meta:
                console.print(f"[dim]Fontes: {', '.join(sources[:5])}[/]")
            console.print()

    except KeyboardInterrupt:
        pass

    console.print("\n[dim]Chat encerrado.[/]")
    audit(
        "cli_chat_end",
        {
            "turns": len([h for h in history if h["role"] == "user"]),
            "doc_id": doc_id or "multi",
        },
    )


@main.command(
    epilog=(
        "Exemplos:\n\n"
        "  pdfsearchable embed\n\n"
        "  pdfsearchable embed --force\n\n"
        "  pdfsearchable embed --model nomic-embed-text\n\n"
        "  # Após gerar embeddings, usar busca semântica:\n"
        "  pdfsearchable search --semantic 'imigrantes portugueses 1920'"
    ),
)
@click.option(
    "--model",
    default="nomic-embed-text",
    show_default=True,
    help="Modelo Ollama para embeddings semânticos.",
)
@click.option("--force", is_flag=True, help="Re-gerar embeddings mesmo se já existirem.")
def embed(model: str, force: bool) -> None:
    """
    Gera embeddings semânticos dos documentos via Ollama (nomic-embed-text).

    Necessário para usar [cyan]pdfsearchable search --semantic[/].
    Requer Ollama em execução com o modelo de embeddings instalado:
    [dim]ollama pull nomic-embed-text[/]
    """
    from pdfsearchable.store import load_index, load_file_text, STORE_DIR
    import json
    import struct
    import sqlite3 as _sq
    import urllib.request as _req
    import urllib.error

    ollama_url = (os.environ.get("PDFSEARCHABLE_OLLAMA_URL") or "http://localhost:11434").rstrip("/")
    emb_db = STORE_DIR / "embeddings.sqlite"

    def _get_embedding(text: str) -> list[float] | None:
        payload = json.dumps({"model": model, "prompt": text[:8000]}).encode()
        req = _req.Request(
            f"{ollama_url}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with _req.urlopen(req, timeout=60) as resp:  # nosec
                data = json.loads(resp.read())
                return data.get("embedding")
        except urllib.error.URLError as e:
            logger.warning("Ollama embeddings: %s", e)
            return None

    def _vec_to_blob(vec: list[float]) -> bytes:
        return struct.pack(f"{len(vec)}f", *vec)

    # Inicializar DB de embeddings
    STORE_DIR.mkdir(exist_ok=True)
    conn = _sq.connect(emb_db, timeout=30)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS embeddings "
        "(file_id TEXT PRIMARY KEY, embedding BLOB, model TEXT, indexed_at TEXT)"
    )
    conn.commit()

    try:
        idx = load_index()
    except Exception as e:
        _abort_index_error(e)
    files = idx.get("files", [])
    if not files:
        console.print("[yellow]Nenhum documento no índice.[/]")
        return

    if not force:
        existing = {r[0] for r in conn.execute("SELECT file_id FROM embeddings WHERE model=?", (model,)).fetchall()}
        files = [f for f in files if f.get("id") not in existing]

    if not files:
        console.print(f"[dim]Todos os documentos já têm embeddings para o modelo '{model}'.[/]")
        conn.close()
        return


    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=24, complete_style="cyan", finished_style="green"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task(f"Gerando embeddings ({model})…", total=len(files))
        ok = 0
        chunks_total = 0
        for f in files:
            fid = f.get("id", "")
            name = f.get("name", fid)
            num_pages = int(f.get("num_pages", 0) or 0)
            progress.update(task, description=f"Embedding: {name[:40]}", advance=1)
            text = load_file_text(fid)
            if not text or not text.strip():
                continue
            vec = _get_embedding(text)
            if vec:
                blob = _vec_to_blob(vec)
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO embeddings(file_id, embedding, model, indexed_at) VALUES (?,?,?,?)",
                        (fid, blob, model, now),
                    )
                    conn.commit()
                    ok += 1
                except Exception as _db_err:
                    logger.warning("Falha ao salvar embedding para %s: %s", fid, _db_err)
                # Chunks por página (RAG fino): snippets relevantes em vez de início do doc
                if num_pages > 0:
                    try:
                        from pdfsearchable.semantic_search import embed_document_pages
                        saved = embed_document_pages(fid, num_pages, model, ollama_url)
                        chunks_total += saved
                    except Exception as _chunk_err:
                        logger.debug("embed_document_pages falhou para %s: %s", fid, _chunk_err)
        progress.update(task, description="Concluído")

    conn.close()
    console.print(
        Panel(
            f"[green]✓[/] {ok}/{len(files)} embedding(s) gerado(s) com modelo [bold]{model}[/].\n"
            f"[green]✓[/] {chunks_total} chunk(s) por página para RAG fino.\n"
            "[dim]Use [cyan]pdfsearchable search --semantic CONSULTA[/] para busca semântica.[/]",
            title="[bold green]Embeddings prontos[/]",
            border_style="green",
        )
    )
    audit("cli_embed", {"model": model, "count": ok, "chunks": chunks_total})


@main.command("info")
@click.argument("doc", metavar="ID_OU_NOME")
def info_cmd(doc: str) -> None:
    """
    Mostra metadados detalhados de um documento pelo ID ou nome (parcial).

    \b
    Exemplos:
      pdfsearchable info a1b2c3d4e5f6a1b2
      pdfsearchable info contrato
    """
    from pdfsearchable.store import load_index, load_file_text, STORE_DIR

    idx = load_index()
    files = idx.get("files", [])
    if not files:
        console.print("[yellow]Nenhum documento indexado.[/]")
        return

    # Match by exact ID first, then partial name (case-insensitive)
    needle = doc.strip().lower()
    match = next((f for f in files if f.get("id", "") == doc), None)
    if match is None:
        match = next((f for f in files if needle in (f.get("name") or "").lower()), None)
    if match is None:
        console.print(f"[red]Documento não encontrado:[/] {doc}")
        console.print("[dim]Use [cyan]pdfsearchable status[/] para ver os IDs disponíveis.[/]")
        return

    fid = match.get("id", "")
    name = match.get("name", fid)

    # Check text availability
    text = load_file_text(fid)
    text_chars = len(text) if text and text.strip() else 0

    from rich.table import Table
    from rich.panel import Panel as _Panel

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Campo", style="dim", min_width=20)
    table.add_column("Valor", style="bold")

    def _row(label: str, value: object) -> None:
        if value is not None and value != "" and value != [] and value != {}:
            table.add_row(label, str(value))

    _row("ID", fid)
    _row("Nome", name)
    _row("Tipo", match.get("doc_type") or "—")
    _row("Idioma", match.get("language") or "—")
    _row("Páginas", match.get("num_pages") or "—")
    _row("Palavras", match.get("word_count") or "—")
    _row("Texto extraído", f"{text_chars:,} caracteres" if text_chars else "[red]Nenhum[/]")
    _row("Indexado em", (match.get("indexed_at") or "")[:19] or "—")
    _row("Actualizado em", (match.get("updated_at") or "")[:19] or "—")

    tags = match.get("tags") or []
    if tags:
        _row("Tags", ", ".join(tags))

    parties = match.get("parties") or []
    if parties:
        _row("Partes", "; ".join(str(p) for p in parties[:5]) + (" …" if len(parties) > 5 else ""))

    dates = match.get("identified_dates") or []
    if dates:
        _row("Datas encontradas", ", ".join(dates[:8]) + (" …" if len(dates) > 8 else ""))

    summary = match.get("summary") or ""
    if summary:
        _row("Resumo", summary[:200] + ("…" if len(summary) > 200 else ""))

    subject = match.get("subject") or ""
    if subject:
        _row("Assunto", subject)

    content_hash = match.get("content_hash") or ""
    if content_hash:
        _row("Hash (conteúdo)", content_hash)

    original_path = match.get("original_path") or ""
    if original_path:
        _row("Caminho original", original_path)

    meta = match.get("metadata") or {}
    if meta.get("title"):
        _row("Título (PDF)", meta["title"])
    if meta.get("author"):
        _row("Autor (PDF)", meta["author"])
    if meta.get("creation_date") or meta.get("creationDate"):
        _row("Data criação (PDF)", meta.get("creation_date") or meta.get("creationDate", ""))
    if meta.get("producer"):
        _row("Producer (PDF)", meta["producer"])

    # Check embedding availability
    emb_db = STORE_DIR / "embeddings.sqlite"
    has_embedding = False
    if emb_db.exists():
        import sqlite3 as _sq
        try:
            with _sq.connect(str(emb_db), timeout=5) as _c:
                row = _c.execute("SELECT model FROM embeddings WHERE file_id=?", (fid,)).fetchone()
                if row:
                    has_embedding = True
                    _row("Embedding", f"[green]Disponível[/] (modelo: {row[0]})")
        except Exception:
            pass
    if not has_embedding:
        _row("Embedding", "[dim]Não gerado[/] (use [cyan]pdfsearchable embed[/])")

    ocr_pct = match.get("ocr_percentage")
    if ocr_pct is not None:
        _row("Texto via OCR", f"{ocr_pct}%")

    console.print(
        _Panel(
            table,
            title=f"[bold]{name}[/]",
            border_style="blue",
            expand=False,
        )
    )
    audit("cli_info", {"file_id": fid})


@main.command("dedup-semantic")
@click.option("--threshold", "-t", type=float, default=0.98, show_default=True,
              help="Limiar de similaridade cosine (0-1). 0.98 = quase idêntico.")
@click.option("--model", default="nomic-embed-text", show_default=True,
              help="Modelo Ollama usado para embeddings.")
def dedup_semantic_cmd(threshold: float, model: str) -> None:
    """
    Encontra documentos com texto semanticamente duplicado.

    Diferente de ``duplicates`` (que usa content_hash binário), este comando
    usa embeddings para detectar texto quase-idêntico mesmo em PDFs distintos
    (ex.: mesmo contrato digitalizado de duas formas). Requer embeddings
    gerados com ``pdfsearchable embed``.

    \b
    Exemplos:
      pdfsearchable dedup-semantic
      pdfsearchable dedup-semantic --threshold 0.95
    """
    from pdfsearchable.semantic_search import find_semantic_duplicates
    from pdfsearchable.store import load_index

    idx = load_index()
    files_by_id = {f.get("id"): f for f in idx.get("files", [])}

    try:
        pairs = find_semantic_duplicates(threshold=threshold, model=model)
    except Exception as e:
        console.print(f"[red]Falha ao buscar duplicados semânticos:[/] {e}")
        raise click.Abort() from e

    if not pairs:
        console.print(
            f"[green]Nenhum duplicado semântico encontrado[/] (threshold={threshold:.2f})."
        )
        return

    from rich.table import Table
    table = Table(title=f"Duplicados semânticos (threshold={threshold:.2f})")
    table.add_column("Score", justify="right", style="yellow")
    table.add_column("Documento A", style="cyan")
    table.add_column("Documento B", style="cyan")
    for p in pairs[:100]:
        a = files_by_id.get(p["a"], {}).get("name", p["a"])
        b = files_by_id.get(p["b"], {}).get("name", p["b"])
        table.add_row(f"{p['score']:.4f}", a[:50], b[:50])
    console.print(table)
    if len(pairs) > 100:
        console.print(f"[dim]… e mais {len(pairs) - 100} par(es).[/]")
    audit("cli_dedup_semantic", {"threshold": threshold, "pairs": len(pairs)})


@main.command("benchmark-markdown")
@click.argument("file_id_or_name", required=False)
@click.option("--iterations", "-n", type=int, default=5, show_default=True,
              help="Número de iterações por estratégia (exclui warm-up).")
@click.option("--json", "as_json", is_flag=True, help="Imprime resultado em JSON.")
def benchmark_markdown_cmd(
    file_id_or_name: str | None, iterations: int, as_json: bool
) -> None:
    """
    Mede o speedup da conversão PDF → Markdown do pdfsearchable vs baseline PyMuPDF.

    O baseline re-extrai o texto do PDF a cada chamada (custo de pipeline sem
    cache). O pdfsearchable usa o texto pré-extraído em
    ``arquivos-processados/`` e aplica o template de export. Pipelines que
    exportam a mesma coleção mais do que uma vez (RAG, LlamaIndex, Obsidian)
    beneficiam directamente do cache.

    Se ``FILE_ID_OR_NAME`` for omitido, corre no primeiro documento indexado.

    \b
    Exemplos:
      pdfsearchable benchmark-markdown                    # primeiro doc, 5 iter
      pdfsearchable benchmark-markdown contrato -n 10
      pdfsearchable benchmark-markdown a1b2c3d4e5f6a1b2 --json
    """
    import json as _json

    from pdfsearchable.markdown_bench import benchmark_markdown
    from pdfsearchable.store import load_index

    idx = load_index()
    files = idx.get("files", [])
    if not files:
        console.print("[yellow]Índice vazio. Corra `pdfsearchable add` primeiro.[/]")
        raise click.Abort()

    target = None
    if file_id_or_name:
        for f in files:
            if f.get("id") == file_id_or_name or (
                file_id_or_name.lower() in (f.get("name") or "").lower()
            ):
                target = f
                break
        if target is None:
            console.print(f"[red]Documento não encontrado:[/] {file_id_or_name}")
            raise click.Abort()
    else:
        target = files[0]

    pdf_path = Path(target.get("path") or "")
    if not pdf_path.is_file():
        # fallback: arquivos-processados/<id>.pdf
        pdf_path = Path("arquivos-processados") / f"{target.get('id')}.pdf"
    if not pdf_path.is_file():
        console.print(f"[red]PDF não acessível:[/] {pdf_path}")
        raise click.Abort()

    try:
        result = benchmark_markdown(
            pdf_path=pdf_path, file_id=target.get("id"), iterations=iterations
        )
    except Exception as e:
        console.print(f"[red]Falha no benchmark:[/] {e}")
        raise click.Abort() from e

    if as_json:
        console.print(_json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        from rich.table import Table
        t = Table(title=f"PDF → Markdown — {pdf_path.name} ({result.pages} páginas)")
        t.add_column("Estratégia", style="cyan")
        t.add_column("Média (s)", justify="right")
        t.add_column("Min (s)", justify="right")
        t.add_column("Max (s)", justify="right")
        t.add_row(
            "baseline (PyMuPDF re-parse)",
            f"{result.baseline_avg_s:.4f}",
            f"{min(result.baseline_times_s):.4f}",
            f"{max(result.baseline_times_s):.4f}",
        )
        t.add_row(
            "pdfsearchable (texto cacheado)",
            f"{result.pdfsearchable_avg_s:.4f}",
            f"{min(result.pdfsearchable_times_s):.4f}",
            f"{max(result.pdfsearchable_times_s):.4f}",
        )
        console.print(t)
        emoji = "🚀" if result.speedup >= 5.0 else ("⚡" if result.speedup >= 2.0 else "➡")
        console.print(
            f"{emoji} [bold]Speedup: {result.speedup:.2f}×[/] "
            f"(iterations={iterations})"
        )
    audit(
        "cli_benchmark_markdown",
        {
            "file_id": target.get("id"),
            "pages": result.pages,
            "iterations": iterations,
            "speedup": round(result.speedup, 3),
        },
    )


@main.command("migrate")
@click.option("--dry-run", is_flag=True, help="Mostrar que mudanças seriam feitas sem gravar.")
def migrate_cmd(dry_run: bool) -> None:
    """
    Migra o índice para o schema mais recente (v1/v2 → v3).

    Lê .pdfsearchable/index.json, aplica migrações, e grava se houver mudanças.
    Usa backup rotativo automático em .pdfsearchable/.snapshots/ (sempre activo neste comando).

    \b
    Exemplos:
      pdfsearchable migrate --dry-run
      pdfsearchable migrate
    """
    import copy as _copy
    import json as _json
    from pdfsearchable.store import (
        META_FILE,
        INDEX_VERSION,
        _migrate_index,
        _ensure_store,
        save_index,
    )

    _ensure_store()
    if not META_FILE.exists():
        console.print("[yellow]Nenhum índice encontrado.[/] Use [cyan]pdfsearchable add[/].")
        return

    try:
        raw = _json.loads(META_FILE.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError) as e:
        console.print(f"[red]Não foi possível ler o índice:[/] {e}")
        raise click.Abort() from e

    before_version = raw.get("version", 1)
    before_files = len(raw.get("files", []))
    before_snapshot = _copy.deepcopy(raw)

    migrated = _migrate_index(raw)
    after_files = len(migrated.get("files", []))
    changed = migrated != before_snapshot

    console.print(f"[dim]Schema actual do arquivo:[/] [bold]v{before_version}[/]")
    console.print(f"[dim]Schema alvo:[/] [bold]v{INDEX_VERSION}[/]")
    console.print(f"[dim]Arquivos no índice:[/] [bold]{before_files}[/] → [bold]{after_files}[/]")

    if before_version == INDEX_VERSION and not changed:
        console.print("[green]✓ Índice já está na versão actual — nada a fazer.[/]")
        return

    if dry_run:
        console.print("[yellow]Dry-run: nenhuma mudança gravada.[/]")
        console.print(f"[dim]Mudanças detectadas: {changed}[/]")
        return

    # Forçar snapshot antes de gravar (independente de PDFSEARCHABLE_AUTO_SNAPSHOT)
    os.environ["PDFSEARCHABLE_AUTO_SNAPSHOT"] = "1"
    try:
        save_index(migrated)
    except Exception as e:
        console.print(f"[red]Falha ao gravar índice migrado:[/] {e}")
        raise click.Abort() from e

    console.print(
        Panel(
            f"[green]✓[/] Índice migrado: v{before_version} → v{INDEX_VERSION}.\n"
            f"[dim]Snapshot antigo salvo em .pdfsearchable/.snapshots/[/]",
            title="[bold green]Migração concluída[/]",
            border_style="green",
        )
    )
    audit("cli_migrate", {"from_version": before_version, "to_version": INDEX_VERSION})


@main.command("inspect")
@click.argument("doc", metavar="ID_OU_NOME")
@click.option("--json", "as_json", is_flag=True, help="Emitir JSON bruto (ideal para scripts).")
def inspect_cmd(doc: str, as_json: bool) -> None:
    """
    Inspeciona metadados COMPLETOS de um documento (entidades, redacções,
    forense, contratos, extended, warnings), formatado para leitura humana.

    \b
    Exemplos:
      pdfsearchable inspect epstein
      pdfsearchable inspect a1b2c3d4e5f6a1b2 --json
    """
    from pdfsearchable.store import load_index

    idx = load_index()
    files = idx.get("files", [])
    needle = doc.strip().lower()
    match = next((f for f in files if f.get("id", "") == doc), None)
    if match is None:
        match = next((f for f in files if needle in (f.get("name") or "").lower()), None)
    if match is None:
        console.print(f"[red]Documento não encontrado:[/] {doc}")
        return

    if as_json:
        console.print_json(data=match)
        return

    from rich.panel import Panel as _Panel
    from rich.table import Table

    name = match.get("name", match.get("id", "?"))
    meta = match.get("metadata") or {}

    # Painel 1: Identificação
    t1 = Table(show_header=False, box=None)
    t1.add_column("Campo", style="dim", min_width=18)
    t1.add_column("Valor")
    for k, label in [
        ("id", "ID"),
        ("doc_type", "Tipo"),
        ("classification_source", "Fonte da classif."),
        ("classification_confidence", "Confiança"),
        ("language", "Idioma"),
        ("num_pages", "Páginas"),
        ("word_count", "Palavras"),
        ("ocr_percentage", "OCR %"),
        ("ocr_avg_confidence", "OCR conf. média"),
        ("file_size", "Tamanho (bytes)"),
        ("content_hash", "Hash conteúdo"),
        ("indexed_at", "Indexado em"),
        ("updated_at", "Actualizado em"),
    ]:
        v = match.get(k)
        if v not in (None, "", [], {}):
            t1.add_row(label, str(v))
    console.print(_Panel(t1, title=f"[bold]{name}[/] · Identificação", border_style="blue"))

    # Painel 2: Enriquecimento (summary/subject/tags/parties)
    t2 = Table(show_header=False, box=None)
    t2.add_column("Campo", style="dim", min_width=18)
    t2.add_column("Valor")
    if match.get("summary"):
        t2.add_row("Resumo", match["summary"])
    if match.get("subject"):
        t2.add_row("Assunto", match["subject"])
    if match.get("tags"):
        t2.add_row("Tags", ", ".join(match["tags"]))
    if match.get("parties"):
        t2.add_row("Partes", "; ".join(str(p) for p in match["parties"]))
    if match.get("confidentiality"):
        t2.add_row("Confidencialidade", str(match["confidentiality"]))
    if t2.rows:
        console.print(_Panel(t2, title="Enriquecimento", border_style="cyan"))

    # Painel 3: Entidades identificadas (agregador)
    entity_keys = [
        ("identified_emails", "E-mails"),
        ("identified_cpfs", "CPFs"),
        ("identified_cnpjs", "CNPJs"),
        ("identified_ips", "IPs"),
        ("identified_phones", "Telefones"),
        ("identified_addresses", "Endereços"),
        ("identified_urls", "URLs"),
        ("identified_domains", "Domínios"),
        ("identified_dates", "Datas"),
        ("identified_locations", "Localizações"),
        ("identified_ceps", "CEPs"),
        ("identified_processos", "Processos"),
        ("identified_placas", "Placas"),
        ("identified_rgs", "RGs"),
        ("identified_hashes", "Hashes"),
        ("identified_coordenadas", "Coordenadas"),
        ("identified_leis", "Leis citadas"),
    ]
    t3 = Table(show_header=False, box=None)
    t3.add_column("Entidade", style="dim", min_width=18)
    t3.add_column("Ocorrências")
    for k, label in entity_keys:
        v = match.get(k) or []
        if v:
            preview = ", ".join(str(x) for x in v[:8])
            if len(v) > 8:
                preview += f" … (+{len(v) - 8})"
            t3.add_row(label, preview)
    if t3.rows:
        console.print(_Panel(t3, title="Entidades identificadas", border_style="magenta"))

    # Painel 4: Detecções opcionais (redacção, forense, contrato)
    t4 = Table(show_header=False, box=None)
    t4.add_column("Relatório", style="dim", min_width=18)
    t4.add_column("Conteúdo")
    if rr := meta.get("redaction_report"):
        t4.add_row(
            "Redacções",
            f"zonas={rr.get('total_zones')} · "
            f"suspeito={rr.get('suspicious')} · "
            f"{rr.get('summary', '')}",
        )
    if fr := meta.get("forensics"):
        t4.add_row(
            "Forense",
            f"risco={fr.get('risk_score')} · "
            f"suspeito={fr.get('suspicious')} · "
            f"anomalias={len(fr.get('anomalies') or [])}\n"
            f"{fr.get('summary', '')}",
        )
    if cd := meta.get("contract_data"):
        t4.add_row(
            "Contrato",
            f"início={cd.get('start_date')} · fim={cd.get('end_date')} · "
            f"renovação={cd.get('renewal_date')} · duração={cd.get('duration_months')}m · "
            f"auto={cd.get('auto_renewal')} · conf={cd.get('confidence')}",
        )
    if t4.rows:
        console.print(_Panel(t4, title="Detecções opcionais", border_style="yellow"))

    # Painel 5: Metadata PDF (XMP, signatures, etc.)
    t5 = Table(show_header=False, box=None)
    t5.add_column("Campo", style="dim", min_width=18)
    t5.add_column("Valor")
    for k in ("title", "author", "subject", "keywords", "creation_date", "mod_date", "producer", "creator"):
        if meta.get(k):
            t5.add_row(k.replace("_", " ").capitalize(), str(meta[k])[:200])
    ext = meta.get("extended") or {}
    for k, label in [
        ("signatures", "Assinaturas digitais"),
        ("form_fields", "Campos de formulário"),
        ("annotations", "Anotações"),
        ("attached_files", "Anexos"),
        ("outline", "TOC entradas"),
        ("hyperlinks", "Hyperlinks"),
    ]:
        v = ext.get(k)
        if v:
            t5.add_row(label, f"{len(v)} entrada(s)")
    if t5.rows:
        console.print(_Panel(t5, title="Metadata do PDF", border_style="green"))

    audit("cli_inspect", {"file_id": match.get("id")})


# ---------------------------------------------------------------------------
# Grafo de conhecimento
# ---------------------------------------------------------------------------

@main.command("graph")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Caminho do arquivo HTML de saída (padrão: .pdfsearchable/graph.html).")
def graph_cmd(output: Path | None) -> None:
    """Gera grafo interactivo de entidades e relações entre documentos (D3.js)."""
    ensure_store()
    from pdfsearchable.knowledge_graph import generate_graph_html, get_graph_stats
    idx = load_index()
    files = idx.get("files", [])
    if not files:
        console.print("[yellow]Nenhum documento indexado.[/]")
        return
    out_path = output or (STORE_DIR / "graph.html")
    stats = get_graph_stats(files)
    with console.status("A construir grafo de conhecimento…"):
        generate_graph_html(files, out_path)
    console.print(
        Panel(
            f"[green]✓[/] Grafo gerado com [bold]{stats['nodes']}[/] nós e "
            f"[bold]{stats['edges']}[/] ligações.\n"
            f"Tipos: {', '.join(f'{v} {k}' for k, v in stats['entity_types'].items())}\n\n"
            f"[cyan]Abrir:[/] file://{out_path}",
            title="[bold]Grafo de Conhecimento[/]",
            border_style="blue",
        )
    )
    audit("cli_graph", {"nodes": stats["nodes"], "edges": stats["edges"]})


# ---------------------------------------------------------------------------
# Linha do tempo
# ---------------------------------------------------------------------------

@main.command("timeline")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table",
              help="Formato de saída.")
def timeline_cmd(fmt: str) -> None:
    """Exibe cronologia automática de documentos detectada a partir das datas nos PDFs."""
    ensure_store()
    from pdfsearchable.timeline import build_timeline, group_by_year, timeline_stats
    idx = load_index()
    entries = build_timeline(idx.get("files", []))
    if not entries:
        console.print("[yellow]Nenhuma data detectada nos documentos indexados.[/]")
        return
    stats = timeline_stats(entries)
    if fmt == "json":
        import dataclasses
        console.print_json(_json.dumps([dataclasses.asdict(e) for e in entries], ensure_ascii=False))
        return
    by_year = group_by_year(entries)
    console.print(f"\n[bold]Linha do Tempo[/] — {stats['total']} documento(s) "
                  f"· span {stats['span_years']} ano(s) "
                  f"· de [cyan]{stats.get('oldest', '?')}[/] a [cyan]{stats.get('newest', '?')}[/]\n")
    for year, year_entries in by_year.items():
        console.print(f"[bold yellow]{year}[/]")
        for e in year_entries:
            date_str = e.date_iso
            conf_icon = "●" if e.confidence >= 0.8 else "○"
            console.print(f"  {conf_icon} [cyan]{date_str}[/]  {e.name}  [dim]{e.doc_type}[/]")
    console.print()


# ---------------------------------------------------------------------------
# Detecção de redacções
# ---------------------------------------------------------------------------

@main.command("redactions")
@click.argument("doc_id", required=False, default=None)
@click.option("--all", "show_all", is_flag=True, default=False,
              help="Mostrar todos os documentos, mesmo sem redacções.")
def redactions_cmd(doc_id: str | None, show_all: bool) -> None:
    """Detecta redacções e zonas ocultas nos PDFs indexados.

    Se DOC_ID for fornecido, analisa apenas esse documento em detalhe.
    """
    ensure_store()
    from pdfsearchable.redaction import detect_redactions
    idx = load_index()
    files = idx.get("files", [])
    if doc_id:
        match = next((f for f in files if f.get("id", "").startswith(doc_id)
                      or doc_id.lower() in f.get("name", "").lower()), None)
        if not match:
            console.print(f"[red]Documento '{doc_id}' não encontrado.[/]")
            return
        files = [match]

    table = Table("Documento", "Zonas", "Suspeito", "Resumo", box=box.SIMPLE_HEAVY)
    found_any = False
    for f in files:
        orig = f.get("original_path") or f.get("name", "")
        path = Path(orig) if orig else None
        # Usar redaction_report do metadata se disponível
        meta = f.get("metadata") or {}
        rr_cached = meta.get("redaction_report")
        if rr_cached:
            zones = rr_cached.get("total_zones", 0)
            suspicious = rr_cached.get("suspicious", False)
            summary = rr_cached.get("summary", "")
        elif path and path.exists():
            with console.status(f"Analisando {f.get('name', '')}…"):
                rr = detect_redactions(path)
            zones = rr.total_redacted_zones
            suspicious = rr.suspicious
            summary = rr.summary
        else:
            continue
        if not show_all and zones == 0 and not suspicious:
            continue
        found_any = True
        sus_icon = "[red]⚠ Sim[/]" if suspicious else "[green]Não[/]"
        table.add_row(f.get("name", ""), str(zones), sus_icon, summary or "—")
    if found_any:
        console.print(table)
    else:
        console.print("[green]✓ Nenhuma redacção ou zona suspeita detectada.[/]")


# ---------------------------------------------------------------------------
# Análise forense
# ---------------------------------------------------------------------------

@main.command("forensics")
@click.argument("doc_id", required=False, default=None)
@click.option("--min-risk", type=int, default=20,
              help="Pontuação mínima de risco para mostrar (0–100, padrão 20).")
def forensics_cmd(doc_id: str | None, min_risk: int) -> None:
    """Analisa PDFs à procura de anomalias e sinais de adulteração.

    Se DOC_ID for fornecido, analisa apenas esse documento em detalhe.
    """
    ensure_store()
    from pdfsearchable.forensics import analyse_forensics
    idx = load_index()
    files = idx.get("files", [])
    if doc_id:
        match = next((f for f in files if f.get("id", "").startswith(doc_id)
                      or doc_id.lower() in f.get("name", "").lower()), None)
        if not match:
            console.print(f"[red]Documento '{doc_id}' não encontrado.[/]")
            return
        files = [match]
        # Detalhe completo
        orig = match.get("original_path") or match.get("name", "")
        path = Path(orig) if orig else None
        if path and path.exists():
            with console.status("A analisar…"):
                fr = analyse_forensics(path)
            console.print(Panel(
                f"[bold]Risco:[/] {fr.risk_score}/100  "
                f"{'[red]⚠ Suspeito[/]' if fr.suspicious else '[green]OK[/]'}\n\n"
                + "\n".join(
                    f"[{'red' if a['severity']=='high' else 'yellow' if a['severity']=='medium' else 'dim'}]"
                    f"[{a['severity'].upper()}][/] {a['type']}: {a['detail']}"
                    for a in fr.anomalies
                ) or "[dim]Sem anomalias[/]",
                title=f"[bold]Forense — {match.get('name', '')}[/]",
                border_style="red" if fr.suspicious else "green",
            ))
        else:
            # Usar cache
            cached = (match.get("metadata") or {}).get("forensics") or {}
            if cached:
                console.print(f"Risco (cache): {cached.get('risk_score', 0)}/100 — {cached.get('summary', '—')}")
            else:
                console.print("[yellow]Arquivo não disponível para análise directa.[/]")
        return

    table = Table("Documento", "Risco", "Anomalias", "Resumo", box=box.SIMPLE_HEAVY)
    found = False
    for f in files:
        cached = (f.get("metadata") or {}).get("forensics") or {}
        risk = cached.get("risk_score", 0)
        if risk < min_risk:
            continue
        found = True
        risk_color = "red" if risk >= 60 else "yellow" if risk >= 40 else "dim"
        table.add_row(
            f.get("name", ""),
            f"[{risk_color}]{risk}[/]",
            str(len(cached.get("anomalies", []))),
            cached.get("summary", "—"),
        )
    if found:
        console.print(table)
    else:
        console.print(
            f"[green]✓ Nenhum documento com risco >= {min_risk}.[/]\n"
            "[dim]Active análise com PDFSEARCHABLE_FORENSICS=1 e reindexe.[/]"
        )


# ---------------------------------------------------------------------------
# Extracção de tabelas
# ---------------------------------------------------------------------------

@main.command("tables")
@click.argument("doc_id")
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default=None,
              help="Directório de saída (padrão: directório actual).")
@click.option("--format", "fmt", type=click.Choice(["csv", "json"]), default="csv",
              help="Formato de saída.")
@click.option("--img2table", "use_img2table", is_flag=True, default=False,
              help="Usar img2table para páginas sem tabelas nativas (requer pip install img2table).")
def tables_cmd(doc_id: str, output_dir: Path | None, fmt: str, use_img2table: bool) -> None:
    """Extrai tabelas estruturadas de um documento para CSV ou JSON.

    DOC_ID pode ser o ID completo, prefixo ou parte do nome do arquivo.
    """
    ensure_store()
    from pdfsearchable.table_extractor import extract_tables, tables_to_csv, tables_to_json
    idx = load_index()
    files = idx.get("files", [])
    match = next((f for f in files if f.get("id", "").startswith(doc_id)
                  or doc_id.lower() in f.get("name", "").lower()), None)
    if not match:
        console.print(f"[red]Documento '{doc_id}' não encontrado.[/]")
        return
    orig = match.get("original_path") or match.get("name", "")
    path = Path(orig) if orig else None
    if not path or not path.exists():
        console.print(f"[red]Arquivo não encontrado: {orig}[/]")
        return
    out_dir = output_dir or Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)
    with console.status(f"A extrair tabelas de {match.get('name', '')}…"):
        tables = extract_tables(path, use_img2table=use_img2table)
    if not tables:
        console.print("[yellow]Nenhuma tabela encontrada neste documento.[/]")
        return
    stem = path.stem
    if fmt == "json":
        out = tables_to_json(tables, out_dir, stem)
        console.print(f"[green]✓[/] {len(tables)} tabela(s) → [cyan]{out}[/]")
    else:
        paths = tables_to_csv(tables, out_dir, stem)
        for p in paths:
            console.print(f"[green]✓[/] [cyan]{p}[/]")
        console.print(f"[bold]{len(tables)}[/] tabela(s) exportada(s) para {out_dir}")
    audit("cli_tables", {"file_id": match.get("id"), "count": len(tables), "format": fmt})


# ---------------------------------------------------------------------------
# Gestão de contratos
# ---------------------------------------------------------------------------

@main.command("contracts")
@click.option("--days", type=int, default=90,
              help="Janela de alerta em dias (padrão: 90).")
@click.option("--alert", is_flag=True, default=False,
              help="Enviar alertas por e-mail (requer PDFSEARCHABLE_SMTP_HOST).")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def contracts_cmd(days: int, alert: bool, fmt: str) -> None:
    """Lista contratos indexados e alerta sobre os que estão a expirar."""
    ensure_store()
    from pdfsearchable.contracts import check_expiring_contracts, get_contracts_summary, send_expiry_alerts
    summary = get_contracts_summary()
    alerts = check_expiring_contracts(days_ahead=days)
    if fmt == "json":
        console.print_json(_json.dumps(alerts, default=lambda o: o.__dict__, ensure_ascii=False))
        return
    console.print(
        f"\n[bold]Contratos[/] — total: {summary['total']} · "
        f"expirados: [red]{summary['expired']}[/] · "
        f"a expirar 30d: [yellow]{summary['expiring_30d']}[/] · "
        f"a expirar 90d: {summary['expiring_90d']} · "
        f"sem data: [dim]{summary['no_date']}[/]\n"
    )
    if not alerts:
        console.print(f"[green]✓ Nenhum contrato a expirar nos próximos {days} dias.[/]")
    else:
        _SCOLORS = {"expired": "red", "critical": "red", "warning": "yellow", "notice": "cyan"}
        table = Table("Documento", "Fim", "Dias", "Auto-renov.", "Estado", box=box.SIMPLE_HEAVY)
        for a in alerts:
            c = _SCOLORS.get(a.severity, "white")
            days_str = (f"[red]Expirado há {abs(a.days_until_expiry)}d[/]"
                        if a.days_until_expiry < 0 else f"[{c}]{a.days_until_expiry}d[/]")
            table.add_row(a.name, a.end_date, days_str,
                          "[green]Sim[/]" if a.auto_renewal else "Não", f"[{c}]{a.severity}[/]")
        console.print(table)
        if alert:
            recipients_env = os.environ.get("PDFSEARCHABLE_ALERT_RECIPIENTS", "")
            recipients = [r.strip() for r in recipients_env.split(",") if r.strip()]
            if not recipients:
                console.print("[yellow]⚠ Defina PDFSEARCHABLE_ALERT_RECIPIENTS para enviar e-mails.[/]")
            else:
                smtp_host = os.environ.get("PDFSEARCHABLE_SMTP_HOST", "localhost")
                smtp_port = int(os.environ.get("PDFSEARCHABLE_SMTP_PORT", "587"))
                smtp_user = os.environ.get("PDFSEARCHABLE_SMTP_USER", "")
                smtp_pass = os.environ.get("PDFSEARCHABLE_SMTP_PASS", "")
                ok, errs = send_expiry_alerts(
                    alerts, recipients,
                    smtp_host=smtp_host, smtp_port=smtp_port,
                    smtp_user=smtp_user, smtp_pass=smtp_pass,
                )
                if ok:
                    console.print(f"[green]✓ Alertas enviados para {ok} destinatário(s).[/]")
                for err in errs:
                    console.print(f"[red]Erro SMTP:[/] {err}")


# ---------------------------------------------------------------------------
# Correcção de tipo (loop de aprendizagem)
# ---------------------------------------------------------------------------

@main.command("set-type")
@click.argument("doc_id")
@click.argument("new_type")
@click.option("--feedback/--no-feedback", default=True,
              help="Salvar como exemplo de aprendizagem (padrão: sim).")
def set_type_cmd(doc_id: str, new_type: str, feedback: bool) -> None:
    """Corrige o tipo de um documento e guarda o exemplo para aprendizagem futura.

    DOC_ID pode ser o ID completo, prefixo ou parte do nome.
    NEW_TYPE deve ser um tipo válido (ex.: contrato, relatório, ata, edital…).
    """
    ensure_store()
    idx = load_index()
    files = idx.get("files", [])
    match = next((f for f in files if f.get("id", "").startswith(doc_id)
                  or doc_id.lower() in f.get("name", "").lower()), None)
    if not match:
        console.print(f"[red]Documento '{doc_id}' não encontrado.[/]")
        return
    file_id = match["id"]
    old_type = match.get("doc_type", "?")
    ok = update_doc_type(file_id, new_type, source="manual")
    if not ok:
        console.print("[red]Falha ao actualizar tipo.[/]")
        return
    console.print(f"[green]✓[/] {match.get('name', '')} · {old_type} → [bold]{new_type}[/]")
    if feedback:
        try:
            from pdfsearchable.classifier_feedback import record_correction
            text = load_file_text(file_id) or ""
            record_correction(file_id, new_type, text[:500], source="manual")
            console.print("[dim]Exemplo salvo para aprendizagem futura.[/]")
        except Exception as _e:
            console.print(f"[dim]Aviso: não foi possível salvar exemplo: {_e}[/]")
    audit("cli_set_type", {"file_id": file_id, "old": old_type, "new": new_type})


# ---------------------------------------------------------------------------
# Feedback do classificador
# ---------------------------------------------------------------------------

@main.command("feedback")
@click.argument("action", type=click.Choice(["list", "clear"]), default="list")
def feedback_cmd(action: str) -> None:
    """Gere exemplos de aprendizagem do classificador.

    \b
    list   — Lista todos os exemplos salvos.
    clear  — Remove todos os exemplos.
    """
    ensure_store()
    from pdfsearchable.classifier_feedback import list_examples, clear_examples, example_count
    if action == "clear":
        clear_examples()
        console.print("[green]✓ Exemplos de aprendizagem removidos.[/]")
        return
    examples = list_examples()
    if not examples:
        console.print("[dim]Nenhum exemplo salvo. Use [cyan]pdfsearchable set-type[/] para corrigir classificações.[/]")
        return
    table = Table("Arquivo", "Tipo correcto", "Origem", "Data", box=box.SIMPLE_HEAVY)
    for ex in examples:
        table.add_row(
            ex.get("file_id", "")[:16],
            ex.get("correct_type", ""),
            ex.get("source", ""),
            (ex.get("added_at") or "")[:10],
        )
    console.print(table)
    console.print(f"[dim]{example_count()} exemplo(s) · máximo 50[/]")


# ---------------------------------------------------------------------------
# Anotações
# ---------------------------------------------------------------------------

@main.command("annotations")
@click.argument("doc_id")
@click.option("--export", "do_export", is_flag=True, default=False,
              help="Exportar anotações como JSON.")
@click.option("--delete", "del_id", default=None,
              help="ID da anotação a remover.")
def annotations_cmd(doc_id: str, do_export: bool, del_id: str | None) -> None:
    """Lista, exporta ou remove anotações de um documento.

    DOC_ID pode ser o ID completo, prefixo ou parte do nome.
    """
    ensure_store()
    from pdfsearchable.annotations import AnnotationStore
    idx = load_index()
    files = idx.get("files", [])
    match = next((f for f in files if f.get("id", "").startswith(doc_id)
                  or doc_id.lower() in f.get("name", "").lower()), None)
    if not match:
        console.print(f"[red]Documento '{doc_id}' não encontrado.[/]")
        return
    file_id = match["id"]
    store = AnnotationStore(STORE_DIR)
    if del_id:
        ok = store.delete(file_id, del_id)
        if ok:
            console.print(f"[green]✓ Anotação {del_id} removida.[/]")
        else:
            console.print(f"[red]Anotação '{del_id}' não encontrada.[/]")
        return
    if do_export:
        data = store.export_all(file_id)
        console.print_json(_json.dumps(data, ensure_ascii=False))
        return
    anns = store.get(file_id)
    if not anns:
        console.print(f"[dim]Nenhuma anotação em '{match.get('name', '')}'.[/]")
        return
    table = Table("ID", "Tipo", "Pág.", "Texto", "Nota", box=box.SIMPLE_HEAVY)
    for a in anns:
        table.add_row(
            a.get("id", "")[:8],
            a.get("type", ""),
            str(a.get("page", "")),
            (a.get("text") or "")[:40],
            (a.get("note") or "")[:40],
        )
    console.print(table)
    console.print(f"[dim]{store.count(file_id)} anotação(ões) · use [cyan]--export[/] para JSON completo[/]")


# ---------------------------------------------------------------------------
# Duplicatas semânticas (extensão do comando duplicates)
# ---------------------------------------------------------------------------

@main.command("similar")
@click.option("--threshold", type=float, default=0.92,
              help="Limiar de similaridade cosine (0–1, padrão 0.92).")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def similar_cmd(threshold: float, fmt: str) -> None:
    """Detecta documentos semanticamente semelhantes (near-duplicates).

    Requer embeddings gerados previamente com [cyan]pdfsearchable embed[/].
    """
    ensure_store()
    groups = get_semantic_duplicate_groups(threshold)
    if not groups:
        emb_db = STORE_DIR / "embeddings.sqlite"
        if not emb_db.exists():
            console.print("[yellow]⚠ Nenhum embedding disponível. Execute [cyan]pdfsearchable embed[/] primeiro.[/]")
        else:
            console.print(f"[green]✓ Nenhum par de documentos com similaridade ≥ {threshold}.[/]")
        return
    if fmt == "json":
        out = [
            [{"id": f.get("id"), "name": f.get("name"), "doc_type": f.get("doc_type")} for f in grp]
            for grp in groups
        ]
        console.print_json(_json.dumps(out, ensure_ascii=False))
        return
    for i, grp in enumerate(groups, 1):
        console.print(f"\n[bold yellow]Grupo {i}[/] — {len(grp)} documento(s) semelhantes:")
        for f in grp:
            console.print(f"  · [cyan]{f.get('name', '')}[/]  [dim]{f.get('id', '')[:12]}…[/]  {f.get('doc_type', '')}")
    console.print(f"\n[dim]{len(groups)} grupo(s) detectado(s) com limiar {threshold}[/]")


if __name__ == "__main__":
    main()
