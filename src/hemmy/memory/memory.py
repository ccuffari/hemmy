"""Memoria dell'agente: breve termine (sessione) + lungo termine.

- Short-term: cronologia dei messaggi/passi della sessione corrente, usata per
  costruire il contesto ReAct passato al LLM.
- Long-term: conoscenza persistente tra sessioni (es. diagnosi ricorrenti,
  schema noti, pattern di errore). Può essere salvata su file/DB/vector store.

STUB: interfacce minime da completare.
"""

from __future__ import annotations

from typing import Any


class ShortTermMemory:
    """Memoria di sessione: lista ordinata di eventi (thought/action/observation)."""

    def __init__(self, max_items: int = 50) -> None:
        self.max_items = max_items
        self._events: list[dict[str, Any]] = []

    def add(self, role: str, content: str) -> None:
        """Aggiunge un evento alla cronologia, con troncamento FIFO."""
        self._events.append({"role": role, "content": content})
        if len(self._events) > self.max_items:
            self._events = self._events[-self.max_items :]

    def history(self) -> list[dict[str, Any]]:
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()


class LongTermMemory:
    """Memoria persistente tra sessioni.

    TODO: implementare backend (file JSON / SQLite / vector store) per
    salvataggio e retrieval di conoscenza durevole.
    """

    def __init__(self, backend: Any = None) -> None:
        self.backend = backend

    def remember(self, key: str, value: Any) -> None:
        raise NotImplementedError

    def recall(self, query: str) -> list[Any]:
        raise NotImplementedError
