#!/bin/bash
set -e

# Run database migrations
echo "Running database migrations..."
alembic upgrade head

# Start the application
echo "Starting LLM Security Scanner..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000