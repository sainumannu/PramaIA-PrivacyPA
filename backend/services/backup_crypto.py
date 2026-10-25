"""
Cifratura a riposo dei file di backup (§57: «backup database cifrato»).

Formato: intestazione (`PPABK1` + salt 16 byte + prefisso nonce 4 byte) e poi blocchi da 1 MiB cifrati con AES-256-GCM, ciascuno
preceduto dalla sua lunghezza (4 byte). Il nonce di ogni blocco è prefisso + contatore; l'intestazione, il numero del blocco e un
indicatore «ultimo blocco» sono dati autenticati: un blocco alterato, spostato, mancante o un file troncato non si decifra.

La chiave AES si ricava con scrypt dalla passphrase `BACKUP_ENCRYPTION_KEY` e dal salt del file. Senza passphrase i backup restano
in chiaro (e il servizio lo segnala). Perdere la passphrase significa perdere i backup: va custodita a parte (vedi runbook).
"""
import hashlib
import os
import struct
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"PPABK1"
SALT_LEN, PREFIX_LEN, CHUNK = 16, 4, 1024 * 1024
HEADER_LEN = len(MAGIC) + SALT_LEN + PREFIX_LEN
SUFFIX = ".enc"


class BackupCryptoError(ValueError):
    """Passphrase mancante o errata, file non cifrato, alterato o troncato."""


def passphrase() -> str | None:
    return os.getenv("BACKUP_ENCRYPTION_KEY") or None


def is_encrypted(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def _key(secret: str, salt: bytes) -> bytes:
    return hashlib.scrypt(secret.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


def _nonce(prefix: bytes, index: int) -> bytes:
    return prefix + struct.pack(">Q", index)


def _aad(header: bytes, index: int, final: bool) -> bytes:
    return header + struct.pack(">Q", index) + (b"\x01" if final else b"\x00")


def encrypt_file(src: Path, dst: Path, secret: str | None = None) -> None:
    secret = secret or passphrase()
    if not secret:
        raise BackupCryptoError("Passphrase di cifratura non configurata (BACKUP_ENCRYPTION_KEY)")
    salt, prefix = os.urandom(SALT_LEN), os.urandom(PREFIX_LEN)
    header = MAGIC + salt + prefix
    aes = AESGCM(_key(secret, salt))
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        fout.write(header)
        index, block = 0, fin.read(CHUNK)
        while True:
            following = fin.read(CHUNK)
            final = not following
            sealed = aes.encrypt(_nonce(prefix, index), block, _aad(header, index, final))
            fout.write(struct.pack(">I", len(sealed)) + sealed)
            if final:
                break
            index, block = index + 1, following


def decrypt_file(src: Path, dst: Path, secret: str | None = None) -> None:
    secret = secret or passphrase()
    if not secret:
        raise BackupCryptoError("Passphrase di cifratura non configurata (BACKUP_ENCRYPTION_KEY)")
    with open(src, "rb") as fin:
        header = fin.read(HEADER_LEN)
        if len(header) != HEADER_LEN or not header.startswith(MAGIC):
            raise BackupCryptoError("Il file non è un backup cifrato")
        salt, prefix = header[len(MAGIC):len(MAGIC) + SALT_LEN], header[len(MAGIC) + SALT_LEN:]
        aes = AESGCM(_key(secret, salt))
        try:
            with open(dst, "wb") as fout:
                index, size = 0, fin.read(4)
                if len(size) != 4:
                    raise BackupCryptoError("Backup cifrato troncato")
                while size:
                    (length,) = struct.unpack(">I", size)
                    sealed = fin.read(length)
                    following = fin.read(4)
                    if len(sealed) != length:
                        raise BackupCryptoError("Backup cifrato troncato")
                    fout.write(aes.decrypt(_nonce(prefix, index), sealed, _aad(header, index, not following)))
                    index, size = index + 1, following
                    if size and len(size) != 4:
                        raise BackupCryptoError("Backup cifrato troncato")
        except InvalidTag:
            dst.unlink(missing_ok=True)
            raise BackupCryptoError("Passphrase errata oppure file alterato o troncato") from None
        except BackupCryptoError:
            dst.unlink(missing_ok=True)
            raise
