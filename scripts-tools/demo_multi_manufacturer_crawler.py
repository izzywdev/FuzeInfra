#!/usr/bin/env python3
"""
Demo: Multi-Manufacturer Robot Catalog Crawler
Showcases the new crawler system that discovers robots from multiple manufacturers
and automatically manages brands and products in WordPress
"""

import sys
import os
import json
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.wordpress.wp_client import WordPressClient
from src.crawlers.robot_catalog_crawler import RobotCatalogCrawler
from src.utils.logger import get_logger


def demo_multi_manufacturer_crawler():
    """Demonstrate the multi-manufacturer robot catalog crawler"""
    
    print("🤖 MULTI-MANUFACTURER ROBOT CATALOG CRAWLER DEMO")
    print("=" * 80)
    print("🎯 This demo showcases automatic robot discovery across manufacturers")
    print("📋 Features:")
    print("   • Discovers robots from multiple manufacturers (FANUC, ABB, etc.)")
    print("   • Automatically creates/manages brands as WordPress categories")
    print("   • Creates complete product listings with specifications")
    print("   • Handles duplicate detection and updates")
    print("   • Provides comprehensive statistics and reporting")
    
    # Initialize system
    print(f"\n🔧 INITIALIZING MULTI-MANUFACTURER CRAWLER")
    print("-" * 60)
    
    wp_client = WordPressClient()
    
    if not wp_client.test_connection():
        print("❌ Failed to connect to WordPress")
        return False
    
    print("✅ WordPress connection successful!")
    
    catalog_crawler = RobotCatalogCrawler(wp_client)
    
    # Show available manufacturers
    manufacturers = catalog_crawler.list_available_manufacturers()
    print(f"\n📋 Available manufacturers: {', '.join(manufacturers)}")
    
    # Demo 1: Test mode crawl (no actual products created)
    print(f"\n🧪 DEMO 1: TEST MODE CRAWL")
    print("-" * 60)
    print("📝 Running in test mode - discovers robots but doesn't create products")
    
    test_results = catalog_crawler.crawl_all_manufacturers(
        manufacturers=['fanuc'],  # Start with FANUC only
        limit_per_manufacturer=3,  # Limit to 3 robots
        test_mode=True
    )
    
    print(f"\n📊 TEST MODE RESULTS:")
    print(f"   Robots discovered: {test_results['summary']['total_robots_discovered']}")
    print(f"   Duration: {test_results['summary']['duration']}")
    print(f"   Success rate: {test_results['summary']['success_rate']:.1f}%")
    
    # Demo 2: Brand management showcase
    print(f"\n🏭 DEMO 2: BRAND MANAGEMENT")
    print("-" * 60)
    
    # Get current brand statistics
    brand_stats = catalog_crawler.get_brand_statistics()
    print(f"📈 Current brand statistics:")
    print(f"   Total brands: {brand_stats['total_brands']}")
    print(f"   Brands with products: {brand_stats['brands_with_products']}")
    print(f"   Total robot products: {brand_stats['total_products']}")
    
    if brand_stats['brand_breakdown']:
        print(f"\n🏷️  Brand breakdown:")
        for brand, info in brand_stats['brand_breakdown'].items():
            print(f"   • {brand}: {info['products']} products (ID: {info['id']})")
    
    # Demo 3: Single manufacturer crawl (production mode)
    print(f"\n🚀 DEMO 3: PRODUCTION CRAWL - SINGLE MANUFACTURER")
    print("-" * 60)
    print("⚠️  This will create actual products on your WordPress site!")
    
    proceed = input("Do you want to proceed with production crawl? (y/N): ").lower().strip()
    
    if proceed == 'y':
        print(f"\n🏭 Starting FANUC production crawl...")
        
        production_results = catalog_crawler.crawl_single_manufacturer(
            manufacturer_name='fanuc',
            limit=2,  # Limit to 2 robots for demo
            test_mode=False
        )
        
        print(f"\n🎉 PRODUCTION CRAWL COMPLETE!")
        print(f"📊 Results:")
        print(f"   Robots discovered: {production_results['summary']['total_robots_discovered']}")
        print(f"   Robots processed: {production_results['summary']['total_robots_processed']}")
        print(f"   Products created: {production_results['summary']['products_created']}")
        print(f"   Products updated: {production_results['summary']['products_updated']}")
        print(f"   Brands created: {production_results['summary']['brands_created']}")
        print(f"   Success rate: {production_results['summary']['success_rate']:.1f}%")
        print(f"   Duration: {production_results['summary']['duration']}")
        
        # Show manufacturer-specific results
        for manufacturer, stats in production_results['by_manufacturer'].items():
            print(f"\n🏭 {manufacturer.upper()} Results:")
            print(f"   • Robots discovered: {stats['robots_discovered']}")
            print(f"   • Products created: {stats['products_created']}")
            print(f"   • Products updated: {stats['products_updated']}")
            print(f"   • Brand created: {stats['brand_created']}")
            print(f"   • Errors: {stats['errors']}")
        
        # Save detailed results
        results_file = f"data/multi_manufacturer_demo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        os.makedirs('data', exist_ok=True)
        
        with open(results_file, 'w', encoding='utf-8') as f:
            json.dump(production_results, f, indent=2, ensure_ascii=False)
        
        print(f"\n💾 Detailed results saved: {results_file}")
        
    else:
        print("📝 Skipping production crawl")
    
    # Demo 4: Show updated brand statistics
    print(f"\n📈 DEMO 4: UPDATED STATISTICS")
    print("-" * 60)
    
    updated_brand_stats = catalog_crawler.get_brand_statistics()
    print(f"📊 Updated brand statistics:")
    print(f"   Total brands: {updated_brand_stats['total_brands']}")
    print(f"   Brands with products: {updated_brand_stats['brands_with_products']}")
    print(f"   Total robot products: {updated_brand_stats['total_products']}")
    
    # Show growth
    product_growth = updated_brand_stats['total_products'] - brand_stats['total_products']
    if product_growth > 0:
        print(f"   📈 Growth: +{product_growth} new robot products!")
    
    # Demo 5: Multi-manufacturer possibilities
    print(f"\n🌐 DEMO 5: MULTI-MANUFACTURER EXPANSION")
    print("-" * 60)
    print("🚀 Future capabilities:")
    print("   • Add ABB robots: catalog_crawler.crawl_single_manufacturer('abb')")
    print("   • Add Universal Robots: catalog_crawler.add_crawler('ur', UniversalRobotsCrawler())")
    print("   • Add KUKA robots: catalog_crawler.add_crawler('kuka', KukaCrawler())")
    print("   • Crawl all at once: catalog_crawler.crawl_all_manufacturers()")
    
    print(f"\n💡 Extension examples:")
    print("   • Automatic competitive analysis across brands")
    print("   • Price monitoring and updates")
    print("   • Specification comparison tables")
    print("   • Market trends and insights")
    print("   • Customer preference analytics")
    
    print(f"\n🎉 MULTI-MANUFACTURER CRAWLER DEMO COMPLETE!")
    print("=" * 80)
    print("🌟 Your robot catalog is now powered by intelligent multi-manufacturer discovery!")
    print("🚀 Ready to scale across the entire industrial robot market!")
    
    return True


