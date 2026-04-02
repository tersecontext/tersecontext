FROM golang:1.23-alpine AS builder
WORKDIR /src

# Install zoekt-index binary
RUN apk add --no-cache git && \
    go install github.com/sourcegraph/zoekt/cmd/zoekt-index@latest

COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN go build -o /zoekt-indexer ./cmd/

FROM alpine:3.19
RUN apk add --no-cache ca-certificates
COPY --from=builder /root/go/bin/zoekt-index /usr/local/bin/zoekt-index
COPY --from=builder /zoekt-indexer /usr/local/bin/zoekt-indexer
EXPOSE 8099
CMD ["/usr/local/bin/zoekt-indexer"]
