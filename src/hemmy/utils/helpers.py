"""Utility trasversali: logger, schema_diff, retry.

STUB: implementazioni base/segnaposto da rifinire.
"""

from __future__ import annotations

import functools
import getpass
import logging
import re
import time
from typing import Any, Callable

# Pattern di segreti da oscurare prima di qualsiasi output verso il modello LLM.
# I gruppi (chiave=) vengono mantenuti; il valore viene sostituito con ***REDACTED***.
_SECRET_KV_PATTERNS = [
    re.compile(r"(AccountKey=)[^;]+", re.IGNORECASE),
    re.compile(r"(SharedAccessKey=)[^;]+", re.IGNORECASE),
    re.compile(r"(Pwd=)[^;]+", re.IGNORECASE),
    re.compile(r"(Password=)[^;]+", re.IGNORECASE),
    re.compile(r"(Uid=)[^;]+", re.IGNORECASE),
    re.compile(r"(User ID=)[^;]+", re.IGNORECASE),
    re.compile(r"(client_secret=)[^;&\s]+", re.IGNORECASE),
    re.compile(r"(sig=)[^;&\s]+", re.IGNORECASE),  # SAS token
]
# Segreti "interi" (senza chiave=): connection string complete, bearer/API key, token.
_SECRET_BLOB_PATTERNS = [
    re.compile(r"DefaultEndpointsProtocol=.+?(?:core\.windows\.net|$)", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"ghp_[A-Za-z0-9]+"),
    re.compile(r"x-access-token:[^@\s]+@"),  # URL git con token
]

_REDACTED = "***REDACTED***"


def redact_secrets(text: str | None) -> str | None:
    """Oscura credenziali/connection string/token in un testo.

    Rete di sicurezza applicata a QUALSIASI output prima che raggiunga il modello LLM
    (Observation) o i log, così che password, username, connection string, chiavi e
    token non vengano mai esposti al modello.
    """
    if not text:
        return text
    for pat in _SECRET_KV_PATTERNS:
        text = pat.sub(lambda m: m.group(1) + _REDACTED, text)
    for pat in _SECRET_BLOB_PATTERNS:
        text = pat.sub(_REDACTED, text)
    return text


class SessionSecretProvider:
    """Provider di segreti con cache di sessione (in-process).

    Chiede il segreto all'operatore la prima volta per una data risorsa (label) e lo
    riusa per le chiamate successive nella stessa sessione, evitando di ri-chiederlo
    a ogni tool. Il segreto resta SOLO nel processo Python: non viene mai passato al
    modello LLM, restituito nelle Observation o loggato.
    """

    def __init__(self, prompt_fn: Callable[[str], str] | None = None) -> None:
        self._cache: dict[str, str] = {}
        # Funzione di richiesta segreto iniettabile: fn(label) -> str. Default = prompt
        # CLI (getpass). La UI web inietta un callback basato su eventi/queue.
        self._prompt_fn = prompt_fn or prompt_secret

    def __call__(self, label: str) -> str:
        if label in self._cache:
            return self._cache[label]
        value = self._prompt_fn(label)
        if value:
            self._cache[label] = value
        return value

    def clear(self) -> None:
        """Svuota la cache (es. a fine sessione)."""
        self._cache.clear()


def prompt_secret(label: str) -> str:
    """Legge un segreto dall'operatore (man-in-the-middle) senza eco a schermo.

    Il valore restituito NON deve mai essere passato al modello LLM, inserito in
    Observation/memoria o scritto nei log: viene usato solo in modo transitorio dal
    tool che lo richiede.
    """
    print("\n" + "-" * 60)
    print("[INPUT SICURO] Segreto richiesto all'operatore.")
    print(f"  {label}")
    print("  Il valore NON verrà mostrato, salvato o passato al modello LLM.")
    print("-" * 60)
    try:
        return getpass.getpass("Incolla il valore e premi INVIO (vuoto = annulla): ")
    except (EOFError, KeyboardInterrupt):
        return ""


def get_logger(name: str = "hemmy", level: int = logging.INFO) -> logging.Logger:
    """Restituisce un logger configurato (formato coerente, un solo handler)."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(level)
    return logger


def schema_diff(
    source: list[dict[str, Any]],
    target: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Confronta lo schema sorgente (Blob) con quello destinazione (SQL).

    Restituisce colonne mancanti, in eccesso e con tipo divergente.
    TODO: normalizzare i tipi (es. VARCHAR vs string) prima del confronto.
    """
    src = {c["name"]: c.get("type") for c in source}
    tgt = {c["name"]: c.get("type") for c in target}
    return {
        "only_in_source": [n for n in src if n not in tgt],
        "only_in_target": [n for n in tgt if n not in src],
        "type_mismatch": [n for n in src if n in tgt and src[n] != tgt[n]],
    }


def retry(times: int = 3, delay: float = 1.0, backoff: float = 2.0) -> Callable:
    """Decorator di retry con backoff esponenziale per chiamate di rete."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            _delay = delay
            last_exc: Exception | None = None
            for _ in range(times):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - retry generico
                    last_exc = exc
                    time.sleep(_delay)
                    _delay *= backoff
            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator
