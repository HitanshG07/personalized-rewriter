"""Quality gate for findings.json: exit 1 if a non-allowlisted finding is at or above --fail-on.

    python ai_tools/security_gate.py --findings findings.json --fail-on HIGH --allowlist security/allowlist.txt

Allowlist: one entry per line, either 'RULE_ID' or 'RULE_ID PATH_PREFIX' (for example
'DS-0002 Dockerfile.baseline'). With a path, the rule is only accepted for locations that start with it.
'#' starts a comment; always write the reason. Findings with UNKNOWN severity (for example pip-audit-only
results) never block; they are still reported.
"""
import argparse
import json
import sys
from pathlib import Path

SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]


def load_allowlist(path):
    """Return a set of (rule_id, location_prefix); an empty prefix means the rule is accepted everywhere."""
    p = Path(path) if path else None
    if not p or not p.exists():
        return set()
    entries = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        parts = line.split("#", 1)[0].split()
        if parts:
            entries.add((parts[0], parts[1] if len(parts) > 1 else ""))
    return entries


def is_allowed(finding, allowed):
    location = finding.get("location", "")
    return any(rule == finding.get("rule_id") and location.startswith(prefix) for rule, prefix in allowed)


def blocking(findings, fail_on, allowed):
    limit = SEVERITIES.index(fail_on)
    out = []
    for f in findings:
        sev = f.get("severity")
        rank = SEVERITIES.index(sev) if sev in SEVERITIES else len(SEVERITIES) - 1
        if rank <= limit and not is_allowed(f, allowed):
            out.append(f)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--findings", default="findings.json")
    ap.add_argument("--fail-on", default="HIGH", choices=["CRITICAL", "HIGH", "MEDIUM", "LOW"])
    ap.add_argument("--allowlist", default="security/allowlist.txt")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.findings).read_text(encoding="utf-8"))
    findings = data.get("findings", []) if isinstance(data, dict) else data
    allowed = load_allowlist(args.allowlist)
    bad = blocking(findings, args.fail_on, allowed)
    for f in bad:
        where = " ".join(x for x in (f.get("package"), f.get("installed_version")) if x) or f.get("location", "")
        print(f"BLOCKING {f.get('severity')} {f.get('rule_id')} {where}")
    if bad:
        print(f"Gate FAILED: {len(bad)} finding(s) at {args.fail_on} or above (allowlist: security/allowlist.txt)")
        return 1
    print(f"Gate passed: no non-allowlisted {args.fail_on}+ findings ({len(allowed)} allowlist entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
