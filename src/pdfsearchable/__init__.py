"""pdfsearchable — Processa, indexa e pesquisa em PDFs. Report visual Apple-like, gerado apenas em modo servidor (pdfsearchable serve)."""

import sys as _sys

# Verificação de versão Python em runtime (o pyproject.toml exige >=3.10).
# Mensagem clara antes de qualquer ImportError de sintaxe.
if _sys.version_info < (3, 10):
    raise RuntimeError(
        f"pdfsearchable requer Python 3.10 ou superior. "
        f"Versão atual: {_sys.version_info.major}.{_sys.version_info.minor}.{_sys.version_info.micro}. "
        f"Atualize o Python: https://www.python.org/downloads/"
    )

__version__ = "0.4.0"

from pdfsearchable.exceptions import (
    PdfSearchableError,
    ValidationError,
    IndexingError,
    StoreError,
    ReportError,
    OcrError,
    ConfigError,
)

__all__ = [
    "ConfigError",
    "IndexingError",
    "OcrError",
    "PdfSearchableError",
    "ReportError",
    "StoreError",
    "ValidationError",
    "__version__",
]
