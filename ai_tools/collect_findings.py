"""Merge scanner output into one sanitized, deduplicated findings list.

Usage:
    python ai_tools/collect_findings.py \
        --bandit bandit.json --pip-audit pip-audit.json \
        --trivy trivy.json --gitleaks gitleaks.json \
        --out findings.json

Any scanner file that is missing is skipped with a warning.
Secret values from Gitleaks/Trivy are never copied; only rule, file and line.
This script only reports. It never changes code, files or infrastructure.
"""
import argparse
import json
import re
import sys
from pathlib import Path

SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
SEVERITY_MAP = {
    "CRITICAL": "CRITICAL",
    "HIGH": "HIGH",
    "MEDIUM": "MEDIUM",
    "MODERATE": "MEDIUM",
    "LOW": "LOW",
    "INFO": "LOW",
    "INFORMATIONAL": "LOW",
}
MAX_TEXT = 300

REDACTIONS = [
    (re.compile(r"sk-or-[A-Za-z0-9_\-]{10,}"), "[REDACTED]"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[=:]\s*\S+"), r"\1=[REDACTED]"),
    (re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"), "[REDACTED]"),
]


def clean(text):
    """Redact token-like strings, collapse whitespace and cap the length."""
    text = " ".join(str(text or "").split())
    for pattern, repl in REDACTIONS:
        text = pattern.sub(repl, text)
    return text[:MAX_TEXT]


def norm_severity(value):
    return SEVERITY_MAP.get(str(value or "").upper(), "UNKNOWN")


def norm_path(value):
    return str(value or "").replace("\\", "/")


def make(tool, rule_id, severity, location="", package="", installed="", fix="", description=""):
    return {
        "tool": tool,
        "rule_id": str(rule_id or "UNKNOWN"),
        "severity": norm_severity(severity),
        "location": norm_path(location),
        "package": package or "",
        "installed_version": installed or "",
        "fix": clean(fix),
        "description": clean(description),
    }


def load_json(path):
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        print(f"warning: {path} not found, skipping", file=sys.stderr)
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"warning: {path} is not valid JSON ({exc}), skipping", file=sys.stderr)
        return None


def parse_bandit(data):
    out = []
    for r in (data or {}).get("results", []):
        loc = f"{r.get('filename', '')}:{r.get('line_number', '')}"
        out.append(make("bandit", r.get("test_id"), r.get("issue_severity"),
                        location=loc, description=r.get("issue_text")))
    return out


def parse_pip_audit(data):
    deps = data.get("dependencies", []) if isinstance(data, dict) else (data or [])
    out = []
    for dep in deps:
        for v in dep.get("vulns", []) or []:
            ids = [v.get("id", "")] + list(v.get("aliases", []) or [])
            cve = next((i for i in ids if str(i).startswith("CVE-")), None)
            out.append(make("pip-audit", cve or v.get("id"), "UNKNOWN",
                            package=dep.get("name"), installed=dep.get("version"),
                            fix=", ".join(v.get("fix_versions") or []),
                            description=v.get("description")))
    return out


def parse_trivy(data):
    out = []
    for res in (data or {}).get("Results", []) or []:
        target = res.get("Target", "")
        for v in res.get("Vulnerabilities", []) or []:
            out.append(make("trivy", v.get("VulnerabilityID"), v.get("Severity"),
                            location=target, package=v.get("PkgName"),
                            installed=v.get("InstalledVersion"), fix=v.get("FixedVersion"),
                            description=v.get("Title") or v.get("Description")))
        for m in res.get("Misconfigurations", []) or []:
            out.append(make("trivy", m.get("ID"), m.get("Severity"), location=target,
                            fix=m.get("Resolution"),
                            description=m.get("Title") or m.get("Message")))
        for s in res.get("Secrets", []) or []:
            loc = f"{target}:{s.get('StartLine', '')}"
            out.append(make("trivy", s.get("RuleID"), s.get("Severity"), location=loc,
                            description=s.get("Title")))
    return out


def parse_gitleaks(data):
    out = []
    for f in data or []:
        loc = f"{f.get('File', '')}:{f.get('StartLine', '')}"
        out.append(make("gitleaks", f.get("RuleID"), "HIGH", location=loc,
                        description=f.get("Description")))
    return out


def dedupe(findings):
    merged = {}
    for f in findings:
        if f["package"]:
            key = ("vuln", f["rule_id"], f["package"], f["installed_version"])
        else:
            key = (f["tool"], f["rule_id"], f["location"])
        if key not in merged:
            merged[key] = {**f, "tools": [f["tool"]]}
            continue
        m = merged[key]
        if f["tool"] not in m["tools"]:
            m["tools"].append(f["tool"])
        if SEVERITY_ORDER.index(f["severity"]) < SEVERITY_ORDER.index(m["severity"]):
            m["severity"] = f["severity"]
        for field in ("fix", "description", "location"):
            if not m[field] and f[field]:
                m[field] = f[field]
    for m in merged.values():
        m.pop("tool", None)
    return sorted(merged.values(), key=lambda x: (SEVERITY_ORDER.index(x["severity"]), x["rule_id"]))


def collect(bandit=None, pip_audit=None, trivy=None, gitleaks=None):
    findings = []
    findings += parse_bandit(load_json(bandit))
    findings += parse_pip_audit(load_json(pip_audit))
    findings += parse_trivy(load_json(trivy))
    findings += parse_gitleaks(load_json(gitleaks))
    result = dedupe(findings)
    counts = {s: sum(1 for f in result if f["severity"] == s) for s in SEVERITY_ORDER}
    return {"summary": {"total": len(result), "by_severity": counts}, "findings": result}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bandit")
    ap.add_argument("--pip-audit", dest="pip_audit")
    ap.add_argument("--trivy")
    ap.add_argument("--gitleaks")
    ap.add_argument("--out", default="findings.json")
    args = ap.parse_args(argv)

    report = collect(args.bandit, args.pip_audit, args.trivy, args.gitleaks)
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    s = report["summary"]
    print(f"{s['total']} findings -> {args.out}  {s['by_severity']}")


if __name__ == "__main__":
    main()
