"""DEVOPS AI (security mode): triage merged scanner findings. Advisory only.

Reads findings.json (written by collect_findings.py), asks the DevOps AI to rank each finding and prints a
markdown report. It never runs commands, edits files, opens PRs or changes infrastructure.
Scanner facts (rule, severity, package, location) always come from findings.json; the AI only adds judgement.
Reuses the sanitizer, OpenRouter client, retries/fallbacks and evidence logging of ai_devops_analyzer.py.

    python ai_tools/security_triage.py --findings findings.json [--model M] [--max-findings 15]
"""
import argparse
import json
import sys
from pathlib import Path

try:
    import ai_devops_analyzer as analyzer
except ImportError:  # imported as ai_tools.security_triage
    from ai_tools import ai_devops_analyzer as analyzer

PROMPT_VERSION = "security-v1"
REQUIRED_KEYS = {"summary", "triage", "limitations"}
PROMPT = (
    "You are a vulnerability triage assistant for a small FastAPI service (SQLite storage, calls an LLM through "
    "OpenRouter over HTTPS) that is built into a Docker image and deployed to Kubernetes. You receive a numbered "
    "list of sanitized scanner findings from Bandit, pip-audit and Trivy. Each line is: number | severity | rule id | "
    "package version or location | fix hint | short description. Judge ONLY from the information given. Never invent "
    "CVE details, versions or file contents; when the information is not enough, answer 'unknown'. Never recommend "
    "disabling a scanner, lowering a gate or ignoring a HIGH or CRITICAL finding as the fix. "
    "Reply with ONLY a JSON object with keys: summary (string, max 40 words), triage (list with exactly one object "
    "per input finding), limitations (string). Each triage object has keys: id (the input number, integer), "
    "priority (one of P0, P1, P2, P3 where P0 means fix now), exploitable_in_this_app (yes|unlikely|unknown), "
    "false_positive_likelihood (low|medium|high), reason (string, max 25 words), recommended_fix (string, max 25 words)."
)

# Register the new prompt with the existing analyzer (its ask() looks prompts up by version).
analyzer.PROMPTS[PROMPT_VERSION] = PROMPT
analyzer.REQUIRED[PROMPT_VERSION] = REQUIRED_KEYS

TITLE = "AI vulnerability triage"
PRIORITIES = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
EXPLOITABLE = {"yes", "unlikely", "unknown"}
FP_LEVELS = {"low", "medium", "high"}
BLANK = {"priority": "unrated", "exploitable": "unknown", "false_positive": "unknown", "reason": "", "fix": ""}


def load_findings(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("findings", []) if isinstance(data, dict) else list(data)


def where(f):
    return " ".join(x for x in (f.get("package"), f.get("installed_version")) if x) or f.get("location", "")


def describe(i, f):
    return (f"{i} | {f.get('severity', 'UNKNOWN')} | {f.get('rule_id', '')} | {where(f)} | "
            f"fix: {f.get('fix') or 'none given'} | {f.get('description', '')}")


def cell(text, limit=110):
    text = " ".join(str(text or "").split()).replace("|", "/")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def clean_triage(output, count):
    """Keep only rows for real input numbers; normalise the AI's enum values; ignore anything invented."""
    rows = {}
    for item in (output or {}).get("triage") or []:
        if not isinstance(item, dict):
            continue
        try:
            fid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if not 1 <= fid <= count or fid in rows:
            continue
        priority = str(item.get("priority", "")).strip().upper()[:2]
        exploitable = str(item.get("exploitable_in_this_app", "")).strip().lower()
        false_positive = str(item.get("false_positive_likelihood", "")).strip().lower()
        rows[fid] = {
            "priority": priority if priority in PRIORITIES else "unrated",
            "exploitable": exploitable if exploitable in EXPLOITABLE else "unknown",
            "false_positive": false_positive if false_positive in FP_LEVELS else "unknown",
            "reason": str(item.get("reason", "")),
            "fix": str(item.get("recommended_fix", "")),
        }
    return rows


def render(findings, shown, rows, output, result):
    lines = [f"## {TITLE}", ""]
    if output.get("summary"):
        lines += [cell(output["summary"], 300), ""]
    lines += [
        "| # | Severity | Rule | Where | AI priority | Exploitable here | False-positive risk | Why and suggested fix |",
        "|---|---|---|---|---|---|---|---|",
    ]
    order = sorted(range(1, len(shown) + 1),
                   key=lambda i: (PRIORITIES.get(rows.get(i, BLANK)["priority"], 4), i))
    for i in order:
        f, r = shown[i - 1], rows.get(i, BLANK)
        advice = f"{r['reason']} Fix: {r['fix'] or f.get('fix') or 'none given'}".strip()
        lines.append(
            f"| {i} | {cell(f.get('severity'))} | {cell(f.get('rule_id'))} | {cell(where(f), 60)} | "
            f"{r['priority']} | {r['exploitable']} | {r['false_positive']} | {cell(advice, 240)} |")
    if len(findings) > len(shown):
        lines += ["", f"_Showing the {len(shown)} highest-severity of {len(findings)} findings._"]
    if output.get("limitations"):
        lines += ["", f"_Limitations: {cell(output['limitations'], 200)}_"]
    model = result.get("model_returned") or result.get("model_requested")
    lines += ["", f"_Model: {model} - prompt {PROMPT_VERSION} - {result.get('latency_ms')} ms - "
                  "advisory only: a human reviews every fix._"]
    return "\n".join(lines) + "\n"


def run(findings_path, model=None, max_findings=15):
    findings = load_findings(findings_path)
    if not findings:
        md = f"## {TITLE}\n\nNo findings to triage.\n"
        analyzer.emit(md)
        return md
    shown = findings[:max_findings]  # collector output is already sorted, highest severity first
    content = "Scanner findings (highest severity first):\n" + "\n".join(
        describe(i, f) for i, f in enumerate(shown, 1))
    content, meta = analyzer.sanitize(content)  # second pass right before sending, as the CI mode does
    result = analyzer.ask(PROMPT_VERSION, content, model)
    path = analyzer.save_evidence("security", content, meta, result)
    output = result.get("output")
    if result.get("error") or output is None:
        md = analyzer.to_markdown(TITLE, result)
    else:
        md = render(findings, shown, clean_triage(output, len(shown)), output, result)
    analyzer.emit(md)
    print(f"evidence -> {path}", file=sys.stderr)
    return md


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--findings", default="findings.json")
    ap.add_argument("--model", help="defaults to ANALYZER_MODEL, then OPENROUTER_MODEL")
    ap.add_argument("--max-findings", type=int, default=15)
    args = ap.parse_args(argv)
    run(args.findings, args.model, args.max_findings)


if __name__ == "__main__":
    main()
