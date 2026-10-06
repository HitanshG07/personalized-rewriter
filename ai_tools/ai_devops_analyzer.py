"""DEVOPS AI: OpenRouter-backed analysis of CI failures, Dockerfiles and Prometheus metrics.

Advisory only - it never runs commands, commits, merges, rotates secrets or changes infrastructure.
Everything sent to OpenRouter is sanitized first; raw input is never saved as evidence.

  sanitize RAW [--delete-raw]                  redact secrets, print sanitized log (stdlib only; CI uses this)
  ci --log FILE... [--delete-raw]              diagnose a failed pipeline (JOB_RESULTS env = needs context)
  docker [--baseline docs/evidence/docker_baseline.json]
  metrics --snapshot FILE | --prometheus URL   analyze a Prometheus snapshot (URL mode saves the snapshot first)
"""
import argparse
import datetime
import json
import os
import re
import subprocess  # fixed git argv list only, never shell=True
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs" / "evidence" / "ai"

# ---------------------------------------------------------------- sanitizer (stdlib only)

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_GH_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z ", re.M)

RULES: list[tuple[str, re.Pattern, str]] = [
    ("auth_header", re.compile(r"(?i)(authorization\s*[:=]\s*)\S+(\s+\S+)?"), r"\1[REDACTED]"),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    ("openrouter_key", re.compile(r"sk-or-[A-Za-z0-9_-]{8,}"), "[REDACTED:openrouter_key]"),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "[REDACTED:api_key]"),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), "[REDACTED:github_token]"),
    ("dockerhub_token", re.compile(r"\bdckr_pat_[A-Za-z0-9_-]{10,}"), "[REDACTED:dockerhub_token]"),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[REDACTED:jwt]"),
    (
        "key_value",
        re.compile(r"(?i)\b([A-Z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Z0-9_]*)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"),
        r"\1\2[REDACTED]",
    ),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED:email]"),
    ("long_hex", re.compile(r"(?<!sha256:)(?<![\w:])[A-Fa-f0-9]{32,}(?!\w)"), "[REDACTED:hex]"),
]
_B64 = re.compile(r"(?<![\w/+])[A-Za-z0-9+/]{40,}={0,2}(?![\w/+=])")


def _redact_b64(match: re.Match) -> str:
    s = match.group(0)  # only mixed-case + digit runs look like secrets; plain words/paths pass
    if re.search(r"[A-Z]", s) and re.search(r"[a-z]", s) and re.search(r"\d", s):
        return "[REDACTED:base64]"
    return s


def sanitize(text: str) -> tuple[str, dict]:
    """Redact secrets and strip ANSI/timestamp noise. Returns (sanitized_text, metadata)."""
    text = _GH_TIMESTAMP.sub("", _ANSI.sub("", text))
    counts = {}
    for name, pattern, repl in RULES:
        text, counts[name] = pattern.subn(repl, text)
    before = text.count("[REDACTED:base64]")
    text = _B64.sub(_redact_b64, text)
    counts["base64"] = text.count("[REDACTED:base64]") - before
    return text, {"sanitized": True, "redactions": {k: v for k, v in counts.items() if v}}


_SIGNAL = re.compile(r"(?i)(##\[error\]|error|fail|exception|traceback|assert|denied|fatal|critical|leak|vulnerab)")


def trim(text: str, context: int = 6, tail: int = 60, max_chars: int = 14000) -> tuple[str, dict]:
    """Keep lines near error signals plus the last `tail` lines, capped - fewer tokens, less noise."""
    lines = text.splitlines()
    keep = set(range(max(0, len(lines) - tail), len(lines)))
    for i, line in enumerate(lines):
        if _SIGNAL.search(line):
            keep.update(range(max(0, i - context), min(len(lines), i + context + 1)))
    out, prev = [], -2
    for i in sorted(keep):
        if i != prev + 1:
            out.append("...")
        out.append(lines[i])
        prev = i
    result = "\n".join(out)
    if len(result) > max_chars:
        result = "...\n" + result[-max_chars:]
    return result, {"lines_in": len(lines), "lines_kept": len(keep)}


