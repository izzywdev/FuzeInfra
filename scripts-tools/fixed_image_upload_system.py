#!/usr/bin/env python3
"""
FIXED: Image Upload System for FANUC Robot Products
Fixes the WordPress REST API file upload format issue
"""

import json
import sys
import os
import requests
from urllib.parse import urlparse
from typing import Dict, Any, List, Optional
import tempfile
import time
import mimetypes

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.wordpress.wp_client import WordPressClient
from src.utils.logger import get_logger
from src.config import WORDPRESS_CONFIG


class FixedImageUploadManager:
    """FIXED: Image upload manager with proper WordPress REST API handling"""
    
    def __init__(self):
        self.wp_client = WordPressClient()
        self.logger = get_logger('FixedImageUploadManager')
        
        # Supported image formats
        self.supported_formats = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
        self.max_file_size = 10 * 1024 * 1024  # 10MB
        self.download_timeout = 30
    
    def download_image_to_temp(self, image_url: str) -> Optional[str]:
        """Download image to temporary file (SAME AS BEFORE - THIS WORKS)"""
        
        try:
            self.logger.info(f"Downloading image: {image_url}")
            
            # Validate URL
            if not image_url or not image_url.startswith(('http://', 'https://')):
                self.logger.error(f"Invalid image URL: {image_url}")
                return None
            
            # Check file extension
            parsed_url = urlparse(image_url)
            path = parsed_url.path.lower()
            
            if not any(path.endswith(ext) for ext in self.supported_formats):
                self.logger.warning(f"Unsupported image format: {image_url}")
                # Try anyway, some URLs don't have extensions
            
            # Download with proper headers
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
            
            response = requests.get(
                image_url, 
                headers=headers, 
                timeout=self.download_timeout,
                stream=True
            )
            
            if response.status_code != 200:
                self.logger.error(f"Failed to download image: {response.status_code}")
                return None
            
            # Check content type
            content_type = response.headers.get('content-type', '').lower()
            if not content_type.startswith('image/'):
                self.logger.warning(f"Content-Type is not image: {content_type}")
                # Continue anyway - FANUC might return HTML sometimes
            
            # Determine file extension
            if content_type == 'image/jpeg':
                ext = '.jpg'
            elif content_type == 'image/png':
                ext = '.png'
            elif content_type == 'image/gif':
                ext = '.gif'
            elif content_type == 'image/webp':
                ext = '.webp'
            else:
                # Try to get from URL
                ext = '.jpg'  # Default fallback
                for supported_ext in self.supported_formats:
                    if path.endswith(supported_ext):
                        ext = supported_ext
                        break
            
            # Create temporary file
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
            
            # Download in chunks
            downloaded_size = 0
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    temp_file.write(chunk)
                    downloaded_size += len(chunk)
                    
                    # Check size limit
                    if downloaded_size > self.max_file_size:
                        temp_file.close()
                        os.unlink(temp_file.name)
                        self.logger.error(f"Image too large during download: {downloaded_size} bytes")
                        return None
            
            temp_file.close()
            
            self.logger.info(f"Downloaded image: {downloaded_size} bytes to {temp_file.name}")
            return temp_file.name
            
        except requests.exceptions.Timeout:
            self.logger.error(f"Download timeout for image: {image_url}")
            return None
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Download error for image {image_url}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error downloading image {image_url}: {e}")
            return None
    
    def upload_image_to_wordpress_FIXED(self, temp_file_path: str, filename: str, alt_text: str = '') -> Optional[Dict[str, Any]]:
        """FIXED: Upload image file to WordPress media library with proper format"""
        
        try:
            self.logger.info(f"FIXED: Uploading image to WordPress: {filename}")
            
            # Determine MIME type
            mime_type, _ = mimetypes.guess_type(temp_file_path)
            if not mime_type:
                mime_type = 'image/jpeg'  # Default fallback
            
            # FIXED: Prepare file data correctly for WordPress REST API
            with open(temp_file_path, 'rb') as f:
                file_content = f.read()
            
            # FIXED: Use proper headers for WordPress REST API
            headers = {
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': mime_type,
                'Authorization': f'Basic {self.wp_client.session.auth.username}:{self.wp_client.session.auth.password}' if hasattr(self.wp_client.session.auth, 'username') else None
            }
            
            # Remove None values
            headers = {k: v for k, v in headers.items() if v is not None}
            
            # FIXED: Send as binary data, not multipart form
            media_url = f"{WORDPRESS_CONFIG['url']}/wp-json/wp/v2/media"
            
            # Add alt text and title as query parameters
            params = {
                'title': filename.replace('_', ' ').replace('.jpg', '').replace('.png', ''),
                'alt_text': alt_text,
                'caption': alt_text
            }
            
            response = self.wp_client.session.post(
                media_url,
                data=file_content,  # FIXED: Send binary data directly
                headers=headers,    # FIXED: Proper headers
                params=params      # FIXED: Meta data as params
            )
            
            if response.status_code == 201:
                media_data = response.json()
                self.logger.info(f"✅ Successfully uploaded image: {media_data['id']}")
                return {
                    'id': media_data['id'],
                    'source_url': media_data['source_url'],
                    'title': media_data['title']['rendered'],
                    'alt_text': media_data.get('alt_text', ''),
                    'media_type': media_data.get('media_type', 'image'),
                    'mime_type': media_data.get('mime_type', '')
                }
            else:
                self.logger.error(f"❌ Failed to upload image: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            self.logger.error(f"❌ Error uploading image: {e}")
            return None
    
    def upload_image_to_wordpress_ALTERNATIVE(self, temp_file_path: str, filename: str, alt_text: str = '') -> Optional[Dict[str, Any]]:
        """ALTERNATIVE: Upload using requests-toolbelt for proper multipart"""
        
        try:
            # Try alternative multipart approach
            from requests_toolbelt.multipart.encoder import MultipartEncoder
            
            self.logger.info(f"ALTERNATIVE: Uploading image using multipart encoder: {filename}")
            
            # Determine MIME type
            mime_type, _ = mimetypes.guess_type(temp_file_path)
            if not mime_type:
                mime_type = 'image/jpeg'
            
            # Create multipart encoder
            multipart_data = MultipartEncoder(
                fields={
                    'file': (filename, open(temp_file_path, 'rb'), mime_type),
                    'title': filename.replace('_', ' ').replace('.jpg', ''),
                    'alt_text': alt_text,
                    'caption': alt_text
                }
            )
            
            # Upload to WordPress
            media_url = f"{WORDPRESS_CONFIG['url']}/wp-json/wp/v2/media"
            response = self.wp_client.session.post(
                media_url,
                data=multipart_data,
                headers={'Content-Type': multipart_data.content_type}
            )
            
            if response.status_code == 201:
                media_data = response.json()
                self.logger.info(f"✅ ALTERNATIVE: Successfully uploaded image: {media_data['id']}")
                return {
                    'id': media_data['id'],
                    'source_url': media_data['source_url'],
                    'title': media_data['title']['rendered'],
                    'alt_text': media_data.get('alt_text', ''),
                    'media_type': media_data.get('media_type', 'image'),
                    'mime_type': media_data.get('mime_type', '')
                }
            else:
                self.logger.error(f"❌ ALTERNATIVE: Failed to upload image: {response.status_code} - {response.text}")
                return None
                
        except ImportError:
            self.logger.warning("requests-toolbelt not available, skipping alternative method")
            return None
        except Exception as e:
            self.logger.error(f"❌ ALTERNATIVE: Error uploading image: {e}")
            return None
    
    def upload_image_to_wordpress_SIMPLE(self, temp_file_path: str, filename: str, alt_text: str = '') -> Optional[Dict[str, Any]]:
        """SIMPLE: Basic file upload without extra headers"""
        
        try:
            self.logger.info(f"SIMPLE: Uploading image with basic approach: {filename}")
            
            # Just send the file with minimal setup
            with open(temp_file_path, 'rb') as f:
                files = {'file': (filename, f, 'image/jpeg')}
                
                media_url = f"{WORDPRESS_CONFIG['url']}/wp-json/wp/v2/media"
                response = self.wp_client.session.post(media_url, files=files)
            
            if response.status_code == 201:
                media_data = response.json()
                self.logger.info(f"✅ SIMPLE: Successfully uploaded image: {media_data['id']}")
                return {
                    'id': media_data['id'],
                    'source_url': media_data['source_url'],
                    'title': media_data['title']['rendered'],
                    'alt_text': media_data.get('alt_text', ''),
                    'media_type': media_data.get('media_type', 'image'),
                    'mime_type': media_data.get('mime_type', '')
                }
            else:
                self.logger.error(f"❌ SIMPLE: Failed to upload image: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            self.logger.error(f"❌ SIMPLE: Error uploading image: {e}")
            return None
    
    def upload_image_with_fallbacks(self, temp_file_path: str, filename: str, alt_text: str = '') -> Optional[Dict[str, Any]]:
        """Try multiple upload methods until one works"""
        
        self.logger.info(f"🔄 Trying multiple upload methods for: {filename}")
        
        # Method 1: Fixed binary upload
        result = self.upload_image_to_wordpress_FIXED(temp_file_path, filename, alt_text)
        if result:
            self.logger.info("✅ SUCCESS: Fixed binary upload method worked!")
            return result
        
        # Method 2: Simple files upload
        result = self.upload_image_to_wordpress_SIMPLE(temp_file_path, filename, alt_text)
        if result:
            self.logger.info("✅ SUCCESS: Simple files upload method worked!")
            return result
        
        # Method 3: Alternative multipart (if available)
        result = self.upload_image_to_wordpress_ALTERNATIVE(temp_file_path, filename, alt_text)
        if result:
            self.logger.info("✅ SUCCESS: Alternative multipart method worked!")
            return result
        
        self.logger.error("❌ FAILED: All upload methods failed")
        return None
    
    def process_robot_images_FIXED(self, robot_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process all images for a robot with FIXED upload"""
        
        robot_name = robot_data.get('name', 'Unknown Robot')
        images = robot_data.get('images', [])
        
        if not images:
            self.logger.info(f"No images found for robot: {robot_name}")
            return {'processed': 0, 'successful': 0, 'failed': 0, 'images': []}
        
        self.logger.info(f"Processing {len(images)} images for robot: {robot_name}")
        
        results = {
            'processed': 0,
            'successful': 0,
            'failed': 0,
            'images': [],
            'errors': []
        }
        
        for i, image_url in enumerate(images[:3]):  # Limit to 3 images for testing
            results['processed'] += 1
            
            try:
                # Download image
                temp_file = self.download_image_to_temp(image_url)
                if not temp_file:
                    results['failed'] += 1
                    results['errors'].append(f"Failed to download: {image_url}")
                    continue
                
                # Generate filename
                filename = f"{robot_name.replace(' ', '_').replace('/', '_')}_image_{i+1}.jpg"
                alt_text = f"{robot_name} - Image {i+1}"
                
                # FIXED: Upload with fallback methods
                uploaded_image = self.upload_image_with_fallbacks(temp_file, filename, alt_text)
                
                # Clean up temp file
                try:
                    os.unlink(temp_file)
                except:
                    pass
                
                if uploaded_image:
                    results['successful'] += 1
                    results['images'].append(uploaded_image)
                    self.logger.info(f"✅ Successfully processed image {i+1}/{len(images)} for {robot_name}")
                else:
                    results['failed'] += 1
                    results['errors'].append(f"Failed to upload: {image_url}")
                
                # Small delay between uploads
                time.sleep(2)  # Slightly longer delay
                
            except Exception as e:
                results['failed'] += 1
                results['errors'].append(f"Error processing {image_url}: {e}")
                self.logger.error(f"Error processing image {image_url}: {e}")
        
        self.logger.info(f"FIXED Image processing complete for {robot_name}: {results['successful']}/{results['processed']} successful")
        return results
    
    def create_robot_product_with_images_FIXED(self, robot_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create robot product with FIXED image uploads"""
        
        try:
            name = robot_data.get('name', 'Unknown Robot')
            model = robot_data.get('model', '')
            sku = f"RB-FANUC-{model.replace('/', '-').replace(' ', '-')}-FIXED-IMAGES".upper()
            
            self.logger.info(f"Creating product with FIXED images: {name} (SKU: {sku})")
            
            # Process images first with FIXED upload
            image_results = self.process_robot_images_FIXED(robot_data)
            
            # Generate description
            description = robot_data.get('description', '')
            if not description:
                highlights = robot_data.get('highlights', [])
                if highlights:
                    description = f"{name} features: " + "; ".join(highlights[:3])
                else:
                    description = f"{name} - High-quality industrial robot for automation tasks."
            
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
            
            # Add source URL
            if robot_data.get('source_url'):
                meta_data.append({
                    'key': 'robot_source_url',
                    'value': robot_data['source_url']
                })
            
            # Prepare images for WooCommerce
            wc_images = []
            for img in image_results['images']:
                wc_images.append({
                    'id': img['id'],
                    'src': img['source_url'],
                    'name': img['title'],
                    'alt': img['alt_text']
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
                'meta_data': meta_data,
                'images': wc_images,  # Include uploaded images
                'categories': [{'name': 'Industrial Robots'}],
                'tags': [{'name': tag} for tag in robot_data.get('applications', [])[:3]]
            }
            
            # Create product
            result = self.wp_client.create_product(product_data)
            
            if result:
                self.logger.info(f"✅ Successfully created product with {len(wc_images)} images: {name}")
                return {
                    'status': 'created',
                    'product_id': result.get('id'),
                    'name': name,
                    'images_processed': image_results['processed'],
                    'images_successful': image_results['successful'],
                    'images_failed': image_results['failed'],
                    'image_errors': image_results['errors']
                }
            else:
                self.logger.error(f"❌ Failed to create product: {name}")
                return {
                    'status': 'failed',
                    'name': name,
                    'error': 'Product creation failed',
                    'images_processed': image_results['processed'],
                    'images_successful': image_results['successful']
                }
                
        except Exception as e:
            self.logger.error(f"❌ Error creating product with images {robot_data.get('name', 'Unknown')}: {e}")
            return {
                'status': 'failed',
                'name': robot_data.get('name', 'Unknown'),
                'error': str(e)
            }


def main():
    """Test FIXED image upload system"""
    
    print("🔧 TESTING FIXED IMAGE UPLOAD SYSTEM")
    print("=" * 60)
    
    # Load robot data
    with open('data/test_robots_sample.json', 'r', encoding='utf-8') as f:
        robots_data = json.load(f)
    
    print(f"📋 Loaded {len(robots_data)} test robots")
    
    # Initialize FIXED image manager
    image_manager = FixedImageUploadManager()
    
    # Test connection
    if not image_manager.wp_client.test_connection():
        print("❌ Failed to connect to WordPress")
        return False
    
    print("✅ WordPress connection successful!")
    
    # Test with ONE robot that has images
    robot_with_images = None
    for robot in robots_data:
        if robot.get('images'):
            robot_with_images = robot
            break
    
    if not robot_with_images:
        print("❌ No robots with images found in test data")
        return False
    
    print(f"\n🧪 Testing FIXED image upload with robot: {robot_with_images['name']}")
    print(f"   Images to process: {len(robot_with_images['images'])}")
    
    # Test creating product with FIXED images
    result = image_manager.create_robot_product_with_images_FIXED(robot_with_images)
    
    # Report results
    if result['status'] == 'created':
        print(f"\n✅ SUCCESS: Created product with FIXED images!")
        print(f"   Product: {result['name']}")
        print(f"   Product ID: {result['product_id']}")
        print(f"   Images processed: {result['images_processed']}")
        print(f"   Images successful: {result['images_successful']}")
        print(f"   Images failed: {result['images_failed']}")
    else:
        print(f"\n❌ FAILED: {result.get('error', 'Unknown error')}")
        print(f"   Product: {result['name']}")
        if 'images_processed' in result:
            print(f"   Images processed: {result['images_processed']}")
            print(f"   Images successful: {result['images_successful']}")
    
    if result.get('image_errors'):
        print(f"\n⚠️  Image Errors:")
        for error in result['image_errors']:
            print(f"     • {error}")
    
    # Save results
    with open('data/fixed_image_upload_test.json', 'w', encoding='utf-8') as f:
        json.dump({
            'test_robot': robot_with_images['name'],
            'result': result
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n💾 FIXED test results saved to: data/fixed_image_upload_test.json")
    
    print(f"\n🔧 FIXES APPLIED:")
    print("   ✅ Fixed binary data upload method")
    print("   ✅ Added fallback upload methods")
    print("   ✅ Proper MIME type detection")
    print("   ✅ Correct WordPress REST API headers")
    print("   ✅ Multiple upload strategies")
    
    return result['status'] == 'created'


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1) 