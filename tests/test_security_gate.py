import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_tools"))

import security_gate as gate  # noqa: E402


def finding(rule, severity, location="", package="pkg"):
    return {"rule_id": rule, "severity": severity, "package": package, "installed_version": "1.0", "location": location}


def write(tmp_path, items):
    path = tmp_path / "findings.json"
    path.write_text(json.dumps({"findings": items}), encoding="utf-8")
    return str(path)


def test_fails_on_high(tmp_path):
    path = write(tmp_path, [finding("CVE-1", "HIGH")])
    assert gate.main(["--findings", path, "--fail-on", "HIGH", "--allowlist", str(tmp_path / "none.txt")]) == 1


def test_passes_when_only_medium_and_low(tmp_path):
    path = write(tmp_path, [finding("CVE-1", "MEDIUM"), finding("CVE-2", "LOW")])
    assert gate.main(["--findings", path, "--fail-on", "HIGH", "--allowlist", str(tmp_path / "none.txt")]) == 0


def test_allowlist_suppresses_a_rule_and_ignores_comments(tmp_path):
    allow = tmp_path / "allow.txt"
    allow.write_text("# accepted risks\nCVE-1   # not reachable, review 2026-12-01\n", encoding="utf-8")
    path = write(tmp_path, [finding("CVE-1", "CRITICAL")])
    assert gate.main(["--findings", path, "--fail-on", "HIGH", "--allowlist", str(allow)]) == 0


def test_allowlist_does_not_hide_other_rules(tmp_path):
    allow = tmp_path / "allow.txt"
    allow.write_text("CVE-1\n", encoding="utf-8")
    path = write(tmp_path, [finding("CVE-1", "CRITICAL"), finding("CVE-2", "HIGH")])
    assert gate.main(["--findings", path, "--fail-on", "HIGH", "--allowlist", str(allow)]) == 1


def test_unknown_severity_never_blocks(tmp_path):
    path = write(tmp_path, [finding("CVE-1", "UNKNOWN")])
    assert gate.main(["--findings", path, "--fail-on", "LOW", "--allowlist", str(tmp_path / "none.txt")]) == 0


def test_scoped_allowlist_only_covers_the_named_file(tmp_path):
    allow = tmp_path / "allow.txt"
    allow.write_text("DS-0002 Dockerfile.baseline   # intentionally weak comparison file\n", encoding="utf-8")
    baseline = finding("DS-0002", "HIGH", "Dockerfile.baseline", package="")
    real = finding("DS-0002", "HIGH", "Dockerfile", package="")
    args = ["--fail-on", "HIGH", "--allowlist", str(allow)]
    assert gate.main(["--findings", write(tmp_path, [baseline])] + args) == 0
    assert gate.main(["--findings", write(tmp_path, [baseline, real])] + args) == 1