def _read_inputs(paths: list[str], delete_raw: bool) -> list[tuple[str, str]]:
    items = []
    for p in paths:
        path = Path(p)
        items.append((path.name, path.read_text(encoding="utf-8", errors="replace")))
        if delete_raw:
            path.unlink()  # raw logs never outlive sanitization
    return items


# ---------------------------------------------------------------- OpenRouter call + evidence

PROMPTS = {
    "ci-v1": (
        "You are a CI/CD failure analyst for a GitHub Actions pipeline (pytest, Bandit, Gitleaks, Docker build, "
        "Trivy, Docker Hub push, kubectl deploy to Minikube). You receive the result of each pipeline job and "
        "sanitized log excerpts ([REDACTED] marks removed secrets). Identify the failed stage and the most probable "
        "root cause using ONLY evidence in the logs. Never recommend disabling tests or security gates as the fix. "
        "Reply with ONLY a JSON object with keys: failed_stage (one of test|build|security|publish|deploy), "
        "probable_cause (string), evidence (list of exact log lines), affected_file_or_config (string), "
        "affected_line (integer or null), recommended_fix (string), verification_steps (list of strings), "
        "confidence (low|medium|high), limitations (string)."
    ),
    "docker-v1": (
        "You are reviewing a Dockerfile for a small FastAPI service. You receive the Dockerfile, requirements.txt, "
        ".dockerignore, the repository file list and measured baseline benchmarks. Inspect: base image choice, "
        "unnecessary build/runtime dependencies, layer/cache ordering, .dockerignore, multi-stage opportunities, "
        "non-root execution, unnecessary files copied, startup/runtime configuration and security trade-offs. "
        "Do not promise specific percentages; describe the expected direction of each effect. "
        "Reply with ONLY a JSON object with keys: issues (list), recommended_changes (list), "
        "expected_effect (list), security_considerations (list), verification_commands (list)."
    ),
    "metrics-v1": (
        "You are analyzing a Prometheus metrics snapshot from a FastAPI service that calls an LLM through "
        "OpenRouter, running as 3 Kubernetes Pods. Identify performance or reliability problems supported by the "
        "numbers. Reply with ONLY a JSON object with keys: observations (list), likely_causes (list), "
        "checks (list), recommended_change (string, ONE configuration or code change to try), "
        "verification_plan (string), limitations (string)."
    ),
}
JSON_ONLY = (
    "\nOutput format: reply with the JSON object ONLY. Begin your reply with '{' and end it with '}'. "
    "Do not write any analysis, reasoning, markdown or text outside the JSON. "
    "Inside string values never use double quotes; use single quotes instead."
)
REQUIRED = {
    "ci-v1": {"failed_stage", "probable_cause", "evidence", "affected_file_or_config", "recommended_fix",
              "verification_steps", "confidence", "limitations"},
    "docker-v1": {"issues", "recommended_changes", "expected_effect", "security_considerations",
                  "verification_commands"},
    "metrics-v1": {"observations", "likely_causes", "checks", "recommended_change", "verification_plan"},
}


def extract_json(text: str, required: set[str]) -> dict | None:
    """First JSON object in `text` that has all required keys (tolerates reasoning text and code fences)."""
    decoder = json.JSONDecoder(strict=False)  # tolerate raw newlines inside string values
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            data, _ = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and required <= data.keys():
            return data
    return None


