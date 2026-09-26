"""Anonimizzazione (opzionale) dei nomi delle risorse verso l'LLM.

Mantiene una TABELLA PRIVATA che mappa i nomi reali delle risorse Azure ad alias
generici (es. 'kviaagentwe01' -> '<vault1>'). Tutto ciò che va al modello viene
anonimizzato; le risposte all'utente vengono ri-mappate ai nomi reali.

Con anonymize=False la mappatura è identità (real -> real): il JSON viene
comunque creato e popolato con i nomi reali, ma nessuna anonimizzazione avviene.

Flusso nel loop dell'agente:
- domanda utente e Observation dei tool -> anonymize() prima di andare al modello;
- args prodotti dal modello (con alias) -> deanonymize_obj() prima di eseguire i tool;
- Final Answer del modello (con alias) -> deanonymize() prima di mostrarla all'utente.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

# Categoria alias in base a parole chiave nella key/id.
_CATEGORY_MAP = {
    "vault": ["vault", "keyvault"],
    "rg": ["resource_group", "resourcegroup", "resourcegroups"],
    "storage": ["storage", "container", "account", "blob", "queue", "table", "share", "fileshare"],
    "sql": ["sql", "server", "database", "mssql"],
    "adf": ["factory", "pipeline", "dataset", "linked_service", "linkedservice", "trigger", "datafactory"],
    "net": ["vnet", "virtual_network", "subnet", "nsg", "private_endpoint", "privateendpoint", "network"],
    "secret": ["secret"],
}

# Key che portano un nome di risorsa (per registrazione mirata dai risultati dei tool).
_NAME_KEYS = {
    "name", "pipeline_name", "table_name", "queue_name", "share_name", "account_name",
    "server_name", "database_name", "vnet_name", "subnet_name", "factory", "container",
    "table", "queue", "share", "vault", "dataset", "source_dataset", "sink_dataset",
    "pipeline", "linked_service", "kv_linked_service", "secret_name", "storage_account",
}

_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,}$")


class Anonymizer:
    def __init__(self, path: str | None = None, anonymize: bool = False) -> None:
        self._path = path
        self._anonymize = anonymize
        self._real_to_alias: dict[str, str] = {}
        self._alias_to_real: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        self._load()

    # ---------------------------------------------------------- persistenza

    def _load(self) -> None:
        if self._path and os.path.exists(self._path):
            try:
                with open(self._path, encoding="utf-8") as f:
                    data = json.load(f)
                self._real_to_alias = data.get("map", {})
                self._alias_to_real = {v: k for k, v in self._real_to_alias.items()}
                self._counters = data.get("counters", {})
            except (json.JSONDecodeError, OSError):
                pass

    def _save(self) -> None:
        if not self._path:
            return
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump({"map": self._real_to_alias, "counters": self._counters}, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ---------------------------------------------------------- registrazione

    @staticmethod
    def _category(hint: str) -> str:
        h = (hint or "").lower()
        for cat, tokens in _CATEGORY_MAP.items():
            if any(t in h for t in tokens):
                return cat
        return "res"

    def register(self, real: str, category: str = "res") -> str | None:
        if not real or not isinstance(real, str):
            return None
        if real in self._real_to_alias:
            return self._real_to_alias[real]
        if self._anonymize:
            self._counters[category] = self._counters.get(category, 0) + 1
            alias = f"<{category}{self._counters[category]}>"
        else:
            # Nessuna anonimizzazione: l'alias coincide con il nome reale.
            alias = real
        self._real_to_alias[real] = alias
        self._alias_to_real[alias] = real
        self._save()
        return alias

    def register_from_result(self, obj: Any, key_hint: str | None = None) -> None:
        """Registra i nomi di risorsa trovati in un risultato/args (dict/list/str)."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                self.register_from_result(v, k)
        elif isinstance(obj, list):
            for item in obj:
                self.register_from_result(item, key_hint)
        elif isinstance(obj, str):
            self._maybe_register(obj, key_hint)

    def _maybe_register(self, value: str, key_hint: str | None) -> None:
        # Resource ID: estrae il resource group e il nome finale.
        if "/subscriptions/" in value and "/providers/" in value:
            parts = value.split("/")
            if "resourceGroups" in parts:
                i = parts.index("resourceGroups")
                if i + 1 < len(parts):
                    self.register(parts[i + 1], "rg")
            if parts:
                self.register(parts[-1], self._category(value))
            return
        key = (key_hint or "").lower()
        name_bearing = key in _NAME_KEYS or key.endswith("name") or key.startswith(("created_", "deleted_"))
        if name_bearing and _VALID_NAME.match(value) and not value.replace(".", "").isdigit():
            self.register(value, self._category(key))

    # ------------------------------------------------------------- trasformazioni

    def anonymize(self, text: str | None) -> str | None:
        """Sostituisce i nomi reali (noti) con i rispettivi alias."""
        if not text:
            return text
        for real in sorted(self._real_to_alias, key=len, reverse=True):
            text = text.replace(real, self._real_to_alias[real])
        return text

    def deanonymize(self, text: str | None) -> str | None:
        """Ripristina i nomi reali a partire dagli alias."""
        if not text:
            return text
        for alias, real in self._alias_to_real.items():
            text = text.replace(alias, real)
        return text

    def deanonymize_obj(self, obj: Any) -> Any:
        """Ripristina i nomi reali nelle stringhe di un oggetto (es. args del tool)."""
        if isinstance(obj, dict):
            return {k: self.deanonymize_obj(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.deanonymize_obj(v) for v in obj]
        if isinstance(obj, str):
            return self.deanonymize(obj)
        return obj