import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import llm_client, metrics, storage
from app.schemas import RewriteIn, RewriteOut, SampleIn, SampleOut

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("rewriter")

INDEX_HTML = Path(__file__).parent / "static" / "index.html"

# reason -> (HTTP status, user-facing message). Never echo upstream bodies or keys.
LLM_ERRORS = {
    "not_configured": (503, "The AI service is not configured."),
    "timeout": (504, "The AI service timed out. Please try again."),
    "rate_limited": (502, "The AI service is busy right now. Please try again later."),
    "auth": (502, "The AI service rejected the request."),
    "upstream_error": (502, "The AI service failed to produce a rewrite."),
    "invalid_response": (502, "The AI service returned an invalid response."),
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    storage.init_db()
    yield


app = FastAPI(title="Personalized Rewriter", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def track_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    endpoint = route.path if route else "unmatched"  # route template keeps label cardinality bounded
    metrics.REQUESTS.labels(endpoint, request.method, str(response.status_code)).inc()
    metrics.REQUEST_LATENCY.labels(endpoint).observe(time.perf_counter() - start)
    if response.status_code >= 400:
        reason = getattr(request.state, "error_reason", str(response.status_code))
        metrics.ERRORS.labels(endpoint, reason).inc()
    return response


@app.exception_handler(RequestValidationError)
async def on_validation_error(request: Request, exc: RequestValidationError):
    request.state.error_reason = "validation"
    return await request_validation_exception_handler(request, exc)


def _fail(request: Request, reason: str, status: int, message: str) -> HTTPException:
    request.state.error_reason = reason
    metrics.REWRITE_FAILURES.labels(reason).inc()
    return HTTPException(status_code=status, detail=message)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(INDEX_HTML)


@app.post("/sample", status_code=201, response_model=SampleOut)
def post_sample(body: SampleIn):
    storage.save_sample(body.user_id, body.sample)
    return SampleOut(user_id=body.user_id, stored=True, chars=len(body.sample))


@app.post("/rewrite", response_model=RewriteOut)
def post_rewrite(body: RewriteIn, request: Request):
    metrics.REWRITE_REQUESTS.inc()
    sample = storage.get_sample(body.user_id)
    if sample is None:  # no OpenRouter call without a style reference
        raise _fail(request, "no_sample", 404, "Please submit a writing sample first.")
    try:
        result, prompt_version = llm_client.rewrite(sample, body.text)
    except llm_client.LLMError as e:
        log.warning("rewrite failed user_id=%s reason=%s", body.user_id, e.reason)
        raise _fail(request, e.reason, *LLM_ERRORS[e.reason])
    return RewriteOut(
        rewrite=result.text, model=result.model, prompt_version=prompt_version, latency_ms=result.latency_ms
    )


@app.get("/health")
def health(request: Request):
    try:
        storage.journal_mode()
    except Exception:
        log.exception("health check: database unreachable")
        request.state.error_reason = "db_unreachable"
        raise HTTPException(status_code=503, detail="Database unreachable.")
    return {"status": "ok"}


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
