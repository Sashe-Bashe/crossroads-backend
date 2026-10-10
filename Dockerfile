FROM python:3.12.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY market ./market
COPY alembic ./alembic
COPY alembic.ini ./
RUN useradd --uid 10001 --create-home market
USER market
CMD ["uvicorn", "market.main:app", "--host", "0.0.0.0", "--port", "8100"]
