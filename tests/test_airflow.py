"""
Tests for Airflow workflow orchestration services
"""
import pytest
import requests
import time


class TestAirflow:
    """Test Apache Airflow workflow orchestration."""
    
    def test_airflow_webserver_health(self, service_urls, wait_for_services):
        """Test Airflow webserver health."""
        # Airflow health endpoint
        response = requests.get(f"{service_urls['airflow']}/health", timeout=10)
        assert response.status_code == 200
        
        health_data = response.json()
        assert "metadatabase" in health_data
        assert health_data["metadatabase"]["status"] == "healthy"

    def test_airflow_login_page(self, service_urls, wait_for_services):
        """Test Airflow login page accessibility."""
        response = requests.get(f"{service_urls['airflow']}/login", timeout=10)
        assert response.status_code == 200
        assert "Airflow" in response.text

    def test_airflow_api_version(self, service_urls, wait_for_services):
        """Test Airflow API version endpoint."""
        response = requests.get(f"{service_urls['airflow']}/api/v1/version", timeout=10)
        assert response.status_code == 200
        
        version_data = response.json()
        assert "version" in version_data
        assert "git_version" in version_data

    def test_airflow_config(self, service_urls, wait_for_services):
        """Test Airflow configuration endpoint."""
        # Basic auth with default credentials (admin/admin)
        auth = ("admin", "admin")
        response = requests.get(f"{service_urls['airflow']}/api/v1/config", auth=auth, timeout=10)
        
        # May return 401 if auth is not set up, but should not return 500
        assert response.status_code in [200, 401, 403]

    def test_airflow_dags_endpoint(self, service_urls, wait_for_services):
        """Test Airflow DAGs endpoint."""
        auth = ("admin", "admin")
        response = requests.get(f"{service_urls['airflow']}/api/v1/dags", auth=auth, timeout=10)
        
        # May return 401 if auth is not set up
        if response.status_code == 200:
            dags_data = response.json()
            assert "dags" in dags_data
            assert isinstance(dags_data["dags"], list)
        else:
            assert response.status_code in [401, 403]

    def test_airflow_pools(self, service_urls, wait_for_services):
        """Test Airflow pools endpoint."""
        auth = ("admin", "admin")
        response = requests.get(f"{service_urls['airflow']}/api/v1/pools", auth=auth, timeout=10)
        
        if response.status_code == 200:
            pools_data = response.json()
            assert "pools" in pools_data
            assert isinstance(pools_data["pools"], list)
        else:
            assert response.status_code in [401, 403]


class TestAirflowFlower:
    """Test Airflow Flower (Celery monitoring)."""
    
    def test_flower_dashboard(self, service_urls, wait_for_services):
        """Test Flower dashboard accessibility."""
        response = requests.get(f"{service_urls['airflow_flower']}", timeout=10)
        assert response.status_code == 200
        assert "Flower" in response.text or "Celery" in response.text

    def test_flower_api_workers(self, service_urls, wait_for_services):
        """Test Flower workers API."""
        response = requests.get(f"{service_urls['airflow_flower']}/api/workers", timeout=10)
        assert response.status_code == 200
        
        workers_data = response.json()
        assert isinstance(workers_data, dict)

    def test_flower_api_tasks(self, service_urls, wait_for_services):
        """Test Flower tasks API."""
        response = requests.get(f"{service_urls['airflow_flower']}/api/tasks", timeout=10)
        assert response.status_code == 200
        
        tasks_data = response.json()
        assert isinstance(tasks_data, dict) 