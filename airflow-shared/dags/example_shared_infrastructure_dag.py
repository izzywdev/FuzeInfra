"""
Example DAG: Shared Infrastructure Demo

This DAG demonstrates how projects can use the shared infrastructure services
provided by FuzeInfra platform.

Author: FuzeInfra Platform
Tags: example, infrastructure, demo
"""

from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator


# Default arguments for the DAG
default_args = {
    'owner': 'fuzeinfra',
    'depends_on_past': False,
    'start_date': datetime(2024, 1, 1),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

# Create the DAG
dag = DAG(
    'example_shared_infrastructure_demo',
    default_args=default_args,
    description='Demonstrates usage of shared infrastructure services',
    schedule_interval=None,  # Manual trigger only
    catchup=False,
    tags=['example', 'infrastructure', 'demo'],
)


def test_postgresql_connection():
    """Test PostgreSQL connection and basic operations."""
    import psycopg2
    
    try:
        # Connect to PostgreSQL (use Airflow connection or environment variables)
        conn = psycopg2.connect(
            host='postgres',
            port=5432,
            database='postgres',  # or your project database
            user='postgres',      # use your configured user
            password='postgres'   # use your configured password
        )
        
        cursor = conn.cursor()
        cursor.execute("SELECT version();")
        version = cursor.fetchone()
        print(f"✅ PostgreSQL connection successful: {version[0]}")
        
        # Create a test table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS airflow_test (
                id SERIAL PRIMARY KEY,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Insert test data
        cursor.execute(
            "INSERT INTO airflow_test (message) VALUES (%s)",
            ("Hello from Airflow DAG!",)
        )
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print("✅ PostgreSQL operations completed successfully")
        
    except Exception as e:
        print(f"❌ PostgreSQL connection failed: {e}")
        raise


def test_redis_connection():
    """Test Redis connection and basic operations."""
    import redis
    
    try:
        # Connect to Redis
        r = redis.Redis(host='redis', port=6379, decode_responses=True)
        
        # Test connection
        if r.ping():
            print("✅ Redis connection successful")
        
        # Set and get a value
        r.set('airflow_test_key', 'Hello from Airflow DAG!')
        value = r.get('airflow_test_key')
        print(f"✅ Redis operations successful: {value}")
        
        # Clean up
        r.delete('airflow_test_key')
        
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        raise


def test_mongodb_connection():
    """Test MongoDB connection and basic operations."""
    import pymongo
    
    try:
        # Connect to MongoDB
        client = pymongo.MongoClient('mongodb://admin:admin123@mongodb:27017/')
        
        # Test connection
        server_info = client.server_info()
        print(f"✅ MongoDB connection successful: {server_info['version']}")
        
        # Use test database
        db = client.airflow_test_db
        collection = db.test_collection
        
        # Insert test document
        doc = {
            'message': 'Hello from Airflow DAG!',
            'timestamp': datetime.now(),
            'source': 'shared_infrastructure_demo'
        }
        result = collection.insert_one(doc)
        print(f"✅ MongoDB insert successful: {result.inserted_id}")
        
        # Clean up
        collection.delete_one({'_id': result.inserted_id})
        client.close()
        
    except Exception as e:
        print(f"❌ MongoDB connection failed: {e}")
        raise


def test_elasticsearch_connection():
    """Test Elasticsearch connection and basic operations."""
    from elasticsearch import Elasticsearch
    
    try:
        # Connect to Elasticsearch
        es = Elasticsearch([{'host': 'elasticsearch', 'port': 9200, 'scheme': 'http'}])
        
        # Test connection
        if es.ping():
            print("✅ Elasticsearch connection successful")
        
        # Index a test document
        doc = {
            'message': 'Hello from Airflow DAG!',
            'timestamp': datetime.now(),
            'source': 'shared_infrastructure_demo'
        }
        
        response = es.index(index='airflow-test', body=doc)
        print(f"✅ Elasticsearch indexing successful: {response['result']}")
        
        # Clean up
        es.indices.delete(index='airflow-test', ignore=[400, 404])
        
    except Exception as e:
        print(f"❌ Elasticsearch connection failed: {e}")
        raise


# Define tasks
test_postgres_task = PythonOperator(
    task_id='test_postgresql_connection',
    python_callable=test_postgresql_connection,
    dag=dag,
)

test_redis_task = PythonOperator(
    task_id='test_redis_connection',
    python_callable=test_redis_connection,
    dag=dag,
)

test_mongodb_task = PythonOperator(
    task_id='test_mongodb_connection',
    python_callable=test_mongodb_connection,
    dag=dag,
)

test_elasticsearch_task = PythonOperator(
    task_id='test_elasticsearch_connection',
    python_callable=test_elasticsearch_connection,
    dag=dag,
)

# Test system commands
system_info_task = BashOperator(
    task_id='get_system_info',
    bash_command='''
    echo "=== System Information ==="
    echo "Hostname: $(hostname)"
    echo "Date: $(date)"
    echo "Disk usage:"
    df -h
    echo "Memory usage:"
    free -h
    echo "=== Docker Network ==="
    echo "Network interfaces:"
    ip addr show | grep -E "inet|docker|eth"
    ''',
    dag=dag,
)

# Set task dependencies
# All database tests can run in parallel
[test_postgres_task, test_redis_task, test_mongodb_task, test_elasticsearch_task] >> system_info_task 