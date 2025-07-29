#!/usr/bin/env python3
"""
Airflow Database Initialization Script

This script creates the Airflow database in PostgreSQL if it doesn't exist.
It should be run after PostgreSQL is started but before Airflow services.
"""

import os
import sys
import time
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from pathlib import Path


def load_env_file():
    """Load environment variables from .env file."""
    env_file = Path(__file__).parent.parent / ".env"
    if not env_file.exists():
        print("❌ .env file not found. Please run setup_environment.py first.")
        return False
    
    env_vars = {}
    with open(env_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env_vars[key] = value
    
    # Set environment variables
    for key, value in env_vars.items():
        os.environ[key] = value
    
    return True


def wait_for_postgres(host, port, user, password, max_attempts=30):
    """Wait for PostgreSQL to be ready."""
    print(f"⏳ Waiting for PostgreSQL at {host}:{port}...")
    
    for attempt in range(max_attempts):
        try:
            conn = psycopg2.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database='postgres'  # Connect to default database
            )
            conn.close()
            print("✅ PostgreSQL is ready!")
            return True
        except psycopg2.OperationalError:
            if attempt < max_attempts - 1:
                print(f"⏳ Attempt {attempt + 1}/{max_attempts} - PostgreSQL not ready, waiting...")
                time.sleep(2)
            else:
                print("❌ PostgreSQL is not responding after maximum attempts")
                return False
    
    return False


def create_airflow_database():
    """Create the Airflow database if it doesn't exist."""
    # Get database connection details from environment
    host = os.getenv('POSTGRES_HOST', 'localhost')
    port = os.getenv('POSTGRES_PORT', '5432')
    user = os.getenv('POSTGRES_USER', 'postgres')
    password = os.getenv('POSTGRES_PASSWORD')
    
    if not password:
        print("❌ POSTGRES_PASSWORD not found in environment variables")
        return False
    
    # Wait for PostgreSQL to be ready
    if not wait_for_postgres(host, port, user, password):
        return False
    
    try:
        # Connect to PostgreSQL
        print("🔗 Connecting to PostgreSQL...")
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database='postgres'  # Connect to default database
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        # Check if airflow database exists
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = 'airflow'")
        exists = cursor.fetchone()
        
        if exists:
            print("✅ Airflow database already exists")
        else:
            print("🏗️  Creating Airflow database...")
            cursor.execute("CREATE DATABASE airflow")
            print("✅ Airflow database created successfully")
        
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Error creating Airflow database: {e}")
        return False


def main():
    """Main function."""
    print("🚀 Airflow Database Initialization")
    print("=" * 40)
    
    # Load environment variables
    if not load_env_file():
        sys.exit(1)
    
    # Create Airflow database
    if create_airflow_database():
        print("\n✅ Airflow database initialization completed successfully!")
        print("📋 Next steps:")
        print("1. Start Airflow services with docker-compose")
        print("2. Access Airflow UI at http://localhost:8082")
        sys.exit(0)
    else:
        print("\n❌ Airflow database initialization failed!")
        sys.exit(1)


if __name__ == "__main__":
    main() 