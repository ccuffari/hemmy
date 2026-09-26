"""Registro provider LLM + client unificato (BYOK multi-provider).

Perché è possibile con un'interfaccia così piccola: il loop ReAct dell'agente
(`core/agent.py`) NON usa il function-calling nativo dei provider — il tool-calling
è un protocollo TESTUALE proprio (Thought / Action / Action Input / Final Answer)
che l'agente stessa parsa dalla risposta. Il "contratto" con l'LLM si riduce quindi
a: dati dei messaggi, ritorna il testo generato. Questo permette di supportare
provider molto diversi con un'unica interfaccia (`LLMClient.complete`):

- Famiglia "openai" (endpoint Chat Completions OpenAI-compatibile): DeepSeek,
  OpenAI, Google Gemini (endpoint beta OpenAI-compatibile), Meta Llama (via un
  host a scelta dell'utente: la maggior parte dei provider Llama sono
  OpenAI-compatibili), self-hosted (Ollama/vLLM/LM Studio espongono tutti un
  endpoint OpenAI-compatibile in locale).
- Famiglia "anthropic" (Messages API, SDK `anthropic` dedicato): Claude.

BYOK (Bring Your Own Key): ogni utente fornisce la propria API key dalle
Impostazioni della UI — mai una chiave condivisa via variabile d'ambiente. Il
fallback a `DEEPSEEK_API_KEY`/`DEEPSEEK_BASE_URL`/`DEEPSEEK_MODEL` resta SOLO per
l'uso CLI locale senza portale (nessun utente autenticato): vedi
`interfaces/cli._build_agent`.
"""

from __future__ import annotations

import time
from typing import Any

# id interno -> metadati. Usato sia per costruire il client giusto sia per il
# menu a tendina delle Impostazioni (via `provider_catalog()`, esposto in
# GET /api/llm/providers — nessun segreto qui dentro).
PROVIDERS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "label": "DeepSeek", "tagline": "Codice e ragionamento", "kind": "openai",
        "default_base_url": "https://api.deepseek.com", "default_model": "deepseek-chat",
        "base_url_editable": True,
    },
    "anthropic": {
        "label": "Anthropic Claude", "tagline": "Agentic reasoning", "kind": "anthropic",
        "default_base_url": None, "default_model": "claude-sonnet-4-5-20250929",
        "base_url_editable": False,
    },
    "openai": {
        "label": "OpenAI · GPT", "tagline": "Versatilità ed ecosistema", "kind": "openai",
        "default_base_url": "https://api.openai.com/v1", "default_model": "gpt-4.1-mini",
        "base_url_editable": True,
    },
    "gemini": {
        "label": "Google Gemini", "tagline": "Contesto lungo", "kind": "openai",
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.5-pro", "base_url_editable": True,
    },
    "llama": {
        "label": "Meta Llama", "tagline": "Open weights", "kind": "openai",
        "default_base_url": None, "default_model": "llama-3.3-70b-instruct",
        "base_url_editable": True,
    },
    "self_hosted": {
        "label": "Self-hosted / Local", "tagline": "Ollama, vLLM, LM Studio", "kind": "openai",
        "default_base_url": "http://localhost:11434/v1", "default_model": "llama3.1",
        "base_url_editable": True,
    },
}

DEFAULT_PROVIDER = "deepseek"

# Eccezioni transitorie note delle due famiglie di SDK: nome della classe, non il
# tipo importato, così questo modulo non richiede `anthropic` come hard-dependency
# per poter anche solo essere importato (l'SDK viene importato pigramente).
_TRANSIENT_EXC_NAMES = {
    "APITimeoutError", "APIConnectionError", "RateLimitError",
    "InternalServerError", "OverloadedError",
}


def provider_catalog() -> list[dict[str, Any]]:
    """Vista pubblica (senza segreti) per il menu Impostazioni della UI."""
    return [
        {
            "id": pid,
            "label": meta["label"],
            "tagline": meta["tagline"],
            "default_model": meta["default_model"],
            "default_base_url": meta["default_base_url"],
            "base_url_editable": meta["base_url_editable"],
        }
        for pid, meta in PROVIDERS.items()
    ]


