# Paperless → Firefly III Finance Pipeline
# Multi-stage build for minimal production image

FROM python:3.12-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY pyproject.toml .
COPY README.md .
COPY src/ ./src/

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Production image
FROM python:3.12-slim as production

LABEL maintainer="Paperless-Firefly Pipeline" \
      description="Deterministic finance document extraction pipeline" \
      version="0.1.0"

# Create non-root user
RUN groupadd -r paperless && useradd -r -g paperless paperless

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY src/ ./src/
COPY docker/entrypoint.sh /entrypoint.sh

# Create directories for data persistence
RUN mkdir -p /app/data /app/config && \
    chown -R paperless:paperless /app

# Make entrypoint executable
RUN chmod +x /entrypoint.sh

# Environment variables with sensible defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STATE_DB_PATH=/app/data/state.db \
    CONFIG_PATH=/app/config/config.yaml \
    DJANGO_SETTINGS_MODULE=paperless_firefly.review.web.settings \
    # Server settings
    HOST=0.0.0.0 \
    PORT=8080

# Expose the web interface port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:${PORT}/', timeout=5)" || exit 1

# Switch to non-root user
USER paperless

# Volume for persistent data
VOLUME ["/app/data", "/app/config"]

ENTRYPOINT ["/entrypoint.sh"]
CMD ["server"]
