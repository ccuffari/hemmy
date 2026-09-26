"""Cifratura a riposo dei segreti applicativi per-utente (nacl.secret.SecretBox).

La master key, in ordine di priorità:
1. `SECRETS_MASTER_KEY` (env var, base64 di 32 byte) — OBBLIGATORIA in produzione
   (Cloud Run/container con filesystem effimero): se il processo la generasse da
   solo ad ogni riavvio, i segreti già cifrati diventerebbero illeggibili.
2. File locale (`config/.master.key`, gitignored) — generato al primo avvio se
   manca sia l'env var sia il file. Comodo per sviluppo locale, dove il disco
   persiste tra i riavvii.

Usata sia da `auth.users.UserStore` (SQLite) sia da `auth.supabase_users.SupabaseUserStore`
(Postgres): stessa cifratura, cambia solo dove finisce il ciphertext.
"""

from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path


def _default_local_path() -> Path:
    # Ancorato alla project root, MAI un percorso relativo bare (quello
    # dipende dalla cwd del processo — bug osservato in pratica: un file
    # "vagante" `src/hemmy/config/.master.key`) e MAI solo `parents[3]` da
    # questo file (funziona solo con install editable; con un install
    # normale — quello di produzione — il pacchetto vive sotto
    # site-packages, tutt'altra gerarchia rispetto a dove sta `config/`).
    # `HEMMY_PROJECT_ROOT` (impostata dal Dockerfile) è la fonte di verità
    # in un container; in produzione comunque questo path non dovrebbe mai
    # essere raggiunto, perché lì `SECRETS_MASTER_KEY` è sempre impostata.
    env_root = os.environ.get("HEMMY_PROJECT_ROOT")
    root = Path(env_root) if env_root else Path(__file__).resolve().parents[3]
    return root / "config" / ".master.key"


def load_master_key(local_path: str | None = None) -> bytes:
    env_key = os.environ.get("SECRETS_MASTER_KEY")
    if env_key:
        key = base64.b64decode(env_key)
        if len(key) != 32:
            raise ValueError(
                "SECRETS_MASTER_KEY non valida: attesi 32 byte codificati in base64 "
                f"(trovati {len(key)})."
            )
        return key

    p = Path(local_path) if local_path else _default_local_path()
    if p.exists():
        return base64.b64decode(p.read_text().strip())

    key = secrets.token_bytes(32)  # SecretBox key size
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(base64.b64encode(key).decode())
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return key


def encrypt(plaintext: str, key: bytes) -> str:
    from nacl.secret import SecretBox

    box = SecretBox(key)
    return base64.b64encode(box.encrypt(plaintext.encode())).decode()


def decrypt(token: str, key: bytes) -> str:
    from nacl.secret import SecretBox

    box = SecretBox(key)
    return box.decrypt(base64.b64decode(token)).decode()
