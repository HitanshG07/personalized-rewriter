import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_tools"))

import collect_findings as cf  # noqa: E402

SECRET = "sk-or-v1-" + "ab" * 20  # built at runtime on purpose


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def sample_files(tmp_path):
    bandit = write(tmp_path / "bandit.json", {"results": [{
        "test_id": "B105", "issue_severity": "MEDIUM", "filename": "app\\main.py",
        "line_number": 12, "issue_text": f"Possible hardcoded password {SECRET}"}]})
    pip_audit = write(tmp_path / "pip.json", {"dependencies": [{
        "name": "requests", "version": "2.0.0",
        "vulns": [{"id": "GHSA-xxxx", "aliases": ["CVE-2024-0001"],
                   "fix_versions": ["2.32.0"], "description": "Header leak"}]}]})
    trivy = write(tmp_path / "trivy.json", {"Results": [{
        "Target": "requirements.txt",
        "Vulnerabilities": [{"VulnerabilityID": "CVE-2024-0001", "PkgName": "requests",
                             "InstalledVersion": "2.0.0", "FixedVersion": "2.32.0",
                             "Severity": "HIGH", "Title": "Header leak"}],
        "Secrets": [{"RuleID": "aws-key", "Severity": "CRITICAL", "StartLine": 3,
                     "Title": "AWS key", "Match": SECRET}]}]})
    gitleaks = write(tmp_path / "gitleaks.json", [{
        "RuleID": "generic-api-key", "File": ".env", "StartLine": 1,
        "Description": "API key", "Secret": SECRET, "Match": SECRET}])
    return bandit, pip_audit, trivy, gitleaks


def test_merge_dedupe_and_sort(tmp_path):
    b, p, t, g = sample_files(tmp_path)
    report = cf.collect(b, p, t, g)
    findings = report["findings"]

    cve = [f for f in findings if f["rule_id"] == "CVE-2024-0001"]
    assert len(cve) == 1
    assert sorted(cve[0]["tools"]) == ["pip-audit", "trivy"]
    assert cve[0]["severity"] == "HIGH"
    assert cve[0]["fix"] == "2.32.0"

    order = [cf.SEVERITY_ORDER.index(f["severity"]) for f in findings]
    assert order == sorted(order)
    assert report["summary"]["total"] == len(findings) == 4


def test_secrets_never_in_output(tmp_path):
    b, p, t, g = sample_files(tmp_path)
    text = json.dumps(cf.collect(b, p, t, g))
    assert SECRET not in text
    assert "abab" not in text


def test_windows_paths_are_normalised(tmp_path):
    b, _, _, _ = sample_files(tmp_path)
    finding = cf.collect(bandit=b)["findings"][0]
    assert finding["location"] == "app/main.py:12"


def test_missing_and_invalid_files_are_skipped(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    report = cf.collect(bandit=str(tmp_path / "nope.json"), trivy=str(bad))
    assert report["summary"]["total"] == 0
