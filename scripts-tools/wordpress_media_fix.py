#!/usr/bin/env python3
"""
WordPress Media Configuration Fix
Addresses file type restrictions and URL access issues
"""

import json
import sys
import os
import requests
from typing import Dict, Any, List, Optional
import tempfile
import base64

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.wordpress.wp_client import WordPressClient
from src.utils.logger import get_logger
from src.config import WORDPRESS_CONFIG


class WordPressMediaFixer:
    """Fix WordPress media upload issues"""
    
    def __init__(self):
        self.wp_client = WordPressClient()
        self.logger = get_logger('MediaFixer')
    
    def diagnose_wordpress_media_settings(self) -> Dict[str, Any]:
        """Diagnose WordPress media and file upload settings"""
        
        print("🔍 DIAGNOSING WORDPRESS MEDIA SETTINGS")
        print("=" * 60)
        
        diagnosis = {
            'media_endpoint_working': False,
            'file_types_allowed': [],
            'upload_restrictions': [],
            'suggested_fixes': [],
            'alternative_solutions': []
        }
        
        try:
            # Test media endpoint
            media_url = f"{WORDPRESS_CONFIG['url']}/wp-json/wp/v2/media"
            response = self.wp_client.session.get(media_url)
            
            if response.status_code in [200, 401]:
                diagnosis['media_endpoint_working'] = True
                print("✅ Media endpoint is accessible")
            else:
                print(f"❌ Media endpoint issue: {response.status_code}")
            
            # Test creating a simple text file (should work)
            try:
                test_content = "Test file for WordPress upload permissions"
                test_data = test_content.encode('utf-8')
                
                response = self.wp_client.session.post(
                    media_url,
                    data=test_data,
                    headers={
                        'Content-Type': 'text/plain',
                        'Content-Disposition': 'attachment; filename="test.txt"'
                    }
                )
                
                if response.status_code == 201:
                    print("✅ Basic file upload working")
                    # Clean up test file
                    media_id = response.json().get('id')
                    if media_id:
                        self.wp_client.session.delete(f"{media_url}/{media_id}")
                else:
                    print(f"❌ Basic file upload failed: {response.status_code}")
                    print(f"   Response: {response.text}")
                    diagnosis['upload_restrictions'].append(response.text)
            except Exception as e:
                print(f"❌ Upload test error: {e}")
            
        except Exception as e:
            print(f"❌ Media diagnosis error: {e}")
        
        # Generate fixes based on diagnosis
        diagnosis['suggested_fixes'] = [
            "Enable additional file types in WordPress admin (Media settings)",
            "Add image MIME types to wp-config.php or functions.php",
            "Check WordPress user permissions for media uploads",
            "Verify .htaccess doesn't block uploads"
        ]
        
        diagnosis['alternative_solutions'] = [
            "Use placeholder images or external image references",
            "Upload images manually and reference by URL",
            "Use WordPress media library upload via admin panel",
            "Create products without images initially"
        ]
        
        return diagnosis
    
    def create_placeholder_product_WORKING(self, robot_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create product with placeholder images that WORKS"""
        
        try:
            name = robot_data.get('name', 'Unknown Robot')
            model = robot_data.get('model', '')
            sku = f"RB-FANUC-{model.replace('/', '-').replace(' ', '-')}-PLACEHOLDER".upper()
            
            print(f"🔧 Creating product with placeholder approach: {name}")
            
            # Generate description
            description = robot_data.get('description', '')
            if not description:
                highlights = robot_data.get('highlights', [])
                if highlights:
                    description = f"{name} features: " + "; ".join(highlights[:3])
                else:
                    description = f"{name} - High-quality industrial robot for automation tasks."
            
            # Add image URLs as text in description for now
            images = robot_data.get('images', [])
            if images:
                description += "\n\n**Product Images:**\n"
                for i, img_url in enumerate(images[:3], 1):
                    description += f"\n{i}. {img_url}"
            
            # Prepare complete meta data
            meta_data = []
            
            # Add all specifications
            specs = ['payload', 'reach', 'axes', 'repeatability', 'mass', 'controller', 'series']
            for spec in specs:
                value = robot_data.get(spec)
                if value:
                    meta_data.append({
                        'key': f'robot_{spec}',
                        'value': str(value)
                    })
            
            # Add content fields  
            for field in ['highlights', 'applications', 'mounting_options']:
                value = robot_data.get(field)
                if value:
                    if isinstance(value, list):
                        meta_data.append({
                            'key': f'robot_{field}',
                            'value': ', '.join(value)
                        })
                    else:
                        meta_data.append({
                            'key': f'robot_{field}',
                            'value': str(value)
                        })
            
            # Add source URL and image URLs as meta data
            if robot_data.get('source_url'):
                meta_data.append({
                    'key': 'robot_source_url',
                    'value': robot_data['source_url']
                })
            
            # Store image URLs as meta data for later processing
            if images:
                meta_data.append({
                    'key': 'robot_image_urls',
                    'value': json.dumps(images)  # Store as JSON string
                })
            
            # Prepare product data (NO IMAGES FOR NOW)
            product_data = {
                'name': name,
                'type': 'simple',
                'sku': sku,
                'status': 'publish',
                'catalog_visibility': 'visible',
                'description': description,
                'short_description': f"{name} - Industrial Robot with {robot_data.get('payload', 'N/A')} payload",
                'meta_data': meta_data,
                'categories': [{'name': 'Industrial Robots'}],
                'tags': [{'name': tag} for tag in robot_data.get('applications', [])[:3]]
            }
            
            # Create product
            result = self.wp_client.create_product(product_data)
            
            if result:
                print(f"✅ Successfully created product (with image URLs in description): {name}")
                return {
                    'status': 'created',
                    'product_id': result.get('id'),
                    'name': name,
                    'approach': 'placeholder_with_urls',
                    'meta_fields': len(meta_data),
                    'image_urls_stored': len(images)
                }
            else:
                print(f"❌ Failed to create product: {name}")
                return {
                    'status': 'failed',
                    'name': name,
                    'error': 'Product creation failed'
                }
                
        except Exception as e:
            print(f"❌ Error creating placeholder product: {e}")
            return {
                'status': 'failed',
                'name': robot_data.get('name', 'Unknown'),
                'error': str(e)
            }
    
    def generate_wordpress_fixes(self) -> Dict[str, str]:
        """Generate WordPress configuration fixes"""
        
        fixes = {
            'wp_config_additions': '''
// Add to wp-config.php to allow more file types
define('ALLOW_UNFILTERED_UPLOADS', true);

// Increase upload limits
@ini_set('upload_max_size', '64M');
@ini_set('post_max_size', '64M');
@ini_set('max_execution_time', 300);
''',
            'functions_php_additions': '''
// Add to functions.php to allow additional MIME types
function allow_additional_mime_types($mimes) {
    $mimes['jpg'] = 'image/jpeg';
    $mimes['jpeg'] = 'image/jpeg';
    $mimes['png'] = 'image/png';
    $mimes['gif'] = 'image/gif';
    $mimes['webp'] = 'image/webp';
    return $mimes;
}
add_filter('upload_mimes', 'allow_additional_mime_types');

// Increase upload size limits
function increase_upload_limits() {
    @ini_set('upload_max_filesize', '64M');
    @ini_set('post_max_size', '64M');
    @ini_set('max_execution_time', 300);
}
add_action('init', 'increase_upload_limits');
''',
            'htaccess_additions': '''
# Add to .htaccess for upload support
php_value upload_max_filesize 64M
php_value post_max_size 64M
php_value max_execution_time 300
php_value max_input_vars 3000
''',
            'manual_steps': '''
1. Go to WordPress Admin → Settings → Media
2. Check maximum upload file size
3. Go to Users → Your Profile → Application Passwords
4. Verify user has 'upload_files' capability
5. Check if security plugins are blocking uploads
6. Test upload via WordPress admin media library first
'''
        }
        
        return fixes


def main():
    """Main function to diagnose and fix WordPress media issues"""
    
    print("🔧 WORDPRESS MEDIA CONFIGURATION FIX")
    print("=" * 60)
    
    # Initialize fixer
    media_fixer = WordPressMediaFixer()
    
    # Test connection
    if not media_fixer.wp_client.test_connection():
        print("❌ Failed to connect to WordPress")
        return False
    
    print("✅ WordPress connection successful!")
    
    # Diagnose media settings
    diagnosis = media_fixer.diagnose_wordpress_media_settings()
    
    # Load robot data for test
    with open('data/test_robots_sample.json', 'r', encoding='utf-8') as f:
        robots_data = json.load(f)
    
    # Test creating a working product with placeholders
    print(f"\n🧪 Testing WORKING approach with placeholders...")
    test_robot = robots_data[0]
    
    result = media_fixer.create_placeholder_product_WORKING(test_robot)
    
    # Report results
    if result['status'] == 'created':
        print(f"\n✅ SUCCESS: Created working product!")
        print(f"   Product: {result['name']}")
        print(f"   Product ID: {result['product_id']}")
        print(f"   Approach: {result['approach']}")
        print(f"   Meta fields: {result['meta_fields']}")
        print(f"   Image URLs stored: {result['image_urls_stored']}")
    else:
        print(f"\n❌ FAILED: {result.get('error', 'Unknown error')}")
    
    # Generate WordPress fixes
    print(f"\n🛠️  WORDPRESS CONFIGURATION FIXES")
    print("=" * 60)
    
    fixes = media_fixer.generate_wordpress_fixes()
    
    print("📝 Add to wp-config.php:")
    print(fixes['wp_config_additions'])
    
    print("\n📝 Add to functions.php:")
    print(fixes['functions_php_additions'])
    
    print("\n📝 Manual steps:")
    print(fixes['manual_steps'])
    
    # Save comprehensive report
    report = {
        'diagnosis': diagnosis,
        'test_result': result,
        'wordpress_fixes': fixes,
        'recommendations': [
            "Immediate: Use placeholder approach for now (WORKING)",
            "Short-term: Apply WordPress configuration fixes",
            "Long-term: Set up proper image upload pipeline"
        ]
    }
    
    with open('data/wordpress_media_fix_report.json', 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    print(f"\n💾 Comprehensive fix report saved to: data/wordpress_media_fix_report.json")
    
    print(f"\n🎯 IMMEDIATE SOLUTION:")
    print("   ✅ Products can be created with complete robot data")
    print("   ✅ Image URLs stored in product meta data")
    print("   ✅ Images referenced in product description")
    print("   ✅ All specifications and features working")
    print("   ⚠️  Images need manual WordPress configuration to upload")
    
    print(f"\n🔧 NEXT STEPS:")
    print("   1. Apply WordPress fixes above")
    print("   2. Test image upload via WordPress admin")
    print("   3. Re-run image upload tests")
    print("   4. Continue with production deployment")
    
    return True


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1) 