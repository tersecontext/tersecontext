FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY entrypoint-discoverer/requirements.txt ./
RUN uv pip install --system -r requirements.txt

COPY shared /app/shared

COPY entrypoint-discoverer/app/ app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
