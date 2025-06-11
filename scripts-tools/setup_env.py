#!/usr/bin/env python3
"""
Setup script to create .env file for WordPress credentials
"""

import os

def create_env_file():
    """Create .env file with WordPress credentials template"""
    
    env_content = """# WordPress Credentials for SmartHubShopper.com
# Get these from Google Password Manager

# Your WordPress site URL
WP_SITE_URL=https://smarthubshopper.com

# Your WordPress username (from Google Password Manager)
WP_USERNAME=your_username_here

# Your WordPress Application Password (from Google Password Manager)
# NOTE: This should be an Application Password, not your regular password
# Create one at: WordPress Admin → Users → Your Profile → Application Passwords
WP_APP_PASSWORD=your_app_password_here

# API endpoints (these should be correct)
WP_API_BASE=/wp-json/wp/v2
WP_WC_API_BASE=/wp-json/wc/v3
"""

    env_file_path = '.env'
    
    try:
        # Check if .env already exists
        if os.path.exists(env_file_path):
            response = input(f".env file already exists. Overwrite? (y/N): ")
            if response.lower() != 'y':
                print("❌ .env file creation cancelled")
                return False
        
        # Write the .env file
        with open(env_file_path, 'w', encoding='utf-8') as f:
            f.write(env_content)
        
        print("✅ .env file created successfully!")
        print("\n📝 Next steps:")
        print("1. Open Google Password Manager")
        print("2. Find your SmartHubShopper.com credentials") 
        print("3. Edit .env file and replace:")
        print("   - your_username_here → your actual WordPress username")
        print("   - your_app_password_here → your WordPress application password")
        print("\n🔑 WordPress Application Password Info:")
        print("   - NOT your regular login password")
        print("   - Format: 'xxxx xxxx xxxx xxxx' (with spaces)")
        print("   - Create at: WordPress Admin → Users → Profile → Application Passwords")
        print("\n🧪 Test after setup:")
        print("   python main.py sync --data-file data/test_robots_sample.json")
        
        return True
        
    except Exception as e:
        print(f"❌ Error creating .env file: {e}")
        return False


def validate_env_file():
    """Validate that .env file has the required credentials"""
    
    if not os.path.exists('.env'):
        print("❌ .env file not found")
        return False
    
    try:
        # Load environment variables from .env
        with open('.env', 'r', encoding='utf-8') as f:
            env_content = f.read()
        
        # Check for required variables
        required_vars = ['WP_SITE_URL', 'WP_USERNAME', 'WP_APP_PASSWORD']
        missing_vars = []
        placeholder_vars = []
        
        for var in required_vars:
            if f"{var}=" not in env_content:
                missing_vars.append(var)
            elif f"{var}=your_" in env_content or f"{var}=https://example.com" in env_content:
                placeholder_vars.append(var)
        
        if missing_vars:
            print(f"❌ Missing variables in .env: {', '.join(missing_vars)}")
            return False
        
        if placeholder_vars:
            print(f"⚠️  Placeholder values found in .env: {', '.join(placeholder_vars)}")
            print("   Please update these with your actual credentials from Google Password Manager")
            return False
        
        print("✅ .env file looks good!")
        return True
        
    except Exception as e:
        print(f"❌ Error validating .env file: {e}")
        return False


def main():
    """Main function"""
    print("🔧 WordPress Credentials Setup for Mendys FANUC Scraper")
    print("=" * 60)
    
    # Create .env file
    if create_env_file():
        print("\n" + "=" * 60)
        print("🔍 Validating .env file...")
        validate_env_file()


if __name__ == '__main__':
    main() 