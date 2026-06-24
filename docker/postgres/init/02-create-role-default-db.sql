-- Pre-create a database whose name matches the POSTGRES_USER role ("fuzeinfra").
--
-- PostgreSQL clients that connect without specifying a database fall back to a
-- database named after the connecting role. Health probes such as
-- `pg_isready -U fuzeinfra` (no -d) therefore target database "fuzeinfra",
-- which otherwise does not exist (the real application DB is "fuzeinfra_db").
-- Each such probe makes the backend log: FATAL: database "fuzeinfra" does not
-- exist -- noise that trips the CRIT-log Grafana/Loki alert.
--
-- Creating an (empty) role-default database makes those connections succeed and
-- silences the FATAL noise. Like 01-create-airflow-db.sql this runs only on the
-- FIRST initialization (empty data dir); the \gexec guard makes it a no-op if
-- the database already exists.
SELECT 'CREATE DATABASE fuzeinfra'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'fuzeinfra')\gexec
