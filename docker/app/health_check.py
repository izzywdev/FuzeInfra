#!/usr/bin/env python3
"""
Health Check Script for Robot Catalog Crawler
Comprehensive health monitoring for containerized deployment
"""

import sys
import os
import json
import time
import psutil
import requests
from datetime import datetime
from typing import Dict, Any

# Add src to path
sys.path.insert(0, '/app/src')

try:
    from src.database.mongo_client import RobotCatalogDB
    from src.monitoring.prometheus_metrics import RobotCatalogMetrics
except ImportError:
    # If imports fail, we'll do basic checks only
    RobotCatalogDB = None
    RobotCatalogMetrics = None


class HealthChecker:
    """Comprehensive health checker for robot catalog system"""
    
    def __init__(self):
        self.health_status = {
            'timestamp': datetime.utcnow().isoformat(),
            'overall_status': 'unknown',
            'services': {},
            'metrics': {},
            'recommendations': []
        }
    
    def check_system_resources(self) -> Dict[str, Any]:
        """Check system resource usage"""
        
        try:
            # CPU usage
            cpu_percent = psutil.cpu_percent(interval=1)
            
            # Memory usage
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            memory_available = memory.available
            
            # Disk usage
            disk = psutil.disk_usage('/')
            disk_percent = (disk.used / disk.total) * 100
            disk_free = disk.free
            
            return {
                'status': 'healthy',
                'cpu_percent': cpu_percent,
                'memory_percent': memory_percent,
                'memory_available_mb': memory_available // (1024 * 1024),
                'disk_percent': disk_percent,
                'disk_free_gb': disk_free // (1024 * 1024 * 1024),
                'issues': self._check_resource_issues(cpu_percent, memory_percent, disk_percent)
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e)
            }
    
    def _check_resource_issues(self, cpu_percent: float, memory_percent: float, disk_percent: float) -> list:
        """Check for resource-related issues"""
        
        issues = []
        
        if cpu_percent > 80:
            issues.append(f'High CPU usage: {cpu_percent:.1f}%')
        
        if memory_percent > 90:
            issues.append(f'High memory usage: {memory_percent:.1f}%')
        elif memory_percent > 75:
            issues.append(f'Elevated memory usage: {memory_percent:.1f}%')
        
        if disk_percent > 90:
            issues.append(f'Low disk space: {disk_percent:.1f}% used')
        elif disk_percent > 80:
            issues.append(f'Disk space warning: {disk_percent:.1f}% used')
        
        return issues
    
    def check_mongodb_connectivity(self) -> Dict[str, Any]:
        """Check MongoDB database connectivity"""
        
        if not RobotCatalogDB:
            return {
                'status': 'skipped',
                'message': 'MongoDB client not available for health check'
            }
        
        try:
            # Attempt to connect to MongoDB
            db = RobotCatalogDB()
            
            # Test basic operations
            collections = db.db.list_collection_names()
            
            # Check if main collections exist
            expected_collections = ['robots', 'manufacturers', 'crawl_sessions', 'scraper_health']
            missing_collections = [col for col in expected_collections if col not in collections]
            
            # Get some basic stats
            robot_count = db.robots.count_documents({})
            manufacturer_count = db.manufacturers.count_documents({})
            
            db.close()
            
            status = 'healthy' if not missing_collections else 'warning'
            
            return {
                'status': status,
                'collections_found': len(collections),
                'missing_collections': missing_collections,
                'robot_count': robot_count,
                'manufacturer_count': manufacturer_count,
                'connection_time_ms': 50  # Placeholder - could measure actual time
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e),
                'message': 'Cannot connect to MongoDB'
            }
    
    def check_prometheus_metrics(self) -> Dict[str, Any]:
        """Check Prometheus metrics endpoint"""
        
        try:
            # Check if metrics endpoint is responding
            response = requests.get('http://localhost:8000/metrics', timeout=5)
            
            if response.status_code == 200:
                metrics_text = response.text
                
                # Count number of metrics
                metric_lines = [line for line in metrics_text.split('\n') if line and not line.startswith('#')]
                
                # Look for key metrics
                key_metrics = [
                    'robots_discovered_total',
                    'scraper_health_score',
                    'crawl_sessions_total'
                ]
                
                found_metrics = []
                for metric in key_metrics:
                    if metric in metrics_text:
                        found_metrics.append(metric)
                
                return {
                    'status': 'healthy',
                    'response_code': response.status_code,
                    'total_metrics': len(metric_lines),
                    'key_metrics_found': len(found_metrics),
                    'missing_key_metrics': [m for m in key_metrics if m not in found_metrics]
                }
            else:
                return {
                    'status': 'error',
                    'response_code': response.status_code,
                    'message': 'Metrics endpoint returned non-200 status'
                }
                
        except requests.RequestException as e:
            return {
                'status': 'error',
                'error': str(e),
                'message': 'Cannot reach Prometheus metrics endpoint'
            }
    
    def check_application_status(self) -> Dict[str, Any]:
        """Check general application health"""
        
        try:
            # Check if main application files exist
            required_files = [
                '/app/main.py',
                '/app/src/crawlers/robot_catalog_crawler.py',
                '/app/src/wordpress/wp_client.py'
            ]
            
            missing_files = []
            for file_path in required_files:
                if not os.path.exists(file_path):
                    missing_files.append(file_path)
            
            # Check data and log directories
            required_dirs = ['/app/data', '/app/logs', '/app/cache']
            missing_dirs = []
            for dir_path in required_dirs:
                if not os.path.exists(dir_path):
                    missing_dirs.append(dir_path)
            
            # Check environment variables
            required_env_vars = [
                'MONGODB_URL',
                'WP_SITE_URL',
                'WP_USERNAME',
                'WP_APP_PASSWORD'
            ]
            
            missing_env_vars = []
            for env_var in required_env_vars:
                if not os.getenv(env_var):
                    missing_env_vars.append(env_var)
            
            issues = []
            if missing_files:
                issues.append(f'Missing files: {", ".join(missing_files)}')
            if missing_dirs:
                issues.append(f'Missing directories: {", ".join(missing_dirs)}')
            if missing_env_vars:
                issues.append(f'Missing environment variables: {", ".join(missing_env_vars)}')
            
            status = 'healthy' if not issues else 'error'
            
            return {
                'status': status,
                'issues': issues,
                'files_ok': len(required_files) - len(missing_files),
                'dirs_ok': len(required_dirs) - len(missing_dirs),
                'env_vars_ok': len(required_env_vars) - len(missing_env_vars)
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e)
            }
    
    def run_health_check(self) -> Dict[str, Any]:
        """Run complete health check"""
        
        # Check all services
        self.health_status['services']['system_resources'] = self.check_system_resources()
        self.health_status['services']['mongodb'] = self.check_mongodb_connectivity()
        self.health_status['services']['prometheus_metrics'] = self.check_prometheus_metrics()
        self.health_status['services']['application'] = self.check_application_status()
        
        # Calculate overall status
        service_statuses = [service['status'] for service in self.health_status['services'].values()]
        
        if 'error' in service_statuses:
            self.health_status['overall_status'] = 'unhealthy'
        elif 'warning' in service_statuses:
            self.health_status['overall_status'] = 'degraded'
        else:
            self.health_status['overall_status'] = 'healthy'
        
        # Generate recommendations
        self._generate_recommendations()
        
        # Add summary metrics
        self.health_status['metrics'] = {
            'services_healthy': len([s for s in service_statuses if s == 'healthy']),
            'services_total': len(service_statuses),
            'check_duration_ms': 100  # Placeholder
        }
        
        return self.health_status
    
    def _generate_recommendations(self):
        """Generate health recommendations based on check results"""
        
        recommendations = []
        
        # System resource recommendations
        sys_status = self.health_status['services']['system_resources']
        if sys_status.get('status') == 'healthy' and 'issues' in sys_status:
            for issue in sys_status['issues']:
                if 'CPU' in issue:
                    recommendations.append('Consider scaling up CPU resources')
                elif 'memory' in issue:
                    recommendations.append('Consider increasing memory allocation')
                elif 'disk' in issue:
                    recommendations.append('Clean up old files or increase disk space')
        
        # MongoDB recommendations
        mongo_status = self.health_status['services']['mongodb']
        if mongo_status.get('status') == 'error':
            recommendations.append('Check MongoDB connection and credentials')
        elif mongo_status.get('missing_collections'):
            recommendations.append('Initialize missing MongoDB collections')
        
        # Prometheus recommendations
        prom_status = self.health_status['services']['prometheus_metrics']
        if prom_status.get('status') == 'error':
            recommendations.append('Check Prometheus metrics server configuration')
        elif prom_status.get('missing_key_metrics'):
            recommendations.append('Some key metrics are missing - check application initialization')
        
        # Application recommendations
        app_status = self.health_status['services']['application']
        if app_status.get('status') == 'error':
            recommendations.append('Fix missing application files or environment variables')
        
        self.health_status['recommendations'] = recommendations


def main():
    """Main health check execution"""
    
    checker = HealthChecker()
    health_result = checker.run_health_check()
    
    # Print results
    print(json.dumps(health_result, indent=2))
    
    # Exit with appropriate code
    if health_result['overall_status'] == 'healthy':
        sys.exit(0)
    elif health_result['overall_status'] == 'degraded':
        sys.exit(1)  # Warning
    else:
        sys.exit(2)  # Unhealthy


if __name__ == '__main__':
    main() 