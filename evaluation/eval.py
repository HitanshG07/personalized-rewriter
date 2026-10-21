"""APPLICATION AI evaluation (PROJECT_PLAN §4): fixed inputs, saved results, blind human scoring.

  python evaluation/eval.py models                                   which candidate models are available today
  python evaluation/eval.py run --label prompt_v1 --model M --prompt v1 [--round 1]
                                                                     real calls; resumable; pinned model, no fallback
  python evaluation/eval.py copy --src prompt_v2 --dst model_A       reuse a round-1 run in round 2 (no calls)
  python evaluation/eval.py score --round 1                          blind 1-5 scoring (Candidate 1/2/3, no labels shown)
  python evaluation/eval.py summary                                  evaluation_summary.csv from saved files (no calls)

Round 1 = prompt_v1, prompt_v2, prompt_v3 on model A.  Round 2 = model_A, model_B, model_C with the best prompt.
"""
import argparse
import csv
import datetime
import hashlib
import json
import os
import random
import subprocess  # fixed git argv only
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

INPUTS = HERE / "inputs.json"
RESULTS = HERE / "results"
META = HERE / "evaluation_metadata.json"
SUMMARY = HERE / "evaluation_summary.csv"
ROUNDS = {1: ["prompt_v1", "prompt_v2", "prompt_v3"], 2: ["model_A", "model_B", "model_C"]}
CANDIDATE_MODELS = {"A": "minimax/minimax-m3.1-flash:free", "B": "google/gemma-4-31b-it:free",
                    "C": "nvidia/nemotron-3.5-lightning:free"}
TEMPERATURE, MAX_TOKENS = 0.7, 1000  # same as the deployed app (k8s/app-config.env)
SCORE_KEYS = ("quality", "meaning", "tone")


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)  # atomic: an interrupted run never corrupts saved results


def inputs() -> list[dict]:
    return load(INPUTS, [])


def results_path(label: str) -> Path:
    return RESULTS / f"{label}_results.json"


