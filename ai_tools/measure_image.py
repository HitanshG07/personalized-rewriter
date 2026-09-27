"""Reproducible Docker image benchmark for Deliverable B (baseline vs AI-optimized). Stdlib only.

Usage:  python ai_tools/measure_image.py --label baseline
        python ai_tools/measure_image.py --label optimized --dockerfile Dockerfile

Writes docs/evidence/docker_<label>.json. Numbers are measured, never assumed.
"""
import argparse
import datetime
import hashlib
import json
import shutil
import socket
import subprocess  # fixed docker/trivy/git argv lists, never shell=True
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")


def run(*args: str, check: bool = True) -> str:
    return subprocess.run(args, cwd=ROOT, check=check, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()


def timed_build(tag: str, dockerfile: str, *extra: str) -> float:
    start = time.perf_counter()
    run("docker", "build", *extra, "-t", tag, "-f", dockerfile, ".")
    return round(time.perf_counter() - start, 2)


def count_vulns(report: dict) -> dict:
    """Trivy JSON -> counts per severity, total and fixable (has a FixedVersion)."""
    total = dict.fromkeys(SEVERITIES, 0)
    fixable = dict.fromkeys(SEVERITIES, 0)
    for result in report.get("Results") or []:
        for v in result.get("Vulnerabilities") or []:
            sev = v.get("Severity", "UNKNOWN")
            sev = sev if sev in total else "UNKNOWN"
            total[sev] += 1
            if v.get("FixedVersion"):
                fixable[sev] += 1
    return {"total": total, "fixable": fixable}


def trivy_counts(tag: str) -> dict | None:
    if not shutil.which("trivy"):
        return None
    out = run("trivy", "image", "--quiet", "--scanners", "vuln", "--format", "json", tag)
    return count_vulns(json.loads(out))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def startup_seconds(tag: str, limit_s: float = 60) -> float | None:
    """Time from `docker run` until /health returns 200."""
    port = free_port()
    start = time.perf_counter()
    cid = run("docker", "run", "-d", "-p", f"127.0.0.1:{port}:8000", "-e", "DB_PATH=/tmp/bench.db", tag)
    try:
        while time.perf_counter() - start < limit_s:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:  # nosec B310
                    if r.status == 200:
                        return round(time.perf_counter() - start, 2)
            except OSError:
                time.sleep(0.2)
        return None
    finally:
        run("docker", "rm", "-f", cid, check=False)


def measure(tag: str, dockerfile: str) -> dict:
    cold = timed_build(tag, dockerfile, "--no-cache")  # base image already pulled; build cache disabled
    warm = timed_build(tag, dockerfile)  # nothing changed: fully cached
    marker = ROOT / "app" / "_bench_marker.py"  # simulate a code-only change (content, not mtime)
    marker.write_text(f"# {time.time()}\n")
    try:
        code_change = timed_build(tag, dockerfile)
    finally:
        marker.unlink()
        timed_build(tag, dockerfile)  # rebuild so the measured image has no marker file

    inspect = json.loads(run("docker", "image", "inspect", tag))[0]
    return {
        "image_size_mb": round(inspect["Size"] / 1_000_000, 1),
        "cold_build_s": cold,
        "warm_build_s": warm,
        "code_change_rebuild_s": code_change,
        "filesystem_layers": len(inspect["RootFS"]["Layers"]),
        "history_steps": len(run("docker", "history", "-q", tag).splitlines()),
        "trivy": trivy_counts(tag),
        "startup_to_healthy_s": startup_seconds(tag),
        "runtime_uid": run("docker", "run", "--rm", "--entrypoint", "id", tag, "-u"),
        "configured_user": inspect["Config"].get("User") or "root (unset)",
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--label", required=True, help="e.g. baseline / optimized")
    p.add_argument("--dockerfile", default="Dockerfile")
    p.add_argument("--tag", default=None)
    args = p.parse_args()
    tag = args.tag or f"personalized-rewriter:bench-{args.label}"

    result = {
        "label": args.label,
        "tag": tag,
        "measured_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "dockerfile_sha256": hashlib.sha256((ROOT / args.dockerfile).read_bytes()).hexdigest(),
        "code_commit": run("git", "rev-parse", "--short", "HEAD", check=False) or None,
        "docker_version": run("docker", "version", "--format", "{{.Server.Version}}"),
        **measure(tag, args.dockerfile),
    }
    out = ROOT / "docs" / "evidence" / f"docker_{args.label}.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    print(f"\nsaved -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
