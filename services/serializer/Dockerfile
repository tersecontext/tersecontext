FROM golang:1.24-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOFLAGS=-mod=mod go build -o serializer ./cmd/main.go

FROM alpine:3.19
RUN apk add --no-cache ca-certificates
COPY --from=builder /app/serializer /usr/local/bin/serializer
EXPOSE 8089 8090
CMD ["serializer"]
