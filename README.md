# Personalized Rewriter

A small FastAPI service that rewrites rough notes in **your** writing style, using an LLM through OpenRouter. The DevOps/DevSecOps pipeline around it is the real project: GitHub Actions, pytest, Docker, Bandit, Gitleaks, Trivy, Docker Hub, Kubernetes (Minikube), Prometheus and Grafana, plus AI-assisted DevOps analysis.

## Three AI roles

| Role | Tool | Job |
|---|---|---|
| Development AI | Claude | Helps build and debug the project (not part of the system) |
| Application AI | OpenRouter | Rewrites text at runtime (`POST /rewrite`) |
| DevOps AI | OpenRouter via `ai_tools/ai_devops_analyzer.py` | Diagnoses CI failures and reviews the Dockerfile and metrics (advisory only) |

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /` | One-page UI |
| `POST /sample` | Save a writing sample `{user_id, sample}` |
| `POST /rewrite` | Rewrite `{user_id, text}` in the saved style |
| `GET /health` | Liveness/readiness (checks the SQLite DB) |
| `GET /metrics` | Prometheus metrics |
| `GET /docs` | Swagger UI |

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
Copy-Item .env.example .env      # then put your own OPENROUTER_API_KEY / OPENROUTER_MODEL in .env (never commit it)
.venv\Scripts\python -m uvicorn app.main:app --port 8000 --env-file .env
```

## Test

```powershell
.venv\Scripts\python -m pytest -q     # OpenRouter is mocked: 0 real API calls
```

The live OpenRouter check is manual and opt-in: set `RUN_LIVE=1` and run `tests/test_integration_openrouter.py`.

## Security rules

- The API key is supplied only at runtime (`.env` locally, GitHub/Kubernetes Secrets). It never goes into code, images, Git or screenshots.
- CI uploads only **sanitized** logs; raw logs are never uploaded or committed.
- The DevOps AI is advisory: it never runs commands, commits, merges or changes infrastructure.
