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

**Residual finding (not part of the accepted changes):** 2 fixable HIGH remain, in the base image's Python packaging tools (`wheel` CVE-2026-24049 → 0.46.2, `jaraco.context` CVE-2026-23949 → 6.1.0). A candidate for a follow-up AI iteration.

**Conclusion:** the AI's core recommendation (slim base + non-root) was correct and **removes the fixable CRITICAL vulnerabilities that block the Trivy gate**. It also contained three factually wrong claims and an unrequested prediction, all caught by human verification.
