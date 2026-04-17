"""
Gestão de anotações e destaques por documento PDF.

As anotações são armazenadas em arquivos JSON independentes do index.json,
em `store_dir/annotations/<file_id>.json`. Thread-safe via RLock por file_id.
Escrita atómica: temp file + Path.replace() (igual ao padrão de store.py).
"""

import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger("pdfsearchable.annotations")

# Regex para validar file_id (16 hex, igual ao padrão de store.py)
_FILE_ID_RE = re.compile(r"^[a-fA-F0-9]{16}$")

# Regex para validar cor em formato #RRGGBB
_COLOR_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

_VALID_TYPES = frozenset({"highlight", "note"})
_DEFAULT_COLOR = "#FFFF00"
_SCHEMA_VERSION = 1


def _now_iso() -> str:
    """Retorna a data/hora actual em ISO 8601 UTC (ex.: 2024-01-01T12:00:00+00:00)."""
    return datetime.now(timezone.utc).isoformat()


def _validate_file_id(file_id: str) -> bool:
    """Valida que file_id tem formato 16 hex (evita path traversal)."""
    return bool(file_id and _FILE_ID_RE.match(file_id))


def _validate_color(color: str) -> bool:
    """Valida que color é uma string #RRGGBB válida."""
    return bool(color and _COLOR_HEX_RE.match(color))


def _validate_position(position: Any) -> bool:
    """
    Valida que position é um dict com x e y numéricos no intervalo [0, 1].
    Retorna False se o valor for inválido.
    """
    if not isinstance(position, dict):
        return False
    try:
        x = float(position.get("x", -1))
        y = float(position.get("y", -1))
        return 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0
    except (TypeError, ValueError):
        return False


