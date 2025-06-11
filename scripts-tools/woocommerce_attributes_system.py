#!/usr/bin/env python3
"""
WooCommerce Attributes System for Robot Products
Strategic approach using proper WooCommerce attributes instead of meta data
"""

import json
import sys
import os
from typing import Dict, Any, List

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.wordpress.wp_client import WordPressClient
from src.utils.logger import get_logger
from src.config import WORDPRESS_CONFIG


class WooCommerceAttributesManager:
    """Manage WooCommerce product attributes for robots"""
    
    def __init__(self):
        self.wp_client = WordPressClient()
        self.logger = get_logger('AttributesManager')
        
        # Define robot attribute mapping
        self.robot_attributes = {
            # Core specifications
            'payload': {
                'name': 'Payload',
                'slug': 'payload',
                'type': 'select',
                'order_by': 'menu_order',
                'has_archives': True
            },
            'reach': {
                'name': 'Reach',
                'slug': 'reach',
                'type': 'select',
                'order_by': 'menu_order',
                'has_archives': True
            },
            'axes': {
                'name': 'Axes',
                'slug': 'axes',
                'type': 'select',
                'order_by': 'menu_order',
                'has_archives': True
            },
            'controller': {
                'name': 'Controller',
                'slug': 'controller',
                'type': 'select',
                'order_by': 'name',
                'has_archives': True
            },
            'series': {
                'name': 'Robot Series',
                'slug': 'robot-series',
                'type': 'select',
                'order_by': 'name',
                'has_archives': True
            },
            'applications': {
                'name': 'Applications',
                'slug': 'applications',
                'type': 'select',
                'order_by': 'name',
                'has_archives': True
            },
            'mounting_options': {
                'name': 'Mounting Options',
                'slug': 'mounting-options',
                'type': 'select',
                'order_by': 'name',
                'has_archives': True
            }
        }
    
    def create_woocommerce_attributes(self) -> Dict[str, Any]:
        """Create WooCommerce product attributes"""
        
        self.logger.info("Creating WooCommerce product attributes...")
        
        results = {
            'created': [],
            'existing': [],
            'failed': []
        }
        
        for attr_key, attr_config in self.robot_attributes.items():
            try:
                # Check if attribute already exists
                existing_attr = self.get_attribute(attr_config['slug'])
                
                if existing_attr:
                    self.logger.info(f"Attribute {attr_config['name']} already exists")
                    results['existing'].append(attr_config['name'])
                    continue
                
                # Create new attribute
                attr_data = {
                    'name': attr_config['name'],
                    'slug': attr_config['slug'],
                    'type': attr_config['type'],
                    'order_by': attr_config['order_by'],
                    'has_archives': attr_config['has_archives']
                }
                
                created_attr = self.create_attribute(attr_data)
                
                if created_attr:
                    self.logger.info(f"Created attribute: {attr_config['name']}")
                    results['created'].append(attr_config['name'])
                else:
                    self.logger.error(f"Failed to create attribute: {attr_config['name']}")
                    results['failed'].append(attr_config['name'])
                    
            except Exception as e:
                self.logger.error(f"Error creating attribute {attr_config['name']}: {e}")
                results['failed'].append(attr_config['name'])
        
        return results
    
    def get_attribute(self, slug: str) -> Dict[str, Any]:
        """Get existing attribute by slug"""
        
        try:
            url = f"{WORDPRESS_CONFIG['url']}/wp-json/wc/v3/products/attributes"
            response = self.wp_client.session.get(url)
            
            if response.status_code == 200:
                attributes = response.json()
                
                for attr in attributes:
                    if attr.get('slug') == slug:
                        return attr
                        
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting attribute {slug}: {e}")
            return None
    
    def create_attribute(self, attr_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create new WooCommerce attribute"""
        
        try:
            url = f"{WORDPRESS_CONFIG['url']}/wp-json/wc/v3/products/attributes"
            response = self.wp_client.session.post(url, json=attr_data)
            
            if response.status_code == 201:
                return response.json()
            else:
                self.logger.error(f"Failed to create attribute: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error creating attribute: {e}")
            return None
    
    def create_attribute_terms(self, attribute_id: int, terms: List[str]) -> Dict[str, Any]:
        """Create terms for an attribute"""
        
        results = {
            'created': [],
            'existing': [],
            'failed': []
        }
        
        for term in terms:
            try:
                # Check if term exists
                existing_term = self.get_attribute_term(attribute_id, term)
                
                if existing_term:
                    results['existing'].append(term)
                    continue
                
                # Create new term
                term_data = {
                    'name': term,
                    'slug': term.lower().replace(' ', '-').replace('/', '-')
                }
                
                created_term = self.create_attribute_term(attribute_id, term_data)
                
                if created_term:
                    results['created'].append(term)
                else:
                    results['failed'].append(term)
                    
            except Exception as e:
                self.logger.error(f"Error creating term {term}: {e}")
                results['failed'].append(term)
        
        return results
    
    def get_attribute_term(self, attribute_id: int, term_name: str) -> Dict[str, Any]:
        """Get attribute term by name"""
        
        try:
            url = f"{WORDPRESS_CONFIG['url']}/wp-json/wc/v3/products/attributes/{attribute_id}/terms"
            response = self.wp_client.session.get(url)
            
            if response.status_code == 200:
                terms = response.json()
                
                for term in terms:
                    if term.get('name').lower() == term_name.lower():
                        return term
                        
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting term {term_name}: {e}")
            return None
    
    def create_attribute_term(self, attribute_id: int, term_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create attribute term"""
        
        try:
            url = f"{WORDPRESS_CONFIG['url']}/wp-json/wc/v3/products/attributes/{attribute_id}/terms"
            response = self.wp_client.session.post(url, json=term_data)
            
            if response.status_code == 201:
                return response.json()
            else:
                self.logger.error(f"Failed to create term: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error creating term: {e}")
            return None
    
    def setup_robot_attributes_from_data(self, robots_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Setup attributes and terms based on actual robot data"""
        
        self.logger.info("Setting up robot attributes from data...")
        
        # Create base attributes first
        attr_results = self.create_woocommerce_attributes()
        
        # Collect unique values from robot data
        unique_values = {
            'payload': set(),
            'reach': set(),
            'axes': set(),
            'controller': set(),
            'series': set(),
            'applications': set(),
            'mounting_options': set()
        }
        
        for robot in robots_data:
            # Collect values
            if robot.get('payload'):
                unique_values['payload'].add(robot['payload'])
            if robot.get('reach'):
                unique_values['reach'].add(robot['reach'])
            if robot.get('axes'):
                unique_values['axes'].add(str(robot['axes']))
            if robot.get('controller'):
                unique_values['controller'].add(robot['controller'])
            if robot.get('series'):
                unique_values['series'].add(robot['series'].replace('_', ' ').title())
            
            # Handle arrays
            for app in robot.get('applications', []):
                unique_values['applications'].add(app)
            for mount in robot.get('mounting_options', []):
                unique_values['mounting_options'].add(mount)
        
        # Create terms for each attribute
        term_results = {}
        
        for attr_key, values in unique_values.items():
            if not values:
                continue
                
            attr_config = self.robot_attributes[attr_key]
            
            # Get attribute ID
            attr = self.get_attribute(attr_config['slug'])
            if not attr:
                self.logger.error(f"Attribute {attr_config['name']} not found")
                continue
            
            # Create terms
            term_results[attr_key] = self.create_attribute_terms(
                attr['id'], 
                list(values)
            )
            
            self.logger.info(f"Created terms for {attr_config['name']}: {len(term_results[attr_key]['created'])} new, {len(term_results[attr_key]['existing'])} existing")
        
        return {
            'attributes': attr_results,
            'terms': term_results,
            'unique_values': {k: list(v) for k, v in unique_values.items()}
        }


class AttributeBasedProductManager:
    """Product manager that uses WooCommerce attributes"""
    
    def __init__(self):
        self.wp_client = WordPressClient()
        self.attributes_manager = WooCommerceAttributesManager()
        self.logger = get_logger('AttributeProductManager')
    
    def convert_robot_to_product_attributes(self, robot_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Convert robot data to WooCommerce product attributes"""
        
        attributes = []
        
        # Map robot fields to attributes
        attribute_mappings = {
            'payload': robot_data.get('payload'),
            'reach': robot_data.get('reach'),
            'axes': str(robot_data.get('axes', '')),
            'controller': robot_data.get('controller'),
            'series': robot_data.get('series', '').replace('_', ' ').title()
        }
        
        for attr_key, value in attribute_mappings.items():
            if not value:
                continue
                
            attr_config = self.attributes_manager.robot_attributes[attr_key]
            attr = self.attributes_manager.get_attribute(attr_config['slug'])
            
            if attr:
                attributes.append({
                    'id': attr['id'],
                    'name': attr['name'],
                    'options': [value],
                    'visible': True,
                    'variation': False
                })
        
        # Handle multi-value attributes
        applications = robot_data.get('applications', [])
        if applications:
            attr = self.attributes_manager.get_attribute('applications')
            if attr:
                attributes.append({
                    'id': attr['id'],
                    'name': 'Applications',
                    'options': applications[:3],  # Limit to 3
                    'visible': True,
                    'variation': False
                })
        
        mounting_options = robot_data.get('mounting_options', [])
        if mounting_options:
            attr = self.attributes_manager.get_attribute('mounting-options')
            if attr:
                attributes.append({
                    'id': attr['id'],
                    'name': 'Mounting Options',
                    'options': mounting_options,
                    'visible': True,
                    'variation': False
                })
        
        return attributes
    
    def create_robot_product_with_attributes(self, robot_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create robot product using WooCommerce attributes"""
        
        try:
            name = robot_data.get('name', 'Unknown Robot')
            model = robot_data.get('model', '')
            sku = f"RB-FANUC-{model.replace('/', '-').replace(' ', '-')}".upper()
            
            self.logger.info(f"Creating product with attributes: {name} (SKU: {sku})")
            
            # Generate description
            description = robot_data.get('description', '')
            if not description:
                highlights = robot_data.get('highlights', [])
                if highlights:
                    description = f"{name} features: " + "; ".join(highlights[:3])
                else:
                    description = f"{name} - High-quality industrial robot for automation tasks."
            
            # Convert to attributes
            attributes = self.convert_robot_to_product_attributes(robot_data)
            
            # Minimal meta data (only non-attribute data)
            meta_data = []
            
            # Add non-attribute meta data
            if robot_data.get('repeatability'):
                meta_data.append({
                    'key': 'robot_repeatability',
                    'value': robot_data['repeatability']
                })
            
            if robot_data.get('mass'):
                meta_data.append({
                    'key': 'robot_mass',
                    'value': robot_data['mass']
                })
            
            if robot_data.get('source_url'):
                meta_data.append({
                    'key': 'robot_source_url',
                    'value': robot_data['source_url']
                })
            
            # Prepare product data
            product_data = {
                'name': name,
                'type': 'simple',
                'sku': sku,
                'status': 'publish',
                'catalog_visibility': 'visible',
                'description': description,
                'short_description': f"{name} - Industrial Robot with {robot_data.get('payload', 'N/A')} payload",
                'attributes': attributes,  # Use attributes instead of meta data
                'meta_data': meta_data,   # Only non-attribute meta data
                'categories': [{'name': 'Industrial Robots'}],
                'tags': [{'name': tag} for tag in robot_data.get('applications', [])[:3]]
            }
            
            # Create product
            result = self.wp_client.create_product(product_data)
            
            if result:
                self.logger.info(f"Successfully created product with {len(attributes)} attributes: {name}")
                return {'status': 'created', 'product_id': result.get('id'), 'name': name, 'attributes_count': len(attributes)}
            else:
                self.logger.error(f"Failed to create product: {name}")
                return {'status': 'failed', 'name': name, 'error': 'Creation failed'}
                
        except Exception as e:
            self.logger.error(f"Error creating product {robot_data.get('name', 'Unknown')}: {e}")
            return {'status': 'failed', 'name': robot_data.get('name', 'Unknown'), 'error': str(e)}


def main():
    """Main function to setup WooCommerce attributes system"""
    
    print("🏗️  WooCommerce Attributes System Setup")
    print("=" * 60)
    
    # Load robot data
    with open('data/test_robots_sample.json', 'r', encoding='utf-8') as f:
        robots_data = json.load(f)
    
    print(f"📋 Loaded {len(robots_data)} test robots")
    
    # Initialize managers
    attributes_manager = WooCommerceAttributesManager()
    
    # Test connection
    if not attributes_manager.wp_client.test_connection():
        print("❌ Failed to connect to WordPress")
        return False
    
    print("✅ WordPress connection successful!")
    
    # Setup attributes
    print("\n🔧 Setting up WooCommerce attributes...")
    setup_results = attributes_manager.setup_robot_attributes_from_data(robots_data)
    
    # Report results
    attr_results = setup_results['attributes']
    print(f"\n📊 ATTRIBUTE SETUP RESULTS:")
    print(f"   ✅ Created: {len(attr_results['created'])} attributes")
    print(f"   ℹ️  Existing: {len(attr_results['existing'])} attributes")
    print(f"   ❌ Failed: {len(attr_results['failed'])} attributes")
    
    if attr_results['created']:
        print(f"   New attributes: {', '.join(attr_results['created'])}")
    
    # Report term results
    term_results = setup_results['terms']
    total_terms_created = sum(result['created'].__len__() for result in term_results.values())
    total_terms_existing = sum(result['existing'].__len__() for result in term_results.values())
    
    print(f"\n📋 TERM SETUP RESULTS:")
    print(f"   ✅ Created: {total_terms_created} terms")
    print(f"   ℹ️  Existing: {total_terms_existing} terms")
    
    # Show unique values found
    unique_values = setup_results['unique_values']
    print(f"\n🔍 UNIQUE VALUES DISCOVERED:")
    for attr_name, values in unique_values.items():
        if values:
            print(f"   • {attr_name.title()}: {len(values)} values")
            print(f"     {', '.join(list(values)[:5])}")
    
    # Save results
    with open('data/woocommerce_attributes_setup.json', 'w', encoding='utf-8') as f:
        json.dump(setup_results, f, indent=2, ensure_ascii=False)
    
    print(f"\n💾 Setup results saved to: data/woocommerce_attributes_setup.json")
    
    print(f"\n🎯 NEXT STEPS:")
    print("   1. Verify attributes in WooCommerce admin")
    print("   2. Test product creation with attributes")
    print("   3. Enable frontend filtering")
    
    return True


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1) 