def ask(prompt_version: str, user_content: str, model: str | None) -> dict:
    """Call OpenRouter via the shared client. Imported lazily so `sanitize` needs no third-party packages."""
    sys.path.insert(0, str(ROOT))
    from app.llm_client import LLMError, chat

    model = model or os.getenv("ANALYZER_MODEL") or os.getenv("OPENROUTER_MODEL", "")
    # Availability over reproducibility here: free models get throttled upstream, so the DevOps AI retries with
    # backoff and may fall back to other free models. (Formal evaluation never uses fallback - PROJECT_PLAN D13.)
    fallbacks = [m.strip() for m in os.getenv("ANALYZER_FALLBACK_MODELS", "").split(",") if m.strip()]
    record = {"prompt_version": prompt_version, "model_requested": model, "fallback_models": fallbacks}
    try:
        r = chat(
            [
                {"role": "system", "content": PROMPTS[prompt_version] + JSON_ONLY},
                # Log lines like f"cp {path}" get copied into the JSON answer unescaped and break it,
                # so the AI only ever sees single quotes.
                {"role": "user", "content": user_content.replace('"', "'") + "\n\n" + JSON_ONLY.strip()},
            ],
            model=model, temperature=0.2, max_tokens=3000, timeout_s=120,
            fallback_models=fallbacks, attempts=3, retry_delay_s=5,
            # thinking models otherwise spend the whole budget on hidden reasoning and return empty content
            extra_body={"reasoning": {"enabled": False}},
            retry_invalid=True,  # free models occasionally return an empty answer; try again / next model
        )
    except LLMError as e:
        return {**record, "error": e.reason, "output": None}
    parsed = extract_json(r.text, REQUIRED[prompt_version])
    return {
        **record,
        "model_returned": r.model,
        "provider": r.provider,
        "latency_ms": r.latency_ms,
        "tokens": {"prompt": r.prompt_tokens, "completion": r.completion_tokens},
        "structured": parsed is not None,
        "output": parsed,
        "output_text": r.text,
    }


def save_evidence(mode: str, sanitized_input: str, sanitization: dict, result: dict) -> Path:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now().astimezone()
    path = EVIDENCE / f"{mode}_{now:%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({
        "mode": mode,
        "created_at": now.isoformat(timespec="seconds"),
        "sanitization": sanitization,
        "input_sanitized": sanitized_input,  # raw input is never stored
        **result,
    }, indent=2) + "\n", encoding="utf-8")
    return path


def to_markdown(title: str, result: dict) -> str:
    if result.get("error") or result.get("output") is None:
        if result.get("output_text"):
            return f"## {title}\n\n_Unstructured AI output (review manually):_\n\n{result['output_text']}\n"
        return f"## {title}\n\nAI diagnosis unavailable ({result.get('error', 'no output')}). Inspect the logs manually.\n"
    lines = [f"## {title}", ""]
    for key, value in result["output"].items():
        label = key.replace("_", " ").capitalize()
        if isinstance(value, list):
            lines += [f"**{label}:**"] + [f"- {v}" for v in value] + [""]
        else:
            lines += [f"**{label}:** {value}", ""]
    lines.append(f"_Model: {result.get('model_returned') or result['model_requested']} · prompt {result['prompt_version']}"
                 f" · {result.get('latency_ms')} ms · advisory only, verify before applying._")
    return "\n".join(lines) + "\n"


def emit(markdown: str) -> None:
    print(markdown)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(markdown + "\n")


# ---------------------------------------------------------------- modes

def cmd_sanitize(args) -> None:
    [(_, raw)] = _read_inputs([args.raw], args.delete_raw)
    text, meta = sanitize(raw)
    sys.stdout.write(text)
    print(json.dumps(meta), file=sys.stderr)


def cmd_ci(args) -> None:
    parts, meta = [], {"sanitized": True, "redactions": {}, "files": []}
    for name, raw in _read_inputs(args.log, args.delete_raw):
        clean, m = sanitize(raw)
        trimmed, t = trim(clean)
        parts.append(f"### {name}\n```\n{trimmed}\n```")
        for k, v in m["redactions"].items():
            meta["redactions"][k] = meta["redactions"].get(k, 0) + v
        meta["files"].append({"name": name, **t})
    job_results = os.getenv("JOB_RESULTS", "{}")
    try:  # keep only job -> result, drop outputs
        job_results = json.dumps({k: v.get("result") for k, v in json.loads(job_results).items()})
    except (ValueError, AttributeError):
        pass
    content = f"Pipeline job results: {job_results}\n\nSanitized log excerpts:\n\n" + "\n\n".join(parts)
    content = sanitize(content)[0]  # second pass right before sending
    result = ask("ci-v1", content, args.model)
    path = save_evidence("ci", content, meta, result)
    emit(to_markdown("AI CI failure diagnosis", result))
    print(f"evidence -> {path}", file=sys.stderr)


