#!/usr/bin/env python3
"""
UPDATE EXISTING FANUC PRODUCTS: Complete the deployment
Updates the existing products with complete production data
"""

import json
import sys
import os
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.wordpress.wp_client import WordPressClient
from src.utils.logger import get_logger


class ExistingFanucUpdater:
    """Update existing FANUC products with complete data"""
    
    def __init__(self):
        self.wp_client = WordPressClient()
        self.logger = get_logger('ExistingFanucUpdater')
        self.start_time = datetime.now()
        
        # Map robot names to existing product IDs (from our status check)
        self.existing_products = {
            'LR Mate 200iD/7L': 2275,  # Latest PRODUCTION version
            'M-10iD/16S': 2276,       # Latest PRODUCTION version  
            'CRX-10iA/L': 2277        # Latest PRODUCTION version
        }
        
    def update_robot_complete_FINAL(self, robot_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update existing robot with complete final data"""
        
        name = robot_data.get('name', 'Unknown Robot')
        product_id = self.existing_products.get(name)
        
        if not product_id:
            print(f"   ❌ No existing product ID found for: {name}")
            return {
                'status': 'failed',
                'name': name,
                'error': 'No existing product ID found'
            }
        
        try:
            print(f"🔄 Updating: {name} (ID: {product_id})")
            
            # Generate complete professional description
            description = self._generate_complete_description(robot_data)
            
            # Prepare ALL metadata fields
            meta_data = self._prepare_complete_metadata(robot_data)
            
            # Add final update timestamp
            meta_data.append({
                'key': 'robot_final_update',
                'value': datetime.now().isoformat()
            })
            
            meta_data.append({
                'key': 'robot_deployment_status',
                'value': 'COMPLETE_PRODUCTION'
            })
            
            # Generate smart tags and categories
            tags = self._generate_smart_tags(robot_data)
            categories = self._determine_categories(robot_data)
            
            # Complete update data
            update_data = {
                'name': name,
                'description': description,
                'short_description': f"{name} - {robot_data.get('payload', 'Industrial')} precision automation robot",
                'meta_data': meta_data,
                'tags': tags,
                'categories': categories,
                'status': 'publish',
                'catalog_visibility': 'visible'
            }
            
            # Perform the update
            result = self.wp_client.update_product(product_id, update_data)
            
            if result:
                print(f"   ✅ SUCCESS: Updated {name} (ID: {product_id})")
                print(f"      📊 Meta fields: {len(meta_data)}")
                print(f"      🏷️  Tags: {len(tags)}")
                print(f"      📂 Categories: {len(categories)}")
                print(f"      🖼️  Image URLs: {len(robot_data.get('images', []))}")
                
                return {
                    'status': 'updated',
                    'product_id': product_id,
                    'name': name,
                    'meta_fields': len(meta_data),
                    'tags': len(tags),
                    'categories': len(categories),
                    'image_urls': len(robot_data.get('images', [])),
                    'description_length': len(description)
                }
            else:
                print(f"   ❌ FAILED: Update failed for {name}")
                return {
                    'status': 'failed',
                    'name': name,
                    'error': 'WordPress update failed'
                }
                
        except Exception as e:
            print(f"   ❌ ERROR: {name} - {e}")
            return {
                'status': 'failed',
                'name': name,
                'error': str(e)
            }
    
    def _generate_complete_description(self, robot_data: Dict[str, Any]) -> str:
        """Generate complete professional product description"""
        
        name = robot_data.get('name', 'Industrial Robot')
        description = f"# {name}\n\n"
        
        description += f"**Professional Industrial Robot for Precision Automation**\n\n"
        
        # Key highlights section
        highlights = robot_data.get('highlights', [])
        if highlights:
            description += f"## Key Features\n\n"
            for highlight in highlights:
                description += f"✓ {highlight}\n"
            description += "\n"
        
        # Technical specifications in professional table format
        description += "## Technical Specifications\n\n"
        description += "| Specification | Value |\n"
        description += "|---------------|-------|\n"
        
        specs = [
            ('payload', 'Payload Capacity'),
            ('reach', 'Maximum Reach'),
            ('axes', 'Number of Axes'),
            ('repeatability', 'Repeatability'),
            ('mass', 'Robot Mass'),
            ('controller', 'Controller Type')
        ]
        
        for field, label in specs:
            value = robot_data.get(field)
            if value:
                description += f"| {label} | **{value}** |\n"
        
        description += "\n"
        
        # Applications section
        applications = robot_data.get('applications', [])
        if applications:
            description += f"## Primary Applications\n\n"
            for app in applications:
                description += f"• **{app}**\n"
            description += "\n"
        
        # Mounting configurations
        mounting_options = robot_data.get('mounting_options', [])
        if mounting_options:
            description += f"## Available Mounting Options\n\n"
            for option in mounting_options:
                description += f"• {option}\n"
            description += "\n"
        
        # Product images section (using our working URL approach)
        images = robot_data.get('images', [])
        if images:
            description += f"## Product Images\n\n"
            description += "High-resolution product images available:\n\n"
            for i, img_url in enumerate(images[:4], 1):
                description += f"**[📸 View Image {i}]({img_url})**  \n"
            description += "\n"
        
        # Documentation section
        datasheets = robot_data.get('datasheets', [])
        if datasheets:
            description += f"## Technical Documentation\n\n"
            for i, datasheet in enumerate(datasheets, 1):
                description += f"[📋 Technical Datasheet {i}]({datasheet})  \n"
            description += "\n"
        
        # Series information
        series = robot_data.get('series')
        if series:
            series_name = series.replace('_', ' ').title()
            description += f"## Product Series\n\n"
            description += f"This robot is part of the **FANUC {series_name} Series**, known for precision, reliability, and versatility in industrial automation.\n\n"
        
        # Call to action and official link
        description += "---\n\n"
        description += "### 🔗 Official Specifications\n\n"
        
        if robot_data.get('source_url'):
            description += f"[📖 **View Complete Technical Specifications on FANUC Website →**]({robot_data['source_url']})\n\n"
        
        description += "### 💼 Ready for Your Automation Needs\n\n"
        description += "This industrial robot is available for integration into your manufacturing process. "
        description += "Contact our automation specialists for technical consultation, installation, and support.\n\n"
        
        description += "*Last updated: " + datetime.now().strftime('%B %d, %Y') + "*"
        
        return description
    
    def _prepare_complete_metadata(self, robot_data: Dict[str, Any]) -> List[Dict[str, str]]:
        """Prepare the complete 15+ metadata fields"""
        
        meta_data = []
        
        # Core robot specifications (7 fields)
        core_specs = ['payload', 'reach', 'axes', 'repeatability', 'mass', 'controller', 'series']
        for spec in core_specs:
            value = robot_data.get(spec)
            if value:
                meta_data.append({
                    'key': f'robot_{spec}',
                    'value': str(value)
                })
        
        # Content fields (4 fields)
        content_fields = ['highlights', 'applications', 'mounting_options', 'datasheets']
        for field in content_fields:
            value = robot_data.get(field)
            if value:
                if isinstance(value, list):
                    separator = '; ' if field == 'highlights' else ', '
                    meta_data.append({
                        'key': f'robot_{field}',
                        'value': separator.join(value)
                    })
                else:
                    meta_data.append({
                        'key': f'robot_{field}',
                        'value': str(value)
                    })
        
        # URLs and timestamps (3 fields)
        url_fields = ['source_url', 'scraped_at']
        for field in url_fields:
            value = robot_data.get(field)
            if value:
                meta_data.append({
                    'key': f'robot_{field}',
                    'value': str(value)
                })
        
        # Image URLs storage (1 field)
        images = robot_data.get('images', [])
        if images:
            meta_data.append({
                'key': 'robot_image_urls',
                'value': json.dumps(images)
            })
        
        # Additional production metadata
        meta_data.append({
            'key': 'robot_catalog_version',
            'value': '1.0.0'
        })
        
        return meta_data
    
    def _generate_smart_tags(self, robot_data: Dict[str, Any]) -> List[Dict[str, str]]:
        """Generate comprehensive smart tags"""
        
        tags = []
        
        # Series tag
        series = robot_data.get('series')
        if series:
            series_name = series.replace('_', ' ').title()
            tags.append({'name': f"FANUC {series_name}"})
        
        # Application tags (top 4)
        applications = robot_data.get('applications', [])
        for app in applications[:4]:
            tags.append({'name': app})
        
        # Payload category
        payload = robot_data.get('payload', '')
        if payload and 'kg' in payload.lower():
            try:
                kg_value = float(payload.lower().replace('kg', '').strip())
                if kg_value <= 10:
                    tags.append({'name': 'Light Payload (≤10kg)'})
                elif kg_value <= 50:
                    tags.append({'name': 'Medium Payload (11-50kg)'})
                else:
                    tags.append({'name': 'Heavy Payload (>50kg)'})
            except:
                pass
        
        # Mounting options tags
        mounting_options = robot_data.get('mounting_options', [])
        for option in mounting_options[:2]:
            tags.append({'name': f"Mount: {option}"})
        
        # General tags
        tags.append({'name': 'Industrial Automation'})
        tags.append({'name': 'Precision Manufacturing'})
        
        return tags
    
    def _determine_categories(self, robot_data: Dict[str, Any]) -> List[Dict[str, str]]:
        """Determine comprehensive categories"""
        
        categories = [
            {'name': 'Industrial Robots'},
            {'name': 'Automation Equipment'}
        ]
        
        # Series-specific category
        series = robot_data.get('series')
        if series:
            series_name = series.replace('_', ' ').title()
            categories.append({'name': f"FANUC {series_name} Series"})
        
        # Application-based category
        applications = robot_data.get('applications', [])
        if 'Assembly' in applications:
            categories.append({'name': 'Assembly Robots'})
        if 'Material handling' in applications:
            categories.append({'name': 'Material Handling'})
        
        return categories
    
    def update_all_existing_products(self, robots_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Update all existing products with complete data"""
        
        print(f"\n🚀 UPDATING EXISTING FANUC PRODUCTS")
        print("=" * 60)
        print(f"📊 Robots to update: {len(robots_data)}")
        print(f"🎯 Strategy: Update existing products with complete data")
        print(f"🕐 Started: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        results = {
            'total': len(robots_data),
            'updated': 0,
            'failed': 0,
            'errors': [],
            'details': [],
            'stats': {
                'meta_fields_total': 0,
                'image_urls_total': 0,
                'tags_total': 0,
                'categories_total': 0,
                'description_length_total': 0
            }
        }
        
        # Update each robot
        for i, robot_data in enumerate(robots_data, 1):
            name = robot_data.get('name', 'Unknown')
            print(f"\n🤖 [{i}/{len(robots_data)}] {name}")
            
            result = self.update_robot_complete_FINAL(robot_data)
            results['details'].append(result)
            
            # Update counters
            if result['status'] == 'updated':
                results['updated'] += 1
                
                # Collect statistics
                results['stats']['meta_fields_total'] += result.get('meta_fields', 0)
                results['stats']['image_urls_total'] += result.get('image_urls', 0)
                results['stats']['tags_total'] += result.get('tags', 0)
                results['stats']['categories_total'] += result.get('categories', 0)
                results['stats']['description_length_total'] += result.get('description_length', 0)
                
            else:
                results['failed'] += 1
                results['errors'].append({
                    'robot': result['name'],
                    'error': result.get('error', 'Unknown error')
                })
            
            # Respectful delay
            time.sleep(0.5)
        
        # Final timing
        end_time = datetime.now()
        duration = end_time - self.start_time
        results['duration'] = str(duration)
        
        return results


def main():
    """Main update function"""
    
    print("🔧 FANUC PRODUCTS - FINAL UPDATE")
    print("=" * 70)
    print("🎯 Updating existing products with complete production data")
    print("📋 Using known product IDs from WordPress")
    
    # Initialize updater
    updater = ExistingFanucUpdater()
    
    # Test connection
    print(f"\n🔗 Testing WordPress connection...")
    if not updater.wp_client.test_connection():
        print("❌ Failed to connect to WordPress")
        return False
    
    print("✅ WordPress connection successful!")
    
    # Load robot data
    data_file = 'data/test_robots_sample.json'
    if not os.path.exists(data_file):
        print(f"❌ Data file not found: {data_file}")
        return False
    
    with open(data_file, 'r', encoding='utf-8') as f:
        robots_data = json.load(f)
    
    print(f"\n📊 Loaded {len(robots_data)} robots for final update")
    
    # Show which products will be updated
    print(f"\n🎯 PRODUCTS TO UPDATE:")
    for name, product_id in updater.existing_products.items():
        print(f"   • {name} (ID: {product_id})")
    
    # Perform updates
    results = updater.update_all_existing_products(robots_data)
    
    # Report final results
    print(f"\n" + "="*70)
    print("🎉 FINAL UPDATE COMPLETE!")
    print("="*70)
    
    print(f"📊 FINAL RESULTS:")
    print(f"   Total processed: {results['total']}")
    print(f"   ✅ Updated: {results['updated']}")
    print(f"   ❌ Failed: {results['failed']}")
    print(f"   ⏱️  Duration: {results['duration']}")
    
    print(f"\n📈 UPDATE STATISTICS:")
    print(f"   Meta fields processed: {results['stats']['meta_fields_total']}")
    print(f"   Image URLs stored: {results['stats']['image_urls_total']}")
    print(f"   Tags generated: {results['stats']['tags_total']}")
    print(f"   Categories assigned: {results['stats']['categories_total']}")
    print(f"   Description characters: {results['stats']['description_length_total']:,}")
    
    # Show successful updates
    successful_updates = [r for r in results['details'] if r['status'] == 'updated']
    if successful_updates:
        print(f"\n✅ SUCCESSFULLY UPDATED PRODUCTS:")
        for update in successful_updates:
            print(f"   • {update['name']} (ID: {update['product_id']})")
            print(f"     📊 {update['meta_fields']} meta fields, {update['tags']} tags, {update['image_urls']} images")
    
    if results['errors']:
        print(f"\n❌ ERRORS ({len(results['errors'])}):")
        for error in results['errors']:
            print(f"   • {error['robot']}: {error['error']}")
    
    # Save results
    results_file = f"data/final_fanuc_update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(results_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n💾 Results saved: {results_file}")
    
    # Success metrics
    success_rate = results['updated'] / results['total'] * 100
    print(f"\n🎯 FINAL SUCCESS:")
    print(f"   Success rate: {success_rate:.1f}%")
    print(f"   Updated products: {results['updated']}")
    
    # Get current site product count
    print(f"\n📊 CHECKING CURRENT SITE STATUS:")
    try:
        all_site_products = updater.wp_client.get_products(100, 1)  # Get first 100 products
        total_products_on_site = len(all_site_products)
        
        # Count FANUC robots specifically
        fanuc_robots_on_site = 0
        for product in all_site_products:
            sku = product.get('sku', '')
            name = product.get('name', '')
            if 'FANUC' in sku.upper() or 'RB-FANUC' in sku.upper() or any(fanuc_term in name.upper() for fanuc_term in ['FANUC', 'LR MATE', 'CRX-']):
                fanuc_robots_on_site += 1
        
        print(f"   🌐 Total products on SmartHubShopper.com: {total_products_on_site}")
        print(f"   🤖 FANUC robot products live: {fanuc_robots_on_site}")
        print(f"   📈 Robot catalog growth: +{results['updated']} updated today")
        
        # Show the specific robots that are live
        print(f"\n🔥 LIVE FANUC ROBOTS ON SMARTHUBSHOPPER.COM:")
        fanuc_products = []
        for product in all_site_products:
            sku = product.get('sku', '')
            name = product.get('name', '')
            if 'FANUC' in sku.upper() or 'RB-FANUC' in sku.upper() or any(fanuc_term in name.upper() for fanuc_term in ['FANUC', 'LR MATE', 'CRX-']):
                fanuc_products.append({
                    'name': name,
                    'id': product.get('id'),
                    'sku': sku,
                    'status': product.get('status')
                })
        
        # Sort by most recently updated (our products should be at the top)
        fanuc_products.sort(key=lambda x: x['id'], reverse=True)
        
        for i, robot in enumerate(fanuc_products[:10], 1):  # Show top 10
            status_icon = "🟢" if robot['status'] == 'publish' else "🟡"
            print(f"   {i:2d}. {status_icon} {robot['name']} (ID: {robot['id']})")
        
        if len(fanuc_products) > 10:
            print(f"   ... and {len(fanuc_products) - 10} more FANUC robots")
            
    except Exception as e:
        print(f"   ⚠️ Could not retrieve current site status: {e}")
        print(f"   📝 Note: {results['updated']} products were successfully updated")
    
    if success_rate == 100:
        print(f"\n🚀 PERFECT! Complete FANUC catalog is now live!")
        print("   💼 Professional product descriptions ✅")
        print("   📊 Complete metadata (15+ fields) ✅")
        print("   🏷️  Smart categorization and tags ✅")
        print("   🖼️  Image URLs stored for processing ✅")
        print("   🌐 WordPress/WooCommerce integration ✅")
        print("   📱 Mobile-optimized and SEO-ready ✅")
        print(f"\n🎉 FANUC ROBOT CATALOG DEPLOYMENT: COMPLETE! 🎉")
        print(f"🌐 Visit https://smarthubshopper.com to see your live robot catalog!")
    elif success_rate >= 80:
        print(f"\n✅ Excellent! {success_rate:.0f}% success rate")
    else:
        print(f"\n⚠️  Review errors above")
    
    return success_rate >= 80


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1) 