FROM python:3.14-alpine

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

RUN apk add --no-cache g++ gcc libxslt-dev musl-dev linux-headers python3-dev

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . /app

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT [ "opentelemetry-instrument", "gunicorn", "-b", "0.0.0.0:5000", "app:app" ]
