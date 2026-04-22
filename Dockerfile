# ── Stage 1: dependency builder ───────────────────────────────────────────────
# Install dependencies in an isolated layer so the final image only copies
# the compiled wheels, keeping the image lean and reproducible.
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build tools needed for asyncpg/cryptography compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir /build/wheels -r requirements.txt


# ── Stage 2: runtime image ─────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Security: run as non-root user (OWASP A05)
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --shell /bin/sh --create-home appuser

WORKDIR /app

# Copy pre-built wheels from builder stage and install (no compiler needed)
COPY --from=builder /build/wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/* \
    && rm -rf /wheels

# Copy application source
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini .

# Ownership to non-root user
RUN chown -R appuser:appgroup /app

USER appuser

# Health check — GKE liveness probe equivalent for local docker usage
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

EXPOSE 8000

# Use exec form to ensure SIGTERM reaches uvicorn (not a shell wrapper)
CMD ["uvicorn", "src.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
