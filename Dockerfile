FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY chronos ./chronos
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn chronos.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

