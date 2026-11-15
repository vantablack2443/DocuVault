#!/bin/sh
# Start command for hosts without a separate migration step (Render free plan).
# `alembic upgrade head` does nothing when the schema is already current.
set -e
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
