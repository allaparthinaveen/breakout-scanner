# Multi-Arch Production Dockerfile for Breakout Scanner Cloud Daemon
# Supports linux/arm64 (AWS Graviton t4g.small) and linux/amd64 (x86_64)

FROM python:3.11-slim as base

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC

WORKDIR /app

# Install system dependencies (build-essential, curl, tzdata)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Create application non-root user
RUN useradd -m -u 1000 appuser && \
    mkdir -p /app/data /app/logs && \
    chown -R appuser:appuser /app

# Copy application codebase
COPY --chown=appuser:appuser . /app

USER appuser

# Health check using heartbeat status
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python3 production_daemon.py --status || exit 1

# Default cloud daemon execution across all markets
ENTRYPOINT ["python3", "production_daemon.py"]
CMD ["--startup-scan"]
