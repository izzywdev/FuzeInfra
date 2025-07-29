#!/usr/bin/env python3
"""
Non-interactive Environment Setup Script for CI/CD

This script creates a .env file with secure passwords for CI/CD environments.
"""

import os
import shutil
import secrets
import string
import base64
from pathlib import Path


def generate_secure_password(length=16):
    """Generate a secure random password."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def generate_secret_key(length=32):
    """Generate a secure secret key."""
    return secrets.token_urlsafe(length)


def generate_fernet_key():
    """Generate a Fernet key for Airflow encryption."""
    # Generate 32 random bytes and encode as base64
    key = secrets.token_bytes(32)
    return base64.urlsafe_b64encode(key).decode('utf-8')


def setup_environment_ci():
    """Setup environment configuration file for CI/CD."""
    project_root = Path(__file__).parent.parent
    template_file = project_root / "environment.template"
    env_file = project_root / ".env"
    
    # Check if template exists
    if not template_file.exists():
        print(f"❌ Template file not found: {template_file}")
        return False
    
    try:
        # Copy template to .env (overwrite if exists)
        shutil.copy2(template_file, env_file)
        print(f"✅ Created .env file from template")
        
        # Read the content
        with open(env_file, 'r') as f:
            content = f.read()
        
        # Generate secure passwords automatically
        print("🔄 Generating secure passwords...")
        
        replacements = {
            'your_secure_postgres_password': generate_secure_password(),
            'your_secure_mongodb_password': generate_secure_password(),
            'your_secure_admin_ui_password': generate_secure_password(),
            'your_secure_redis_password': generate_secure_password(),
            'your_secure_neo4j_password': generate_secure_password(),
            'your_secure_rabbitmq_password': generate_secure_password(),
            'your_secure_grafana_password': generate_secure_password(),
            'your_secure_airflow_password': generate_secure_password(),
            'your_secure_fernet_key_here': generate_fernet_key(),
            'your_jwt_secret_key_here_change_in_production': generate_secret_key(),
            'your_encryption_key_here_change_in_production': generate_secret_key()
        }
        
        for old_value, new_value in replacements.items():
            content = content.replace(old_value, new_value)
        
        # Write the updated content back
        with open(env_file, 'w') as f:
            f.write(content)
        
        print(f"✅ Environment file created successfully: {env_file}")
        return True
        
    except Exception as e:
        print(f"❌ Error setting up environment: {e}")
        return False


if __name__ == "__main__":
    import sys
    success = setup_environment_ci()
    sys.exit(0 if success else 1) 