#!/usr/bin/env python3
"""
Environment Setup Script for FuzeInfra Platform

This script helps users create and configure their .env file
from the environment.template file.
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


def setup_environment(non_interactive=False):
    """Setup environment configuration file."""
    if not non_interactive:
        print("🔧 FuzeInfra Platform Environment Setup")
        print("=" * 50)
    
    project_root = Path(__file__).parent.parent
    template_file = project_root / "environment.template"
    env_file = project_root / ".env"
    
    # Check if template exists
    if not template_file.exists():
        print(f"❌ Template file not found: {template_file}")
        return False
    
    # Check if .env already exists
    if env_file.exists() and not non_interactive:
        response = input("⚠️  .env file already exists. Overwrite? (y/N): ")
        if response.lower() != 'y':
            print("✅ Setup cancelled. Existing .env file preserved.")
            return True
    
    try:
        # Copy template to .env
        shutil.copy2(template_file, env_file)
        if not non_interactive:
            print(f"✅ Created .env file from template")
        
        # Read the content
        with open(env_file, 'r') as f:
            content = f.read()
        
        # Ask user if they want to generate secure passwords (or auto-generate in non-interactive mode)
        if non_interactive:
            generate_passwords_flag = True
        else:
            generate_passwords = input("\n🔐 Generate secure passwords for services? (Y/n): ")
            generate_passwords_flag = generate_passwords.lower() != 'n'
            
        if generate_passwords_flag:
            if not non_interactive:
                print("🔄 Generating secure passwords...")
            
            # Replace default passwords with secure ones
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
        
        # Project customization (use defaults in non-interactive mode)
        if not non_interactive:
            print("\n📝 Project Customization")
            project_name = input("Enter project name (default: fuzeinfra): ").strip()
            if project_name:
                content = content.replace('COMPOSE_PROJECT_NAME=fuzeinfra', f'COMPOSE_PROJECT_NAME={project_name}')
            
            # Ask for database name
            db_name = input("Enter database name (default: fuzeinfra_db): ").strip()
            if db_name:
                content = content.replace('POSTGRES_DB=fuzeinfra_db', f'POSTGRES_DB={db_name}')
        
        # Write the updated content back
        with open(env_file, 'w') as f:
            f.write(content)
        
        if not non_interactive:
            print(f"\n✅ Environment file created successfully: {env_file}")
            print("\n📋 Next Steps:")
            print("1. Review and customize the .env file as needed")
            print("2. Start the infrastructure: ./infra-up.sh (Linux/Mac) or ./infra-up.bat (Windows)")
            print("3. Access services using the URLs in the .env file")
            
            print("\n⚠️  SECURITY REMINDER:")
            print("- Never commit the .env file to version control")
            print("- Keep your passwords secure")
            print("- Change default passwords in production")
        
        return True
        
    except Exception as e:
        print(f"❌ Error setting up environment: {e}")
        return False


def show_help():
    """Show help information."""
    print("Environment Setup Script for FuzeInfra Platform")
    print("")
    print("Usage:")
    print("  python setup_environment.py")
    print("")
    print("This script will:")
    print("1. Copy environment.template to .env")
    print("2. Generate secure passwords for services")
    print("3. Allow customization of project settings")
    print("")


def main():
    """Main function."""
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] in ['--help', '-h']:
        show_help()
        return
    
    # Check for non-interactive flag
    non_interactive = '--non-interactive' in sys.argv
    
    success = setup_environment(non_interactive=non_interactive)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main() 