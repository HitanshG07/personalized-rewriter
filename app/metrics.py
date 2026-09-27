from prometheus_client import Counter, Histogram

# prometheus_client appends "_total" to counter names.
REQUESTS = Counter("requests", "HTTP requests", ["endpoint", "method", "status"])
REQUEST_LATENCY = Histogram("request_latency_seconds", "HTTP request latency", ["endpoint"])
ERRORS = Counter("errors", "HTTP error responses", ["endpoint", "reason"])

REWRITE_REQUESTS = Counter("rewrite_requests", "Rewrite requests received")
REWRITE_FAILURES = Counter("rewrite_failures", "Rewrite requests that failed", ["reason"])

OPENROUTER_REQUESTS = Counter("openrouter_requests", "HTTP attempts sent to OpenRouter")
OPENROUTER_FAILURES = Counter("openrouter_failures", "Failed OpenRouter attempts", ["reason"])
LLM_LATENCY = Histogram(
    "llm_latency_seconds",
    "OpenRouter round-trip time for successful calls",
    buckets=(0.5, 1, 2, 3, 5, 8, 13, 20, 30, 60),
)
OPENROUTER_TOKENS = Counter("openrouter_tokens", "Tokens reported by OpenRouter", ["type"])
