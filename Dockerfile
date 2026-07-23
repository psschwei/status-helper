# syntax=docker/dockerfile:1

# --- Builder: resolve and install dependencies into a self-contained venv ---
# Uses the official uv image so the lockfile (uv.lock) drives a reproducible install.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

# Byte-compile and don't copy the wheel cache into the layer; link mode copy avoids
# hardlink warnings across the bind mounts below.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# 1) Install *only* dependencies first, from the lockfile, so this layer is cached
#    and only busts when uv.lock / pyproject.toml change (not on every source edit).
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# 2) Copy the project source and install the project itself into the venv.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev


# --- Runtime: a slim image with just the venv and source, running as non-root ---
FROM python:3.12-slim-bookworm AS runtime

# Don't buffer stdout/stderr (so logs show up promptly in `kubectl logs`), no .pyc writes.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Create an unprivileged user and the data directory the SQLite DB lives in (a PVC is
# mounted here in Kubernetes; the chown makes it writable when run without a volume too).
RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app app \
    && mkdir -p /data \
    && chown -R app:app /data

WORKDIR /app

# Bring over the built virtualenv and the application source from the builder stage.
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src

USER app

EXPOSE 8000

# host 0.0.0.0 so the container is reachable from outside; no --reload in production.
CMD ["uvicorn", "status_assistant.main:app", "--host", "0.0.0.0", "--port", "8000"]
