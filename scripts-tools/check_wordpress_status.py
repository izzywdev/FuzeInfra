#!/usr/bin/env python3
"""
WordPress Status Check: What FANUC robots are already in WordPress?
"""

import json
import sys
import os
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.wordpress.wp_client import WordPressClient
from src.utils.logger import get_logger


def main():
    print("🔍 WORDPRESS FANUC ROBOT STATUS CHECK")
    print("=" * 60)
    
    # Initialize client
    wp_client = WordPressClient()
    
    # Test connection
    print("🔗 Testing WordPress connection...")
    if not wp_client.test_connection():
        print("❌ Failed to connect to WordPress")
        return False
    
    print("✅ WordPress connection successful!")
    
    # Get all products
    print("\n📦 Fetching all products from WordPress...")
    
    try:
        # Get products in batches
        all_products = []
        page = 1
        per_page = 50
        
        while True:
            products = wp_client.get_products(per_page, page)
            if not products:
                break
            
            print(f"   Fetched page {page}: {len(products)} products")
            all_products.extend(products)
            
            # Break if we got less than per_page (last page)
            if len(products) < per_page:
                break
            
            page += 1
            
            # Safety break
            if page > 20:
                print("   Safety break at page 20")
                break
        
        print(f"\n📊 Total products found: {len(all_products)}")
        
        # Filter FANUC robot products
        fanuc_products = []
        for product in all_products:
            sku = product.get('sku', '')
            name = product.get('name', '')
            
            if 'FANUC' in sku.upper() or 'RB-FANUC' in sku.upper() or any(fanuc_term in name.upper() for fanuc_term in ['FANUC', 'LR MATE', 'CRX-']):
                fanuc_products.append(product)
        
        print(f"🤖 FANUC robot products found: {len(fanuc_products)}")
        
        if fanuc_products:
            print(f"\n📋 FANUC PRODUCTS IN WORDPRESS:")
            print("-" * 60)
            
            for i, product in enumerate(fanuc_products, 1):
                print(f"{i}. **{product.get('name', 'No Name')}**")
                print(f"   ID: {product.get('id')}")
                print(f"   SKU: {product.get('sku', 'No SKU')}")
                print(f"   Status: {product.get('status', 'unknown')}")
                print(f"   Created: {product.get('date_created', 'unknown')}")
                
                # Check for meta data
                meta_data = product.get('meta_data', [])
                robot_meta = [m for m in meta_data if m.get('key', '').startswith('robot_')]
                print(f"   Robot Meta Fields: {len(robot_meta)}")
                
                # Check for images
                images = product.get('images', [])
                print(f"   Product Images: {len(images)}")
                
                print()
        
        # Check for our specific test robots
        print(f"\n🎯 CHECKING FOR OUR TEST ROBOTS:")
        print("-" * 40)
        
        test_skus = [
            'RB-FANUC-LR-200ID-7L',
            'RB-FANUC-M-10ID-16S', 
            'RB-FANUC-CRX-10IA-L'
        ]
        
        for sku in test_skus:
            found_product = None
            for product in all_products:
                if product.get('sku', '') == sku:
                    found_product = product
                    break
            
            if found_product:
                print(f"✅ {sku}")
                print(f"   Name: {found_product.get('name')}")
                print(f"   ID: {found_product.get('id')}")
                print(f"   Status: {found_product.get('status')}")
                
                # Count meta fields
                meta_data = found_product.get('meta_data', [])
                robot_meta = [m for m in meta_data if m.get('key', '').startswith('robot_')]
                print(f"   Meta fields: {len(robot_meta)}")
                
                # Show some meta fields
                if robot_meta:
                    print("   Sample meta:")
                    for meta in robot_meta[:3]:
                        print(f"     {meta.get('key')}: {meta.get('value', '')[:50]}...")
                
            else:
                print(f"❌ {sku} - NOT FOUND")
            
            print()
        
        # Save detailed report
        report = {
            'timestamp': datetime.now().isoformat(),
            'total_products': len(all_products),
            'fanuc_products_count': len(fanuc_products),
            'fanuc_products': fanuc_products,
            'test_robots_status': []
        }
        
        for sku in test_skus:
            found = any(p.get('sku') == sku for p in all_products)
            product_data = next((p for p in all_products if p.get('sku') == sku), None)
            
            report['test_robots_status'].append({
                'sku': sku,
                'found': found,
                'product_data': product_data
            })
        
        # Save report
        report_file = f"data/wordpress_status_check_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        print(f"💾 Detailed report saved: {report_file}")
        
        # Summary
        print(f"\n📈 SUMMARY:")
        print(f"   Total WordPress products: {len(all_products)}")
        print(f"   FANUC robot products: {len(fanuc_products)}")
        test_robots_found = sum(1 for sku in test_skus if any(p.get('sku') == sku for p in all_products))
        print(f"   Our test robots found: {test_robots_found}/3")
        
        if test_robots_found == 3:
            print(f"\n🎉 SUCCESS! All test robots are in WordPress!")
            print("   ✅ Products exist and can be updated")
            print("   🔧 We can now update them with complete metadata")
        elif test_robots_found > 0:
            print(f"\n⚠️  Partial success - {test_robots_found} robots found")
        else:
            print(f"\n❌ No test robots found - something is wrong")
        
        return test_robots_found > 0
        
    except Exception as e:
        print(f"❌ Error checking WordPress status: {e}")
        return False


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1) 