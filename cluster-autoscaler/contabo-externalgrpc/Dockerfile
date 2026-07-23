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
# /reaper is the billing-aware reaper's one-shot binary (run as an hourly
# CronJob — see helm/fuzeinfra/templates/autoscaler/reaper.yaml), shipped in
# the same image as the server so there's a single image/tag to build and
# promote for this component.
RUN CGO_ENABLED=0 GOOS=linux go build -o /out/reaper ./cmd/reaper

# --- Runtime stage -------------------------------------------------------
FROM gcr.io/distroless/static:nonroot

COPY --from=build /out/server /server
COPY --from=build /out/reaper /reaper

USER nonroot

EXPOSE 8086

ENTRYPOINT ["/server"]