def cmd_docker(args) -> None:
    def read(name):
        p = ROOT / name
        return p.read_text(encoding="utf-8") if p.exists() else "(missing)"

    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout
    baseline = Path(args.baseline)
    content = (
        f"Dockerfile:\n```\n{read(args.dockerfile)}\n```\n\nrequirements.txt:\n```\n{read('requirements.txt')}\n```\n\n"
        f".dockerignore:\n```\n{read('.dockerignore')}\n```\n\nRepository files:\n```\n{files}\n```\n\n"
        f"Baseline measurements:\n```json\n{baseline.read_text(encoding='utf-8') if baseline.exists() else '(not measured)'}\n```"
    )
    content, meta = sanitize(content)
    result = ask("docker-v1", content, args.model)
    path = save_evidence("docker", content, meta, result)
    emit(to_markdown("AI Dockerfile optimization review", result))
    print(f"evidence -> {path}", file=sys.stderr)


PROM_QUERIES = {
    "request_rate_per_s": "sum by (endpoint) (rate(requests_total[5m]))",
    "error_rate_per_s": "sum by (endpoint, reason) (rate(errors_total[5m]))",
    "rewrite_failures_by_reason": "sum by (reason) (increase(rewrite_failures_total[15m]))",
    "openrouter_failures_by_reason": "sum by (reason) (increase(openrouter_failures_total[15m]))",
    "llm_latency_p50_s": "histogram_quantile(0.5, sum by (le) (rate(llm_latency_seconds_bucket[15m])))",
    "llm_latency_p95_s": "histogram_quantile(0.95, sum by (le) (rate(llm_latency_seconds_bucket[15m])))",
    "rewrite_latency_p95_s": 'histogram_quantile(0.95, sum by (le) (rate(request_latency_seconds_bucket{endpoint="/rewrite"}[15m])))',
    "completion_tokens_per_call": 'increase(openrouter_tokens_total{type="completion"}[15m]) / ignoring(type) increase(openrouter_requests_total[15m])',
    "pods_up": 'count(up{job="rewriter"} == 1)',
}


def fetch_snapshot(base_url: str) -> dict:
    snap = {"taken_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "queries": {}}
    for name, q in PROM_QUERIES.items():
        url = f"{base_url.rstrip('/')}/api/v1/query?" + urllib.parse.urlencode({"query": q})
        with urllib.request.urlopen(url, timeout=10) as r:  # nosec B310
            data = json.load(r)["data"]["result"]
        snap["queries"][name] = {"promql": q, "result": [{"labels": d["metric"], "value": d["value"][1]} for d in data]}
    return snap


def cmd_metrics(args) -> None:
    if args.prometheus:
        snap = fetch_snapshot(args.prometheus)
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        snap_path = EVIDENCE / f"prometheus_snapshot_{datetime.datetime.now():%Y%m%d-%H%M%S}.json"
        snap_path.write_text(json.dumps(snap, indent=2) + "\n", encoding="utf-8")
        print(f"snapshot -> {snap_path}", file=sys.stderr)
    else:
        snap = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    content, meta = sanitize("Prometheus snapshot:\n```json\n" + json.dumps(snap, indent=2) + "\n```")
    result = ask("metrics-v1", content, args.model)
    path = save_evidence("metrics", content, meta, result)
    emit(to_markdown("AI metrics analysis", result))
    print(f"evidence -> {path}", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sanitize")
    s.add_argument("raw")
    s.add_argument("--delete-raw", action="store_true")
    s.set_defaults(fn=cmd_sanitize)

    c = sub.add_parser("ci")
    c.add_argument("--log", nargs="+", required=True)
    c.add_argument("--delete-raw", action="store_true")
    c.set_defaults(fn=cmd_ci)

    d = sub.add_parser("docker")
    d.add_argument("--dockerfile", default="Dockerfile")
    d.add_argument("--baseline", default=str(ROOT / "docs" / "evidence" / "docker_baseline.json"))
    d.set_defaults(fn=cmd_docker)

    m = sub.add_parser("metrics")
    g = m.add_mutually_exclusive_group(required=True)
    g.add_argument("--snapshot")
    g.add_argument("--prometheus", help="e.g. http://127.0.0.1:9090")
    m.set_defaults(fn=cmd_metrics)

    for sp in (c, d, m):
        sp.add_argument("--model", help="defaults to ANALYZER_MODEL, then OPENROUTER_MODEL")

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
