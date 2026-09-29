# Builder stage
FROM python:3.12-slim as builder

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

# Set working directory
WORKDIR /app

# Copy pyproject.toml and uv.lock
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv pip install --system --no-cache-dir -e .

# Copy source code
COPY app/ app/
COPY rules/ rules/
COPY alembic.ini alembic/
COPY alembic/ alembic/

# Runtime stage
FROM python:3.12-slim as runtime

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r scanner && useradd -r -g scanner scanner

# Set working directory
WORKDIR /app

# Copy from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app/ app/
COPY --from=builder /app/rules/ rules/
COPY --from=builder /app/alembic.ini alembic.ini
COPY --from=builder /app/alembic/ alembic/

# Set permissions
RUN chown -R scanner:scanner /app
USER scanner

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Start the application
CMD ["uv", "run", "python", "-m", "app.main"]