"""Tool per i file allegati dall'utente (primitiva di upload della UI web).

I file caricati via il pulsante 📎 finiscono in `uploads/` (loopback, mai in rete).
Questi tool danno all'agente — e ai tool generati a runtime (`meta.*`) — un modo
per SCOPRIRE i file allegati e leggerne il contenuto testuale, così un nuovo tool può
partire dal path reale invece di dipendere dall'upload.
"""

from __future__ import annotations

import os
from typing import Any


def list_uploads(uploads_dir: str) -> list[dict[str, Any]]:
    """Elenca i file allegati disponibili (nome, path assoluto, dimensione, mtime)."""
    if not os.path.isdir(uploads_dir):
        return []
    out: list[dict[str, Any]] = []
    for name in sorted(os.listdir(uploads_dir)):
        path = os.path.join(uploads_dir, name)
        if not os.path.isfile(path):
            continue
        st = os.stat(path)
        out.append({
            "name": name,
            "path": path,
            "size_bytes": st.st_size,
            "modified": int(st.st_mtime),
            "ext": os.path.splitext(name)[1].lower().lstrip("."),
        })
    return out


def read_upload_text(uploads_dir: str, name: str, max_bytes: int = 200_000) -> dict[str, Any]:
    """Legge il contenuto TESTUALE di un file allegato (es. .drawio/.xml/.json/.csv/.tf).

    Rifiuta path traversal (solo file dentro `uploads/`). Per file binari (es. .pptx)
    ritorna un errore: quelli vanno gestiti da un tool dedicato che sa parsarli.
    """
    safe = os.path.basename(name)
    path = os.path.join(uploads_dir, safe)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Allegato non trovato: {safe}")
    with open(path, "rb") as f:
        raw = f.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"'{safe}' non è testo UTF-8 (probabile file binario): serve un tool dedicato per parsarlo."
        ) from exc
    return {"name": safe, "path": path, "truncated": truncated, "content": text}
