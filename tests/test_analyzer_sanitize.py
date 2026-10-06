"""Proves secrets never survive sanitization before logs are uploaded or sent to OpenRouter.

Fake secrets are assembled at runtime so Gitleaks never sees a secret-shaped literal in this file.
"""
import json

from ai_tools import ai_devops_analyzer as az

FAKE_OR = "sk-" + "or-v1-" + "a1b2c3d4" * 6
FAKE_GH = "gh" + "p_" + "Zx9Yw8Vu7" * 4
FAKE_DH = "dckr" + "_pat_" + "Q1w2E3r4T5y6"
FAKE_JWT = "ey" + "JhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "dozjgNryP4J3jVmNHl0w5N"
FAKE_B64 = "QmFzZTY0U2VjcmV0" + "VmFsdWVUaGF0SXNMb25n" + "RW5vdWdoMTIzNDU2Nzg5"

RAW_LOG = f"""2026-09-27T10:00:01.1234567Z \x1b[31mRun pytest\x1b[0m
Authorization: Bearer {FAKE_OR}
curl -H "Authorization: token {FAKE_GH}" https://api.github.com
OPENROUTER_API_KEY={FAKE_OR}
DOCKERHUB_TOKEN: {FAKE_DH}
password="hunter2-not-real"
session {FAKE_JWT}
blob {FAKE_B64}
contact hetmul@example.com
FAILED tests/test_rewrite.py::test_missing_sample - assert 422 == 404
image sha256:{"ab" * 32}
path /home/runner/work/personalized-rewriter/personalized-rewriter/app/main.py
"""


def test_no_secret_survives():
    clean, meta = az.sanitize(RAW_LOG)
    for secret in (FAKE_OR, FAKE_GH, FAKE_DH, FAKE_JWT, FAKE_B64, "hunter2-not-real", "hetmul@example.com"):
        assert secret not in clean, secret
    assert "Bearer [REDACTED]" in clean or "Authorization: [REDACTED]" in clean
    assert meta["sanitized"] is True and sum(meta["redactions"].values()) >= 7


def test_useful_diagnostic_lines_are_kept():
    clean, _ = az.sanitize(RAW_LOG)
    assert "FAILED tests/test_rewrite.py::test_missing_sample - assert 422 == 404" in clean
    assert "/app/main.py" in clean
    assert "sha256:" + "ab" * 32 in clean  # image digests are not secrets
    assert "\x1b[" not in clean and not clean.startswith("2026-09-27T")  # ANSI + timestamps stripped


def test_trim_keeps_error_context_and_tail():
    lines = [f"noise line {i}" for i in range(500)]
    lines[100] = "E   AssertionError: assert 422 == 404"
    text, meta = az.trim("\n".join(lines), context=2, tail=5)
    assert "AssertionError" in text and "noise line 99" in text and "noise line 499" in text
    assert "noise line 300" not in text
    assert meta == {"lines_in": 500, "lines_kept": 10}


def test_extract_json_handles_fences_and_missing_keys():
    required = {"a", "b"}
    assert az.extract_json('```json\n{"a": 1, "b": [2]}\n```', required) == {"a": 1, "b": [2]}
    assert az.extract_json('{"a": 1}', required) is None
    assert az.extract_json("no json here", required) is None


def test_sanitize_cli_deletes_raw_file(tmp_path, capsys):
    raw = tmp_path / "test_raw.log"
    raw.write_text(RAW_LOG, encoding="utf-8")
    az.main(["sanitize", str(raw), "--delete-raw"])
    out = capsys.readouterr()
    assert FAKE_OR not in out.out and "FAILED tests/test_rewrite.py" in out.out
    assert json.loads(out.err)["sanitized"] is True
    assert not raw.exists()


def test_ci_mode_degrades_gracefully_without_key(tmp_path, monkeypatch, capsys):
    """No OpenRouter key -> 'AI diagnosis unavailable' in the step summary, no exception, evidence is sanitized."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("JOB_RESULTS", json.dumps({"test": {"result": "failure", "outputs": {}}}))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(az, "EVIDENCE", tmp_path / "evidence")
    log = tmp_path / "test.log"
    log.write_text(RAW_LOG, encoding="utf-8")

    az.main(["ci", "--log", str(log)])

    assert "AI diagnosis unavailable (not_configured)" in summary.read_text(encoding="utf-8")
    [evidence] = (tmp_path / "evidence").glob("ci_*.json")
    record = json.loads(evidence.read_text(encoding="utf-8"))
    assert record["sanitization"]["sanitized"] is True
    assert FAKE_OR not in evidence.read_text(encoding="utf-8")
    assert '"test": "failure"' in record["input_sanitized"]


def test_extract_json_after_reasoning_text_with_braces():
    text = 'Thinking: the {test} job passed and set {x} ... Final answer:\n{"a": "ok", "b": ["x"]}\nDone.'
    assert az.extract_json(text, {"a", "b"}) == {"a": "ok", "b": ["x"]}


def test_ai_never_receives_double_quotes(monkeypatch):
    """A copied log line like f"cp {path}" used to break the AI's JSON answer (Task 2)."""
    from app import llm_client

    sent = {}

    def fake_chat(messages, **kwargs):
        sent["user"] = messages[1]["content"]
        return llm_client.LLMResult('{"failed_stage": "security"}', "m", None, 1, 1, 1)

    monkeypatch.setattr(llm_client, "chat", fake_chat)
    az.ask("ci-v1", 'Location: app/storage.py:60\nsubprocess.run(f"cp {path} {path}.bak", shell=True)', model="m")
    assert '"' not in sent["user"]
    assert "f'cp {path} {path}.bak'" in sent["user"]


def test_extract_json_tolerates_newlines_inside_strings():
    text = '{"a": "line one\nline two", "b": ["x"]}'
    assert az.extract_json(text, {"a", "b"})["a"] == "line one\nline two"
