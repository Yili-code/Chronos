FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY chronos ./chronos
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["uvicorn", "chronos.main:app", "--host", "0.0.0.0", "--port", "8000"]

