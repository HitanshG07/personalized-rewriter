"""Second provider (Groq/Google) support. No network."""
import importlib.util
from pathlib import Path

import httpx
import pytest

from app import llm_client
from app.llm_client import LLMError, chat
from tests.conftest import ok_response

SRC = Path(__file__).resolve().parents[1] / "evaluation" / "eval.py"
spec = importlib.util.spec_from_file_location("eval_providers", SRC)
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def test_split_model():
    assert ev.split_model("groq:llama-3.3-70b-versatile") == ("groq", "llama-3.3-70b-versatile")
    assert ev.split_model("google:gemini-2.5-flash") == ("google", "gemini-2.5-flash")
    assert ev.split_model("minimax/minimax-m3.1-flash:free") == ("openrouter", "minimax/minimax-m3.1-flash:free")


def test_same_model():
    assert ev.same_model("models/gemini-2.5-flash", "gemini-2.5-flash")
    assert ev.same_model("llama-3.3-70b-versatile", "llama-3.3-70b-versatile")
    assert not ev.same_model("other", "gemini-2.5-flash")


def _call(provider="groq"):
    return chat([{"role": "user", "content": "hi"}], model="m", temperature=0.1, max_tokens=5,
                fallback_models=["x"], attempts=1, provider=provider)


def test_groq_request(monkeypatch):
    seen = {}

    def handler(req):
        import json
        seen.update(host=req.url.host, auth=req.headers["authorization"], body=json.loads(req.content))
        return ok_response()

    monkeypatch.setattr(llm_client, "TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setenv("GROQ_API_KEY", "groq-test")
    _call()
    assert seen["host"] == "api.groq.com" and seen["auth"] == "Bearer groq-test"
    assert "models" not in seen["body"]


def test_missing_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(LLMError) as e:
        _call()
    assert e.value.reason == "not_configured"


@pytest.mark.parametrize("status,reason", [(401, "auth"), (429, "rate_limited")])
def test_status_mapping(monkeypatch, status, reason):
    monkeypatch.setattr(llm_client, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(status)))
    monkeypatch.setenv("GROQ_API_KEY", "k")
    with pytest.raises(LLMError) as e:
        _call()
    assert e.value.reason == reason


def test_app_rewrite_uses_llm_provider_env(monkeypatch):
    """The deployed app can be switched to another free provider with LLM_PROVIDER (default openrouter)."""
    from app import llm_client
    seen = {}

    def fake_chat(messages, **kwargs):
        seen.update(kwargs)
        return llm_client.LLMResult("ok", "m", None, 1, 1, 1)

    monkeypatch.setattr(llm_client, "chat", fake_chat)
    monkeypatch.setenv("OPENROUTER_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("LLM_PROVIDER", "google")
    llm_client.rewrite("sample text " * 6, "notes")
    assert seen["provider"] == "google"
    monkeypatch.delenv("LLM_PROVIDER")
    llm_client.rewrite("sample text " * 6, "notes")
    assert seen["provider"] == "openrouter"
