# FuzeInfra self-heal loop metrics exporter
#
#   docker build -t ghcr.io/izzywdev/fuzeinfra/selfheal-metrics-exporter:dev \
#     tools/selfheal-metrics-exporter
#
# Stdlib only — no requirements.txt, nothing to pip install, nothing to CVE-patch.
FROM python:3.12-alpine

WORKDIR /app

COPY exporter.py /app/exporter.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LISTEN_PORT=9109 \
    POLL_INTERVAL_SECONDS=600

# Non-root: the process only reads the public GitHub REST API and writes nothing.
RUN adduser -D -u 10001 exporter
USER 10001

EXPOSE 9109

CMD ["python", "/app/exporter.py"]
