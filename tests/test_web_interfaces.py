"""
Tests for web interface services: Mongo Express, Kafka UI, RabbitMQ Management, Neo4j Browser
"""
import pytest
import requests


class TestMongoExpress:
    """Test Mongo Express web interface."""
    
    def test_mongo_express_login(self, service_urls, wait_for_services):
        """Test Mongo Express login page."""
        response = requests.get(f"{service_urls['mongo_express']}", timeout=10)
        # Should redirect to login or show interface
        assert response.status_code in [200, 401]

    def test_mongo_express_with_auth(self, service_urls, wait_for_services):
        """Test Mongo Express with authentication."""
        auth = ("admin", "admin")
        response = requests.get(f"{service_urls['mongo_express']}", auth=auth, timeout=10)
        assert response.status_code == 200
        assert "mongo" in response.text.lower() or "database" in response.text.lower()


class TestKafkaUI:
    """Test Kafka UI web interface."""
    
    def test_kafka_ui_dashboard(self, service_urls, wait_for_services):
        """Test Kafka UI dashboard."""
        response = requests.get(f"{service_urls['kafka_ui']}", timeout=10)
        assert response.status_code == 200
        assert "kafka" in response.text.lower() or "cluster" in response.text.lower()

    def test_kafka_ui_clusters(self, service_urls, wait_for_services):
        """Test Kafka UI clusters endpoint."""
        response = requests.get(f"{service_urls['kafka_ui']}/api/clusters", timeout=10)
        assert response.status_code == 200
        
        clusters_data = response.json()
        assert isinstance(clusters_data, list)
        assert len(clusters_data) >= 1  # Should have at least one cluster

    def test_kafka_ui_topics(self, service_urls, wait_for_services):
        """Test Kafka UI topics endpoint."""
        response = requests.get(f"{service_urls['kafka_ui']}/api/clusters/local/topics", timeout=10)
        assert response.status_code == 200
        
        topics_data = response.json()
        assert isinstance(topics_data, list)


class TestRabbitMQManagement:
    """Test RabbitMQ Management web interface."""
    
    def test_rabbitmq_management_login(self, service_urls, wait_for_services):
        """Test RabbitMQ Management login page."""
        response = requests.get(f"{service_urls['rabbitmq_management']}", timeout=10)
        assert response.status_code == 200
        assert "rabbitmq" in response.text.lower() or "management" in response.text.lower()

    def test_rabbitmq_management_api_overview(self, service_urls, wait_for_services):
        """Test RabbitMQ Management API overview."""
        auth = ("admin", "admin123")
        response = requests.get(f"{service_urls['rabbitmq_management']}/api/overview", auth=auth, timeout=10)
        assert response.status_code == 200
        
        overview_data = response.json()
        assert "rabbitmq_version" in overview_data
        assert "erlang_version" in overview_data

    def test_rabbitmq_management_api_nodes(self, service_urls, wait_for_services):
        """Test RabbitMQ Management API nodes."""
        auth = ("admin", "admin123")
        response = requests.get(f"{service_urls['rabbitmq_management']}/api/nodes", auth=auth, timeout=10)
        assert response.status_code == 200
        
        nodes_data = response.json()
        assert isinstance(nodes_data, list)
        assert len(nodes_data) >= 1

    def test_rabbitmq_management_api_queues(self, service_urls, wait_for_services):
        """Test RabbitMQ Management API queues."""
        auth = ("admin", "admin123")
        response = requests.get(f"{service_urls['rabbitmq_management']}/api/queues", auth=auth, timeout=10)
        assert response.status_code == 200
        
        queues_data = response.json()
        assert isinstance(queues_data, list)


class TestNeo4jBrowser:
    """Test Neo4j Browser web interface."""
    
    def test_neo4j_browser_access(self, service_urls, wait_for_services):
        """Test Neo4j Browser accessibility."""
        response = requests.get(f"{service_urls['neo4j_browser']}", timeout=10)
        assert response.status_code == 200
        assert "neo4j" in response.text.lower() or "graph" in response.text.lower()

    def test_neo4j_browser_api(self, service_urls, wait_for_services):
        """Test Neo4j Browser API endpoints."""
        # Test the discovery endpoint
        response = requests.get(f"{service_urls['neo4j_browser']}/", timeout=10)
        assert response.status_code == 200 