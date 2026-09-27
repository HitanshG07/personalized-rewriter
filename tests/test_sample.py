import threading

from app import storage
from tests.conftest import SAMPLE


def test_store_sample(client):
    r = client.post("/sample", json={"user_id": "hitansh", "sample": SAMPLE})
    assert r.status_code == 201
    assert r.json() == {"user_id": "hitansh", "stored": True, "chars": len(SAMPLE)}
    assert storage.get_sample("hitansh") == SAMPLE


def test_sample_is_upserted(client):
    client.post("/sample", json={"user_id": "u1", "sample": SAMPLE})
    newer = SAMPLE + " Newer version of my writing."
    client.post("/sample", json={"user_id": "u1", "sample": newer})
    assert storage.get_sample("u1") == newer


def test_invalid_user_id_rejected(client):
    assert client.post("/sample", json={"user_id": "bad id!", "sample": SAMPLE}).status_code == 422


def test_short_sample_rejected(client):
    assert client.post("/sample", json={"user_id": "u1", "sample": "too short"}).status_code == 422


def test_wal_mode_and_concurrent_writers(client):
    """Several writers with their own connections at once (like 3 Pods): no 'database is locked', no lost writes."""
    assert storage.journal_mode() == "wal"
    errors = []

    def writer(n):
        try:
            for i in range(25):
                storage.save_sample(f"user{n}_{i}", SAMPLE)
        except Exception as e:  # noqa: BLE001 - collected and asserted below
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert all(storage.get_sample(f"user{n}_{i}") == SAMPLE for n in range(8) for i in range(25))


def test_health_and_metrics_endpoints(client):
    assert client.get("/health").json() == {"status": "ok"}
    body = client.get("/metrics").text
    assert "requests_total" in body and "rewrite_requests_total" in body


def test_ui_page_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Personalized Rewriter" in r.text
