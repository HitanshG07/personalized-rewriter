# Stage 1 - builder: install the Python dependencies into /install (nothing else from this stage ships).
FROM python:3.11-slim-trixie AS builder
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2 - runtime: slim Debian 13 base + installed dependencies + app code, running as a non-root user.
FROM python:3.11-slim-trixie
RUN useradd --uid 10001 --no-create-home appuser \
    && mkdir -p /app/data && chown 10001 /app/data
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app/ app/

# Numeric UID so Kubernetes runAsNonRoot can verify it.
USER 10001
EXPOSE 8000

# For `docker run`; Kubernetes ignores this and uses its own liveness/readiness probes on /health.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
