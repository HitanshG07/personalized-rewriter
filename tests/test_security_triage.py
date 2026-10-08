import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_tools"))

import security_triage as st  # noqa: E402

SECRET = "sk-or-v1-" + "ab" * 20  # built at runtime on purpose


def finding(i, severity="HIGH", **extra):
    return {"rule_id": f"CVE-2024-{i:04d}", "severity": severity, "package": "requests",
            "installed_version": "2.0.0", "location": "requirements.txt", "fix": "2.32.0",
            "description": "Header leak", "tools": ["trivy"], **extra}


def write(tmp_path, findings):
    path = tmp_path / "findings.json"
    path.write_text(json.dumps({"findings": findings}), encoding="utf-8")
    return str(path)


def ok_result(triage):
    return {"prompt_version": st.PROMPT_VERSION, "model_requested": "test-model", "model_returned": "test-model",
            "latency_ms": 5, "structured": True, "output_text": "{}",
            "output": {"summary": "Short summary.", "triage": triage, "limitations": "Limited context."}}


def item(fid, priority="P1", **extra):
    return {"id": fid, "priority": priority, "exploitable_in_this_app": "yes",
            "false_positive_likelihood": "low", "reason": "Reachable.", "recommended_fix": "Upgrade.", **extra}


@pytest.fixture
def fake_ai(monkeypatch, tmp_path):
    """Replace the OpenRouter call: 0 real API calls, evidence goes to tmp, CI summary is not polluted."""
    state = {"calls": 0, "content": "", "result": None}
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(st.analyzer, "EVIDENCE", tmp_path / "evidence")

    def fake_ask(version, content, model):
        state["calls"] += 1
        state["content"] = content
        return state["result"]

    monkeypatch.setattr(st.analyzer, "ask", fake_ask)
    return state


def test_prompt_is_registered_with_the_analyzer():
    assert st.PROMPT_VERSION in st.analyzer.PROMPTS
    assert st.analyzer.REQUIRED[st.PROMPT_VERSION] == st.REQUIRED_KEYS


def test_report_uses_scanner_facts_and_sorts_by_ai_priority(fake_ai, tmp_path):
    fake_ai["result"] = ok_result([
        item(1, "P3"), item(2, "P0"), item(99, "P0", rule_id="CVE-FAKE")])
    md = st.run(write(tmp_path, [finding(1), finding(2)]))
    assert "CVE-FAKE" not in md
    assert md.index("CVE-2024-0002") < md.index("CVE-2024-0001")
    assert "requests 2.0.0" in md


def test_finding_the_ai_skipped_is_marked_unrated(fake_ai, tmp_path):
    fake_ai["result"] = ok_result([item(1, "P0")])
    md = st.run(write(tmp_path, [finding(1), finding(2)]))
    row = next(line for line in md.splitlines() if "CVE-2024-0002" in line)
    assert "unrated" in row


def test_invalid_ai_values_are_normalised(fake_ai, tmp_path):
    fake_ai["result"] = ok_result([item(1, "banana", exploitable_in_this_app="maybe")])
    md = st.run(write(tmp_path, [finding(1)]))
    row = next(line for line in md.splitlines() if "CVE-2024-0001" in line)
    assert "unrated" in row and "unknown" in row


def test_no_findings_means_no_api_call(fake_ai, tmp_path):
    md = st.run(write(tmp_path, []))
    assert fake_ai["calls"] == 0
    assert "No findings" in md


def test_secrets_are_not_sent_to_the_ai(fake_ai, tmp_path):
    fake_ai["result"] = ok_result([item(1)])
    st.run(write(tmp_path, [finding(1, description=f"Hardcoded key {SECRET}")]))
    assert SECRET not in fake_ai["content"]


def test_ai_failure_gives_a_clear_message_not_a_crash(fake_ai, tmp_path):
    fake_ai["result"] = {"prompt_version": st.PROMPT_VERSION, "model_requested": "m", "error": "timeout", "output": None}
    md = st.run(write(tmp_path, [finding(1)]))
    assert "unavailable" in md


def test_only_the_top_findings_are_sent(fake_ai, tmp_path):
    fake_ai["result"] = ok_result([item(1)])
    md = st.run(write(tmp_path, [finding(i) for i in range(1, 21)]), max_findings=15)
    sent = [line for line in fake_ai["content"].splitlines() if line[:1].isdigit()]
    assert len(sent) == 15
    assert "15 highest-severity of 20" in md
