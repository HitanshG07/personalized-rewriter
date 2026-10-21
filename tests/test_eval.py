"""evaluation/eval.py: resume, pinned metadata, blind scoring, summary. No network."""
import importlib.util
import json
from pathlib import Path

import pytest

from app.llm_client import LLMError, LLMResult

SRC = Path(__file__).resolve().parents[1] / "evaluation" / "eval.py"
ITEMS = [{"sample_id": f"s{i}", "writing_sample": f"sample {i}", "rough_notes": f"notes {i}"} for i in range(3)]


@pytest.fixture
def ev(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("eval_under_test", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    (tmp_path / "inputs.json").write_text(json.dumps(ITEMS), encoding="utf-8")
    for name, rel in (("INPUTS", "inputs.json"), ("RESULTS", "results"), ("META", "meta.json"), ("SUMMARY", "sum.csv")):
        monkeypatch.setattr(m, name, tmp_path / rel)
    monkeypatch.setattr(m, "HERE", tmp_path)
    return m


def result(text="out", model="m1"):
    return LLMResult(text, model, "p", 100, 10, 20)


def test_run_resume_and_metadata(ev):
    calls = []
    chat = lambda messages, **kw: calls.append(1) or result()  # noqa: E731
    assert ev.run("prompt_v1", "m1", "v1", 1, 0, chat=chat, sleep=lambda s: None) == 0
    assert len(calls) == 3 and len(ev.load(ev.results_path("prompt_v1"), {})) == 3
    ev.run("prompt_v1", "m1", "v1", 1, 0, chat=chat, sleep=lambda s: None)
    assert len(calls) == 3  # resume: zero new calls
    m = ev.load(ev.META, {})["prompt_v1"]
    assert (m["model_id"], m["prompt_version"], m["fallback_models"], m["n_samples"]) == ("m1", "v1", [], 3)
    assert len(m["inputs_sha256"]) == 64


def test_run_stops_on_rate_limit(ev):
    def chat(messages, **kw):
        raise LLMError("rate_limited")
    assert ev.run("prompt_v1", "m1", "v1", 1, 0, chat=chat, sleep=lambda s: None) == 2


def test_blind_order(ev):
    labels = ["a", "b", "c"]
    o = ev.blind_order(7, "s0", labels)
    assert o == ev.blind_order(7, "s0", labels) and sorted(o) == labels


def fill(ev):
    for lb in ev.ROUNDS[1]:
        ev.save(ev.results_path(lb), {it["sample_id"]: {"output": f"OUT-{lb[-1]}-{it['sample_id']}",
                "latency_ms": 100 + int(lb[-1]), "completion_tokens": 10, "model_mismatch": False} for it in ITEMS})
        ev.save(ev.META, {**ev.load(ev.META, {}), lb: {"model_id": "secret/model-id", "prompt_version": lb[-2:]}})


def test_score_is_blind_and_saved(ev):
    fill(ev)
    shown = []
    ev.score(1, ask=lambda p: "4 5 3", show=shown.append)
    text = "\n".join(shown)
    assert "prompt_v" not in text and "secret/model-id" not in text and "Candidate 1" in text
    scores = ev.load(ev.HERE / "scores_round1.json", {})
    assert len(scores) == 9 and scores["prompt_v2|s1"] == {"quality": 4, "meaning": 5, "tone": 3}


def test_summarize_averages(ev):
    fill(ev)
    ev.save(ev.HERE / "scores_round1.json", {f"prompt_v1|{it['sample_id']}": {"quality": q, "meaning": 5, "tone": 3}
                                            for it, q in zip(ITEMS, (2, 3, 4))})
    row = next(r for r in ev.summarize() if r["run"] == "prompt_v1")
    assert row["n_scored"] == 3 and row["avg_quality"] == 3 and row["avg_meaning"] == 5
    assert row["avg_overall"] == round((3 + 5 + 3) / 3, 2) and row["avg_latency_ms"] == 101
