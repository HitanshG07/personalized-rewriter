"""MANUAL live test (Layer 1): makes ONE real OpenRouter call. Never runs in CI.

Run:  RUN_LIVE=1 dotenv run -- pytest tests/test_integration_openrouter.py -s
"""
import os

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="live OpenRouter test; set RUN_LIVE=1")


def test_real_openrouter_rewrite(tmp_path, monkeypatch):
    from app.main import app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "live.db"))
    with TestClient(app) as client:
        client.post(
            "/sample",
            json={"user_id": "live", "sample": "I keep things short and friendly. No fluff, just the key points."},
        )
        r = client.post("/rewrite", json={"user_id": "live", "text": "meeting moved to friday\nreport due monday"})
    assert r.status_code == 200, r.text
    body = r.json()
    print("\nmodel:", body["model"], "| latency_ms:", body["latency_ms"], "\n", body["rewrite"])
    assert body["rewrite"].strip()
