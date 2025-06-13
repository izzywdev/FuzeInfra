#!/usr/bin/env python3
"""
FuzeInfra Test Runner

This script runs the complete infrastructure test suite locally.
It starts the infrastructure, waits for services to be ready, runs tests, and cleans up.
"""

import subprocess
import sys
import time
import requests
import os
from pathlib import Path


def run_command(cmd, check=True, shell=True):
    """Run a shell command and return the result."""
    print(f"Running: {cmd}")
    try:
        result = subprocess.run(cmd, shell=shell, check=check, capture_output=True, text=True)
        if result.stdout:
            print(result.stdout)
        return result
    except subprocess.CalledProcessError as e:
        print(f"Command failed: {e}")
        if e.stderr:
            print(f"Error: {e.stderr}")
        if check:
            sys.exit(1)
        return e


def check_service_health(url, timeout=300):
    """Check if a service is healthy by making HTTP requests."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                print(f"✓ Service at {url} is healthy")
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(5)
    
    print(f"✗ Service at {url} failed to become healthy within {timeout} seconds")
    return False


def main():
    """Main test runner function."""
    print("🚀 FuzeInfra Test Runner")
    print("=" * 50)
    
    # Change to project root directory
    project_root = Path(__file__).parent.parent
    os.chdir(project_root)
    
    # Check if Docker is running
    print("📋 Checking Docker...")
    result = run_command("docker --version", check=False)
    if result.returncode != 0:
        print("❌ Docker is not available. Please install and start Docker.")
        sys.exit(1)
    
    # Check if docker-compose is available
    result = run_command("docker-compose --version", check=False)
    if result.returncode != 0:
        print("❌ docker-compose is not available. Please install docker-compose.")
        sys.exit(1)
    
    # Install test dependencies
    print("📦 Installing test dependencies...")
    run_command("pip install -r tests/requirements.txt")
    
    # Create Docker network
    print("🌐 Creating Docker network...")
    run_command("docker network create FuzeInfra", check=False)
    
    # Check if .env file exists
    if not os.path.exists(".env"):
        print("⚙️  Creating environment file...")
        run_command("python scripts-tools/setup_environment.py")
    
    try:
        # Start infrastructure services
        print("🏗️  Starting infrastructure services...")
        run_command("docker-compose -f docker-compose.FuzeInfra.yml up -d")
        
        # Wait for services to start
        print("⏳ Waiting for services to start (60 seconds)...")
        time.sleep(60)
        
        # Check service health
        print("🔍 Checking service health...")
        services_to_check = [
            "http://localhost:9090/-/ready",  # Prometheus
            "http://localhost:3001/api/health",  # Grafana
            "http://localhost:9093/-/ready",  # Alertmanager
            "http://localhost:3100/ready",  # Loki
            "http://localhost:9200",  # Elasticsearch
        ]
        
        all_healthy = True
        for service_url in services_to_check:
            if not check_service_health(service_url):
                all_healthy = False
        
        if not all_healthy:
            print("⚠️  Some services are not healthy, but continuing with tests...")
        
        # Run tests
        print("🧪 Running infrastructure tests...")
        test_result = run_command("pytest tests/ -v --tb=short --color=yes", check=False)
        
        if test_result.returncode == 0:
            print("✅ All tests passed!")
        else:
            print("❌ Some tests failed!")
            
    except KeyboardInterrupt:
        print("\n🛑 Test run interrupted by user")
        
    finally:
        # Cleanup
        print("🧹 Cleaning up...")
        run_command("docker-compose -f docker-compose.FuzeInfra.yml down -v", check=False)
        run_command("docker network rm FuzeInfra", check=False)
        
        # Show final status
        if 'test_result' in locals() and test_result.returncode == 0:
            print("🎉 Test run completed successfully!")
            sys.exit(0)
        else:
            print("💥 Test run completed with failures!")
            sys.exit(1)


if __name__ == "__main__":
    main() 