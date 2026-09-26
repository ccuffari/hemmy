"""Test di `core.llm_providers` (BYOK multi-provider): risoluzione della config
per-utente, validazione, e le due famiglie di SDK (openai-compatibile / anthropic)
con client FINTI — nessuna chiamata di rete reale."""

from __future__ import annotations

import pytest

from hemmy.core import llm_providers as lp


# --------------------------------------------------------------- provider_catalog


def test_provider_catalog_has_all_six_providers():
    ids = {p["id"] for p in lp.provider_catalog()}
    assert ids == {"deepseek", "anthropic", "openai", "gemini", "llama", "self_hosted"}


def test_provider_catalog_never_exposes_secrets():
    for p in lp.provider_catalog():
        assert "api_key" not in p
        assert set(p.keys()) == {"id", "label", "tagline", "default_model", "default_base_url", "base_url_editable"}


# --------------------------------------------------------------- resolve_llm_config


def test_resolve_llm_config_defaults_to_deepseek_when_nothing_set():
    cfg = lp.resolve_llm_config(settings=None, api_key="sk-x", agent_yaml=None)
    assert cfg["provider"] == "deepseek"
    assert cfg["model"] == "deepseek-chat"
    assert cfg["base_url"] == "https://api.deepseek.com"
    assert cfg["api_key"] == "sk-x"


def test_resolve_llm_config_honors_user_settings_over_everything():
    settings = {"llm_provider": "anthropic", "llm_model": "claude-x", "llm_base_url": "https://custom"}
    cfg = lp.resolve_llm_config(settings=settings, api_key="ak-1", agent_yaml={"provider": "deepseek", "model": "deepseek-chat"})
    assert cfg == {"provider": "anthropic", "model": "claude-x", "base_url": "https://custom", "api_key": "ak-1"}


def test_resolve_llm_config_falls_back_to_agent_yaml_only_when_provider_matches():
    # agent_yaml ha un model/base_url per "deepseek": se l'utente sceglie "openai",
    # quei valori NON devono essere riusati (sarebbero un modello DeepSeek per un
    # client OpenAI).
    agent_yaml = {"provider": "deepseek", "model": "deepseek-chat", "base_url": "https://api.deepseek.com"}
    cfg = lp.resolve_llm_config(settings={"llm_provider": "openai"}, api_key="sk-openai", agent_yaml=agent_yaml)
    assert cfg["provider"] == "openai"
    assert cfg["model"] == "gpt-4.1-mini"  # default del provider, non quello DeepSeek
    assert cfg["base_url"] == "https://api.openai.com/v1"


def test_resolve_llm_config_unknown_provider_falls_back_to_default():
    cfg = lp.resolve_llm_config(settings={"llm_provider": "not-a-real-provider"}, api_key="x", agent_yaml=None)
    assert cfg["provider"] == lp.DEFAULT_PROVIDER


def test_resolve_llm_config_api_key_has_no_shared_default():
    cfg = lp.resolve_llm_config(settings=None, api_key=None, agent_yaml=None)
    assert cfg["api_key"] is None


# --------------------------------------------------------------- LLMClient: validazione


def test_llm_client_rejects_unknown_provider():
    with pytest.raises(ValueError, match="sconosciuto"):
        lp.LLMClient(provider="not-real", api_key="x")


def test_llm_client_requires_api_key():
    with pytest.raises(ValueError, match="BYOK"):
        lp.LLMClient(provider="deepseek", api_key=None)
    with pytest.raises(ValueError, match="BYOK"):
        lp.LLMClient(provider="deepseek", api_key="")


def test_llm_client_openai_family_builds_without_network(monkeypatch):
    client = lp.LLMClient(provider="deepseek", api_key="sk-test")
    assert client.kind == "openai"
    assert client.model == "deepseek-chat"


def test_llm_client_anthropic_family_builds_without_network():
    client = lp.LLMClient(provider="anthropic", api_key="ak-test")
    assert client.kind == "anthropic"
    assert client.model == "claude-sonnet-4-5-20250929"


def test_llm_client_custom_model_and_base_url_override_defaults():
    client = lp.LLMClient(provider="self_hosted", api_key="unused", model="mixtral", base_url="http://localhost:1234/v1")
    assert client.model == "mixtral"


# --------------------------------------------------------------- LLMClient.complete


class _FakeChoice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})


class _FakeOpenAIResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeOpenAISDKClient:
    def __init__(self):
        self.calls = []
        self.chat = type("Chat", (), {"completions": self})

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeOpenAIResponse("ciao dal finto openai")


def test_complete_openai_family_returns_text(monkeypatch):
    client = lp.LLMClient(provider="deepseek", api_key="sk-test")
    fake = _FakeOpenAISDKClient()
    client._client = fake
    out = client.complete([{"role": "user", "content": "ciao"}])
    assert out == "ciao dal finto openai"
    assert fake.calls[0]["model"] == "deepseek-chat"


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeAnthropicResponse:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


class _FakeAnthropicSDKClient:
    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeAnthropicResponse("ciao da claude")


def test_complete_anthropic_family_splits_system_prompt_out_of_messages():
    client = lp.LLMClient(provider="anthropic", api_key="ak-test")
    fake = _FakeAnthropicSDKClient()
    client._client = fake
    out = client.complete([
        {"role": "system", "content": "sei un assistente"},
        {"role": "user", "content": "ciao"},
    ])
    assert out == "ciao da claude"
    call = fake.calls[0]
    assert call["system"] == "sei un assistente"
    assert call["messages"] == [{"role": "user", "content": "ciao"}]
    assert not any(m.get("role") == "system" for m in call["messages"])


def test_complete_retries_on_transient_error_then_succeeds(monkeypatch):
    client = lp.LLMClient(provider="deepseek", api_key="sk-test", max_retries=2)
    monkeypatch.setattr(lp.time, "sleep", lambda _s: None)

    class _RateLimitError(Exception):
        pass

    _RateLimitError.__name__ = "RateLimitError"

    calls = {"n": 0}

    def _flaky(messages, temperature):
        calls["n"] += 1
        if calls["n"] < 2:
            raise _RateLimitError("rate limited")
        return "ok dopo retry"

    monkeypatch.setattr(client, "_complete_openai", _flaky)
    assert client.complete([{"role": "user", "content": "x"}]) == "ok dopo retry"
    assert calls["n"] == 2


def test_complete_does_not_retry_on_non_transient_error(monkeypatch):
    client = lp.LLMClient(provider="deepseek", api_key="sk-test")

    def _boom(messages, temperature):
        raise ValueError("api key non valida")

    monkeypatch.setattr(client, "_complete_openai", _boom)
    with pytest.raises(ValueError, match="api key non valida"):
        client.complete([{"role": "user", "content": "x"}])
