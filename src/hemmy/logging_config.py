"""Configurazione centralizzata del logging per Hemmy.

Obiettivo: sapere sempre cosa sta facendo il backend, a che punto è un turno,
dove si è bloccato, quanto ci ha messo ogni fase. Due output:

- Console (stderr) leggibile in sviluppo: timestamp, livello, logger, messaggio.
- Formato strutturato JSON opzionale (env `HEMMY_LOG_JSON=1`), utile se i log
  vengono raccolti da un aggregatore.

Livelli:
  - `HEMMY_LOG_LEVEL=DEBUG` per vedere ogni evento SSE, ogni tool, ogni step.
  - default INFO: setup, tool call, fine turno, errori.
  - `WARNING` per ridurre il rumore in produzione.

Uso tipico all'avvio del processo (cli.py, web.py):

    from hemmy.logging_config import setup_logging
    setup_logging()

Poi ovunque:

    import logging
    log = logging.getLogger("hemmy.turn")
    log.info("turno avviato user=%s", uid)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any


# --------------------------------------------------------------------------- Formatter


class _HumanFormatter(logging.Formatter):
    """Formato leggibile: `HH:MM:SS.mmm LEVEL [logger] messaggio`."""

    _COLORS = {
        "DEBUG": "\033[36m",     # cyan
        "INFO": "\033[32m",      # green
        "WARNING": "\033[33m",   # yellow
        "ERROR": "\033[31m",     # red
        "CRITICAL": "\033[35m",  # magenta
    }
    _RESET = "\033[0m"

    def __init__(self, use_color: bool = True) -> None:
        super().__init__()
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        ms = int((record.created - int(record.created)) * 1000)
        level = record.levelname
        if self._use_color:
            color = self._COLORS.get(level, "")
            level_disp = f"{color}{level:<5}{self._RESET}"
        else:
            level_disp = f"{level:<5}"

        # Nome logger "corto": rimuove il prefisso "hemmy." se presente.
        name = record.name
        if name.startswith("hemmy."):
            name = name[6:]
        name_disp = f"[{name}]" if name else ""

        base = f"{ts}.{ms:03d} {level_disp} {name_disp} {record.getMessage()}"

        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)

        # Extra opzionali: se il chiamante ha passato `extra={"user_id": ..., "phase": ...}`
        extra_bits = []
        for key in ("user_id", "phase", "tool", "provider", "duration_ms"):
            val = getattr(record, key, None)
            if val is not None:
                extra_bits.append(f"{key}={val}")
        if extra_bits:
            base += "  " + " ".join(extra_bits)

        return base


class _JsonFormatter(logging.Formatter):
    """Formato JSON: una riga per record, con campi standard + extra."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("user_id", "phase", "tool", "provider", "duration_ms"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


# --------------------------------------------------------------------------- Setup


_CONFIGURED = False


def setup_logging(level: str | None = None) -> None:
    """Configura il root logger. Idempotente: chiamate multiple sono no-op.

    Livello: env `HEMMY_LOG_LEVEL` (default `INFO`), o argomento esplicito.
    JSON: env `HEMMY_LOG_JSON=1` per output JSON a riga singola.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    lvl_name = (level or os.environ.get("HEMMY_LOG_LEVEL", "INFO")).upper()
    lvl = getattr(logging, lvl_name, logging.INFO)

    use_json = os.environ.get("HEMMY_LOG_JSON", "").strip() in ("1", "true", "yes")

    handler = logging.StreamHandler(stream=sys.stderr)
    if use_json:
        handler.setFormatter(_JsonFormatter())
    else:
        # Colore solo se stderr è un TTY (evita escape in file/pipe).
        use_color = sys.stderr.isatty()
        handler.setFormatter(_HumanFormatter(use_color=use_color))

    root = logging.getLogger()
    # Rimuove handler preesistenti (es. basicConfig chiamato altrove).
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(lvl)

    # Silenzia i logger troppo verbosi di terze parti, a meno che l'utente
    # non abbia esplicitamente chiesto DEBUG.
    if lvl > logging.DEBUG:
        for noisy in ("urllib3", "httpx", "httpcore", "asyncio", "botocore",
                      "boto3", "azure", "msal", "openai", "anthropic"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Helper: `log = get_logger("turn")` → logger `hemmy.turn`."""
    return logging.getLogger(f"hemmy.{name}")


# --------------------------------------------------------------------------- Timing helper


class timed:
    """Context manager per misurare la durata di una fase e loggarla.

    Uso:
        with timed(log, "chiamata LLM", user_id=uid) as t:
            agent.run(...)
        # al __exit__: log INFO con duration_ms=...

    Se il blocco solleva un'eccezione, logga a livello ERROR/WARNING con
    la durata e rilancia.
    """

    def __init__(self, logger: logging.Logger, label: str,
                 level: int = logging.INFO, **extra: Any) -> None:
        self._logger = logger
        self._label = label
        self._level = level
        self._extra = extra
        self._t0 = 0.0

    def __enter__(self) -> "timed":
        self._t0 = time.time()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        dur_ms = int((time.time() - self._t0) * 1000)
        extra = {**self._extra, "duration_ms": dur_ms}
        if exc_type is not None:
            # Non logghiamo il traceback qui: lo farà il chiamante.
            self._logger.warning("%s FALLITO dopo %dms", self._label, dur_ms, extra=extra)
            return False
        self._logger.log(self._level, "%s completato", self._label, extra=extra)
        return False