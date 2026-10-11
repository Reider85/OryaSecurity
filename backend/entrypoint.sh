#!/bin/bash
set -e

# Activate virtual environment
source .venv/bin/activate

# Run database migrations
echo "Running database migrations..."
alembic upgrade head

# Start the application
echo "Starting LLM Security Scanner..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000