#!/usr/bin/env python3
"""
Analyze product mapping between robot data structure and WooCommerce structure
"""

import json
import sys
import os
from typing import Dict, Any, List
from pprint import pprint

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.wordpress.wp_client import WordPressClient
from src.config import WORDPRESS_CONFIG


def load_robot_data_structure():
    """Load our designed robot data structure"""
    
    print("📋 Loading Robot Data Structure...")
    
    # Load test data to see our structure
    with open('data/test_robots_sample.json', 'r', encoding='utf-8') as f:
        robots_data = json.load(f)
    
    # Get structure from first robot
    sample_robot = robots_data[0]
    
    robot_structure = {
        'core_fields': {
            'name': sample_robot.get('name'),
            'model': sample_robot.get('model'),
            'series': sample_robot.get('series'),
            'source_url': sample_robot.get('source_url'),
            'scraped_at': sample_robot.get('scraped_at'),
        },
        'specifications': {
            'payload': sample_robot.get('payload'),
            'reach': sample_robot.get('reach'),
            'axes': sample_robot.get('axes'),
            'repeatability': sample_robot.get('repeatability'),
            'mass': sample_robot.get('mass'),
            'controller': sample_robot.get('controller'),
        },
        'content': {
            'description': sample_robot.get('description'),
            'highlights': sample_robot.get('highlights'),
            'applications': sample_robot.get('applications'),
            'mounting_options': sample_robot.get('mounting_options'),
        },
        'media': {
            'images': sample_robot.get('images'),
            'datasheets': sample_robot.get('datasheets'),
        },
        'raw_data': {
            'raw_specifications': sample_robot.get('raw_specifications'),
        }
    }
    
    return robot_structure, robots_data


def get_woocommerce_products():
    """Get actual products from WooCommerce"""
    
    print("🛒 Retrieving WooCommerce Products...")
    
    wp_client = WordPressClient()
    
    if not wp_client.test_connection():
        print("❌ Cannot connect to WooCommerce")
        return []
    
    # Get products with SKUs that match our robot pattern
    response = wp_client.session.get(f"{WORDPRESS_CONFIG['url']}/wp-json/wc/v3/products")
    
    if response.status_code == 200:
        all_products = response.json()
        
        # Filter for FANUC robot products
        robot_products = []
        for product in all_products:
            sku = product.get('sku', '')
            if sku.startswith('RB-FANUC-'):
                robot_products.append(product)
        
        print(f"✅ Found {len(robot_products)} FANUC robot products in WooCommerce")
        return robot_products
    else:
        print(f"❌ Failed to retrieve products: {response.status_code}")
        return []


def analyze_woocommerce_structure(products):
    """Analyze the structure of WooCommerce products"""
    
    if not products:
        return {}
    
    print("🔍 Analyzing WooCommerce Product Structure...")
    
    sample_product = products[0]
    
    wc_structure = {
        'basic_fields': {
            'id': sample_product.get('id'),
            'name': sample_product.get('name'),
            'slug': sample_product.get('slug'),
            'sku': sample_product.get('sku'),
            'type': sample_product.get('type'),
            'status': sample_product.get('status'),
            'description': sample_product.get('description'),
            'short_description': sample_product.get('short_description'),
        },
        'meta_data': sample_product.get('meta_data', []),
        'categories': sample_product.get('categories', []),
        'tags': sample_product.get('tags', []),
        'images': sample_product.get('images', []),
        'attributes': sample_product.get('attributes', []),
        'default_attributes': sample_product.get('default_attributes', []),
    }
    
    return wc_structure


