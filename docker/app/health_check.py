#!/usr/bin/env python3
"""
Generic Infrastructure Health Check Script
Comprehensive health monitoring for shared infrastructure services
"""

import sys
import os
import json
import time
import psutil
import requests
from datetime import datetime
from typing import Dict, Any


class InfrastructureHealthChecker:
    """Generic health checker for shared infrastructure services"""
    
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
    
    def check_database_connectivity(self) -> Dict[str, Any]:
        """Check database connectivity (PostgreSQL, MongoDB, Redis)"""
        
        results = {}
        
        # PostgreSQL check
        try:
            import psycopg2
            postgres_host = os.getenv('POSTGRES_HOST', 'localhost')
            postgres_port = os.getenv('POSTGRES_PORT', '5432')
            postgres_db = os.getenv('POSTGRES_DB', 'fuzeinfra_db')
            postgres_user = os.getenv('POSTGRES_USER', 'fuzeinfra')
            postgres_password = os.getenv('POSTGRES_PASSWORD', '')
            
            conn = psycopg2.connect(
                host=postgres_host,
                port=postgres_port,
                database=postgres_db,
                user=postgres_user,
                password=postgres_password
            )
            conn.close()
            
            results['postgresql'] = {
                'status': 'healthy',
                'host': postgres_host,
                'port': postgres_port,
                'database': postgres_db
            }
            
        except Exception as e:
            results['postgresql'] = {
                'status': 'error',
                'error': str(e)
            }
        
        # MongoDB check
        try:
            from pymongo import MongoClient
            mongodb_host = os.getenv('MONGODB_HOST', 'localhost')
            mongodb_port = int(os.getenv('MONGODB_PORT', '27017'))
            
            client = MongoClient(mongodb_host, mongodb_port, serverSelectionTimeoutMS=5000)
            client.server_info()  # Test connection
            client.close()
            
            results['mongodb'] = {
                'status': 'healthy',
                'host': mongodb_host,
                'port': mongodb_port
            }
            
        except Exception as e:
            results['mongodb'] = {
                'status': 'error',
                'error': str(e)
            }
        
        # Redis check
        try:
            import redis
            redis_host = os.getenv('REDIS_HOST', 'localhost')
            redis_port = int(os.getenv('REDIS_PORT', '6379'))
            redis_password = os.getenv('REDIS_PASSWORD', None)
            
            r = redis.Redis(host=redis_host, port=redis_port, password=redis_password, socket_timeout=5)
            r.ping()
            
            results['redis'] = {
                'status': 'healthy',
                'host': redis_host,
                'port': redis_port
            }
            
        except Exception as e:
            results['redis'] = {
                'status': 'error',
                'error': str(e)
            }
        
        return results
    
    def check_monitoring_services(self) -> Dict[str, Any]:
        """Check monitoring services (Prometheus, Grafana)"""
        
        results = {}
        
        # Prometheus check
        try:
            prometheus_url = os.getenv('PROMETHEUS_URL', 'http://localhost:9090')
            response = requests.get(f'{prometheus_url}/api/v1/query?query=up', timeout=5)
            
            if response.status_code == 200:
                results['prometheus'] = {
                    'status': 'healthy',
                    'url': prometheus_url,
                    'response_code': response.status_code
                }
            else:
                results['prometheus'] = {
                    'status': 'error',
                    'url': prometheus_url,
                    'response_code': response.status_code
                }
                
        except Exception as e:
            results['prometheus'] = {
                'status': 'error',
                'error': str(e)
            }
        
        # Grafana check
        try:
            grafana_url = os.getenv('GRAFANA_URL', 'http://localhost:3000')
            response = requests.get(f'{grafana_url}/api/health', timeout=5)
            
            if response.status_code == 200:
                results['grafana'] = {
                    'status': 'healthy',
                    'url': grafana_url,
                    'response_code': response.status_code
                }
            else:
                results['grafana'] = {
                    'status': 'error',
                    'url': grafana_url,
                    'response_code': response.status_code
                }
                
        except Exception as e:
            results['grafana'] = {
                'status': 'error',
                'error': str(e)
            }
        
        return results
    
    def check_message_queues(self) -> Dict[str, Any]:
        """Check message queue services (Kafka, RabbitMQ)"""
        
        results = {}
        
        # Kafka check
        try:
            from kafka import KafkaProducer
            kafka_host = os.getenv('KAFKA_HOST', 'localhost')
            kafka_port = os.getenv('KAFKA_PORT', '9092')
            
            producer = KafkaProducer(
                bootstrap_servers=[f'{kafka_host}:{kafka_port}'],
                request_timeout_ms=5000,
                api_version_auto_timeout_ms=5000
            )
            producer.close()
            
            results['kafka'] = {
                'status': 'healthy',
                'host': kafka_host,
                'port': kafka_port
            }
            
        except Exception as e:
            results['kafka'] = {
                'status': 'error',
                'error': str(e)
            }
        
        # RabbitMQ check (if configured)
        try:
            rabbitmq_host = os.getenv('RABBITMQ_HOST')
            if rabbitmq_host:
                import pika
                rabbitmq_port = int(os.getenv('RABBITMQ_PORT', '5672'))
                rabbitmq_user = os.getenv('RABBITMQ_USER', 'guest')
                rabbitmq_password = os.getenv('RABBITMQ_PASSWORD', 'guest')
                
                credentials = pika.PlainCredentials(rabbitmq_user, rabbitmq_password)
                connection = pika.BlockingConnection(
                    pika.ConnectionParameters(
                        host=rabbitmq_host,
                        port=rabbitmq_port,
                        credentials=credentials,
                        socket_timeout=5
                    )
                )
                connection.close()
                
                results['rabbitmq'] = {
                    'status': 'healthy',
                    'host': rabbitmq_host,
                    'port': rabbitmq_port
                }
            else:
                results['rabbitmq'] = {
                    'status': 'not_configured',
                    'message': 'RabbitMQ not configured'
                }
                
        except Exception as e:
            results['rabbitmq'] = {
                'status': 'error',
                'error': str(e)
            }
        
        return results
    
    def check_application_status(self) -> Dict[str, Any]:
        """Check general application health"""
        
        try:
            # Check if basic directories exist
            required_dirs = ['/app/data', '/app/logs']
            missing_dirs = []
            for dir_path in required_dirs:
                if not os.path.exists(dir_path):
                    missing_dirs.append(dir_path)
            
            # Check disk space in data directory
            data_disk_usage = None
            if os.path.exists('/app/data'):
                data_disk = psutil.disk_usage('/app/data')
                data_disk_usage = {
                    'total_gb': data_disk.total // (1024 * 1024 * 1024),
                    'used_gb': data_disk.used // (1024 * 1024 * 1024),
                    'free_gb': data_disk.free // (1024 * 1024 * 1024),
                    'percent_used': (data_disk.used / data_disk.total) * 100
                }
            
            # Check environment variables
            required_env_vars = ['POSTGRES_DB', 'POSTGRES_USER']
            missing_env_vars = [var for var in required_env_vars if not os.getenv(var)]
            
            status = 'healthy'
            if missing_dirs or missing_env_vars:
                status = 'warning'
            
            return {
                'status': status,
                'missing_directories': missing_dirs,
                'missing_env_vars': missing_env_vars,
                'data_disk_usage': data_disk_usage,
                'uptime_seconds': time.time() - psutil.boot_time()
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e)
            }
    
    def run_health_check(self) -> Dict[str, Any]:
        """Run comprehensive health check"""
        
        print("🔍 Running infrastructure health check...")
        
        # System resources
        print("  ⚙️  Checking system resources...")
        self.health_status['services']['system'] = self.check_system_resources()
        
        # Database connectivity
        print("  🗄️  Checking database connectivity...")
        self.health_status['services']['databases'] = self.check_database_connectivity()
        
        # Monitoring services
        print("  📊 Checking monitoring services...")
        self.health_status['services']['monitoring'] = self.check_monitoring_services()
        
        # Message queues
        print("  📨 Checking message queues...")
        self.health_status['services']['message_queues'] = self.check_message_queues()
        
        # Application status
        print("  🚀 Checking application status...")
        self.health_status['services']['application'] = self.check_application_status()
        
        # Generate overall status
        self._determine_overall_status()
        
        # Generate recommendations
        self._generate_recommendations()
        
        print("✅ Health check completed")
        
        return self.health_status
    
    def _determine_overall_status(self):
        """Determine overall system status"""
        
        error_count = 0
        warning_count = 0
        total_checks = 0
        
        for service_category, checks in self.health_status['services'].items():
            if isinstance(checks, dict):
                if 'status' in checks:
                    total_checks += 1
                    if checks['status'] == 'error':
                        error_count += 1
                    elif checks['status'] == 'warning':
                        warning_count += 1
                else:
                    # Handle nested service checks
                    for service_name, service_status in checks.items():
                        if isinstance(service_status, dict) and 'status' in service_status:
                            total_checks += 1
                            if service_status['status'] == 'error':
                                error_count += 1
                            elif service_status['status'] == 'warning':
                                warning_count += 1
        
        if error_count > 0:
            self.health_status['overall_status'] = 'critical'
        elif warning_count > 0:
            self.health_status['overall_status'] = 'warning'
        else:
            self.health_status['overall_status'] = 'healthy'
        
        self.health_status['metrics'] = {
            'total_checks': total_checks,
            'error_count': error_count,
            'warning_count': warning_count,
            'healthy_count': total_checks - error_count - warning_count
        }
    
    def _generate_recommendations(self):
        """Generate recommendations based on health check results"""
        
        recommendations = []
        
        # System resource recommendations
        system_status = self.health_status['services'].get('system', {})
        if system_status.get('cpu_percent', 0) > 80:
            recommendations.append("High CPU usage detected. Consider scaling up or optimizing processes.")
        
        if system_status.get('memory_percent', 0) > 90:
            recommendations.append("High memory usage detected. Consider increasing memory or optimizing memory usage.")
        
        if system_status.get('disk_percent', 0) > 90:
            recommendations.append("Low disk space detected. Consider cleaning up old files or expanding storage.")
        
        # Database recommendations
        db_status = self.health_status['services'].get('databases', {})
        for db_name, db_info in db_status.items():
            if db_info.get('status') == 'error':
                recommendations.append(f"{db_name.title()} database is not accessible. Check connection settings and service status.")
        
        # Monitoring recommendations
        monitoring_status = self.health_status['services'].get('monitoring', {})
        for service_name, service_info in monitoring_status.items():
            if service_info.get('status') == 'error':
                recommendations.append(f"{service_name.title()} monitoring service is not accessible. Check service configuration.")
        
        self.health_status['recommendations'] = recommendations


