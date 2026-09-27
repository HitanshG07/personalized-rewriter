"""OpenRouter access for APPLICATION AI (personalized rewriting). The only module that talks to OpenRouter."""
import os
import time
from dataclasses import dataclass

import httpx

from app import metrics

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRY_DELAY_S = 1.0
TRANSPORT: httpx.BaseTransport | None = None  # tests swap in httpx.MockTransport; None = real network

_V2 = (
    "You are a rewriting assistant. Rewrite the rough notes in the style of the writing sample.\n"
    "- Preserve the meaning of the notes.\n"
    "- Do not invent facts.\n"
    "- Follow the tone of the writing sample; use it only as a style reference.\n"
    "- Return only the rewritten text."
)
PROMPTS = {
    "v1": "Rewrite the notes in the style of the writing sample.",
    "v2": _V2,
    "v3": _V2
    + "\n\nBefore producing the final text, ensure:\n"
    "1. all factual content comes from the notes;\n"
    "2. tone matches the style reference;\n"
    "3. the output is coherent;\n"
    "4. unnecessary repetition is removed.",
}


class LLMError(Exception):
    """reason: not_configured | timeout | rate_limited | auth | upstream_error | invalid_response"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class LLMResult:
    text: str
    model: str
    provider: str | None
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None


def build_messages(sample: str, notes: str, prompt_version: str) -> list[dict]:
    return [
        {"role": "system", "content": PROMPTS[prompt_version]},
        {"role": "user", "content": f"STYLE REFERENCE\n{sample}\n\nCONTENT TO REWRITE\n{notes}"},
    ]


def chat(
    messages: list[dict],
    *,
    model: str,
    temperature: float,
    max_tokens: int,
    fallback_models: list[str] | None = None,
    timeout_s: float = 30.0,
    attempts: int = 2,
    retry_delay_s: float | None = None,
) -> LLMResult:
    """Chat completion with timeout, retries on timeout/429/5xx (linear backoff), and response validation.

    The app uses the defaults (2 attempts, RETRY_DELAY_S) - that is the measured baseline.
    """
    delay = RETRY_DELAY_S if retry_delay_s is None else retry_delay_s
    key = os.getenv("OPENROUTER_API_KEY")
    if not key or not model:
        raise LLMError("not_configured")

    payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if fallback_models:  # disabled during formal evaluation/benchmarking (PROJECT_PLAN §4)
        payload["models"] = [model, *fallback_models]

    reason = "upstream_error"
    with httpx.Client(transport=TRANSPORT, timeout=timeout_s) as client:
        for attempt in range(attempts):
            if attempt:
                time.sleep(delay * attempt)
            metrics.OPENROUTER_REQUESTS.inc()
            start = time.perf_counter()
            try:
                resp = client.post(OPENROUTER_URL, json=payload, headers={"Authorization": f"Bearer {key}"})
            except httpx.TimeoutException:
                reason, retry = "timeout", True
            except httpx.HTTPError:
                reason, retry = "upstream_error", True
            else:
                elapsed = time.perf_counter() - start
                if resp.status_code == 200:
                    result = _parse(resp, elapsed)  # raises LLMError("invalid_response")
                    metrics.LLM_LATENCY.observe(elapsed)
                    return result
                if resp.status_code == 429:
                    reason, retry = "rate_limited", True
                elif resp.status_code in (401, 402, 403):
                    reason, retry = "auth", False
                else:
                    reason, retry = "upstream_error", resp.status_code >= 500
            metrics.OPENROUTER_FAILURES.labels(reason).inc()
            if not retry:
                break
    raise LLMError(reason)


def _parse(resp: httpx.Response, elapsed: float) -> LLMResult:
    try:
        body = resp.json()
        text = body["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        text, body = None, {}
    if not isinstance(text, str) or not text.strip():
        metrics.OPENROUTER_FAILURES.labels("invalid_response").inc()
        raise LLMError("invalid_response")

    usage = body.get("usage") or {}
    for kind in ("prompt", "completion"):
        if isinstance(usage.get(f"{kind}_tokens"), int):
            metrics.OPENROUTER_TOKENS.labels(kind).inc(usage[f"{kind}_tokens"])
    return LLMResult(
        text=text.strip(),
        model=body.get("model", ""),
        provider=body.get("provider"),
        latency_ms=round(elapsed * 1000),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
    )


def rewrite(sample: str, notes: str) -> tuple[LLMResult, str]:
    """Rewrite notes in the sample's style using the runtime configuration. Returns (result, prompt_version)."""
    prompt_version = os.getenv("PROMPT_VERSION", "v1")
    if prompt_version not in PROMPTS:
        raise LLMError("not_configured")
    fallbacks = [m.strip() for m in os.getenv("OPENROUTER_FALLBACK_MODELS", "").split(",") if m.strip()]
    result = chat(
        build_messages(sample, notes, prompt_version),
        model=os.getenv("OPENROUTER_MODEL", ""),
        temperature=float(os.getenv("TEMPERATURE", "0.7")),
        max_tokens=int(os.getenv("MAX_OUTPUT_TOKENS", "400")),
        fallback_models=fallbacks,
        timeout_s=float(os.getenv("OPENROUTER_TIMEOUT_S", "30")),
    )
    return result, prompt_version
