FROM python:3.12-slim AS base

# Install Node.js (for pyright + typescript-language-server)
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs npm curl && \
    rm -rf /var/lib/apt/lists/*

# Install Go (for gopls)
RUN curl -fsSL https://go.dev/dl/go1.23.0.linux-amd64.tar.gz | tar -C /usr/local -xz
ENV PATH="/usr/local/go/bin:$PATH"

# Install language servers
RUN npm install -g pyright typescript-language-server typescript && \
    go install golang.org/x/tools/gopls@latest
ENV PATH="/root/go/bin:$PATH"

RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

USER app
EXPOSE 8101
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8101"]
