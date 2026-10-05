FROM python:3.13-alpine

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . /app

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT [ "gunicorn", "--no-control-socket", "-b", "0.0.0.0:5000", "app:app" ]
