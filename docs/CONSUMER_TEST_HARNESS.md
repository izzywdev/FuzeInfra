# Consumer Test-Harness

A trimmed, **version-accurate** slice of the FuzeInfra base services for consumer
repos' CI. Instead of each consumer hand-rolling its own Postgres/Redis/Kafka/…
(and drifting from the versions production actually runs), it vendors FuzeInfra
as a git submodule and stands up
[`docker-compose.consumer-test.yml`](../docker-compose.consumer-test.yml) pinned
from the single-source-of-truth [`versions.env`](../versions.env).

## Services

| Service | Image (from `versions.env`) | Default host port |
|---------|-----------------------------|-------------------|
| Postgres | `POSTGRES_IMAGE` (`postgres:15`) | `5432` |
| Redis | `REDIS_IMAGE` (`redis:7-alpine`) | `6379` |
| Kafka (KRaft) | `KAFKA_IMAGE` (`confluentinc/cp-kafka:7.6.1`) | `29092` |
| ChromaDB | `CHROMADB_IMAGE` (`chromadb/chroma:0.5.23`) | `8000` |
| MailHog | `MAILHOG_IMAGE` (`mailhog/mailhog:v1.0.1`) | `1025` SMTP / `8025` UI |

The datastore/queue tags mirror `docker-compose.FuzeInfra.yml`. MailHog has no
prod equivalent (dev/CI mail catcher) and is pinned for reproducibility.

## Single source of truth for versions

[`versions.env`](../versions.env) is the **one place** image versions live for
consumer CI. Bump an image in `docker-compose.FuzeInfra.yml` → bump it in
`versions.env` too (and vice-versa) so CI stays prod-accurate.

## Usage from a consumer repo

Add FuzeInfra as a submodule, then in CI:

```bash
git submodule update --init --recursive

docker compose \
  --env-file fuzeinfra/versions.env \
  -f fuzeinfra/docker-compose.consumer-test.yml \
  up -d --wait

# Services on localhost — run your tests against them:
#   postgres   localhost:5432  (db/user/pass from versions.env)
#   redis      localhost:6379
#   kafka      localhost:29092
#   chromadb   localhost:8000  (GET /api/v2/heartbeat to check readiness)
#   mailhog    localhost:1025 (SMTP) / localhost:8025 (UI/API)

# Tear down (also removes the ephemeral network):
docker compose -f fuzeinfra/docker-compose.consumer-test.yml down -v
```

### Example GitHub Actions step

```yaml
- name: Stand up FuzeInfra test infra
  run: |
    docker compose --env-file fuzeinfra/versions.env \
      -f fuzeinfra/docker-compose.consumer-test.yml up -d --wait
- name: Run tests
  env:
    DATABASE_URL: postgresql://fuzeinfra:fuzeinfra_test@localhost:5432/fuzeinfra_test
    REDIS_URL: redis://localhost:6379/0
    KAFKA_BOOTSTRAP_SERVERS: localhost:29092
    CHROMADB_URL: http://localhost:8000
    SMTP_URL: smtp://localhost:1025
  run: <your test command>
```

## Notes

- **Ephemeral by design:** its own bridge network, no host volumes — nothing
  persists between runs and it won't collide with a developer's full
  `./infra-up.sh` stack (override `TEST_*_PORT` in `versions.env` if it does).
- ChromaDB runs with `IS_PERSISTENT=false` for clean test isolation.
- `--wait` blocks until healthchecks pass; ChromaDB is distroless (no in-container
  probe) so poll `GET /api/v2/heartbeat` from the test job if you need a gate.
