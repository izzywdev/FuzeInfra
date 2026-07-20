# ChromaDB

The production Helm deployment is authenticated. Every application receives a
unique bearer token bound server-side to one tenant/database, plus an explicit
NetworkPolicy peer. Prefixes are naming conventions only and are not access
control. See `docs/consuming-repos/CHROMADB_PROVISIONING.md`.

The legacy Docker Compose stack is for local development and does not represent
the production security boundary. Do not expose its Chroma port outside the
developer machine and never reuse production credentials there.
