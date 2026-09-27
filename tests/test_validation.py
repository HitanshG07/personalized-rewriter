import pytest

from app.schemas import normalize


@pytest.mark.parametrize("text", ["", "   ", "\n\t \n"])
def test_empty_notes_rejected_before_any_call(with_sample, openrouter, text):
    r = with_sample.post("/rewrite", json={"user_id": "hitansh", "text": text})
    assert r.status_code == 422
    assert openrouter.calls == []


def test_notes_length_cap(with_sample, openrouter):
    r = with_sample.post("/rewrite", json={"user_id": "hitansh", "text": "x" * 2001})
    assert r.status_code == 422
    assert openrouter.calls == []


def test_sample_length_cap(client):
    assert client.post("/sample", json={"user_id": "u1", "sample": "x" * 4001}).status_code == 422


def test_missing_field_rejected(client):
    assert client.post("/rewrite", json={"user_id": "hitansh"}).status_code == 422


def test_normalize_collapses_spaces_but_keeps_line_breaks():
    assert normalize("  a   b\t\tc \n\n\n\n d  ") == "a b c\n\nd"
    assert normalize("- one\n- two") == "- one\n- two"


def test_whitespace_is_trimmed_before_sending(with_sample, openrouter):
    with_sample.post("/rewrite", json={"user_id": "hitansh", "text": "  meeting    moved  "})
    assert openrouter.calls[0]["messages"][1]["content"].endswith("CONTENT TO REWRITE\nmeeting moved")
