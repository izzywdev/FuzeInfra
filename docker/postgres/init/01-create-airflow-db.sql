-- Pre-create the Airflow metadata database so Airflow's entrypoint connection
-- check / migration succeeds on first boot.
--
-- Postgres runs files in /docker-entrypoint-initdb.d only on FIRST initialization
-- (empty data dir). The conditional \gexec makes this a no-op if the database
-- somehow already exists.
SELECT 'CREATE DATABASE airflow'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'airflow')\gexec
