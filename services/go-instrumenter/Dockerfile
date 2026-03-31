FROM golang:1.24-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o go-instrumenter ./cmd/main.go

FROM golang:1.24-alpine
# Runtime needs Go toolchain for building instrumented binaries
RUN apk add --no-cache ca-certificates git
ENV GOTOOLCHAIN=auto
COPY --from=builder /app/go-instrumenter /usr/local/bin/go-instrumenter
EXPOSE 8098
CMD ["go-instrumenter"]