def show_system_architecture():
    """Show the system architecture"""
    
    print("\n🏗️  SYSTEM ARCHITECTURE")
    print("=" * 60)
    print("📁 Multi-Manufacturer Crawler Structure:")
    print("""
    src/crawlers/
    ├── base_crawler.py          # Abstract base crawler framework
    ├── fanuc_crawler.py         # FANUC-specific implementation  
    ├── abb_crawler.py           # ABB-specific implementation
    ├── robot_catalog_crawler.py # Main orchestrator
    └── __init__.py              # Package initialization
    
    src/wordpress/
    ├── wp_client.py             # Enhanced WordPress API client
    ├── brand_manager.py         # Brand/manufacturer management
    └── product_manager.py       # Existing product management
    
    Key Components:
    🤖 RobotSpec               # Standardized robot data structure
    🏭 ManufacturerInfo        # Brand information structure  
    🔧 BaseCrawler             # Extensible crawler framework
    📊 BrandManager            # WordPress brand orchestration
    🚀 RobotCatalogCrawler     # Main system coordinator
    """)
    
    print("🔄 Process Flow:")
    print("1. Discover robot pages from manufacturer websites")
    print("2. Extract standardized robot specifications")
    print("3. Get/create manufacturer brand in WordPress")
    print("4. Generate professional product descriptions")
    print("5. Create/update WordPress products with complete data")
    print("6. Track statistics and generate reports")
    
    print(f"\n✨ Benefits:")
    print("• 🌐 Scalable across any number of manufacturers")
    print("• 🔄 Automatic brand management and organization")
    print("• 📊 Standardized data structure for consistency")
    print("• 🛡️  Duplicate detection and smart updates")
    print("• 📈 Comprehensive analytics and reporting")
    print("• 🚀 Professional e-commerce ready products")


if __name__ == '__main__':
    try:
        show_system_architecture()
        success = demo_multi_manufacturer_crawler()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n⏸️  Demo interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Demo error: {e}")
        sys.exit(1) 