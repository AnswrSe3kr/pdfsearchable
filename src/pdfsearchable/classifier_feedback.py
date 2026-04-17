"""
Loop de aprendizagem do classificador — guarda exemplos de correcções do utilizador
e injeta-os como few-shot nos prompts Ollama.

Storage: STORE_DIR / "classifier_examples.json"
Thread-safety: _feedback_lock (RLock)
Janela deslizante: MAX_EXAMPLES entradas; a mais antiga é descartada quando o limite é atingido.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# STORE_DIR importado de store para ser consistente com o resto do projecto.
# Importação tardia nos pontos de uso evita importação circular no arranque.
_log = logging.getLogger("pdfsearchable.classifier_feedback")

FEEDBACK_FILE_NAME = "classifier_examples.json"
MAX_EXAMPLES = 50  # janela deslizante

# RLock: permite aquisição reentrante pela mesma thread (padrão do projecto, ver store.py).
_feedback_lock = threading.RLock()


def _feedback_file() -> Path:
    """Retorna o caminho para o arquivo de exemplos (calculado em runtime)."""
    from pdfsearchable.store import STORE_DIR
    return STORE_DIR / FEEDBACK_FILE_NAME


def _load_raw() -> dict[str, Any]:
    """
    Carrega o arquivo de exemplos do disco.
    Retorna estrutura vazia com version=1 se o arquivo não existir ou estiver corrompido.
    Deve ser chamado com _feedback_lock já adquirido.
    """
    path = _feedback_file()
    if not path.exists():
        return {"version": 1, "examples": []}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        # Validação mínima de estrutura
        if not isinstance(data, dict) or "examples" not in data:
            raise ValueError("Estrutura inválida")
        if not isinstance(data["examples"], list):
            raise ValueError("Campo 'examples' não é lista")
        return data
    except Exception as exc:
        _log.warning(
            "Arquivo de exemplos %s corrompido ou inválido (%s) — a partir de zero.",
            path, exc,
        )
        return {"version": 1, "examples": []}


def _save_raw(data: dict[str, Any]) -> None:
    """
    Grava o arquivo de exemplos de forma atómica (temp + replace).
    Deve ser chamado com _feedback_lock já adquirido.
    """
    from pdfsearchable.store import STORE_DIR
    STORE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = _feedback_file()
    tmp = path.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except OSError as exc:
        _log.error("Falha ao gravar exemplos de feedback em %s: %s", path, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def record_correction(
    file_id: str,
    correct_type: str,
    text_snippet: str,
    *,
    source: str = "manual",
) -> None:
    """
    Grava um exemplo de correcção no arquivo de exemplos.

    Thread-safe e idempotente: se file_id já existir, remove o registo anterior
    e adiciona o novo no fim (actualização move o exemplo para o topo da janela).
    Se o número de exemplos atingir MAX_EXAMPLES, descarta o mais antigo (examples[0]).

    Parâmetros
    ----------
    file_id:      ID do arquivo corrigido (sha256[:16] hex).
    correct_type: Tipo correcto conforme KNOWN_TYPES.
    text_snippet: Primeiros 500 caracteres do texto do documento.
    source:       Origem da correcção (ex.: "manual", "api").
    """
    snippet = (text_snippet or "")[:500]
    added_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with _feedback_lock:
        data = _load_raw()
        examples: list[dict[str, Any]] = data.get("examples", [])

        # Remover entrada anterior com o mesmo file_id (idempotência / actualização)
        examples = [e for e in examples if e.get("file_id") != file_id]

        # Janela deslizante: descartar o mais antigo se atingir o limite
        while len(examples) >= MAX_EXAMPLES:
            examples.pop(0)

        examples.append(
            {
                "file_id": file_id,
                "text_snippet": snippet,
                "correct_type": correct_type,
                "source": source,
                "added_at": added_at,
            }
        )
        data["examples"] = examples
        data["version"] = 1
        _save_raw(data)
        _log.debug(
            "Exemplo gravado: file_id=%s type=%s source=%s (total=%d)",
            file_id, correct_type, source, len(examples),
        )


def get_few_shot_examples(max_n: int = 5) -> list[dict]:
    """
    Retorna até max_n exemplos mais recentes para few-shot prompting.

    Retorna lista de dicts com apenas os campos necessários para o prompt:
      [{"text_snippet": str, "correct_type": str}, ...]

    Os exemplos são ordenados do mais antigo para o mais recente (ordem de inserção),
    pois o Ollama processa o contexto de cima para baixo.
    """
    with _feedback_lock:
        data = _load_raw()
        examples = data.get("examples", [])
        # Pegar os últimos max_n (mais recentes), mantendo ordem cronológica
        recent = examples[-max_n:] if len(examples) > max_n else examples
        return [
            {"text_snippet": e.get("text_snippet", ""), "correct_type": e.get("correct_type", "")}
            for e in recent
        ]


def clear_examples() -> None:
    """Remove todos os exemplos salvos (reinicia o arquivo)."""
    with _feedback_lock:
        data = {"version": 1, "examples": []}
        _save_raw(data)
        _log.info("Todos os exemplos de feedback foram removidos.")


def list_examples() -> list[dict]:
    """
    Retorna todos os exemplos com metadados completos.

    Cada elemento é um dict com: file_id, text_snippet, correct_type, source, added_at.
    """
    with _feedback_lock:
        data = _load_raw()
        return list(data.get("examples", []))


def example_count() -> int:
    """Retorna o número de exemplos salvos actualmente."""
    with _feedback_lock:
        data = _load_raw()
        return len(data.get("examples", []))
