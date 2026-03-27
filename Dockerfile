FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project

# Copy source and install project
COPY README.md src/ ./
COPY src/ src/
RUN uv sync --no-dev

FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

# Copy example config
COPY config/config.example.yaml /app/config/config.example.yaml

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["grafana-agent-langgraph"]
