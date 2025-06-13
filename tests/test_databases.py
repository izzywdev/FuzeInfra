"""
Tests for database services: PostgreSQL, MongoDB, Redis, Neo4j, Elasticsearch
"""
import pytest
import time


class TestPostgreSQL:
    """Test PostgreSQL database service."""
    
    def test_postgres_connection(self, postgres_connection, wait_for_services):
        """Test PostgreSQL connection and basic operations."""
        cursor = postgres_connection.cursor()
        
        # Test connection
        cursor.execute("SELECT version();")
        version = cursor.fetchone()
        assert version is not None
        assert "PostgreSQL" in version[0]
        
        # Test database operations
        cursor.execute("CREATE TABLE IF NOT EXISTS test_table (id SERIAL PRIMARY KEY, name VARCHAR(50));")
        cursor.execute("INSERT INTO test_table (name) VALUES ('test_entry');")
        postgres_connection.commit()
        
        cursor.execute("SELECT name FROM test_table WHERE name = 'test_entry';")
        result = cursor.fetchone()
        assert result[0] == "test_entry"
        
        # Cleanup
        cursor.execute("DROP TABLE test_table;")
        postgres_connection.commit()
        cursor.close()

    def test_postgres_airflow_database(self, postgres_connection, wait_for_services):
        """Test that Airflow database is accessible."""
        cursor = postgres_connection.cursor()
        
        # Check if airflow database exists or can be created
        cursor.execute("SELECT datname FROM pg_database WHERE datname = 'airflow';")
        result = cursor.fetchone()
        
        if not result:
            # Create airflow database for testing
            postgres_connection.autocommit = True
            cursor.execute("CREATE DATABASE airflow;")
            postgres_connection.autocommit = False
        
        cursor.close()


class TestMongoDB:
    """Test MongoDB database service."""
    
    def test_mongodb_connection(self, mongodb_connection, wait_for_services):
        """Test MongoDB connection and basic operations."""
        # Test connection
        server_info = mongodb_connection.server_info()
        assert "version" in server_info
        
        # Test database operations
        db = mongodb_connection.test_db
        collection = db.test_collection
        
        # Insert test document
        test_doc = {"name": "test_entry", "value": 123}
        result = collection.insert_one(test_doc)
        assert result.inserted_id is not None
        
        # Query test document
        found_doc = collection.find_one({"name": "test_entry"})
        assert found_doc["name"] == "test_entry"
        assert found_doc["value"] == 123
        
        # Cleanup
        collection.delete_one({"name": "test_entry"})

    def test_mongodb_admin_access(self, mongodb_connection, wait_for_services):
        """Test MongoDB admin access."""
        # List databases (requires admin privileges)
        db_list = mongodb_connection.list_database_names()
        assert "admin" in db_list


class TestRedis:
    """Test Redis cache service."""
    
    def test_redis_connection(self, redis_connection, wait_for_services):
        """Test Redis connection and basic operations."""
        # Test connection
        assert redis_connection.ping() is True
        
        # Test basic operations
        redis_connection.set("test_key", "test_value")
        value = redis_connection.get("test_key")
        assert value == "test_value"
        
        # Test expiration
        redis_connection.setex("temp_key", 1, "temp_value")
        assert redis_connection.get("temp_key") == "temp_value"
        time.sleep(2)
        assert redis_connection.get("temp_key") is None
        
        # Cleanup
        redis_connection.delete("test_key")

    def test_redis_data_structures(self, redis_connection, wait_for_services):
        """Test Redis data structures."""
        # Test list operations
        redis_connection.lpush("test_list", "item1", "item2")
        assert redis_connection.llen("test_list") == 2
        assert redis_connection.rpop("test_list") == "item1"
        
        # Test hash operations
        redis_connection.hset("test_hash", "field1", "value1")
        assert redis_connection.hget("test_hash", "field1") == "value1"
        
        # Cleanup
        redis_connection.delete("test_list", "test_hash")


class TestNeo4j:
    """Test Neo4j graph database service."""
    
    def test_neo4j_connection(self, neo4j_connection, wait_for_services):
        """Test Neo4j connection and basic operations."""
        with neo4j_connection.session() as session:
            # Test connection
            result = session.run("RETURN 'Hello, Neo4j!' AS message")
            record = result.single()
            assert record["message"] == "Hello, Neo4j!"
            
            # Test node creation and query
            session.run("CREATE (n:TestNode {name: 'test_node', value: 123})")
            result = session.run("MATCH (n:TestNode {name: 'test_node'}) RETURN n.value AS value")
            record = result.single()
            assert record["value"] == 123
            
            # Cleanup
            session.run("MATCH (n:TestNode {name: 'test_node'}) DELETE n")

    def test_neo4j_apoc_plugin(self, neo4j_connection, wait_for_services):
        """Test that APOC plugin is available."""
        with neo4j_connection.session() as session:
            # Test APOC function
            result = session.run("RETURN apoc.version() AS version")
            record = result.single()
            assert record["version"] is not None


class TestElasticsearch:
    """Test Elasticsearch service."""
    
    def test_elasticsearch_connection(self, elasticsearch_connection, wait_for_services):
        """Test Elasticsearch connection and basic operations."""
        # Test connection
        assert elasticsearch_connection.ping() is True
        
        # Get cluster info
        info = elasticsearch_connection.info()
        assert "cluster_name" in info
        assert "version" in info

    def test_elasticsearch_indexing(self, elasticsearch_connection, wait_for_services):
        """Test Elasticsearch indexing and search."""
        index_name = "test_index"
        
        # Create test document
        doc = {
            "title": "Test Document",
            "content": "This is a test document for Elasticsearch",
            "timestamp": "2024-01-01T00:00:00"
        }
        
        # Index document
        response = elasticsearch_connection.index(index=index_name, body=doc)
        assert response["result"] in ["created", "updated"]
        
        # Refresh index to make document searchable
        elasticsearch_connection.indices.refresh(index=index_name)
        
        # Search for document
        search_response = elasticsearch_connection.search(
            index=index_name,
            body={"query": {"match": {"title": "Test Document"}}}
        )
        
        assert search_response["hits"]["total"]["value"] >= 1
        assert search_response["hits"]["hits"][0]["_source"]["title"] == "Test Document"
        
        # Cleanup
        elasticsearch_connection.indices.delete(index=index_name, ignore=[400, 404]) 