# ------------------------------------------------------------------ run
def run(label: str, model: str, prompt: str, round_no: int, delay: float, chat=None, sleep=time.sleep) -> int:
    from app.llm_client import LLMError, build_messages
    if chat is None:
        from app.llm_client import chat
    items = inputs()
    out = load(results_path(label), {})
    meta = load(META, {})
    m = meta.setdefault(label, {})
    m.update({"run_id": label, "round": round_no, "model_id": model, "prompt_version": prompt,
              "temperature": TEMPERATURE, "max_output_tokens": MAX_TOKENS, "fallback_models": [],
              "n_samples": len(items), "inputs_sha256": hashlib.sha256(INPUTS.read_bytes()).hexdigest(),
              "code_commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()})
    m.setdefault("evaluation_started", now())
    save(META, meta)

    stopped = False
    for it in items:
        sid = it["sample_id"]
        if sid in out:
            continue  # resumable: never re-call a saved item
        try:
            r = chat(build_messages(it["writing_sample"], it["rough_notes"], prompt), model=model,
                     temperature=TEMPERATURE, max_tokens=MAX_TOKENS, fallback_models=None, attempts=1, timeout_s=90)
        except LLMError as e:
            print(f"  {sid}: {e.reason}")
            if e.reason in ("rate_limited", "auth", "not_configured"):
                stopped = True
                break  # quota or setup problem: stop now, resume later
            sleep(delay)
            continue
        out[sid] = {"sample_id": sid, "label": label, "prompt_version": prompt, "model_requested": model,
                    "model_returned": r.model, "provider": r.provider, "model_mismatch": bool(r.model) and r.model != model,
                    "output": r.text, "latency_ms": r.latency_ms, "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens, "created_at": now()}
        save(results_path(label), out)  # saved the moment it returns
        print(f"  {sid}: ok {r.latency_ms} ms")
        sleep(delay)

    m.update({"evaluation_finished": now() if len(out) == len(items) else None,
              "calls_ok": len(out), "model_mismatches": sum(v["model_mismatch"] for v in out.values()),
              "model_version": sorted({v["model_returned"] for v in out.values()} or [None])})
    save(META, meta)
    print(f"{label}: {len(out)}/{len(items)} saved" + ("  (stopped: rate limit/quota - run again later to resume)" if stopped else ""))
    return 2 if stopped else 0


def copy(src: str, dst: str) -> None:
    data = {sid: {**v, "label": dst} for sid, v in load(results_path(src), {}).items()}
    save(results_path(dst), data)
    meta = load(META, {})
    meta[dst] = {**meta.get(src, {}), "run_id": dst, "round": 2, "copied_from": src}
    save(META, meta)
    print(f"{dst}: {len(data)} results reused from {src} (no API calls)")


# ------------------------------------------------------------------ blind scoring
def blind_order(seed: int, sample_id: str, labels: list[str]) -> list[str]:
    order = list(labels)
    random.Random(f"{seed}:{sample_id}").shuffle(order)
    return order


def score(round_no: int, ask=input, show=print) -> None:
    labels = ROUNDS[round_no]
    runs = {lb: load(results_path(lb), {}) for lb in labels}
    map_path, score_path = HERE / f".blind_map_round{round_no}.json", HERE / f"scores_round{round_no}.json"
    bmap = load(map_path, {"seed": random.SystemRandom().randrange(10**9), "order": {}})
    scores = load(score_path, {})
    meta = load(META, {})
    for lb in labels:
        meta.setdefault(lb, {})["shuffle_seed"] = bmap["seed"]
    save(META, meta)
    todo = [it for it in inputs() if all(it["sample_id"] in runs[lb] for lb in labels)
            and not all(f"{lb}|{it['sample_id']}" in scores for lb in labels)]
    show(f"Round {round_no}: {len(todo)} samples to score. For each candidate type three numbers 1-5: quality meaning tone (e.g. 4 5 3). 'q' to stop.")
    for it in todo:
        sid = it["sample_id"]
        order = bmap["order"].setdefault(sid, blind_order(bmap["seed"], sid, labels))
        save(map_path, bmap)
        show("\n" + "=" * 70 + f"\nSAMPLE {sid}\n--- Writing sample (style to copy):\n{it['writing_sample']}\n--- Rough notes:\n{it['rough_notes']}")
        for n, lb in enumerate(order, 1):
            key = f"{lb}|{sid}"
            if key in scores:
                continue
            show(f"\n--- Candidate {n}:\n{runs[lb][sid]['output']}")
            while True:
                raw = ask(f"Candidate {n} scores (quality meaning tone): ").strip().lower()
                if raw == "q":
                    show("Saved. Run the same command again to continue.")
                    return
                parts = raw.split()
                if len(parts) == 3 and all(p in "12345" and p for p in parts):
                    scores[key] = dict(zip(SCORE_KEYS, map(int, parts)))
                    save(score_path, scores)
                    break
                show("Please type three numbers from 1 to 5, e.g. 4 5 3")
    show("Round complete.")


# ------------------------------------------------------------------ summary
def summarize() -> list[dict]:
    rows = []
    for rnd, labels in ROUNDS.items():
        scores = load(HERE / f"scores_round{rnd}.json", {})
        meta = load(META, {})
        for lb in labels:
            res = load(results_path(lb), {})
            if not res:
                continue
            sc = [scores[k] for k in (f"{lb}|{sid}" for sid in res) if k in scores]
            avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None  # noqa: E731
            row = {"round": rnd, "run": lb, "model": meta.get(lb, {}).get("model_id"), "prompt": meta.get(lb, {}).get("prompt_version"),
                   "n_results": len(res), "n_scored": len(sc),
                   **{f"avg_{k}": avg([s[k] for s in sc]) for k in SCORE_KEYS},
                   "avg_overall": avg([sum(s.values()) / 3 for s in sc]),
                   "avg_latency_ms": avg([v["latency_ms"] for v in res.values()]),
                   "avg_completion_tokens": avg([v["completion_tokens"] for v in res.values() if v.get("completion_tokens") is not None]),
                   "avg_output_chars": avg([len(v["output"]) for v in res.values()]),
                   "model_mismatches": sum(v.get("model_mismatch", False) for v in res.values())}
            rows.append(row)
    return rows


def summary() -> None:
    rows = summarize()
    if not rows:
        print("No results yet.")
        return
    with SUMMARY.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(f"{'rnd':>3} {'run':10} {'prompt':6} {'n':>3} {'scored':>6} {'qual':>5} {'mean':>5} {'tone':>5} {'overall':>7} {'lat ms':>7} {'chars':>6}  model")
    for r in rows:
        f = lambda v: "-" if v is None else v  # noqa: E731
        print(f"{r['round']:>3} {r['run']:10} {str(r['prompt']):6} {r['n_results']:>3} {r['n_scored']:>6} {f(r['avg_quality']):>5} "
              f"{f(r['avg_meaning']):>5} {f(r['avg_tone']):>5} {f(r['avg_overall']):>7} {f(r['avg_latency_ms']):>7} {f(r['avg_output_chars']):>6}  {r['model']}")
    print(f"\nsaved -> {SUMMARY.relative_to(ROOT)}")


def models() -> None:
    import httpx
    ids = {m["id"] for m in httpx.get("https://openrouter.ai/api/v1/models", timeout=30).json()["data"]}
    for k, m in CANDIDATE_MODELS.items():
        print(f"model {k}: {m:45} {'available' if m in ids else 'NOT AVAILABLE'}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("models")
    r = sub.add_parser("run")
    r.add_argument("--label", required=True); r.add_argument("--model", required=True)
    r.add_argument("--prompt", required=True, choices=["v1", "v2", "v3"])
    r.add_argument("--round", type=int, default=1, choices=[1, 2]); r.add_argument("--delay", type=float, default=3.0)
    c = sub.add_parser("copy"); c.add_argument("--src", required=True); c.add_argument("--dst", required=True)
    s = sub.add_parser("score"); s.add_argument("--round", type=int, required=True, choices=[1, 2])
    sub.add_parser("summary")
    a = p.parse_args(argv)
    if a.cmd == "run":
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        if not os.getenv("OPENROUTER_API_KEY"):
            print("OPENROUTER_API_KEY missing in .env"); return 1
        return run(a.label, a.model, a.prompt, a.round, a.delay)
    {"models": models, "summary": summary}.get(a.cmd, lambda: None)()
    if a.cmd == "copy":
        copy(a.src, a.dst)
    if a.cmd == "score":
        score(a.round)
    return 0


if __name__ == "__main__":
    sys.exit(main())
