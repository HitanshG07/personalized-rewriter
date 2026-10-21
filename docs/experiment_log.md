# AI Experiment Log

One entry per AI experiment (PROJECT_PLAN §9.4 / §10). All numbers are measured; nothing is estimated.
Evidence files hold **sanitized** input only.

---

## EXP-01 · DEVOPS AI · CI failure diagnosis on a real security-gate failure

| Field | Value |
|---|---|
| Date | 2026-09-27 |
| Purpose | DEVOPS AI: diagnose why the pipeline failed (PR #8, P5 security gates) |
| Input artifact | Sanitized logs of the failed `build-scan` job + job results (`test`/`source-security` = success, `build-scan` = failure) |
| Sanitized | Yes (0 redactions needed) |
| Prompt version | `ci-v1` |
| Model requested → returned | `google/gemma-4-31b-it:free` → `nvidia/nemotron-3-super-120b-a12b:free` (OpenRouter fallback; Gemma throttled) |
| Evidence | `docs/evidence/ai/ci_trivy-gate_run36317865049.json`, PR #8 comment |

**AI output (structured):** failed stage **security**; cause: CRITICAL CVEs in base-OS package `libunbound8` found by Trivy; affected config: Dockerfile base image; fix: update or minimise the base image; confidence **high**; stated limitation: base image not visible in the logs. Latency **8975 ms**.

**Human verification:** correct stage and cause. The Trivy gate log shows `libunbound8` CVE-2026-50252 / -81642 / -82717 (fixed in 1.26.1-0+deb13u1). **AI error found:** it suggested `python:3.11-slim-bookworm` (Debian 12), but the image is Debian 13 (trixie), so a human had to correct the tag.

**Troubleshooting history (making the DevOps AI reliable on the free tier):**

| Run | Result | Root cause | Fix |
|---|---|---|---|
| 36317392344 | "AI diagnosis unavailable (rate_limited)" | Free Gemma throttled upstream (429 twice) | 3 attempts with backoff + free-model fallback list for the analyzer only |
| 36317526182 | Unstructured text, no JSON | Fallback model Nemotron spent all 1200 output tokens on reasoning | JSON-only instruction, 3000-token budget, robust JSON extraction |
| 36317727138 | `invalid_response` (empty content) | Reasoning consumed the budget; content empty | A direct check showed Nemotron returns clean JSON with `reasoning.enabled=false` → set on analyzer calls |
| 36317865049 | ✅ Structured diagnosis posted to PR | — | — |

The app's own OpenRouter behaviour (2 attempts, 1 s, no fallback) was **not** changed: it is the measured baseline.

---

## EXP-02 · DEVOPS AI · Deliverable B: AI Dockerfile / image optimization

| Field | Value |
|---|---|
| Date | 2026-09-27 |
| Purpose | DEVOPS AI: review the baseline Dockerfile and recommend measurable improvements |
| Input artifact | `Dockerfile`, `requirements.txt`, `.dockerignore`, repo file list, `docs/evidence/docker_baseline.json` |
| Sanitized | Yes |
| Prompt version | `docker-v1` |
| Model returned | `nvidia/nemotron-3-super-120b-a12b:free` (latency 6277 ms) |
| Evidence | `docs/evidence/ai/docker_20260927-173827.json` |

### AI recommendations → human review

| AI claim / recommendation | Human verification | Decision |
|---|---|---|
| Full `python:3.11` base has unnecessary packages | ✅ True: 469 packages; the 3 CRITICAL CVEs are in `libunbound8` (only needed by `libgnutls-dane0t64`, unused by the app) | — |
| Use a slim base | Verified before applying: `python:3.11-slim-trixie` = Debian 13, Python 3.11.16, 87 packages, **no `libunbound8`**, bare base has **0 fixable CRITICAL** | **Accepted** (pinned `-slim-trixie`) |
| Run as non-root | ✅ Baseline runs as uid 0 | **Accepted** (`USER 10001`, numeric for K8s `runAsNonRoot`) |
| Multi-stage build + no pip cache | Reasonable; the gain over slim alone is expected to be small, so it was measured rather than assumed | **Accepted** (`pip --prefix` builder; venv avoided because Python 3.11 venv bundles an old setuptools) |
| Add HEALTHCHECK; widen `.dockerignore` | ✅ No HEALTHCHECK; context included docs/tests/tools | **Accepted** (urllib-based check; K8s keeps its own probes) |
| "Unnecessary copying of entire repository" | ❌ **False**: the Dockerfile only copies `requirements.txt` and `app/` | Rejected (AI error) |
| "Includes development dependencies" | ❌ **False**: dev tools are only in `requirements-dev.txt` | Rejected (AI error) |
| "No vulnerability scanning in pipeline" | ❌ **False**: Trivy runs in CI; the AI was not shown the workflow | Rejected (context limitation) |
| "Expected 70–90% size decrease" | ⚠️ The AI broke the "no predicted percentages" instruction | Ignored; only measured numbers reported |
| Distroless base | No shell for debugging; its Python version doesn't match 3.11 | Rejected for now |

### Measured before → after (same script `ai_tools/measure_image.py`, same machine, base images pre-pulled, one run each)

| Metric | Baseline | AI-optimized | Change |
|---|---:|---:|---:|
| Image size | 1651.5 MB | **213.6 MB** | **−1437.9 MB (−87.1%)** |
| Cold build (`--no-cache`) | 6.56 s | 5.90 s | −0.66 s (−10.1%) |
| Warm build (no change) | 0.79 s | 0.72 s | −0.07 s (−8.9%) |
| Rebuild after code change | 0.94 s | 0.85 s | −0.09 s (−9.6%) |
| Filesystem layers | 11 | 8 | −3 |
| History steps | 19 | 18 | −1 |
| Trivy CRITICAL (fixable / total) | 3 / 19 | **0 / 0** | −3 / −19 |
| Trivy HIGH (fixable / total) | 10 / 326 | 2 / 46 | −8 / −280 |
| Trivy MEDIUM (fixable / total) | 19 / 1786 | 6 / 59 | −13 / −1727 |
| Trivy LOW (fixable / total) | 11 / 1314 | 1 / 58 | −10 / −1256 |
| Startup to healthy `/health` | 0.75 s | 0.96 s | **+0.21 s (slower)** |
| Runtime user | root (uid 0) | **uid 10001 (appuser)** | non-root |
| Docker HEALTHCHECK | none | `healthy` | added |

**Behaviour check (optimized image):** `/health` 200, `/sample` 201 (written by uid 10001 to `/app/data`), missing sample 404, empty input 422, no key 503, `/` and `/metrics` 200.

**Limitations of this measurement:**
- Build and startup times are from **one run each** on a laptop; differences under ~1 s are within normal run-to-run variation and should not be over-claimed.
- Startup got slightly slower (+0.21 s), recorded as measured.
- Size, layers, vulnerabilities and runtime user are deterministic, and those are the main result.

### CI pipeline effect (GitHub Actions, PR #8, same workflow, one run each)

| `build-scan` job | Red run 36317865049 (baseline image) | Green run 36330450751 (AI-optimized) | Change |
|---|---:|---:|---:|
| Docker build step | 23 s | 10 s | −13 s |
| Trivy report step | 16 s | 10 s | −6 s |
| **Whole `build-scan` job** | **48 s** | **28 s** | **−20 s (−41.7%)** |
| Trivy gate | ❌ failed (3 fixable CRITICAL) | ✅ passed | release unblocked |
| `ai-diagnose` | ran (24 s, 1 AI call) | skipped | 0 AI calls on a green run |

GitHub-hosted runners start clean, so every run pulls the base image. The smaller image therefore shortens the pipeline directly, unlike the laptop benchmark where the base image was already cached. These are single runs on shared runners, so expect some run-to-run variation.

**Residual finding (not part of the accepted changes):** 2 fixable HIGH remain, in the base image's Python packaging tools (`wheel` CVE-2026-24049 → 0.46.2, `jaraco.context` CVE-2026-23949 → 6.1.0). A candidate for a follow-up AI iteration.

**Conclusion:** the AI's core recommendation (slim base + non-root) was correct and **removes the fixable CRITICAL vulnerabilities that block the Trivy gate**. It also contained three factually wrong claims and an unrequested prediction, all caught by human verification.

---

## EXP-03 · DEVOPS AI · Deliverable A: blind CI failure diagnosis (manual vs AI)

| Field | Value |
|---|---|
| Date | 2026-09-27 |
| Purpose | DEVOPS AI: does AI reduce the effort to find the root cause of a failed pipeline? |
| Setup | A realistic validation bug was introduced on branch `demo/run-a` (PR #9) **without revealing it to the developer** (PROJECT_PLAN D8) |
| Failed runs | 36330985633 (#14) and 36330985512 (#13), the same commit, triggered twice by a simultaneous force-push of `main` and the PR branch (`test` failed: 3 failed, 36 passed; later stages skipped) |
| Input to AI | Sanitized `test` log + job results |
| Prompt version | `ci-v1` |
| Model returned | `nvidia/nemotron-3-super-120b-a12b:free` · 2049 prompt / 511 completion tokens |

**Ground truth (revealed after both diagnoses):** `app/schemas.py` line 20, `NotesText` `min_length` changed from `1` to `0`, so empty or whitespace-only notes passed validation. `/rewrite` returned 200 instead of 422 and **called OpenRouter with an empty request**.

| | Stage | Root cause | File / line | Time |
|---|---|---|---|---|
| Developer (manual, raw log only) | ✅ test | ❌ not identified | ❌ not identified | stopped after ~2–3 min without identifying the cause or file |
| DevOps AI, run #14 | ✅ test | ✅ "validation for empty/whitespace notes missing; returns 200 instead of 422" | ⚠️ `tests/test_validation.py:9`: where the failure surfaced, not the source file | **3965 ms** |
| DevOps AI, run #13 (independent repeat) | ✅ test | ✅ same cause ("not implemented in the endpoint … 200 instead of 422") | ⚠️ same `tests/test_validation.py:9` | **5720 ms** |

**Consistency:** two independent AI runs on the same failure gave the same stage, cause and file (latencies 3965 ms and 5720 ms). The duplicate run cost one extra free call.

**AI limitation (stated by the AI itself):** "the exact location of the validation logic (e.g., in a Pydantic model …) must be inferred". It pointed to the failing test, not the defective source file.

**Human verification:** following the AI's hint (validation in a Pydantic model), the developer located `NotesText` in `app/schemas.py` (`min_length=0`) and restored `min_length=1`. Verified with the local suite (39 passed), then CI run **36331580056: all jobs green, `ai-diagnose` skipped** (0 AI calls on a green run).

**Extra finding surfaced by the AI's evidence:** its log excerpt shows the empty request reaching the (mocked) OpenRouter call. In production this bug would have **wasted real AI quota on empty input**, which the unit tests catch.

**Conclusion:** the AI correctly identified the stage and the root cause in ~4–6 s (vs ~2–3 min manual without a result), where the manual read of the raw log stopped at the stage. It narrowed the search but mislocated the file, so human verification was still required to find and fix the defect. The AI stays advisory.

**Pipeline bug found during this experiment:** the first attempt (run 36330681093) crashed the `ai-diagnose` job (exit code 2). With a single failed job, `download-artifact` extracts the log straight into the target folder, so the per-job lookup found nothing. It was fixed in the workflow and the blind run was repeated with the same planted bug.

---

## EXP-04 · APPLICATION AI · Prompt and model evaluation (blind, 2 rounds)

| Field | Value |
|---|---|
| Date | 2026-10-22 |
| Purpose | Pick the prompt and model the app ships with, using measured quality instead of guessing |
| Inputs | `evaluation/inputs.json`: 20 writing samples + rough notes (email, academic, LinkedIn, apology, recipe, invitation, …) |
| Tool | `evaluation/eval.py` (`run` → `score` → `summary`): pinned model, no fallback, resumable, blind scoring |
| Settings | temperature 0.7, max output tokens 1000, Google AI Studio free tier |
| Scoring | 1–5 for quality, meaning (no invented facts) and tone; candidates shown as Candidate 1/2/3 in a seeded shuffle, model and prompt hidden |
| Scorer | **AI judge**, blind, from the shuffled sheet without the label map. This is a developer-approved deviation from human scoring (PROJECT_PLAN D7), recorded in `evaluation_metadata.json` |
| Evidence | `evaluation/results/*.json`, `scores_round1.json`, `scores_round2.json`, `evaluation_summary.csv` |

**Model changes from the plan:** the planned OpenRouter models were dropped. `qwen3.8` was removed from OpenRouter, minimax requires reasoning, and the free quota (50 calls/day) was too small. All runs moved to Google AI Studio's free tier. Round 1 on minimax (`minimax_prompt_v1/v2_results.json`) was abandoned and is not scored.

**Round 1: prompt choice** (Gemini 3.5 Flash-Lite, 20 samples each)

| Prompt | Quality | Meaning | Tone | Overall | Avg latency |
|---|---:|---:|---:|---:|---:|
| V1 | 4.50 | 4.05 | 4.70 | 4.42 | 881 ms |
| V2 | 4.80 | 4.50 | 4.55 | 4.62 | 902 ms |
| **V3** | 4.55 | **4.85** | 4.60 | **4.67** | 1095 ms |

V3 wins, mainly on meaning: it invents the fewest facts.

**Round 2: model choice** (prompt V3, 17 samples present for all three models)

| Model | Saved | Quality | Meaning | Tone | Overall | Avg latency |
|---|---:|---:|---:|---:|---:|---:|
| **A: gemini-3.5-flash-lite** | 20/20 | 4.41 | 4.82 | 4.65 | **4.63** | 1.1 s |
| B: gemma-4-31b-it | 19/20 | 2.00 | 5.00 | 4.00 | 3.67 | 39.5 s |
| C: gemini-3.1-flash-lite | 18/20 | 4.41 | 4.06 | 4.65 | 4.37 | 20.9 s |

B and C have partial counts because Google returned repeated "high demand" (503) errors. Only the 17 samples that all three models completed were scored. B's meaning was perfect, but every output leaked its `<thought>…</thought>` reasoning into the text, so its quality score is low and it is not usable as-is. C invented details more often (surnames, recipe steps, enzyme names).

**Decision:** ship **Gemini 3.5 Flash-Lite + prompt V3**. It is pinned in `k8s/app-config.env` and running live (image `e3ed1560`, rewrites ~0.9 s).

**Limitations:** a single AI scorer, not human; 17–20 samples; free-tier latency varies by time of day.

## EXP-05 · DEVOPS AI · Experiment 3: monitoring-driven optimization (Prometheus → AI → verified change)

| Field | Value |
|---|---|
| Date | 2026-10-22 |
| Purpose | Use live Prometheus metrics + AI analysis to find and fix a real runtime problem |
| Setup | App deployed on Docker Desktop Kubernetes, namespace `rewriter-ia2`, 3 replicas, image `hitansh07/personalized-rewriter:e3ed1560` (Gemini 3.5 Flash-Lite, prompt V3, Google AI Studio free tier) |
| Traffic | `.\scripts\demo.ps1 traffic -Requests 20 -Rewrites 15` (15 real rewrites, all succeeded, 641–987 ms each) |
| Input to AI | Prometheus query-API snapshot `docs/evidence/ai/prometheus_snapshot_20261022-015646.json` (sanitized) |
| Prompt version | `metrics-v1` |
| Model | gemini-3.5-flash-lite via Google AI Studio (1837 ms) |
| Evidence | `docs/evidence/ai/metrics_20261022-020016.json` |

**First attempt via OpenRouter:** returned `invalid_response` (free OpenRouter quota nearly exhausted). A second attempt on Gemini returned Python-style single-quoted JSON because the sanitizer converts double quotes to single quotes. Fixed by adding "Keys and string delimiters must be standard JSON double quotes" to the prompt. The unstructured evidence file from the first attempt is `metrics_20261022-020001.json`.

**AI observations:** one pod received no traffic in the window; `/rewrite` p95 latency ~0.98 s closely tracking LLM p95 ~0.975 s, so the external AI call dominates request latency. AI likely cause: uneven load balancing due to session affinity. AI recommended change: switch the Service from session affinity to round-robin.

**Human verification:** `kubectl get svc rewriter -o jsonpath={.spec.sessionAffinity}` = `None` → the AI's cause was **wrong**; its recommended change would do nothing. Per-pod Prometheus counts confirmed the symptom was real: in 30 minutes one pod handled 12 app requests and the other two 0. Real cause identified by the developer: the traffic client reused one HTTP keep-alive connection, and Kubernetes Services balance per TCP connection, not per request.

**Change applied:** the traffic client opens a new connection per request (`-DisableKeepAlive` in `scripts/demo.ps1 traffic`). Same traffic re-run.

**Results:** App requests per pod

| Metric | Before | After |
|---|---:|---:|
| Pod 1 requests | 12 | 14 |
| Pod 2 requests | 0 | 8 |
| Pod 3 requests | 0 | 13 |
| Rewrite latency | ~0.8–1.1 s (range) | ~0.8–1.1 s (range, one outlier 1.8 s) |
| Rewrites succeeded | 15 / 15 | 15 / 15 |

**Conclusion:** the AI correctly spotted the symptom (an idle replica) and that external LLM latency dominates request time, but proposed a wrong cause; human verification against the live cluster found the real cause, and the measured change spread load across all 3 replicas. Limitation: small sample (35 requests per run), single laptop cluster; a production fix would also include a load-testing tool that opens many connections.
