# Dockerfile for the GitHub Bisect Bot server
# This image runs the FastAPI server and executes bisect operations directly

FROM python:3.12-slim-bookworm AS base

# SECURITY: Set environment variables for Python
# DEBIAN_FRONTEND=noninteractive prevents apt from prompting for input
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install system dependencies including build tools for bisect operations
# These are needed because we run bisect directly in this container
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Install Node.js (common for JavaScript projects)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install dev dependencies (pytest) for running tests
RUN pip install --no-cache-dir pytest pytest-asyncio pytest-cov black ruff

# Copy application code
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY tests/ ./tests/
COPY pyproject.toml ./

# SECURITY: Create a non-root user with specific UID/GID
RUN groupadd -r -g 1000 botuser && \
    useradd -r -u 1000 -g botuser -d /app -s /sbin/nologin botuser && \
    chown -R botuser:botuser /app

# SECURITY: Create secrets directory with proper permissions
RUN mkdir -p /app/secrets && chown botuser:botuser /app/secrets

# Create workspace directory for bisect operations (writable by botuser)
RUN mkdir -p /tmp/bisect-workspace && chown botuser:botuser /tmp/bisect-workspace

# SECURITY: Switch to non-root user
USER botuser:botuser

# Expose the default port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health', timeout=5).raise_for_status()" || exit 1

# Run the application
# Use exec form to ensure signals are properly forwarded
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