def main():
    """Main health check execution"""
    
    checker = InfrastructureHealthChecker()
    
    try:
        health_result = checker.run_health_check()
        
        # Print results
        print("\n" + "="*60)
        print("🏥 INFRASTRUCTURE HEALTH CHECK RESULTS")
        print("="*60)
        
        print(f"⏰ Timestamp: {health_result['timestamp']}")
        print(f"🎯 Overall Status: {health_result['overall_status'].upper()}")
        
        if health_result['metrics']:
            metrics = health_result['metrics']
            print(f"📊 Checks: {metrics['healthy_count']} healthy, {metrics['warning_count']} warnings, {metrics['error_count']} errors")
        
        # Print service details
        for category, services in health_result['services'].items():
            print(f"\n📋 {category.replace('_', ' ').title()}:")
            if isinstance(services, dict):
                if 'status' in services:
                    status_icon = "✅" if services['status'] == 'healthy' else "⚠️" if services['status'] == 'warning' else "❌"
                    print(f"  {status_icon} {services['status']}")
                else:
                    for service_name, service_info in services.items():
                        if isinstance(service_info, dict) and 'status' in service_info:
                            status_icon = "✅" if service_info['status'] == 'healthy' else "⚠️" if service_info['status'] == 'warning' else "❌"
                            print(f"  {status_icon} {service_name}: {service_info['status']}")
        
        # Print recommendations
        if health_result['recommendations']:
            print(f"\n💡 Recommendations:")
            for i, rec in enumerate(health_result['recommendations'], 1):
                print(f"  {i}. {rec}")
        
        print("\n" + "="*60)
        
        # Return appropriate exit code
        if health_result['overall_status'] == 'critical':
            sys.exit(1)
        elif health_result['overall_status'] == 'warning':
            sys.exit(2)
        else:
            sys.exit(0)
            
    except Exception as e:
        print(f"❌ Health check failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()