class AnnotationStore:
    """
    Armazenamento de anotações e destaques por documento PDF.

    Cada documento tem o seu próprio arquivo JSON em:
        store_dir/annotations/<file_id>.json

    Thread-safety: RLock individual por file_id, protegido por _meta_lock.
    Escrita atómica: temp file + Path.replace().
    """

    def __init__(self, store_dir: Path) -> None:
        """
        Inicializa o AnnotationStore.

        Parâmetros:
            store_dir: Directório raiz do armazenamento (ex.: .pdfsearchable/).
                       O subdirectório 'annotations/' é criado automaticamente.
        """
        self._annotations_dir = Path(store_dir) / "annotations"
        self._annotations_dir.mkdir(parents=True, exist_ok=True)
        # Lock para proteger a criação de locks individuais por file_id
        self._meta_lock: threading.RLock = threading.RLock()
        # Locks individuais por file_id para operações de leitura/escrita
        self._locks: dict[str, threading.RLock] = {}

    # ------------------------------------------------------------------
    # Gestão de locks
    # ------------------------------------------------------------------

    def _get_lock(self, file_id: str) -> threading.RLock:
        """
        Retorna o RLock associado ao file_id, criando-o se necessário.
        A criação do lock é protegida por _meta_lock para evitar race conditions.
        """
        with self._meta_lock:
            if file_id not in self._locks:
                self._locks[file_id] = threading.RLock()
            return self._locks[file_id]

    # ------------------------------------------------------------------
    # I/O atómico
    # ------------------------------------------------------------------

    def _ann_path(self, file_id: str) -> Path:
        """Retorna o caminho do arquivo JSON de anotações para o file_id."""
        return self._annotations_dir / f"{file_id}.json"

    def _load_raw(self, file_id: str) -> dict[str, Any]:
        """
        Carrega o arquivo JSON de anotações do disco.
        Retorna estrutura vazia se o arquivo não existir.
        Deve ser chamado dentro do lock do file_id.
        """
        path = self._ann_path(file_id)
        if not path.exists():
            return {
                "version": _SCHEMA_VERSION,
                "file_id": file_id,
                "annotations": [],
            }
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            # Garantir que a estrutura tem os campos mínimos
            if not isinstance(data.get("annotations"), list):
                data["annotations"] = []
            data.setdefault("version", _SCHEMA_VERSION)
            data.setdefault("file_id", file_id)
            return data
        except (json.JSONDecodeError, OSError) as exc:
            _log.warning(
                "AnnotationStore._load_raw: falha ao ler '%s': %s — devolvendo estrutura vazia.",
                path, exc,
            )
            return {
                "version": _SCHEMA_VERSION,
                "file_id": file_id,
                "annotations": [],
            }

    def _save_raw(self, file_id: str, data: dict[str, Any]) -> None:
        """
        Grava o arquivo JSON de anotações de forma atómica (temp + rename).
        Deve ser chamado dentro do lock do file_id.
        """
        path = self._ann_path(file_id)
        tmp = path.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            tmp.replace(path)
        except OSError as exc:
            _log.error(
                "AnnotationStore._save_raw: falha ao gravar '%s': %s", path, exc
            )
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def get(self, file_id: str) -> list[dict]:
        """
        Retorna todas as anotações do documento identificado por file_id.

        Parâmetros:
            file_id: Identificador do documento (16 hex).

        Retorna:
            Lista de dicts com as anotações, ou lista vazia se não houver anotações
            ou o file_id for inválido.
        """
        if not _validate_file_id(file_id):
            _log.debug("AnnotationStore.get: file_id inválido '%s'", file_id)
            return []
        with self._get_lock(file_id):
            data = self._load_raw(file_id)
            return list(data.get("annotations", []))

    def add(self, file_id: str, annotation: dict) -> str:
        """
        Adiciona uma anotação ao documento e retorna o ID gerado (uuid4 hex).

        A anotação deve conter os campos obrigatórios:
            - type: 'highlight' ou 'note'
            - page: int >= 1
            - text: str não vazio

        Campos opcionais:
            - note: comentário do utilizador (str)
            - color: cor em #RRGGBB (default '#FFFF00')
            - position: dict com x e y float entre 0 e 1

        Parâmetros:
            file_id: Identificador do documento (16 hex).
            annotation: Dict com os campos da anotação.

        Retorna:
            ID da anotação criada (uuid4 hex).

        Levanta:
            ValueError: Se file_id ou campos obrigatórios forem inválidos.
        """
        if not _validate_file_id(file_id):
            raise ValueError(f"file_id inválido: '{file_id}' (esperado 16 hex)")

        # Validação dos campos obrigatórios
        ann_type = annotation.get("type")
        if ann_type not in _VALID_TYPES:
            raise ValueError(
                f"Campo 'type' inválido: '{ann_type}'. Valores válidos: {sorted(_VALID_TYPES)}"
            )

        page = annotation.get("page")
        if not isinstance(page, int) or page < 1:
            raise ValueError(
                f"Campo 'page' inválido: '{page}'. Deve ser int >= 1."
            )

        text = annotation.get("text")
        if not text or not isinstance(text, str) or not text.strip():
            raise ValueError("Campo 'text' não pode ser vazio.")

        # Validação e normalização dos campos opcionais
        color = annotation.get("color", _DEFAULT_COLOR)
        if not isinstance(color, str):
            color = _DEFAULT_COLOR
        if not _validate_color(color):
            _log.debug(
                "AnnotationStore.add: cor inválida '%s', usando default '%s'",
                color, _DEFAULT_COLOR,
            )
            color = _DEFAULT_COLOR

        position = annotation.get("position")
        if position is not None and not _validate_position(position):
            raise ValueError(
                f"Campo 'position' inválido: '{position}'. "
                "Esperado dict com 'x' e 'y' float entre 0 e 1."
            )

        now = _now_iso()
        ann_id = uuid.uuid4().hex

        new_ann: dict[str, Any] = {
            "id": ann_id,
            "type": ann_type,
            "page": page,
            "text": text.strip(),
            "color": color,
            "created_at": now,
            "updated_at": now,
        }

        note = annotation.get("note")
        if note is not None:
            new_ann["note"] = str(note)

        if position is not None:
            new_ann["position"] = {
                "x": float(position["x"]),
                "y": float(position["y"]),
            }

        with self._get_lock(file_id):
            data = self._load_raw(file_id)
            data["annotations"].append(new_ann)
            self._save_raw(file_id, data)

        _log.debug(
            "AnnotationStore.add: adicionada anotação '%s' ao documento '%s' (página %d)",
            ann_id, file_id, page,
        )
        return ann_id

    def update(self, file_id: str, ann_id: str, data_update: dict) -> bool:
        """
        Actualiza os campos de uma anotação existente.

        Apenas os campos presentes em data_update são alterados.
        Os campos 'id', 'created_at' e 'file_id' não podem ser alterados.
        O campo 'updated_at' é actualizado automaticamente.

        Parâmetros:
            file_id: Identificador do documento (16 hex).
            ann_id: ID da anotação a actualizar.
            data_update: Dict com os campos a actualizar.

        Retorna:
            True se a anotação foi encontrada e actualizada, False caso contrário.
        """
        if not _validate_file_id(file_id):
            return False
        if not ann_id:
            return False

        # Campos imutáveis que não podem ser substituídos
        _IMMUTABLE = frozenset({"id", "created_at"})

        with self._get_lock(file_id):
            data = self._load_raw(file_id)
            annotations = data.get("annotations", [])
            for ann in annotations:
                if ann.get("id") == ann_id:
                    for key, value in data_update.items():
                        if key in _IMMUTABLE:
                            continue
                        # Validações específicas por campo
                        if key == "type" and value not in _VALID_TYPES:
                            raise ValueError(
                                f"Campo 'type' inválido: '{value}'."
                            )
                        if key == "page" and (not isinstance(value, int) or value < 1):
                            raise ValueError(
                                f"Campo 'page' inválido: '{value}'. Deve ser int >= 1."
                            )
                        if key == "color" and not _validate_color(str(value)):
                            raise ValueError(
                                f"Campo 'color' inválido: '{value}'."
                            )
                        if key == "position" and not _validate_position(value):
                            raise ValueError(
                                f"Campo 'position' inválido: '{value}'."
                            )
                        ann[key] = value
                    ann["updated_at"] = _now_iso()
                    self._save_raw(file_id, data)
                    _log.debug(
                        "AnnotationStore.update: anotação '%s' do documento '%s' actualizada.",
                        ann_id, file_id,
                    )
                    return True
        return False

    def delete(self, file_id: str, ann_id: str) -> bool:
        """
        Remove uma anotação do documento.

        Parâmetros:
            file_id: Identificador do documento (16 hex).
            ann_id: ID da anotação a remover.

        Retorna:
            True se a anotação foi encontrada e removida, False caso contrário.
        """
        if not _validate_file_id(file_id):
            return False
        if not ann_id:
            return False

        with self._get_lock(file_id):
            data = self._load_raw(file_id)
            annotations = data.get("annotations", [])
            original_len = len(annotations)
            data["annotations"] = [a for a in annotations if a.get("id") != ann_id]
            if len(data["annotations"]) == original_len:
                return False
            self._save_raw(file_id, data)

        _log.debug(
            "AnnotationStore.delete: anotação '%s' removida do documento '%s'.",
            ann_id, file_id,
        )
        return True

    def export_all(self, file_id: str) -> dict:
        """
        Retorna o dict completo do arquivo de anotações, incluindo metadados,
        adequado para exportação ou backup.

        Parâmetros:
            file_id: Identificador do documento (16 hex).

        Retorna:
            Dict com 'version', 'file_id' e 'annotations'. Devolve estrutura vazia
            se o file_id for inválido ou não houver anotações salvas.
        """
        if not _validate_file_id(file_id):
            return {"version": _SCHEMA_VERSION, "file_id": file_id, "annotations": []}
        with self._get_lock(file_id):
            return self._load_raw(file_id)

    def list_files_with_annotations(self) -> list[str]:
        """
        Retorna a lista de file_ids que têm pelo menos uma anotação salva.

        Percorre os arquivos JSON no directório de anotações e devolve apenas
        os file_ids com lista de anotações não vazia.
        """
        result: list[str] = []
        try:
            for path in sorted(self._annotations_dir.glob("*.json")):
                # Ignorar arquivos temporários
                if path.suffix != ".json" or path.stem.endswith(".tmp"):
                    continue
                file_id = path.stem
                if not _validate_file_id(file_id):
                    continue
                with self._get_lock(file_id):
                    data = self._load_raw(file_id)
                    if data.get("annotations"):
                        result.append(file_id)
        except OSError as exc:
            _log.warning(
                "AnnotationStore.list_files_with_annotations: erro ao listar directório: %s",
                exc,
            )
        return result

    def count(self, file_id: str) -> int:
        """
        Retorna o número de anotações do documento identificado por file_id.

        Parâmetros:
            file_id: Identificador do documento (16 hex).

        Retorna:
            Número de anotações, ou 0 se o file_id for inválido ou não houver anotações.
        """
        if not _validate_file_id(file_id):
            return 0
        with self._get_lock(file_id):
            data = self._load_raw(file_id)
            return len(data.get("annotations", []))