def compare_structures(robot_structure, wc_structure):
    """Compare robot data structure with WooCommerce structure"""
    
    print("\n🔄 STRUCTURE COMPARISON")
    print("=" * 60)
    
    comparison = {
        'mapped_correctly': [],
        'mapped_incorrectly': [],
        'missing_from_wc': [],
        'unused_wc_fields': [],
        'meta_data_analysis': {},
        'suggestions': []
    }
    
    # Check core field mappings
    print("\n📊 CORE FIELD MAPPING:")
    print("-" * 30)
    
    core_mappings = {
        'name': 'name',
        'model': 'sku',  # Model is in SKU
        'description': 'description',
    }
    
    for robot_field, wc_field in core_mappings.items():
        robot_value = robot_structure['core_fields'].get(robot_field)
        wc_value = wc_structure['basic_fields'].get(wc_field)
        
        if robot_value and wc_value:
            comparison['mapped_correctly'].append(f"{robot_field} → {wc_field}")
            print(f"✅ {robot_field} → {wc_field}")
        else:
            comparison['mapped_incorrectly'].append(f"{robot_field} → {wc_field}")
            print(f"❌ {robot_field} → {wc_field} (missing data)")
    
    # Check specifications (should be in meta_data)
    print("\n🔧 SPECIFICATIONS MAPPING:")
    print("-" * 30)
    
    meta_data = wc_structure.get('meta_data', [])
    meta_keys = [item.get('key', '') for item in meta_data]
    
    spec_fields = robot_structure['specifications']
    for spec_name, spec_value in spec_fields.items():
        expected_meta_key = f"robot_{spec_name}"
        
        if expected_meta_key in meta_keys:
            comparison['mapped_correctly'].append(f"{spec_name} → meta:{expected_meta_key}")
            print(f"✅ {spec_name} → meta:{expected_meta_key}")
        else:
            comparison['missing_from_wc'].append(f"{spec_name} (expected meta:{expected_meta_key})")
            print(f"❌ {spec_name} → meta:{expected_meta_key} (MISSING)")
    
    # Check content fields
    print("\n📝 CONTENT MAPPING:")
    print("-" * 30)
    
    content_fields = robot_structure['content']
    for content_name, content_value in content_fields.items():
        if content_name == 'description':
            continue  # Already checked above
        
        expected_meta_key = f"robot_{content_name}"
        if expected_meta_key in meta_keys:
            comparison['mapped_correctly'].append(f"{content_name} → meta:{expected_meta_key}")
            print(f"✅ {content_name} → meta:{expected_meta_key}")
        else:
            comparison['missing_from_wc'].append(f"{content_name} (expected meta:{expected_meta_key})")
            print(f"❌ {content_name} → meta:{expected_meta_key} (MISSING)")
    
    # Check media fields
    print("\n🖼️  MEDIA MAPPING:")
    print("-" * 30)
    
    wc_images = wc_structure.get('images', [])
    robot_images = robot_structure['media'].get('images', [])
    
    if robot_images and not wc_images:
        comparison['missing_from_wc'].append("images (WooCommerce images array empty)")
        print("❌ images → WooCommerce images (MISSING - no images uploaded)")
    elif robot_images and wc_images:
        comparison['mapped_correctly'].append("images → WooCommerce images")
        print("✅ images → WooCommerce images")
    else:
        print("⚠️  No image data to compare")
    
    return comparison


def analyze_meta_data(products):
    """Analyze what meta data is actually stored"""
    
    print("\n🏷️  META DATA ANALYSIS:")
    print("-" * 30)
    
    if not products:
        print("❌ No products to analyze")
        return {}
    
    all_meta_keys = set()
    meta_data_summary = {}
    
    for product in products:
        meta_data = product.get('meta_data', [])
        
        for meta_item in meta_data:
            key = meta_item.get('key', '')
            value = meta_item.get('value', '')
            
            if key.startswith('robot_'):
                all_meta_keys.add(key)
                
                if key not in meta_data_summary:
                    meta_data_summary[key] = []
                meta_data_summary[key].append(value)
    
    print(f"Found {len(all_meta_keys)} robot-related meta fields:")
    for key in sorted(all_meta_keys):
        values = meta_data_summary[key]
        print(f"  • {key}: {len(values)} values")
        if values:
            print(f"    Example: {values[0]}")
    
    return meta_data_summary


