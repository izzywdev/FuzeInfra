# FuzeInfra Custom Hostname API
#
#   docker build -t ghcr.io/izzywdev/fuzeinfra/custom-hostname-api:dev \
#     services/custom-hostname-api
#
# Runtime configuration is entirely environment-driven (see app/config.py) so
# the same image serves kind (PROVIDER=stub) and prod (PROVIDER=cloudflare).
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/app/
COPY openapi.yaml /app/openapi.yaml

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PROVIDER=stub \
    HOST=0.0.0.0 \
    PORT=8080

# Non-root: the service only ever talks to the Kubernetes and Cloudflare APIs
# over the network and writes nothing to disk outside /tmp.
RUN useradd --create-home --uid 10001 apiuser \
    && chown -R apiuser:apiuser /app
USER 10001

EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn app.main:app --host ${HOST} --port ${PORT} --no-server-header"]
