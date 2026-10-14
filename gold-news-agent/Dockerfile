# syntax=docker/dockerfile:1
# Gold News Agent: one image for every service (`gold-agent run <service>`), see docs/runbook.md.
# On the production host pin both base images by digest (`docker buildx imagetools inspect`).

FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM python:3.12-slim-bookworm AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
# Editable install on purpose: config/, prompts/ and data/ are found next to src/ (PROJECT_ROOT).
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm
RUN useradd --uid 10001 --user-group --create-home --shell /usr/sbin/nologin gold
WORKDIR /app
COPY --from=build --chown=gold:gold /app /app
COPY --chown=gold:gold config ./config
COPY --chown=gold:gold prompts ./prompts
RUN install -d -o gold -g gold /app/data /app/docs
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER gold
ENTRYPOINT ["gold-agent"]
CMD ["run", "all"]
