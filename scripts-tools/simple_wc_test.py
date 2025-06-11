#!/usr/bin/env python3
"""
Simple WooCommerce API test
"""

import sys
import os
import requests

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.wordpress.wp_client import WordPressClient
from src.config import WORDPRESS_CONFIG


def test_woocommerce_api():
    """Simple test of WooCommerce API"""
    
    print("🧪 Simple WooCommerce API Test")
    print("=" * 40)
    
    try:
        # Test WordPress connection first
        wp_client = WordPressClient()
        
        if not wp_client.test_connection():
            print("❌ WordPress connection failed")
            return False
        
        print("✅ WordPress connected successfully")
        
        # Test WooCommerce products endpoint
        site_url = WORDPRESS_CONFIG['url']
        wc_products_url = f"{site_url}/wp-json/wc/v3/products"
        
        print(f"🔍 Testing: {wc_products_url}")
        
        # Use the same session with auth headers
        response = wp_client.session.get(wc_products_url)
        
        print(f"📡 Response: {response.status_code}")
        
        if response.status_code == 200:
            products = response.json()
            print(f"✅ WooCommerce API working! Found {len(products)} products")
            
            if products:
                print("📦 Sample products:")
                for product in products[:3]:
                    print(f"   • {product.get('name', 'Unknown')} (ID: {product.get('id')})")
            else:
                print("   (No products found - ready for import!)")
            
            return True
            
        elif response.status_code == 404:
            print("❌ WooCommerce API not found (404)")
            print("   Possible causes:")
            print("   • WooCommerce not installed/activated")
            print("   • Permalinks need refresh")
            return False
            
        elif response.status_code == 401:
            print("❌ Authentication failed (401)")
            return False
            
        elif response.status_code == 403:
            print("❌ Permission denied (403)")
            print("   Your user may not have WooCommerce permissions")
            return False
            
        else:
            print(f"❌ Unexpected response: {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def main():
    """Main function"""
    
    success = test_woocommerce_api()
    
    if success:
        print("\n🎉 WooCommerce API is working!")
        print("   Ready to test 3-product import:")
        print("   python test_sync_no_images.py")
    else:
        print("\n⚠️  WooCommerce API issues detected")
        print("   Check these steps:")
        print("   1. Go to: https://smarthubshopper.com/wp-admin/plugins.php")
        print("   2. Make sure WooCommerce is activated")
        print("   3. Go to: https://smarthubshopper.com/wp-admin/options-permalink.php")
        print("   4. Click 'Save Changes' to refresh permalinks")
    
    return success


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1) 