def generate_solutions(comparison, robot_structure):
    """Generate solutions for mapping issues"""
    
    print("\n🛠️  RECOMMENDED SOLUTIONS")
    print("=" * 60)
    
    solutions = []
    
    # Missing specifications
    missing_specs = [item for item in comparison['missing_from_wc'] if 'robot_' in item]
    if missing_specs:
        solutions.append({
            'issue': 'Missing Robot Specifications in WooCommerce',
            'description': f"Robot specs like {', '.join(missing_specs[:3])} are not being stored as meta data",
            'solution': 'Fix meta data mapping in product creation',
            'implementation': [
                "1. Check if get_products() method supports meta data queries",
                "2. Fix the meta_data array structure in create_product()",
                "3. Ensure WordPress accepts custom meta fields"
            ]
        })
    
    # Image handling
    if any('images' in item for item in comparison['missing_from_wc']):
        solutions.append({
            'issue': 'Images Not Uploading to WooCommerce',
            'description': 'Robot images are not being uploaded or attached to products',
            'solution': 'Enable image uploads and fix upload permissions',
            'implementation': [
                "1. Enable image file types in WordPress (jpg, png, gif)",
                "2. Check upload permissions in wp-admin",
                "3. Fix image upload method in wp_client.py",
                "4. Test with smaller images first"
            ]
        })
    
    # Product attributes
    solutions.append({
        'issue': 'Robot Specifications Should Use WooCommerce Attributes',
        'description': 'Specs like payload, reach, axes should be product attributes for filtering',
        'solution': 'Create WooCommerce product attributes for robot specs',
        'implementation': [
            "1. Create WooCommerce attributes: Payload, Reach, Axes, etc.",
            "2. Map robot specs to these attributes instead of meta data",
            "3. Enable attribute-based filtering on frontend",
            "4. Use attributes for product comparison features"
        ]
    })
    
    # Categories and taxonomy
    solutions.append({
        'issue': 'Better Product Taxonomy',
        'description': 'Robot series and applications should be proper taxonomies',
        'solution': 'Create custom taxonomies for robot classification',
        'implementation': [
            "1. Create 'Robot Series' taxonomy (LR Mate, M-Series, etc.)",
            "2. Create 'Robot Applications' taxonomy (Welding, Assembly, etc.)",
            "3. Use these instead of simple tags",
            "4. Enable taxonomy-based navigation"
        ]
    })
    
    return solutions


def print_solutions(solutions):
    """Print detailed solutions"""
    
    for i, solution in enumerate(solutions, 1):
        print(f"\n{i}. {solution['issue']}")
        print(f"   Issue: {solution['description']}")
        print(f"   Solution: {solution['solution']}")
        print("   Implementation Steps:")
        for step in solution['implementation']:
            print(f"     {step}")


def main():
    """Main analysis function"""
    
    print("🔍 PRODUCT STRUCTURE MAPPING ANALYSIS")
    print("=" * 60)
    
    # Load our robot data structure
    robot_structure, robot_data = load_robot_data_structure()
    
    # Get WooCommerce products
    wc_products = get_woocommerce_products()
    
    if not wc_products:
        print("❌ No WooCommerce products found. Run the sync first!")
        return False
    
    # Analyze WooCommerce structure
    wc_structure = analyze_woocommerce_structure(wc_products)
    
    # Compare structures
    comparison = compare_structures(robot_structure, wc_structure)
    
    # Analyze meta data
    meta_summary = analyze_meta_data(wc_products)
    
    # Generate solutions
    solutions = generate_solutions(comparison, robot_structure)
    
    # Print results
    print("\n📋 MAPPING SUMMARY")
    print("=" * 60)
    print(f"✅ Correctly mapped: {len(comparison['mapped_correctly'])} fields")
    print(f"❌ Missing from WC: {len(comparison['missing_from_wc'])} fields")
    print(f"⚠️  Incorrectly mapped: {len(comparison['mapped_incorrectly'])} fields")
    
    print_solutions(solutions)
    
    # Save detailed analysis
    analysis_result = {
        'robot_structure': robot_structure,
        'woocommerce_structure': wc_structure,
        'comparison': comparison,
        'meta_data_summary': meta_summary,
        'solutions': solutions
    }
    
    with open('data/product_mapping_analysis.json', 'w', encoding='utf-8') as f:
        json.dump(analysis_result, f, indent=2, ensure_ascii=False)
    
    print(f"\n💾 Detailed analysis saved to: data/product_mapping_analysis.json")
    
    return True


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1) 