def resolve_llm_config(
    settings: dict[str, Any] | None,
    api_key: str | None,
    agent_yaml: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Calcola provider/model/base_url effettivi per un utente (BYOK).

    Priorità: settings per-utente (dal portale) > default di `config/agent.yaml`
    (solo se il provider coincide) > default hardcoded del provider. La API key
    NON ha un default condiviso: se manca, `LLMClient` solleva un errore chiaro.
    """
    settings = settings or {}
    agent_yaml = agent_yaml or {}
    provider = settings.get("llm_provider") or agent_yaml.get("provider") or DEFAULT_PROVIDER
    if provider not in PROVIDERS:
        provider = DEFAULT_PROVIDER
    meta = PROVIDERS[provider]
    yaml_matches_provider = agent_yaml.get("provider") == provider
    model = (
        settings.get("llm_model")
        or (agent_yaml.get("model") if yaml_matches_provider else None)
        or meta["default_model"]
    )
    base_url = (
        settings.get("llm_base_url")
        or (agent_yaml.get("base_url") if yaml_matches_provider else None)
        or meta["default_base_url"]
    )
    return {"provider": provider, "model": model, "base_url": base_url, "api_key": api_key}


class LLMClient:
    """Wrapper unico: costruito una volta, espone `.complete(messages, temperature)`.

    Incapsula SOLO le differenze di SDK/shape tra le due famiglie di provider, così
    `core/agent.py` non deve conoscerle: stesso loop ReAct per qualunque provider.
    """

    def __init__(
        self,
        provider: str,
        api_key: str | None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: int = 120,
        max_retries: int = 3,
    ) -> None:
        if provider not in PROVIDERS:
            raise ValueError(f"provider LLM sconosciuto: {provider!r} (attesi: {sorted(PROVIDERS)})")
        if not api_key:
            raise ValueError(
                f"Nessuna API key configurata per il provider '{PROVIDERS[provider]['label']}'. "
                "Impostala dalle Impostazioni (BYOK): è personale, cifrata a riposo, e non è mai "
                "condivisa tra utenti né passata al modello."
            )
        meta = PROVIDERS[provider]
        self.provider = provider
        self.kind = meta["kind"]
        self.model = model or meta["default_model"]
        self.timeout = timeout
        self.max_retries = max_retries
        base_url = base_url or meta["default_base_url"]

        if self.kind == "openai":
            from openai import OpenAI

            self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries)
        elif self.kind == "anthropic":
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - dipendenza sempre installata da pyproject
                raise ImportError(
                    "Il pacchetto 'anthropic' non è installato. Esegui: pip install anthropic"
                ) from exc
            self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)
        else:  # pragma: no cover - difensivo, PROVIDERS è chiuso sopra
            raise ValueError(f"famiglia SDK non gestita: {self.kind}")

    def complete(self, messages: list[dict[str, str]], temperature: float = 0.1) -> str:
        """Invoca il modello e ritorna SOLO il testo della risposta.

        Ritenta sugli errori transitori (timeout/connessione/rate limit) con backoff,
        oltre ai retry integrati nell'SDK.
        """
        attempts = self.max_retries + 1
        last_exc: Exception | None = None
        for i in range(attempts):
            try:
                if self.kind == "openai":
                    return self._complete_openai(messages, temperature)
                return self._complete_anthropic(messages, temperature)
            except Exception as exc:  # noqa: BLE001 - uniformiamo qui il "è transitorio?"
                if type(exc).__name__ not in _TRANSIENT_EXC_NAMES:
                    raise
                last_exc = exc
                if i < attempts - 1:
                    time.sleep(2 * (i + 1))
        raise last_exc  # type: ignore[misc]

    def _complete_openai(self, messages: list[dict[str, str]], temperature: float) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            timeout=self.timeout,
            stream=False,
        )
        return response.choices[0].message.content or ""

    def _complete_anthropic(self, messages: list[dict[str, str]], temperature: float) -> str:
        # L'API Messages di Anthropic vuole il system prompt separato dall'array
        # messages (nessun role "system" al suo interno).
        system = ""
        chat: list[dict[str, str]] = []
        for m in messages:
            if m.get("role") == "system":
                system = (system + "\n\n" + m.get("content", "")).strip()
            else:
                chat.append({"role": m.get("role", "user"), "content": m.get("content", "")})
        response = self._client.messages.create(
            model=self.model,
            system=system,
            messages=chat,
            max_tokens=4096,
            temperature=temperature,
        )
        return "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
