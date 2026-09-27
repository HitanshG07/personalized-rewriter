"""Layer 2 of the OpenRouter usage model: every test runs against a fake OpenRouter (0 real calls)."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app import llm_client
from app.main import app

SAMPLE = "I like keeping my communication simple and direct. I generally avoid unnecessary wording."


def ok_response(text="The meeting has moved to Friday. I'll tell the team.", model="test/model"):
    return httpx.Response(
        200,
        json={
            "model": model,
            "provider": "FakeProvider",
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 42, "completion_tokens": 17},
        },
    )


class FakeOpenRouter:
    """Queue responses (httpx.Response or an exception to raise); records every request body."""

    def __init__(self):
        self.queue: list = []
        self.calls: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        import json

        self.calls.append(json.loads(request.content))
        item = self.queue.pop(0) if self.queue else ok_response()
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def openrouter(monkeypatch):
    fake = FakeOpenRouter()
    monkeypatch.setattr(llm_client, "TRANSPORT", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(llm_client, "RETRY_DELAY_S", 0)
    return fake


@pytest.fixture
def client(openrouter, tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setenv("OPENROUTER_MODEL", "test/model")
    monkeypatch.delenv("PROMPT_VERSION", raising=False)
    monkeypatch.delenv("OPENROUTER_FALLBACK_MODELS", raising=False)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def with_sample(client):
    assert client.post("/sample", json={"user_id": "hitansh", "sample": SAMPLE}).status_code == 201
    return client


def metric_value(client, line_prefix: str) -> float:
    for line in client.get("/metrics").text.splitlines():
        if line.startswith(line_prefix + " "):
            return float(line.rsplit(" ", 1)[1])
    return 0.0
