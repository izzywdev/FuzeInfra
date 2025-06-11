#!/usr/bin/env python3
"""
Setup script for Mendys Robot Scraper
Automated environment setup with virtual environment
"""

import os
import sys
import subprocess
import json
import zipfile
import io
import urllib.request
from pathlib import Path


def check_python_version():
    """Ensure Python 3.9+"""
    if sys.version_info < (3, 9):
        print("❌ Python 3.9+ required. Current version:", sys.version)
        return False
    return True


def create_virtual_environment():
    """Create and activate virtual environment"""
    venv_path = Path("venv")
    
    if venv_path.exists():
        print("✅ Virtual environment already exists")
        return True
    
    try:
        print("🔧 Creating virtual environment...")
        subprocess.run([sys.executable, "-m", "venv", "venv"], check=True)
        print("✅ Virtual environment created")
        return True
    except subprocess.CalledProcessError:
        print("❌ Failed to create virtual environment")
        return False


def install_requirements():
    """Install Python dependencies"""
    
    # Determine pip path based on OS
    if os.name == 'nt':  # Windows
        pip_path = Path("venv/Scripts/pip.exe")
        python_path = Path("venv/Scripts/python.exe")
    else:  # Unix/Linux/macOS
        pip_path = Path("venv/bin/pip")
        python_path = Path("venv/bin/python")
    
    if not pip_path.exists():
        print("❌ Virtual environment not properly created")
        return False
    
    try:
        print("📦 Installing dependencies...")
        subprocess.run([str(pip_path), "install", "-r", "requirements.txt"], check=True)
        print("✅ Dependencies installed")
        return True
    except subprocess.CalledProcessError:
        print("❌ Failed to install dependencies")
        return False


def setup_directories():
    """Create necessary directories"""
    directories = ['data', 'logs', 'data/images']
    
    for directory in directories:
        path = Path(directory)
        if not path.exists():
            path.mkdir(parents=True)
            print(f"📁 Created directory: {directory}")
    
    print("✅ Directories setup complete")


def create_env_template():
    """Create .env template file"""
    env_template = """# WordPress/WooCommerce Configuration
WP_SITE_URL=https://smarthubshopper.com
WP_USERNAME=your_username
WP_APP_PASSWORD=your_app_password
WP_API_BASE=/wp-json/wp/v2
WP_WC_API_BASE=/wp-json/wc/v3

# Database Configuration  
MONGODB_URI=mongodb://localhost:27017/robot_catalog
MONGODB_DATABASE=robot_catalog

# Logging Configuration
LOG_LEVEL=INFO
LOG_FILE=logs/robot_scraper.log

# Rate Limiting Configuration
CRAWL_DELAY=2.0
MAX_RETRIES=3
REQUEST_TIMEOUT=30

# AI Discovery Settings
MAX_SITES_PER_DISCOVERY=30
MAX_PRODUCTS_PER_SITE=50
"""
    
    env_path = Path(".env")
    if not env_path.exists():
        with open(env_path, 'w') as f:
            f.write(env_template)
        print("✅ Created .env template file")
        print("⚠️  Please edit .env with your WordPress credentials")
    else:
        print("✅ .env file already exists")


def main():
    """Main setup process"""
    print("🤖 Mendys Robot Scraper Setup")
    print("=" * 50)
    
    # Check Python version
    if not check_python_version():
        return False
    
    # Create virtual environment
    if not create_virtual_environment():
        return False
    
    # Install dependencies
    if not install_requirements():
        return False
    
    # Setup directories
    setup_directories()
    
    # Create environment template
    create_env_template()
    
    print("\n🎉 Setup Complete!")
    print("=" * 50)
    print("\n📋 Next Steps:")
    print("1. Edit .env file with your WordPress credentials")
    print("2. Activate virtual environment:")
    
    if os.name == 'nt':  # Windows
        print("   venv\\Scripts\\activate")
    else:  # Unix/Linux/macOS  
        print("   source venv/bin/activate")
    
    print("3. Test the connection:")
    print("   python main.py stats")
    print("4. Start scraping:")
    print("   python main.py ai-full-pipeline --sync-to-wordpress")
    
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1) 