# syntax=docker/dockerfile:1

# --- Build stage -------------------------------------------------------
FROM golang:1.25 AS build

WORKDIR /src

# Copy module files first for better layer caching.
COPY go.mod go.sum ./
RUN go mod download

# Copy the rest of the module source.
COPY . .

RUN CGO_ENABLED=0 GOOS=linux go build -o /out/server ./cmd/server

# --- Runtime stage -------------------------------------------------------
FROM gcr.io/distroless/static:nonroot

COPY --from=build /out/server /server

USER nonroot

EXPOSE 8086

ENTRYPOINT ["/server"]
