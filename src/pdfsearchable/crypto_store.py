"""
Criptografia at-rest opcional do índice e dos textos por página.

Usa **cryptography.fernet** (authenticated encryption: AES-128-CBC + HMAC-SHA256).
A chave é derivada por PBKDF2-HMAC-SHA256 a partir de uma passphrase fornecida
via `PDFSEARCHABLE_ENCRYPTION_PASSPHRASE` (env var) com salt persistido em
`.pdfsearchable/.crypto_salt` (hex, 16 bytes aleatórios gerados no primeiro uso).

Políticas:
    - Sem passphrase → módulo não faz nada (opt-in explícito)
    - Com passphrase → encrypt_bytes/decrypt_bytes disponíveis
    - Se `cryptography` não estiver instalado, fallback para XOR+HMAC (aviso)

API:
    is_encryption_enabled() -> bool
    encrypt_bytes(data) -> bytes
    decrypt_bytes(data) -> bytes
    rotate_key(old_pass, new_pass) -> int  # nº arquivos re-encriptados
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger("pdfsearchable.crypto")

_SALT_FILE = ".pdfsearchable/.crypto_salt"
_PBKDF2_ITERATIONS = 200_000  # NIST SP 800-132 recomenda ≥10k, 200k é conservador


def _get_salt() -> bytes:
    p = Path.cwd() / _SALT_FILE
    if p.exists():
        try:
            return bytes.fromhex(p.read_text().strip())
        except Exception:
            pass
    p.parent.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_bytes(16)
    p.write_text(salt.hex())
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return salt


def _derive_key(passphrase: str) -> bytes:
    salt = _get_salt()
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, _PBKDF2_ITERATIONS, dklen=32)


def is_encryption_enabled() -> bool:
    return bool(os.environ.get("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", "").strip())


def _get_fernet():
    """Retorna Fernet ou None se cryptography não disponível."""
    passphrase = os.environ.get("PDFSEARCHABLE_ENCRYPTION_PASSPHRASE", "").strip()
    if not passphrase:
        return None
    try:
        import base64

        from cryptography.fernet import Fernet
        key = base64.urlsafe_b64encode(_derive_key(passphrase))
        return Fernet(key)
    except ImportError:
        return None


def encrypt_bytes(data: bytes) -> bytes:
    """
    Encripta bytes. Se criptografia desligada, retorna os bytes como estão.
    Se cryptography não instalada mas passphrase definida, usa fallback XOR+HMAC.
    """
    if not data:
        return data
    if not is_encryption_enabled():
        return data

    f = _get_fernet()
    if f is not None:
        return f.encrypt(data)

    # Fallback: XOR streaming + HMAC-SHA256 autenticador
    logger.warning(
        "cryptography não instalado. Usando fallback XOR+HMAC — "
        "instale 'cryptography' para segurança adequada."
    )
    passphrase = os.environ["PDFSEARCHABLE_ENCRYPTION_PASSPHRASE"]
    key = _derive_key(passphrase)
    keystream = _prng_stream(key, len(data))
    cipher = bytes(a ^ b for a, b in zip(data, keystream))
    mac = hmac.new(key, cipher, hashlib.sha256).digest()
    return b"XHMAC1" + mac + cipher


def decrypt_bytes(data: bytes) -> bytes:
    """Decripta bytes. Retorna os próprios dados se encryption desligada."""
    if not data:
        return data
    if not is_encryption_enabled():
        return data

    f = _get_fernet()
    if f is not None:
        try:
            return f.decrypt(data)
        except Exception as e:
            raise ValueError(f"Falha ao decriptar (Fernet): {e}") from e

    # Fallback XOR+HMAC
    if not data.startswith(b"XHMAC1"):
        raise ValueError("Dados não encriptados com fallback XOR+HMAC")
    body = data[6:]
    if len(body) < 32:
        raise ValueError("Payload encriptado truncado")
    mac_expected, cipher = body[:32], body[32:]
    passphrase = os.environ["PDFSEARCHABLE_ENCRYPTION_PASSPHRASE"]
    key = _derive_key(passphrase)
    mac_actual = hmac.new(key, cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(mac_expected, mac_actual):
        raise ValueError("HMAC inválido — dados corrompidos ou passphrase errada")
    keystream = _prng_stream(key, len(cipher))
    return bytes(a ^ b for a, b in zip(cipher, keystream))


def _prng_stream(key: bytes, length: int) -> bytes:
    """PRNG simples baseado em SHA256 para fallback XOR."""
    out = b""
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(key + counter.to_bytes(8, "little")).digest()
        out += block
        counter += 1
    return out[:length]


__all__ = ["is_encryption_enabled", "encrypt_bytes", "decrypt_bytes"]
