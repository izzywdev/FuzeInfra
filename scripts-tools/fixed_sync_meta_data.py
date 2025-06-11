#!/usr/bin/env python3
"""
FIXED: Test sync script with complete meta data mapping
"""

import json
import sys
import os
from typing import Dict, Any, List

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.wordpress.wp_client import WordPressClient
from src.utils.logger import get_logger
from src.config import PRODUCT_CONFIG


class FixedNoImageWordPressClient(WordPressClient):
    """WordPress client that skips image uploads"""
    
    def upload_image(self, image_url: str, filename: str = None) -> Dict[str, Any]:
        """Skip image upload and return a placeholder result"""
        self.logger.info(f"Skipping image upload: {image_url}")
        return {
            'id': 999,  # Placeholder ID
            'source_url': image_url,
            'title': 'Skipped Image Upload',
            'alt_text': 'Image upload skipped for testing'
        }


class FixedProductManager:
    """Product manager with FIXED meta data handling"""
    
    def __init__(self):
        self.wp_client = FixedNoImageWordPressClient()
        self.logger = get_logger('FixedProductManager')
    
    def generate_sku(self, robot_data: Dict[str, Any]) -> str:
        """Generate SKU for robot"""
        model = robot_data.get('model', '').replace('/', '-').replace(' ', '-')
        return f"RB-FANUC-{model}".upper()
    
    def create_robot_product(self, robot_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a single robot product with FIXED meta data"""
        
        try:
            name = robot_data.get('name', 'Unknown Robot')
            model = robot_data.get('model', '')
            sku = self.generate_sku(robot_data)
            
            self.logger.info(f"Creating product: {name} (SKU: {sku})")
            
            # Check if product already exists
            existing_products = self.wp_client.get_products({'sku': sku})
            if existing_products:
                self.logger.info(f"Product with SKU {sku} already exists - updating")
                return self.update_robot_product(existing_products[0]['id'], robot_data)
            
            # FIXED: Prepare product data with proper description
            description = robot_data.get('description', '')
            if not description:
                # Generate description from highlights if main description is missing
                highlights = robot_data.get('highlights', [])
                if highlights:
                    description = f"{name} features: " + "; ".join(highlights[:3])
                else:
                    description = f"{name} - High-quality industrial robot for automation tasks."
            
            product_data = {
                'name': name,
                'type': 'simple',
                'sku': sku,
                'status': 'publish',
                'catalog_visibility': 'visible',
                'description': description,  # FIXED: Ensure description is always present
                'short_description': f"{name} - Industrial Robot with {robot_data.get('payload', 'N/A')} payload",
                'meta_data': self._prepare_robot_metadata_FIXED(robot_data),  # FIXED method
                'categories': [{'name': 'Industrial Robots'}],
                'tags': self._prepare_robot_tags(robot_data)
            }
            
            # Create product (without images)
            result = self.wp_client.create_product(product_data)
            
            if result:
                self.logger.info(f"Successfully created product: {name}")
                return {'status': 'created', 'product_id': result.get('id'), 'name': name}
            else:
                self.logger.error(f"Failed to create product: {name}")
                return {'status': 'failed', 'name': name, 'error': 'Creation failed'}
                
        except Exception as e:
            self.logger.error(f"Error creating product {robot_data.get('name', 'Unknown')}: {e}")
            return {'status': 'failed', 'name': robot_data.get('name', 'Unknown'), 'error': str(e)}
    
    def update_robot_product(self, product_id: int, robot_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update existing robot product with FIXED meta data"""
        
        try:
            name = robot_data.get('name', 'Unknown Robot')
            self.logger.info(f"Updating existing product: {name} (ID: {product_id})")
            
            # FIXED: Include description in update
            description = robot_data.get('description', '')
            if not description:
                highlights = robot_data.get('highlights', [])
                if highlights:
                    description = f"{name} features: " + "; ".join(highlights[:3])
                else:
                    description = f"{name} - High-quality industrial robot for automation tasks."
            
            # Prepare update data
            update_data = {
                'description': description,  # FIXED: Include description
                'meta_data': self._prepare_robot_metadata_FIXED(robot_data)  # FIXED method
            }
            
            result = self.wp_client.update_product(product_id, update_data)
            
            if result:
                self.logger.info(f"Successfully updated product: {name}")
                return {'status': 'updated', 'product_id': product_id, 'name': name}
            else:
                self.logger.error(f"Failed to update product: {name}")
                return {'status': 'failed', 'name': name, 'error': 'Update failed'}
                
        except Exception as e:
            self.logger.error(f"Error updating product {robot_data.get('name', 'Unknown')}: {e}")
            return {'status': 'failed', 'name': robot_data.get('name', 'Unknown'), 'error': str(e)}
    
    def _prepare_robot_metadata_FIXED(self, robot_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """FIXED: Prepare robot metadata including missing fields"""
        metadata = []
        
        # Robot specifications
        specs = [
            ('payload', 'Robot Payload'),
            ('reach', 'Robot Reach'),
            ('axes', 'Number of Axes'),
            ('repeatability', 'Repeatability'),
            ('mass', 'Robot Mass'),
            ('controller', 'Controller Type'),
            ('series', 'Robot Series')
        ]
        
        for field, label in specs:
            value = robot_data.get(field)
            if value:
                metadata.append({
                    'key': f'robot_{field}',
                    'value': str(value)
                })
        
        # FIXED: Add highlights (was missing!)
        highlights = robot_data.get('highlights', [])
        if highlights:
            metadata.append({
                'key': 'robot_highlights',
                'value': '; '.join(highlights[:5])  # Join highlights with semicolon
            })
        
        # FIXED: Add mounting options (was missing!)
        mounting_options = robot_data.get('mounting_options', [])
        if mounting_options:
            metadata.append({
                'key': 'robot_mounting_options',
                'value': ', '.join(mounting_options)  # Join mounting options with comma
            })
        
        # Applications (this was working)
        applications = robot_data.get('applications', [])
        if applications:
            metadata.append({
                'key': 'robot_applications',
                'value': ', '.join(applications[:3])  # Limit to 3
            })
        
        # FIXED: Add datasheets
        datasheets = robot_data.get('datasheets', [])
        if datasheets:
            metadata.append({
                'key': 'robot_datasheets',
                'value': ', '.join(datasheets)
            })
        
        # Source URL (this was working)
        source_url = robot_data.get('source_url')
        if source_url:
            metadata.append({
                'key': 'robot_source_url',
                'value': source_url
            })
        
        # FIXED: Add scraped timestamp
        scraped_at = robot_data.get('scraped_at')
        if scraped_at:
            metadata.append({
                'key': 'robot_scraped_at',
                'value': scraped_at
            })
        
        return metadata
    
    def _prepare_robot_tags(self, robot_data: Dict[str, Any]) -> List[Dict[str, str]]:
        """Prepare robot tags"""
        tags = []
        
        # Add series tag
        series = robot_data.get('series')
        if series:
            tags.append({'name': series.replace('_', ' ').title()})
        
        # Add application tags
        applications = robot_data.get('applications', [])
        for app in applications[:3]:  # Limit to 3
            tags.append({'name': app})
        
        # FIXED: Add mounting option tags
        mounting_options = robot_data.get('mounting_options', [])
        for option in mounting_options[:2]:  # Limit to 2
            tags.append({'name': f"Mount: {option}"})
        
        return tags
    
    def sync_robots_to_wordpress(self, robots_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Sync robots to WordPress with FIXED meta data"""
        
        self.logger.info(f"Starting FIXED sync of {len(robots_data)} robots to WordPress...")
        
        results = {
            'created': 0,
            'updated': 0,
            'failed': 0,
            'errors': [],
            'details': [],
            'meta_fields_processed': set()
        }
        
        for i, robot_data in enumerate(robots_data, 1):
            self.logger.info(f"Processing robot {i}/{len(robots_data)}: {robot_data.get('name', 'Unknown')}")
            
            # Track what meta fields we're processing
            meta_data = self._prepare_robot_metadata_FIXED(robot_data)
            for meta_item in meta_data:
                results['meta_fields_processed'].add(meta_item['key'])
            
            result = self.create_robot_product(robot_data)
            results['details'].append(result)
            
            if result['status'] == 'created':
                results['created'] += 1
            elif result['status'] == 'updated':
                results['updated'] += 1
            else:
                results['failed'] += 1
                results['errors'].append({
                    'robot': result['name'],
                    'error': result.get('error', 'Unknown error')
                })
        
        # Convert set to list for JSON serialization
        results['meta_fields_processed'] = list(results['meta_fields_processed'])
        
        return results


def main():
    """Test FIXED sync without images"""
    logger = get_logger('fixed_sync_meta_data')
    
    try:
        # Load test robot data
        data_file = 'data/test_robots_sample.json'
        
        if not os.path.exists(data_file):
            logger.error(f"Test data file not found: {data_file}")
            return False
        
        with open(data_file, 'r', encoding='utf-8') as f:
            robots_data = json.load(f)
        
        logger.info(f"Loaded {len(robots_data)} test robots")
        
        # Initialize FIXED product manager
        product_manager = FixedProductManager()
        
        # Test WordPress connection
        if not product_manager.wp_client.test_connection():
            logger.error("Failed to connect to WordPress")
            return False
        
        logger.info("WordPress connection successful!")
        
        # Sync robots with FIXED meta data
        logger.info("Starting FIXED WordPress sync (complete meta data)...")
        results = product_manager.sync_robots_to_wordpress(robots_data)
        
        # Report results
        print("\n" + "="*70)
        print("🔧 FIXED SYNC RESULTS (Complete Meta Data)")
        print("="*70)
        print(f"✅ Created: {results['created']} products")
        print(f"🔄 Updated: {results['updated']} products") 
        print(f"❌ Failed: {results['failed']} products")
        
        print(f"\n📊 Meta Fields Processed: {len(results['meta_fields_processed'])}")
        for field in sorted(results['meta_fields_processed']):
            print(f"   • {field}")
        
        if results['errors']:
            print("\n❌ Errors:")
            for error in results['errors']:
                print(f"  • {error['robot']}: {error['error']}")
        
        print("\n📋 Product Details:")
        for detail in results['details']:
            status_emoji = "✅" if detail['status'] == 'created' else "🔄" if detail['status'] == 'updated' else "❌"
            print(f"  {status_emoji} {detail['name']} - {detail['status']}")
        
        # Save results
        results_file = 'data/fixed_sync_results.json'
        with open(results_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        
        logger.info(f"FIXED sync results saved to {results_file}")
        
        print(f"\n🎯 FIXES APPLIED:")
        print("   ✅ Added missing highlights meta field")
        print("   ✅ Added missing mounting_options meta field")
        print("   ✅ Fixed empty description field")
        print("   ✅ Added datasheets meta field")
        print("   ✅ Added scraped_at timestamp")
        print("   ✅ Enhanced tags with mounting options")
        
        return True
        
    except Exception as e:
        logger.error(f"FIXED sync failed: {e}")
        return False


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1) 