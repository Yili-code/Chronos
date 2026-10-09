FROM python:3.12-slim

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.12.3 \
    && uv sync --locked --no-dev --no-install-project
COPY chronos ./chronos
RUN uv sync --locked --no-dev

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn chronos.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

