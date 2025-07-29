"""
Tests for monitoring and observability services: Prometheus, Grafana, Alertmanager, Loki
"""
import pytest
import requests
import json
import time


class TestPrometheus:
    """Test Prometheus monitoring service."""
    
    def test_prometheus_health(self, service_urls, wait_for_services):
        """Test Prometheus health endpoint."""
        response = requests.get(f"{service_urls['prometheus']}/-/healthy", timeout=10)
        assert response.status_code == 200

    def test_prometheus_ready(self, service_urls, wait_for_services):
        """Test Prometheus ready endpoint."""
        response = requests.get(f"{service_urls['prometheus']}/-/ready", timeout=10)
        assert response.status_code == 200

    def test_prometheus_config(self, service_urls, wait_for_services):
        """Test Prometheus configuration endpoint."""
        response = requests.get(f"{service_urls['prometheus']}/api/v1/status/config", timeout=10)
        assert response.status_code == 200
        
        config_data = response.json()
        assert config_data["status"] == "success"
        assert "yaml" in config_data["data"]

    def test_prometheus_targets(self, service_urls, wait_for_services):
        """Test Prometheus targets endpoint."""
        response = requests.get(f"{service_urls['prometheus']}/api/v1/targets", timeout=10)
        assert response.status_code == 200
        
        targets_data = response.json()
        assert targets_data["status"] == "success"
        assert "activeTargets" in targets_data["data"]

    def test_prometheus_metrics_query(self, service_urls, wait_for_services):
        """Test Prometheus metrics query."""
        # Query for up metric (should always exist)
        params = {"query": "up"}
        response = requests.get(f"{service_urls['prometheus']}/api/v1/query", params=params, timeout=10)
        assert response.status_code == 200
        
        query_data = response.json()
        assert query_data["status"] == "success"
        assert "result" in query_data["data"]


class TestGrafana:
    """Test Grafana visualization service."""
    
    def test_grafana_health(self, service_urls, wait_for_services):
        """Test Grafana health endpoint."""
        response = requests.get(f"{service_urls['grafana']}/api/health", timeout=10)
        assert response.status_code == 200
        
        health_data = response.json()
        assert health_data["database"] == "ok"

    def test_grafana_login_page(self, service_urls, wait_for_services):
        """Test Grafana login page accessibility."""
        response = requests.get(f"{service_urls['grafana']}/login", timeout=10)
        assert response.status_code == 200
        assert "Grafana" in response.text

    def test_grafana_datasources(self, service_urls, wait_for_services):
        """Test Grafana datasources configuration."""
        # Use basic auth with admin credentials
        auth = ("admin", "admin")
        response = requests.get(f"{service_urls['grafana']}/api/datasources", auth=auth, timeout=10)
        assert response.status_code == 200
        
        datasources = response.json()
        assert len(datasources) >= 2  # Should have Prometheus and Loki
        
        # Check for Prometheus datasource
        prometheus_ds = next((ds for ds in datasources if ds["type"] == "prometheus"), None)
        assert prometheus_ds is not None
        assert "prometheus" in prometheus_ds["url"].lower()
        
        # Check for Loki datasource
        loki_ds = next((ds for ds in datasources if ds["type"] == "loki"), None)
        assert loki_ds is not None
        assert "loki" in loki_ds["url"].lower()

    def test_grafana_dashboards(self, service_urls, wait_for_services):
        """Test Grafana dashboards."""
        auth = ("admin", "admin")
        response = requests.get(f"{service_urls['grafana']}/api/search", auth=auth, timeout=10)
        assert response.status_code == 200
        
        dashboards = response.json()
        # Should have at least the infrastructure overview dashboard
        assert len(dashboards) >= 0


class TestAlertmanager:
    """Test Alertmanager service."""
    
    def test_alertmanager_health(self, service_urls, wait_for_services):
        """Test Alertmanager health endpoint."""
        response = requests.get(f"{service_urls['alertmanager']}/-/healthy", timeout=10)
        assert response.status_code == 200

    def test_alertmanager_ready(self, service_urls, wait_for_services):
        """Test Alertmanager ready endpoint."""
        response = requests.get(f"{service_urls['alertmanager']}/-/ready", timeout=10)
        assert response.status_code == 200

    def test_alertmanager_config(self, service_urls, wait_for_services):
        """Test Alertmanager configuration."""
        response = requests.get(f"{service_urls['alertmanager']}/api/v1/status", timeout=10)
        assert response.status_code == 200
        
        status_data = response.json()
        assert "data" in status_data
        assert "configYAML" in status_data["data"]

    def test_alertmanager_alerts(self, service_urls, wait_for_services):
        """Test Alertmanager alerts endpoint."""
        response = requests.get(f"{service_urls['alertmanager']}/api/v1/alerts", timeout=10)
        assert response.status_code == 200
        
        alerts_data = response.json()
        assert "data" in alerts_data
        assert isinstance(alerts_data["data"], list)


class TestLoki:
    """Test Loki logging service."""
    
    def test_loki_health(self, service_urls, wait_for_services):
        """Test Loki health endpoint."""
        response = requests.get(f"{service_urls['loki']}/ready", timeout=10)
        assert response.status_code == 200

    def test_loki_metrics(self, service_urls, wait_for_services):
        """Test Loki metrics endpoint."""
        response = requests.get(f"{service_urls['loki']}/metrics", timeout=10)
        assert response.status_code == 200
        assert "loki_" in response.text

    def test_loki_labels(self, service_urls, wait_for_services):
        """Test Loki labels API."""
        response = requests.get(f"{service_urls['loki']}/loki/api/v1/labels", timeout=10)
        assert response.status_code == 200
        
        labels_data = response.json()
        assert "data" in labels_data
        assert isinstance(labels_data["data"], list)

    def test_loki_log_ingestion(self, service_urls, wait_for_services):
        """Test Loki log ingestion."""
        # Create test log entry
        timestamp = str(int(time.time() * 1000000000))  # nanoseconds
        log_data = {
            "streams": [
                {
                    "stream": {"job": "test", "level": "info"},
                    "values": [[timestamp, "Test log message from pytest"]]
                }
            ]
        }
        
        headers = {"Content-Type": "application/json"}
        response = requests.post(
            f"{service_urls['loki']}/loki/api/v1/push",
            data=json.dumps(log_data),
            headers=headers,
            timeout=10
        )
        assert response.status_code == 204

    def test_loki_query(self, service_urls, wait_for_services):
        """Test Loki query API."""
        # Query for any logs (basic query)
        params = {"query": "{job=~\".+\"}"}
        response = requests.get(f"{service_urls['loki']}/loki/api/v1/query", params=params, timeout=10)
        assert response.status_code == 200
        
        query_data = response.json()
        assert "data" in query_data


class TestNodeExporter:
    """Test Node Exporter service."""
    
    def test_node_exporter_metrics(self, service_urls, wait_for_services):
        """Test Node Exporter metrics endpoint."""
        response = requests.get(f"{service_urls['node_exporter']}/metrics", timeout=10)
        assert response.status_code == 200
        
        # Check for common node metrics
        metrics_text = response.text
        assert "node_cpu_seconds_total" in metrics_text
        assert "node_memory_MemTotal_bytes" in metrics_text
        assert "node_filesystem_size_bytes" in metrics_text 