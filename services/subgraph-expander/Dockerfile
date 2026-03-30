FROM golang:1.24-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o subgraph-expander ./cmd/main.go

FROM alpine:3.19
RUN apk add --no-cache ca-certificates
COPY --from=builder /app/subgraph-expander /usr/local/bin/subgraph-expander
EXPOSE 8088 8089
CMD ["subgraph-expander"]
