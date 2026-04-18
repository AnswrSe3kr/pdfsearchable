#!/usr/bin/env python3
"""
setup.py — Verifica e instala dependências do pdfsearchable.

Funciona em Windows, Linux e macOS. Detecta o SO, verifica dependências
de sistema (Tesseract, Poppler) e Python, e oferece instalação automática.

Uso:
    python setup.py          # Verifica tudo e instala o que faltar
    python setup.py --check  # Apenas verifica, sem instalar
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
PYTHON_MIN = (3, 10)
PROJECT_ROOT = Path(__file__).resolve().parent

_BOLD = "\033[1m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_CYAN = "\033[96m"
_RESET = "\033[0m"

# Desativar cores no Windows CMD sem suporte ANSI
if platform.system() == "Windows" and not os.environ.get("WT_SESSION"):
    try:
        os.system("")  # habilita VT100 no Windows 10+
    except Exception:
        _BOLD = _GREEN = _YELLOW = _RED = _CYAN = _RESET = ""


def _ok(msg: str) -> None:
    print(f"  {_GREEN}✔{_RESET} {msg}")


def _warn(msg: str) -> None:
    print(f"  {_YELLOW}⚠{_RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"  {_RED}✘{_RESET} {msg}")


def _info(msg: str) -> None:
    print(f"  {_CYAN}ℹ{_RESET} {msg}")


def _header(msg: str) -> None:
    print(f"\n{_BOLD}{msg}{_RESET}")


# ---------------------------------------------------------------------------
# Detecção de SO
# ---------------------------------------------------------------------------
def _detect_os() -> str:
    """Retorna 'linux', 'darwin' ou 'windows'."""
    s = platform.system().lower()
    if s == "darwin":
        return "darwin"
    if s == "windows":
        return "windows"
    return "linux"


def _has_command(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _run(cmd: list[str], check: bool = True, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True, **kw)


# ---------------------------------------------------------------------------
# Verificação de Python
# ---------------------------------------------------------------------------
def check_python() -> bool:
    _header("Python")
    v = sys.version_info
    if (v.major, v.minor) >= PYTHON_MIN:
        _ok(f"Python {v.major}.{v.minor}.{v.micro}")
        return True
    _fail(f"Python {v.major}.{v.minor} — mínimo exigido: {PYTHON_MIN[0]}.{PYTHON_MIN[1]}")
    return False


# ---------------------------------------------------------------------------
# Dependências de sistema
# ---------------------------------------------------------------------------
_SYSTEM_DEPS: dict[str, dict[str, str | list[str]]] = {
    "tesseract": {
        "linux": "sudo apt-get install -y tesseract-ocr tesseract-ocr-por tesseract-ocr-eng tesseract-ocr-deu tesseract-ocr-fra tesseract-ocr-rus tesseract-ocr-spa",
        "darwin": "brew install tesseract tesseract-lang",
        "windows": "choco install tesseract",
        "check": "tesseract",
        "desc": "Tesseract OCR",
    },
    "ollama": {
        "linux": "curl -fsSL https://ollama.com/install.sh | sh",
        "darwin": "brew install ollama",
        "windows": "winget install Ollama.Ollama",
        "check": "ollama",
        "desc": "Ollama (IA local — opcional)",
        "optional": True,
    },
}


def check_system_deps(os_name: str, install: bool = False) -> list[str]:
    _header("Dependências de sistema")
    missing = []
    for name, info in _SYSTEM_DEPS.items():
        cmd = info["check"]
        desc = info["desc"]
        optional = info.get("optional", False)
        if _has_command(cmd):
            version_str = ""
            try:
                r = _run([cmd, "--version"], check=False)
                first_line = (r.stdout or r.stderr or "").strip().split("\n")[0]
                if first_line:
                    version_str = f" ({first_line[:60]})"
            except Exception:
                pass
            _ok(f"{desc}{version_str}")
        else:
            if optional:
                _warn(f"{desc} — não encontrado (opcional)")
            else:
                _fail(f"{desc} — não encontrado")
                missing.append(name)
                install_cmd = info.get(os_name, "")
                if install_cmd:
                    if install:
                        _info(f"Instalando: {install_cmd}")
                        try:
                            subprocess.run(install_cmd, shell=True, check=True)
                            _ok(f"{desc} instalado com sucesso")
                            missing.remove(name)
                        except subprocess.CalledProcessError:
                            _fail(f"Falha ao instalar {desc}")
                    else:
                        _info(f"Para instalar: {install_cmd}")
    return missing


# ---------------------------------------------------------------------------
# Tesseract — idiomas instalados
# ---------------------------------------------------------------------------
def check_tesseract_langs() -> None:
    if not _has_command("tesseract"):
        return
    _header("Tesseract — idiomas OCR")
    try:
        r = _run(["tesseract", "--list-langs"], check=False)
        output = r.stdout or r.stderr or ""
        langs = [l.strip() for l in output.strip().split("\n") if l.strip() and not l.startswith("List")]
        if langs:
            _ok(f"{len(langs)} idioma(s): {', '.join(sorted(langs)[:15])}" +
                (" ..." if len(langs) > 15 else ""))
            recommended = {"por", "eng", "deu", "fra", "rus", "spa"}
            installed = set(langs)
            missing = recommended - installed
            if missing:
                _warn(f"Recomendados em falta: {', '.join(sorted(missing))}")
                os_name = _detect_os()
                if os_name == "linux":
                    pkgs = " ".join(f"tesseract-ocr-{l}" for l in sorted(missing))
                    _info(f"sudo apt-get install -y {pkgs}")
                elif os_name == "darwin":
                    _info("brew install tesseract-lang  (instala todos os idiomas)")
        else:
            _warn("Nenhum idioma encontrado")
    except Exception:
        _warn("Não foi possível listar idiomas do Tesseract")


# ---------------------------------------------------------------------------
# Dependências Python (pip)
# ---------------------------------------------------------------------------
def check_python_deps(install: bool = False) -> list[str]:
    _header("Dependências Python")
    missing = []

    # Verificar se o projeto está instalado
    pyproject = PROJECT_ROOT / "pyproject.toml"
    if not pyproject.exists():
        _fail("pyproject.toml não encontrado")
        return ["pyproject.toml"]

    # Tentar importar dependências core
    core_deps = {
        "click": "click",
        "fitz": "PyMuPDF (pymupdf)",
        "rich": "rich",
        "wordcloud": "wordcloud",
        "jinja2": "Jinja2",
        "pytesseract": "pytesseract",
        "PIL": "Pillow",
        "numpy": "numpy",
    }

    for module, desc in core_deps.items():
        try:
            __import__(module)
            _ok(desc)
        except ImportError:
            _fail(f"{desc} — não instalado")
            missing.append(desc)

    # Dependências opcionais
    optional_deps = {
        "transformers": "transformers (HTR — pip install .[htr])",
        "torch": "PyTorch (HTR — pip install .[htr])",
        "openai": "openai (IA — pip install .[ai])",
        "faiss": "faiss-cpu (semântica — pip install .[semantic])",
        "img2table": "img2table (tabelas — pip install .[tables-ocr])",
    }

    _header("Dependências Python (opcionais)")
    for module, desc in optional_deps.items():
        try:
            __import__(module)
            _ok(desc)
        except ImportError:
            _warn(f"{desc} — não instalado")

    if missing and install:
        _header("Instalando dependências Python")
        _info("pip install -e .")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", str(PROJECT_ROOT)],
                check=True,
            )
            _ok("Dependências core instaladas")
            missing.clear()
        except subprocess.CalledProcessError:
            _fail("Falha ao instalar dependências Python")
    elif missing:
        _info(f"Para instalar: pip install -e {PROJECT_ROOT}")

    return missing


# ---------------------------------------------------------------------------
# Verificação do projeto
# ---------------------------------------------------------------------------
def check_project() -> bool:
    _header("Projeto pdfsearchable")
    try:
        from pdfsearchable import __version__
        _ok(f"pdfsearchable v{__version__} instalado")
        return True
    except ImportError:
        _fail("pdfsearchable não está instalado")
        _info(f"Execute: pip install -e {PROJECT_ROOT}")
        return False


# ---------------------------------------------------------------------------
# Verificar espaço em disco
# ---------------------------------------------------------------------------
def check_disk() -> None:
    _header("Espaço em disco")
    try:
        usage = shutil.disk_usage(PROJECT_ROOT)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        if free_gb > 1:
            _ok(f"{free_gb:.1f} GB livres de {total_gb:.1f} GB")
        else:
            _warn(f"Pouco espaço: {free_gb:.2f} GB livres de {total_gb:.1f} GB")
    except Exception:
        _warn("Não foi possível verificar espaço em disco")


# ---------------------------------------------------------------------------
# Resumo
# ---------------------------------------------------------------------------
def print_summary(errors: list[str]) -> None:
    _header("Resumo")
    if not errors:
        print(f"\n  {_GREEN}{_BOLD}Tudo pronto!{_RESET} O pdfsearchable está configurado.")
        print(f"\n  Primeiros passos:")
        print(f"    {_CYAN}pdfsearchable init{_RESET}               — inicializar projeto")
        print(f"    {_CYAN}pdfsearchable add pasta/{_RESET}         — adicionar PDFs")
        print(f"    {_CYAN}pdfsearchable search \"termo\"{_RESET}     — pesquisar")
        print(f"    {_CYAN}pdfsearchable doctor{_RESET}             — diagnóstico completo")
        print()
    else:
        print(f"\n  {_RED}{_BOLD}Problemas encontrados:{_RESET}")
        for e in errors:
            print(f"    {_RED}•{_RESET} {e}")
        print(f"\n  Execute novamente após resolver: {_CYAN}python setup.py{_RESET}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    check_only = "--check" in sys.argv
    install = not check_only

    print(f"\n{_BOLD}{'='*55}{_RESET}")
    print(f"{_BOLD}  pdfsearchable — Verificação de ambiente{_RESET}")
    print(f"{_BOLD}{'='*55}{_RESET}")
    print(f"  SO: {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"  Python: {sys.version.split()[0]}")

    os_name = _detect_os()
    errors: list[str] = []

    if not check_python():
        errors.append(f"Python >= {PYTHON_MIN[0]}.{PYTHON_MIN[1]} necessário")

    sys_missing = check_system_deps(os_name, install=install)
    if sys_missing:
        errors.extend(f"{_SYSTEM_DEPS[n]['desc']} não instalado" for n in sys_missing)

    check_tesseract_langs()

    py_missing = check_python_deps(install=install)
    if py_missing:
        errors.append("Dependências Python em falta")

    check_project()
    check_disk()
    print_summary(errors)

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
