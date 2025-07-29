"""
Pytest configuration and fixtures for FuzeInfra infrastructure testing.
"""
import pytest
import time
import requests
import psycopg2
import pymongo
import redis
import neo4j
import pika
from elasticsearch import Elasticsearch
from kafka import KafkaProducer, KafkaConsumer
from kafka.errors import KafkaError


@pytest.fixture(scope="session")
def wait_for_services():
    """Wait for all services to be ready before running tests."""
    print("Waiting for services to be ready...")
    time.sleep(30)  # Give services time to start up
    yield
    print("Tests completed.")


@pytest.fixture
def postgres_connection():
    """PostgreSQL connection fixture."""
    try:
        conn = psycopg2.connect(
            host="localhost",
            port=5432,
            database="postgres",
            user="postgres",
            password="postgres"
        )
        yield conn
        conn.close()
    except Exception as e:
        pytest.fail(f"Failed to connect to PostgreSQL: {e}")


@pytest.fixture
def mongodb_connection():
    """MongoDB connection fixture."""
    try:
        client = pymongo.MongoClient("mongodb://admin:admin123@localhost:27017/")
        yield client
        client.close()
    except Exception as e:
        pytest.fail(f"Failed to connect to MongoDB: {e}")


@pytest.fixture
def redis_connection():
    """Redis connection fixture."""
    try:
        r = redis.Redis(host="localhost", port=6379, decode_responses=True)
        yield r
    except Exception as e:
        pytest.fail(f"Failed to connect to Redis: {e}")


@pytest.fixture
def neo4j_connection():
    """Neo4j connection fixture."""
    try:
        driver = neo4j.GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "password123"))
        yield driver
        driver.close()
    except Exception as e:
        pytest.fail(f"Failed to connect to Neo4j: {e}")


@pytest.fixture
def elasticsearch_connection():
    """Elasticsearch connection fixture."""
    try:
        es = Elasticsearch([{"host": "localhost", "port": 9200, "scheme": "http"}])
        yield es
    except Exception as e:
        pytest.fail(f"Failed to connect to Elasticsearch: {e}")


@pytest.fixture
def rabbitmq_connection():
    """RabbitMQ connection fixture."""
    try:
        credentials = pika.PlainCredentials("admin", "admin123")
        parameters = pika.ConnectionParameters("localhost", 5672, "/", credentials)
        connection = pika.BlockingConnection(parameters)
        yield connection
        connection.close()
    except Exception as e:
        pytest.fail(f"Failed to connect to RabbitMQ: {e}")


@pytest.fixture
def kafka_producer():
    """Kafka producer fixture."""
    try:
        producer = KafkaProducer(
            bootstrap_servers=["localhost:29092"],
            value_serializer=lambda x: x.encode("utf-8")
        )
        yield producer
        producer.close()
    except Exception as e:
        pytest.fail(f"Failed to create Kafka producer: {e}")


@pytest.fixture
def service_urls():
    """Dictionary of service URLs for HTTP health checks."""
    return {
        "prometheus": "http://localhost:9090",
        "grafana": "http://localhost:3001",
        "alertmanager": "http://localhost:9093",
        "loki": "http://localhost:3100",
        "node_exporter": "http://localhost:9100",
        "mongo_express": "http://localhost:8081",
        "kafka_ui": "http://localhost:8080",
        "rabbitmq_management": "http://localhost:15672",
        "airflow": "http://localhost:8082",
        "airflow_flower": "http://localhost:5555",
        "neo4j_browser": "http://localhost:7474",
        "elasticsearch": "http://localhost:9200"
    } 