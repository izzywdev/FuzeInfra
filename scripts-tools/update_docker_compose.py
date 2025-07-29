#!/usr/bin/env python3
"""
Docker Compose Update Script for FuzeInfra
Updates container names and volume references to use consistent fuzeinfra naming
"""

import re

def update_docker_compose():
    """Update the docker-compose file with consistent naming"""
    
    # Read the current file
    with open('docker-compose.FuzeInfra.yml', 'r') as f:
        content = f.read()
    
    print("🔄 Updating Docker Compose file...")
    
    # Container name replacements
    container_replacements = {
        'shared-postgres': 'fuzeinfra-postgres',
        'shared-mongodb': 'fuzeinfra-mongodb', 
        'shared-mongo-express': 'fuzeinfra-mongo-express',
        'shared-redis': 'fuzeinfra-redis',
        'shared-neo4j': 'fuzeinfra-neo4j',
        'shared-elasticsearch': 'fuzeinfra-elasticsearch',
        'shared-zookeeper': 'fuzeinfra-zookeeper',
        'shared-kafka': 'fuzeinfra-kafka',
        'shared-kafka-ui': 'fuzeinfra-kafka-ui',
        'shared-rabbitmq': 'fuzeinfra-rabbitmq',
        'shared-prometheus': 'fuzeinfra-prometheus',
        'shared-node-exporter': 'fuzeinfra-node-exporter',
        'shared-alertmanager': 'fuzeinfra-alertmanager',
        'shared-grafana': 'fuzeinfra-grafana',
        'shared-loki': 'fuzeinfra-loki',
        'shared-promtail': 'fuzeinfra-promtail',
        'shared-airflow-init': 'fuzeinfra-airflow-init',
        'shared-airflow-webserver': 'fuzeinfra-airflow-webserver',
        'shared-airflow-scheduler': 'fuzeinfra-airflow-scheduler',
        'shared-airflow-worker': 'fuzeinfra-airflow-worker',
        'shared-airflow-flower': 'fuzeinfra-airflow-flower'
    }
    
    # Volume name replacements
    volume_replacements = {
        'shared-postgres-data': 'fuzeinfra_postgres_data',
        'shared-mongodb-data': 'fuzeinfra_mongodb_data',
        'shared-redis-data': 'fuzeinfra_redis_data',
        'shared-neo4j-data': 'fuzeinfra_neo4j_data',
        'shared-neo4j-logs': 'fuzeinfra_neo4j_logs',
        'shared-elasticsearch-data': 'fuzeinfra_elasticsearch_data',
        'shared-kafka-data': 'fuzeinfra_kafka_data',
        'shared-zookeeper-data': 'fuzeinfra_zookeeper_data',
        'shared-zookeeper-logs': 'fuzeinfra_zookeeper_logs',
        'shared-rabbitmq-data': 'fuzeinfra_rabbitmq_data',
        'shared-prometheus-data': 'fuzeinfra_prometheus_data',
        'shared-grafana-data': 'fuzeinfra_grafana_data',
        'shared-alertmanager-data': 'fuzeinfra_alertmanager_data',
        'shared-loki-data': 'fuzeinfra_loki_data'
    }
    
    # Apply container name replacements
    for old_name, new_name in container_replacements.items():
        content = content.replace(f'container_name: {old_name}', f'container_name: {new_name}')
        # Also update references in environment variables and URLs
        content = content.replace(f'{old_name}:', f'{new_name}:')
        content = content.replace(f'//{old_name}', f'//{new_name}')
        print(f"   ✅ Updated container: {old_name} -> {new_name}")
    
    # Apply volume name replacements
    for old_vol, new_vol in volume_replacements.items():
        content = content.replace(f'name: {old_vol}', f'name: {new_vol}')
        print(f"   ✅ Updated volume: {old_vol} -> {new_vol}")
    
    # Write the updated content back
    with open('docker-compose.FuzeInfra.yml', 'w') as f:
        f.write(content)
    
    print("\n🎉 Docker Compose file updated successfully!")
    print("   All containers will now use 'fuzeinfra-' prefix")
    print("   All volumes will now use 'fuzeinfra_' prefix")
    print("   This will group all containers under 'FuzeInfra' in Docker Desktop")

if __name__ == "__main__":
    update_docker_compose() 