FROM python:3.12-slim

WORKDIR /app

# System deps for common ML/gRPC packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency management
RUN pip install --no-cache-dir uv

# Dependencies installed per-service by copying pyproject.toml first
COPY pyproject.toml .
RUN uv pip install --system -r pyproject.toml 2>/dev/null || true

COPY . .

CMD ["python", "-m", "service"]
