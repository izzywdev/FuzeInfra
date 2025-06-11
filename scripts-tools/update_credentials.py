#!/usr/bin/env python3
"""
Script to update .env file with actual WordPress credentials
"""

import os
import re

def update_env_credentials():
    """Update .env file with actual credentials"""
    
    # The application password provided by the user
    app_password = "xvFn YFgu aFM0 Mroc xBzT m1fV"
    
    # Ask for username
    print("🔧 Updating WordPress credentials in .env file")
    print("=" * 50)
    
    username = input("Enter your WordPress username: ").strip()
    
    if not username:
        print("❌ Username cannot be empty")
        return False
    
    # Read current .env file
    env_file = '.env'
    if not os.path.exists(env_file):
        print("❌ .env file not found")
        return False
    
    try:
        with open(env_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Update username and password
        content = re.sub(r'WP_USERNAME=.*', f'WP_USERNAME={username}', content)
        content = re.sub(r'WP_APP_PASSWORD=.*', f'WP_APP_PASSWORD={app_password}', content)
        
        # Write updated content
        with open(env_file, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"✅ Updated .env file with:")
        print(f"   Username: {username}")
        print(f"   App Password: {'*' * len(app_password)}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error updating .env file: {e}")
        return False

def test_connection():
    """Test WordPress connection"""
    print("\n🧪 Testing WordPress connection...")
    
    # Import here to avoid circular imports
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
    
    try:
        from src.wordpress.wp_client import WordPressClient
        
        wp_client = WordPressClient()
        
        if wp_client.test_connection():
            print("✅ WordPress connection successful!")
            return True
        else:
            print("❌ WordPress connection failed!")
            return False
            
    except Exception as e:
        print(f"❌ Error testing connection: {e}")
        return False

def main():
    """Main function"""
    if update_env_credentials():
        if test_connection():
            print("\n🎉 All set! You can now run:")
            print("   python main.py sync --data-file data/test_robots_sample.json")
        else:
            print("\n⚠️  Credentials updated but connection test failed.")
            print("   Please check your username and try again.")

if __name__ == '__main__':
    main() 