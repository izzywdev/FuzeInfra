import pathlib

root = pathlib.Path(__file__).parent

# ------------------------------------------------- docker-compose.FuzeInfra.yml
p = root / "docker-compose.FuzeInfra.yml"
t = p.read_text(encoding="utf-8")

anchor = """  # Neo4j
  neo4j:"""
new = """  # MariaDB (MySQL protocol) - shared engine for WordPress/Laravel-class
  # consumers. Compose half of the dual-delivery model; the Helm half is
  # helm/fuzeinfra/templates/databases.yaml (mariadb.enabled).
  mariadb:
    image: mariadb:11.4
    container_name: fuzeinfra-mariadb
    environment:
      MARIADB_ROOT_PASSWORD: ${MARIADB_ROOT_PASSWORD}
      # Root must be reachable from other containers on the FuzeInfra network
      # (provisioning/admin tooling); the image otherwise binds root to localhost.
      MARIADB_ROOT_HOST: '%'
      MARIADB_AUTO_UPGRADE: '1'
    ports:
      - "${MARIADB_PORT:-3306}:3306"
    volumes:
      - mariadb_data:/var/lib/mysql
      # Per-consumer databases/users are declared here as idempotent SQL, the
      # local mirror of the `serviceMariadbDatabases` Helm values.
      - ./docker/mariadb/init:/docker-entrypoint-initdb.d:ro
    networks:
      - FuzeInfra
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Neo4j
  neo4j:"""
assert t.count(anchor) == 1, "compose neo4j anchor"
t = t.replace(anchor, new)

vol_anchor = """  neo4j_data:
    name: fuzeinfra_neo4j_data
"""
vol_new = """  mariadb_data:
    name: fuzeinfra_mariadb_data
  neo4j_data:
    name: fuzeinfra_neo4j_data
"""
assert t.count(vol_anchor) == 1, "compose volume anchor"
t = t.replace(vol_anchor, vol_new)
p.write_text(t, encoding="utf-8")

# ------------------------------------------------------------ environment.template
p = root / "environment.template"
t = p.read_text(encoding="utf-8")
anchor = """# Neo4j Configuration
NEO4J_HOST=localhost"""
new = """# MariaDB Configuration (shared MySQL-protocol engine)
MARIADB_HOST=localhost
MARIADB_PORT=3306
MARIADB_ROOT_PASSWORD=your_secure_mariadb_password

# Neo4j Configuration
NEO4J_HOST=localhost"""
assert t.count(anchor) == 1, "env template anchor"
t = t.replace(anchor, new)
p.write_text(t, encoding="utf-8")

# --------------------------------------------------------- setup_environment.py
p = root / "scripts-tools/setup_environment.py"
t = p.read_text(encoding="utf-8")
anchor = "                    'your_secure_neo4j_password': generate_secure_password(),"
new = ("                    'your_secure_mariadb_password': generate_secure_password(),\n"
       "                    'your_secure_neo4j_password': generate_secure_password(),")
assert t.count(anchor) == 1, "setup_environment anchor"
t = t.replace(anchor, new)
p.write_text(t, encoding="utf-8")

print("compose + env template + setup patched")
