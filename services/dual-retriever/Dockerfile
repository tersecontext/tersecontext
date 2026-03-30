FROM golang:1.23-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o dual-retriever ./cmd/main.go

FROM alpine:3.19
RUN apk add --no-cache ca-certificates
COPY --from=builder /app/dual-retriever /usr/local/bin/dual-retriever
EXPOSE 8087 8088
CMD ["dual-retriever"]
