#!/usr/bin/env python3
"""
Script to check WooCommerce REST API availability and help enable it
"""

import requests
import sys
import os
import json
from typing import Dict, Any

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.wordpress.wp_client import WordPressClient
from src.utils.logger import get_logger
from src.config import WORDPRESS_CONFIG


def check_api_endpoints():
    """Check what WordPress REST API endpoints are available"""
    
    logger = get_logger('api_check')
    site_url = WORDPRESS_CONFIG['url']
    
    print("🔍 Checking WordPress REST API endpoints...")
    print("=" * 60)
    
    # Test basic WordPress REST API
    try:
        wp_api_url = f"{site_url}/wp-json/wp/v2/"
        response = requests.get(wp_api_url, timeout=10)
        
        if response.status_code == 200:
            print("✅ WordPress REST API is available")
            api_data = response.json()
            
            # Check available endpoints
            endpoints = list(api_data.get('routes', {}).keys())
            print(f"📋 Found {len(endpoints)} WordPress endpoints")
            
            # Look for WooCommerce specific endpoints
            wc_endpoints = [ep for ep in endpoints if 'wc/' in ep or 'products' in ep]
            if wc_endpoints:
                print(f"🛒 Found {len(wc_endpoints)} WooCommerce-related endpoints:")
                for ep in wc_endpoints[:10]:  # Show first 10
                    print(f"   • {ep}")
            else:
                print("❌ No WooCommerce endpoints found in WordPress API")
        else:
            print(f"❌ WordPress REST API error: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Error checking WordPress API: {e}")
        return False
    
    # Test WooCommerce REST API directly
    print("\n🛒 Checking WooCommerce REST API...")
    try:
        wc_api_url = f"{site_url}/wp-json/wc/v3/"
        response = requests.get(wc_api_url, timeout=10)
        
        if response.status_code == 200:
            print("✅ WooCommerce REST API is available")
            api_data = response.json()
            
            # Check available routes
            routes = list(api_data.get('routes', {}).keys())
            print(f"📋 Found {len(routes)} WooCommerce endpoints")
            
            # Look for products endpoint specifically
            products_endpoints = [r for r in routes if 'products' in r]
            if products_endpoints:
                print("✅ Products endpoints found:")
                for ep in products_endpoints[:5]:
                    print(f"   • {ep}")
            else:
                print("❌ No products endpoints found")
                
        elif response.status_code == 404:
            print("❌ WooCommerce REST API not available (404)")
            print("   This usually means:")
            print("   1. WooCommerce plugin is not active")
            print("   2. WooCommerce REST API is disabled")
            print("   3. Permalink structure needs to be refreshed")
            return False
        else:
            print(f"❌ WooCommerce API error: {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False
            
    except Exception as e:
        print(f"❌ Error checking WooCommerce API: {e}")
        return False
    
    return True


def test_authenticated_wc_api():
    """Test WooCommerce API with authentication"""
    
    print("\n🔐 Testing authenticated WooCommerce API access...")
    
    try:
        wp_client = WordPressClient()
        
        # Test basic auth
        if not wp_client.test_connection():
            print("❌ WordPress authentication failed")
            return False
        
        print("✅ WordPress authentication successful")
        
        # Test WooCommerce products endpoint with auth
        site_url = WORDPRESS_CONFIG['url']
        products_url = f"{site_url}/wp-json/wc/v3/products"
        
        auth_header = wp_client._get_auth_header()
        headers = {
            'Authorization': auth_header,
            'Content-Type': 'application/json'
        }
        
        response = requests.get(products_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            products = response.json()
            print(f"✅ WooCommerce products API working! Found {len(products)} products")
            
            # Show existing products
            if products:
                print("📦 Existing products:")
                for product in products[:3]:
                    print(f"   • {product.get('name', 'Unknown')} (ID: {product.get('id')})")
            else:
                print("   (No products found - this is normal for a fresh install)")
                
            return True
            
        elif response.status_code == 401:
            print("❌ Authentication failed for WooCommerce API")
            print("   Check your WordPress application password")
            return False
            
        elif response.status_code == 403:
            print("❌ Permission denied for WooCommerce API")
            print("   Your user account may not have WooCommerce permissions")
            return False
            
        elif response.status_code == 404:
            print("❌ WooCommerce products endpoint not found")
            print("   WooCommerce REST API may be disabled")
            return False
            
        else:
            print(f"❌ WooCommerce API error: {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False
            
    except Exception as e:
        print(f"❌ Error testing authenticated WooCommerce API: {e}")
        return False


def check_woocommerce_status():
    """Check WooCommerce plugin status via WordPress API"""
    
    print("\n🔌 Checking WooCommerce plugin status...")
    
    try:
        wp_client = WordPressClient()
        site_url = WORDPRESS_CONFIG['url']
        
        # Check plugins endpoint
        plugins_url = f"{site_url}/wp-json/wp/v2/plugins"
        auth_header = wp_client._get_auth_header()
        headers = {
            'Authorization': auth_header,
            'Content-Type': 'application/json'
        }
        
        response = requests.get(plugins_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            plugins = response.json()
            
            # Look for WooCommerce
            wc_plugin = None
            for plugin in plugins:
                if 'woocommerce' in plugin.get('plugin', '').lower():
                    wc_plugin = plugin
                    break
            
            if wc_plugin:
                status = wc_plugin.get('status', 'unknown')
                print(f"✅ WooCommerce plugin found: {wc_plugin.get('name', 'WooCommerce')}")
                print(f"   Status: {status}")
                print(f"   Version: {wc_plugin.get('version', 'unknown')}")
                
                if status == 'active':
                    print("✅ WooCommerce is active")
                    return True
                else:
                    print("❌ WooCommerce is not active")
                    return False
            else:
                print("❌ WooCommerce plugin not found")
                return False
                
        else:
            print(f"❌ Cannot check plugins: {response.status_code}")
            print("   (This is normal - plugins endpoint requires special permissions)")
            return None
            
    except Exception as e:
        print(f"⚠️  Cannot check plugin status: {e}")
        return None


def provide_solutions():
    """Provide solutions for enabling WooCommerce API"""
    
    print("\n🛠️  SOLUTIONS TO ENABLE WOOCOMMERCE REST API")
    print("=" * 60)
    
    site_url = WORDPRESS_CONFIG['url']
    
    print("1. **Check WooCommerce Plugin:**")
    print(f"   • Go to: {site_url}/wp-admin/plugins.php")
    print("   • Make sure WooCommerce is installed and activated")
    print()
    
    print("2. **Enable WooCommerce REST API:**")
    print(f"   • Go to: {site_url}/wp-admin/admin.php?page=wc-settings&tab=advanced&section=rest_api")
    print("   • Make sure REST API is enabled")
    print("   • Create API keys if needed")
    print()
    
    print("3. **Refresh Permalinks:**")
    print(f"   • Go to: {site_url}/wp-admin/options-permalink.php")
    print("   • Click 'Save Changes' (this refreshes URL structure)")
    print()
    
    print("4. **Check User Permissions:**")
    print(f"   • Go to: {site_url}/wp-admin/user-edit.php")
    print("   • Make sure your user has 'Administrator' role")
    print("   • Check that you can access WooCommerce settings")
    print()
    
    print("5. **Test Again:**")
    print("   • After making changes, run: python check_woocommerce_api.py")


def main():
    """Main function"""
    
    print("🔍 WooCommerce REST API Diagnostic Tool")
    print("=" * 60)
    
    # Step 1: Check basic API availability
    api_available = check_api_endpoints()
    
    # Step 2: Check plugin status
    plugin_status = check_woocommerce_status()
    
    # Step 3: Test authenticated access
    auth_success = test_authenticated_wc_api()
    
    # Summary and recommendations
    print("\n📋 DIAGNOSTIC SUMMARY")
    print("=" * 60)
    
    if auth_success:
        print("🎉 WooCommerce REST API is working correctly!")
        print("   You can now run: python test_sync_no_images.py")
        return True
    else:
        print("❌ WooCommerce REST API issues detected")
        
        if not api_available:
            print("   • WordPress REST API is not working")
        elif plugin_status is False:
            print("   • WooCommerce plugin is not active")
        elif plugin_status is None:
            print("   • Cannot determine WooCommerce status")
        
        provide_solutions()
        return False


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1) 