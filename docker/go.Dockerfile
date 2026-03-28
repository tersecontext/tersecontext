FROM golang:1.23-bookworm AS builder

WORKDIR /workspace

# Cache module downloads
COPY go.mod go.sum* ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /service ./cmd/main.go

# ── Runtime image ─────────────────────────────────────────────────────────────
FROM gcr.io/distroless/static-debian12

COPY --from=builder /service /service

ENTRYPOINT ["/service"]
