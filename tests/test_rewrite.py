import httpx

from app import llm_client
from tests.conftest import SAMPLE, metric_value, ok_response

NOTES = "meeting moved to friday\nreport due monday\ninform team"


def rewrite(client, text=NOTES, user_id="hitansh"):
    return client.post("/rewrite", json={"user_id": user_id, "text": text})


def test_valid_rewrite(with_sample, openrouter):
    r = rewrite(with_sample)
    assert r.status_code == 200
    body = r.json()
    assert body["rewrite"] == "The meeting has moved to Friday. I'll tell the team."
    assert body["model"] == "test/model" and body["prompt_version"] == "v1"
    sent = openrouter.calls[0]
    assert sent["model"] == "test/model"
    assert sent["messages"][0]["content"] == llm_client.PROMPTS["v1"]
    assert SAMPLE in sent["messages"][1]["content"] and NOTES in sent["messages"][1]["content"]


def test_missing_sample_makes_no_openrouter_call(client, openrouter):
    before = metric_value(client, "openrouter_requests_total")
    r = rewrite(client, user_id="nobody")
    assert r.status_code == 404
    assert r.json()["detail"] == "Please submit a writing sample first."
    assert openrouter.calls == []
    assert metric_value(client, "openrouter_requests_total") == before


def test_not_configured_returns_503_without_call(with_sample, openrouter, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert rewrite(with_sample).status_code == 503
    assert openrouter.calls == []


def test_timeout_is_retried_once_then_504(with_sample, openrouter):
    openrouter.queue = [httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow")]
    r = rewrite(with_sample)
    assert r.status_code == 504
    assert len(openrouter.calls) == 2
    assert metric_value(with_sample, 'rewrite_failures_total{reason="timeout"}') >= 1


def test_rate_limit_then_success(with_sample, openrouter):
    openrouter.queue = [httpx.Response(429, json={"error": "rate limited"}), ok_response()]
    assert rewrite(with_sample).status_code == 200
    assert len(openrouter.calls) == 2


def test_server_error_twice_is_502(with_sample, openrouter):
    openrouter.queue = [httpx.Response(500), httpx.Response(503)]
    r = rewrite(with_sample)
    assert r.status_code == 502
    assert r.json()["detail"] == "The AI service failed to produce a rewrite."  # fixed message, no upstream body
    assert len(openrouter.calls) == 2


def test_auth_error_is_not_retried(with_sample, openrouter):
    openrouter.queue = [httpx.Response(401, json={"error": {"message": "bad key sk-or-secret"}})]
    r = rewrite(with_sample)
    assert r.status_code == 502
    assert len(openrouter.calls) == 1
    assert "sk-or" not in r.text


def test_empty_model_output_is_rejected(with_sample, openrouter):
    openrouter.queue = [ok_response(text="   ")]
    r = rewrite(with_sample)
    assert r.status_code == 502
    assert r.json()["detail"] == "The AI service returned an invalid response."


def test_malformed_body_is_rejected(with_sample, openrouter):
    openrouter.queue = [httpx.Response(200, json={"unexpected": True})]
    assert rewrite(with_sample).status_code == 502


def test_prompt_version_is_configurable(with_sample, openrouter, monkeypatch):
    monkeypatch.setenv("PROMPT_VERSION", "v3")
    assert rewrite(with_sample).json()["prompt_version"] == "v3"
    assert openrouter.calls[0]["messages"][0]["content"] == llm_client.PROMPTS["v3"]


def test_no_fallback_by_default(with_sample, openrouter):
    rewrite(with_sample)
    assert "models" not in openrouter.calls[0]


def test_fallback_models_sent_when_configured(with_sample, openrouter, monkeypatch):
    monkeypatch.setenv("OPENROUTER_FALLBACK_MODELS", "b/model, c/model")
    rewrite(with_sample)
    assert openrouter.calls[0]["models"] == ["test/model", "b/model", "c/model"]


def test_token_usage_recorded(with_sample, openrouter):
    before = metric_value(with_sample, 'openrouter_tokens_total{type="prompt"}')
    rewrite(with_sample)
    assert metric_value(with_sample, 'openrouter_tokens_total{type="prompt"}') == before + 42


def test_chat_attempts_and_backoff_are_configurable(openrouter, monkeypatch):
    """The DevOps AI uses 3 attempts; the app keeps the 2-attempt baseline."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    openrouter.queue = [httpx.Response(429), httpx.Response(429), ok_response()]
    msgs = [{"role": "user", "content": "hi"}]
    result = llm_client.chat(msgs, model="m", temperature=0, max_tokens=10, attempts=3, retry_delay_s=0)
    assert result.text and len(openrouter.calls) == 3


def test_extra_body_is_sent(openrouter, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    llm_client.chat([{"role": "user", "content": "hi"}], model="m", temperature=0, max_tokens=5,
                    extra_body={"reasoning": {"enabled": False}})
    assert openrouter.calls[0]["reasoning"] == {"enabled": False}
