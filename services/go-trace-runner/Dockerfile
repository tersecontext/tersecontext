FROM golang:1.24-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o go-trace-runner ./cmd/main.go

FROM alpine:3.19
RUN apk add --no-cache ca-certificates git
COPY --from=builder /app/go-trace-runner /usr/local/bin/go-trace-runner
EXPOSE 8099
CMD ["go-trace-runner"]
