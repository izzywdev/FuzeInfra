#!/usr/bin/env python3
"""
Mendys Robot Scraper for SmartHubShopper.com

This script scrapes robot data from multiple manufacturers
and syncs it to the WordPress/WooCommerce site at smarthubshopper.com
"""

import argparse
import json
import sys
import os
from typing import Optional
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.scrapers.fanuc_scraper import FanucScraper
from src.wordpress.product_manager import ProductManager
from src.utils.logger import get_logger
from src.config import DATA_CONFIG
from src.crawlers.robot_catalog_crawler import RobotCatalogCrawler
from src.wordpress.wp_client import WordPressClient
from src.database.mongo_client import RobotCatalogDB
from src.discovery.site_discovery_service import SiteDiscoveryService
from src.discovery.site_state_manager import SiteStateManager, SiteStatus
from src.discovery.ai_discovery_service import AIDiscoveryService
from src.crawlers.multi_site_crawler import MultiSiteCrawler

# Try to import Scrapy components
try:
    from src.scrapers.scrapy_runner import ScrapyManager
    SCRAPY_AVAILABLE = True
except ImportError:
    SCRAPY_AVAILABLE = False


def setup_directories():
    """Create necessary directories"""
    directories = [
        DATA_CONFIG['output_dir'],
        DATA_CONFIG['images_dir'],
        DATA_CONFIG['logs_dir']
    ]
    
    for directory in directories:
        os.makedirs(directory, exist_ok=True)


def scrape_fanuc_robots(limit: Optional[int] = None, use_scrapy: bool = False) -> bool:
    """Scrape FANUC robots"""
    logger = get_logger('main')
    
    try:
        if use_scrapy and SCRAPY_AVAILABLE:
            logger.info("Using Scrapy framework for scraping...")
            
            # Use Scrapy
            scrapy_manager = ScrapyManager()
            robots_data = scrapy_manager.scrape_fanuc_robots(
                limit=limit, 
                sync_to_wordpress=False  # We'll sync separately
            )
            
            if robots_data:
                logger.info(f"Successfully scraped {len(robots_data)} robots using Scrapy")
                return True
            else:
                logger.error("Scrapy scraping failed")
                return False
                
        else:
            if use_scrapy and not SCRAPY_AVAILABLE:
                logger.warning("Scrapy not available, falling back to basic scraper")
            
            logger.info("Using basic scraper for scraping...")
            
            # Use original scraper
            scraper = FanucScraper()
            robots_data = scraper.scrape()
            
            if limit and len(robots_data) > limit:
                logger.info(f"Limiting results to {limit} robots")
                robots_data = robots_data[:limit]
            
            logger.info(f"Successfully scraped {len(robots_data)} robots")
            
            # Save data
            output_file = DATA_CONFIG['robot_data_file']
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(robots_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Robot data saved to {output_file}")
            return True
        
    except Exception as e:
        logger.error(f"Error during scraping: {e}")
        return False
    finally:
        # Cleanup
        if 'scraper' in locals():
            scraper.close_driver()


def sync_to_wordpress(data_file: Optional[str] = None, dry_run: bool = False) -> bool:
    """Sync robot data to WordPress"""
    logger = get_logger('main')
    
    try:
        # Load robot data
        data_file = data_file or DATA_CONFIG['robot_data_file']
        
        if not os.path.exists(data_file):
            logger.error(f"Robot data file not found: {data_file}")
            logger.info("Run scraping first with: python main.py scrape")
            return False
        
        with open(data_file, 'r', encoding='utf-8') as f:
            robots_data = json.load(f)
        
        logger.info(f"Loaded {len(robots_data)} robots from {data_file}")
        
        if dry_run:
            logger.info("DRY RUN MODE - No changes will be made to WordPress")
            # Just validate the data and show what would be done
            for robot in robots_data[:5]:  # Show first 5
                logger.info(f"Would sync: {robot.get('name', 'Unknown')} ({robot.get('model', 'Unknown')})")
            return True
        
        # Initialize product manager
        product_manager = ProductManager()
        
        # Test WordPress connection
        if not product_manager.wp_client.test_connection():
            logger.error("Failed to connect to WordPress. Check your credentials.")
            return False
        
        # Sync robots to WordPress
        logger.info("Starting WordPress sync...")
        results = product_manager.sync_robots_to_wordpress(robots_data)
        
        # Report results
        logger.info("Sync completed!")
        logger.info(f"Created: {results['created']} products")
        logger.info(f"Updated: {results['updated']} products")
        logger.info(f"Failed: {results['failed']} products")
        
        if results['errors']:
            logger.warning("Errors occurred during sync:")
            for error in results['errors']:
                logger.warning(f"  {error['robot']}: {error['error']}")
        
        # Save results
        results_file = 'data/sync_results.json'
        with open(results_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Sync results saved to {results_file}")
        return True
        
    except Exception as e:
        logger.error(f"Error during WordPress sync: {e}")
        return False


def analyze_existing_products() -> bool:
    """Analyze existing products on the WordPress site"""
    logger = get_logger('main')
    
    try:
        product_manager = ProductManager()
        
        if not product_manager.wp_client.test_connection():
            logger.error("Failed to connect to WordPress")
            return False
        
        # Get product statistics
        stats = product_manager.get_product_stats()
        
        logger.info("Current FANUC product statistics:")
        logger.info(f"Total products: {stats.get('total_products', 0)}")
        
        # Status breakdown
        status_stats = stats.get('by_status', {})
        for status, count in status_stats.items():
            logger.info(f"  {status}: {count}")
        
        # Series breakdown
        series_stats = stats.get('by_series', {})
        if series_stats:
            logger.info("Products by series:")
            for series, count in series_stats.items():
                logger.info(f"  {series}: {count}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error analyzing products: {e}")
        return False


def test_wordpress_connection() -> bool:
    """Test WordPress connection"""
    logger = get_logger('main')
    
    try:
        product_manager = ProductManager()
        
        if product_manager.wp_client.test_connection():
            logger.info("WordPress connection test successful!")
            
            # Get site info
            site_info = product_manager.wp_client.get_site_info()
            if site_info:
                logger.info(f"Site: {site_info.get('name', 'Unknown')}")
                logger.info(f"URL: {site_info.get('url', 'Unknown')}")
            
            return True
        else:
            logger.error("WordPress connection test failed!")
            return False
            
    except Exception as e:
        logger.error(f"Error testing WordPress connection: {e}")
        return False


def test_scrapy() -> bool:
    """Test Scrapy functionality"""
    logger = get_logger('main')
    
    if not SCRAPY_AVAILABLE:
        logger.error("Scrapy not available. Install with: pip install scrapy")
        return False
    
    try:
        scrapy_manager = ScrapyManager()
        success = scrapy_manager.test_scraper()
        
        if success:
            logger.info("Scrapy test successful!")
        else:
            logger.error("Scrapy test failed!")
        
        return success
        
    except Exception as e:
        logger.error(f"Error testing Scrapy: {e}")
        return False


def add_multi_manufacturer_commands(parser):
    """Add multi-manufacturer crawler commands"""
    
    # Multi-manufacturer crawler subcommand
    multi_parser = parser.add_parser('multi-crawl', help='Multi-manufacturer robot catalog crawler')
    multi_subparsers = multi_parser.add_subparsers(dest='multi_action', help='Multi-crawler actions')
    
    # Test crawl
    test_parser = multi_subparsers.add_parser('test', help='Test crawl (no products created)')
    test_parser.add_argument('--manufacturers', nargs='+', choices=['fanuc', 'abb'], 
                           help='Manufacturers to crawl (default: all)')
    test_parser.add_argument('--limit', type=int, default=5, 
                           help='Limit robots per manufacturer (default: 5)')
    
    # Production crawl
    prod_parser = multi_subparsers.add_parser('production', help='Production crawl (creates products)')
    prod_parser.add_argument('--manufacturers', nargs='+', choices=['fanuc', 'abb'],
                           help='Manufacturers to crawl (default: all)')
    prod_parser.add_argument('--limit', type=int, 
                           help='Limit robots per manufacturer')
    prod_parser.add_argument('--confirm', action='store_true',
                           help='Skip confirmation prompt')
    
    # Brand management
    brand_parser = multi_subparsers.add_parser('brands', help='Manage robot brands')
    brand_subparsers = brand_parser.add_subparsers(dest='brand_action', help='Brand actions')
    
    brand_subparsers.add_parser('list', help='List all robot brands')
    brand_subparsers.add_parser('stats', help='Show brand statistics')
    
    # Single manufacturer
    single_parser = multi_subparsers.add_parser('single', help='Crawl single manufacturer')
    single_parser.add_argument('manufacturer', choices=['fanuc', 'abb'], 
                             help='Manufacturer to crawl')
    single_parser.add_argument('--limit', type=int, 
                             help='Limit number of robots')
    single_parser.add_argument('--test', action='store_true',
                             help='Test mode (no products created)')


def handle_multi_manufacturer_commands(args):
    """Handle multi-manufacturer crawler commands"""
    
    from src.wordpress.wp_client import WordPressClient
    from src.crawlers.robot_catalog_crawler import RobotCatalogCrawler
    
    wp_client = WordPressClient()
    
    if not wp_client.test_connection():
        print("❌ Failed to connect to WordPress")
        return False
    
    catalog_crawler = RobotCatalogCrawler(wp_client)
    
    if args.multi_action == 'test':
        print(f"🧪 Running test crawl for manufacturers: {args.manufacturers or 'all'}")
        print(f"📊 Limit per manufacturer: {args.limit}")
        
        results = catalog_crawler.crawl_all_manufacturers(
            manufacturers=args.manufacturers,
            limit_per_manufacturer=args.limit,
            test_mode=True
        )
        
        print(f"\n📊 TEST CRAWL RESULTS:")
        print(f"   Robots discovered: {results['summary']['total_robots_discovered']}")
        print(f"   Duration: {results['summary']['duration']}")
        print(f"   Success rate: {results['summary']['success_rate']:.1f}%")
        
        return True
    
    elif args.multi_action == 'production':
        manufacturers = args.manufacturers or ['fanuc', 'abb']
        print(f"🚀 Production crawl for: {manufacturers}")
        print(f"⚠️  This will create actual products on WordPress!")
        
        if not args.confirm:
            proceed = input("Do you want to proceed? (y/N): ").lower().strip()
            if proceed != 'y':
                print("📝 Crawl cancelled")
                return False
        
        results = catalog_crawler.crawl_all_manufacturers(
            manufacturers=manufacturers,
            limit_per_manufacturer=args.limit,
            test_mode=False
        )
        
        print(f"\n🎉 PRODUCTION CRAWL COMPLETE!")
        summary = results['summary']
        print(f"   Robots discovered: {summary['total_robots_discovered']}")
        print(f"   Products created: {summary['products_created']}")
        print(f"   Products updated: {summary['products_updated']}")
        print(f"   Brands created: {summary['brands_created']}")
        print(f"   Success rate: {summary['success_rate']:.1f}%")
        
        return True
    
    elif args.multi_action == 'brands':
        if args.brand_action == 'list':
            brands = catalog_crawler.brand_manager.get_all_brands()
            print(f"\n🏷️  ROBOT BRANDS ({len(brands)} total):")
            for brand in brands:
                print(f"   • {brand['name']} - {brand['count']} products (ID: {brand['id']})")
            return True
            
        elif args.brand_action == 'stats':
            stats = catalog_crawler.get_brand_statistics()
            print(f"\n📊 BRAND STATISTICS:")
            print(f"   Total brands: {stats['total_brands']}")
            print(f"   Brands with products: {stats['brands_with_products']}")
            print(f"   Total robot products: {stats['total_products']}")
            
            if stats['brand_breakdown']:
                print(f"\n🏭 Brand breakdown:")
                for brand, info in stats['brand_breakdown'].items():
                    print(f"   • {brand}: {info['products']} products")
            return True
    
    elif args.multi_action == 'single':
        print(f"🏭 Crawling single manufacturer: {args.manufacturer}")
        
        results = catalog_crawler.crawl_single_manufacturer(
            manufacturer_name=args.manufacturer,
            limit=args.limit,
            test_mode=args.test
        )
        
        mode = "TEST" if args.test else "PRODUCTION"
        print(f"\n🎉 {mode} CRAWL COMPLETE!")
        summary = results['summary']
        print(f"   Robots discovered: {summary['total_robots_discovered']}")
        if not args.test:
            print(f"   Products created: {summary['products_created']}")
            print(f"   Products updated: {summary['products_updated']}")
        print(f"   Success rate: {summary['success_rate']:.1f}%")
        
        return True
    
    return False


def setup_clients():
    """Setup database and WordPress clients"""
    
    # Setup MongoDB
    db = RobotCatalogDB()
    
    # Setup WordPress client
    wp_client = WordPressClient()
    
    return db, wp_client


def cmd_discover_sites(args):
    """Discover new robotics manufacturer sites"""
    
    print("Starting site discovery...")
    
    db, _ = setup_clients()
    discovery_service = SiteDiscoveryService(db)
    
    # Run discovery cycle
    methods = args.methods.split(',') if args.methods else None
    results = discovery_service.run_discovery_cycle(
        methods=methods,
        max_sites_per_method=args.limit
    )
    
    print(f"\nDiscovery complete!")
    print(f"Total sites discovered: {results['total_discovered']}")
    
    for method, count in results['by_method'].items():
        print(f"   {method}: {count} sites")
    
    # Show discovery statistics
    stats = db.get_discovery_statistics()
    print(f"\nDiscovery Statistics:")
    print(f"   Total sites in database: {stats['total_sites']}")
    print(f"   Ready for validation: {stats['ready_for_validation']}")
    print(f"   Ready for crawling: {stats['ready_for_crawling']}")
    
    db.close()


def cmd_manage_sites(args):
    """Manage site states and transitions"""
    
    print("Managing site states...")
    
    db, _ = setup_clients()
    state_manager = SiteStateManager(db)
    
    if args.action == 'status':
        # Show status summary
        summary = state_manager.get_site_status_summary()
        print(f"\nSite Status Summary:")
        for status, count in summary.items():
            if status not in ['total_sites', 'ready_for_validation', 'ready_for_crawling']:
                print(f"   {status}: {count}")
        print(f"\nAdditional Metrics:")
        print(f"   Total sites: {summary['total_sites']}")
        print(f"   Ready for validation: {summary['ready_for_validation']}")
        print(f"   Ready for crawling: {summary['ready_for_crawling']}")
    
    elif args.action == 'auto-transition':
        # Process automatic transitions
        results = state_manager.process_automatic_transitions()
        print(f"\nAutomatic transitions complete!")
        print(f"   Processed: {results['processed']} sites")
        print(f"   Transitioned: {results['transitioned']} sites")
        print(f"   Errors: {results['errors']}")
        
        for status, status_results in results['by_status'].items():
            print(f"   {status}: {status_results['transitioned']}/{status_results['processed']} transitioned")
    
    elif args.action == 'validate':
        # Show sites needing validation
        sites = db.get_sites_for_validation(limit=args.limit or 20)
        print(f"\nSites needing validation ({len(sites)}):")
        
        for site in sites:
            print(f"   {site['domain']} (confidence: {site.get('confidence_score', 0):.2f})")
            print(f"      Status: {site['status']}")
            print(f"      Method: {site.get('discovery_method', 'unknown')}")
            if site.get('title'):
                print(f"      Title: {site['title'][:80]}...")
            print()
    
    elif args.action == 'ready':
        # Show sites ready for crawling
        sites = state_manager.get_sites_for_crawling(limit=args.limit or 10)
        print(f"\nSites ready for crawling ({len(sites)}):")
        
        for site in sites:
            print(f"   {site['domain']} (confidence: {site.get('confidence_score', 0):.2f})")
            print(f"      URL: {site['url']}")
            print(f"      Robotics confidence: {site.get('robotics_confidence', 0):.2f}")
            print()
    
    elif args.action == 'cleanup':
        # Cleanup old sites
        days = args.days or 90
        results = state_manager.cleanup_old_sites(days_old=days)
        print(f"\nCleanup complete!")
        print(f"   Archived: {results['archived_count']} sites")
        print(f"   Old rejected: {results['old_rejected']}")
        print(f"   Old completed: {results['old_completed']}")
    
    db.close()


def cmd_validate_site(args):
    """Manually validate a discovered site"""
    
    print(f"✅ Validating site: {args.domain}")
    
    db, _ = setup_clients()
    state_manager = SiteStateManager(db)
    
    # Find site by domain
    site = db.get_discovered_site(args.domain)
    if not site:
        print(f"❌ Site not found: {args.domain}")
        db.close()
        return
    
    site_id = str(site['_id'])
    
    # Validate the site
    success = state_manager.validate_site(
        site_id=site_id,
        is_valid=args.valid,
        validator=args.validator or "cli_user",
        reason=args.reason or "",
        confidence=args.confidence or 1.0
    )
    
    if success:
        status = "validated" if args.valid else "rejected"
        print(f"✅ Site {args.domain} marked as {status}")
    else:
        print(f"❌ Failed to validate site {args.domain}")
    
    db.close()


def cmd_crawl(args):
    """Crawl robots from manufacturers"""
    
    print("🤖 Starting robot crawl...")
    
    db, wp_client = setup_clients()
    crawler = RobotCatalogCrawler(wp_client)
    
    # Determine manufacturers to crawl
    if args.discovered:
        # Crawl from discovered sites
        state_manager = SiteStateManager(db)
        sites = state_manager.get_sites_for_crawling(limit=args.limit or 5)
        
        if not sites:
            print("📭 No sites ready for crawling")
            db.close()
            return
        
        print(f"🚀 Found {len(sites)} sites ready for crawling")
        
        for site in sites:
            print(f"\n🏭 Crawling site: {site['domain']}")
            site_id = str(site['_id'])
            
            # Mark as crawling
            state_manager.start_crawling_site(site_id)
            
            try:
                # TODO: Implement dynamic crawler creation for discovered sites
                # For now, this is a placeholder
                print(f"   📝 [PLACEHOLDER] Would crawl: {site['url']}")
                
                # Mark as complete (placeholder)
                crawl_results = {
                    'robots_found': 0,
                    'robots_processed': 0,
                    'placeholder': True
                }
                state_manager.complete_crawling_site(site_id, crawl_results)
                
            except Exception as e:
                print(f"   ❌ Crawl failed: {e}")
                state_manager.fail_crawling_site(site_id, str(e))
    
    else:
        # Traditional manufacturer crawling
        manufacturers = args.manufacturers.split(',') if args.manufacturers else None
        
        results = crawler.crawl_all_manufacturers(
            manufacturers=manufacturers,
            limit_per_manufacturer=args.limit,
            test_mode=args.test
        )
        
        print(f"\n✅ Crawl complete!")
        print(f"📊 Results:")
        print(f"   Total robots discovered: {results['total_robots_discovered']}")
        print(f"   Total robots processed: {results['total_robots_processed']}")
        print(f"   Products created: {results['products_created']}")
        print(f"   Products updated: {results['products_updated']}")
        print(f"   Errors: {results['errors']}")
    
    db.close()


def cmd_sync(args):
    """Sync robot data to WordPress"""
    
    print("🔄 Starting WordPress sync...")
    
    db, wp_client = setup_clients()
    
    # Get robots from database
    if args.manufacturer:
        robots = db.get_robots_by_manufacturer(args.manufacturer, limit=args.limit)
    else:
        # Get all robots (implement this method if needed)
        robots = []
        for manufacturer_doc in db.get_all_manufacturers():
            manufacturer_robots = db.get_robots_by_manufacturer(
                manufacturer_doc['name'], 
                limit=args.limit
            )
            robots.extend(manufacturer_robots)
    
    print(f"📊 Found {len(robots)} robots to sync")
    
    synced = 0
    errors = 0
    
    for robot in robots:
        try:
            # TODO: Implement robot to WordPress sync
            print(f"   📝 [PLACEHOLDER] Would sync: {robot.get('name', 'Unknown')}")
            synced += 1
            
        except Exception as e:
            print(f"   ❌ Sync failed for {robot.get('name', 'Unknown')}: {e}")
            errors += 1
    
    print(f"\n✅ Sync complete!")
    print(f"   Synced: {synced}")
    print(f"   Errors: {errors}")
    
    db.close()


def cmd_stats(args):
    """Show system statistics"""
    
    print("📊 System Statistics")
    
    db, _ = setup_clients()
    
    # Discovery statistics
    discovery_stats = db.get_discovery_statistics()
    print(f"\n🔍 Site Discovery:")
    print(f"   Total sites: {discovery_stats['total_sites']}")
    print(f"   Recent discoveries: {discovery_stats['recent_discoveries']}")
    print(f"   Ready for validation: {discovery_stats['ready_for_validation']}")
    print(f"   Ready for crawling: {discovery_stats['ready_for_crawling']}")
    
    # Site status breakdown
    state_manager = SiteStateManager(db)
    status_summary = state_manager.get_site_status_summary()
    print(f"\n📈 Site Status Breakdown:")
    for status, count in status_summary.items():
        if status not in ['total_sites', 'ready_for_validation', 'ready_for_crawling']:
            print(f"   {status}: {count}")
    
    # Robot statistics
    total_robots = db.robots.count_documents({})
    print(f"\n🤖 Robot Data:")
    print(f"   Total robots: {total_robots}")
    
    # Manufacturer breakdown
    manufacturers = db.get_all_manufacturers()
    print(f"   Manufacturers: {len(manufacturers)}")
    
    for manufacturer in manufacturers:
        robot_count = db.robots.count_documents({'manufacturer': manufacturer['name']})
        print(f"      {manufacturer['name']}: {robot_count} robots")
    
    db.close()


def cmd_ai_discover(args):
    """AI-powered robotics site discovery using OpenAI"""
    
    print("🤖 Starting AI-powered robotics site discovery...")
    
    try:
        db, _ = setup_clients()
        
        # Initialize AI discovery service
        ai_discovery = AIDiscoveryService(db)
        
        # Run AI discovery
        summary = ai_discovery.run_full_ai_discovery(
            custom_prompt=args.prompt,
            analyze_sites=not args.no_analysis
        )
        
        print(f"\n✅ AI Discovery completed!")
        print(f"📊 Results:")
        print(f"   Sites discovered: {summary['sites_discovered']}")
        print(f"   Sites analyzed: {summary['sites_analyzed']}")
        print(f"   Duration: {summary['duration_seconds']:.1f}s")
        
        if summary['sites_discovered'] > 0:
            print(f"\n🌐 Discovered Sites:")
            for site in summary['discovered_sites'][:10]:  # Show first 10
                print(f"   • {site['company_name']} ({site['url']}) - Confidence: {site['confidence_level']}/10")
        
        print(f"\n💾 Summary saved to discovery file")
        
        if args.auto_crawl and summary['sites_discovered'] > 0:
            print("\n🚀 Starting automatic crawling of discovered sites...")
            cmd_ai_crawl_args = type('Args', (), {
                'discovered_only': True,
                'limit_sites': args.limit_sites or 5,
                'max_products_per_site': args.max_products or 20,
                'sync_to_wordpress': not args.no_sync
            })()
            cmd_ai_crawl(cmd_ai_crawl_args)
        
        db.close()
        
    except Exception as e:
        print(f"❌ AI Discovery failed: {e}")
        if "OpenAI" in str(e) or "api_key" in str(e):
            print("💡 Make sure OPENAI_API_KEY is set in your .env file")


def cmd_ai_crawl(args):
    """Crawl robotics products from AI-discovered sites"""
    
    print("🕷️ Starting AI-discovered site crawling...")
    
    try:
        db, _ = setup_clients()
        
        # Get discovered sites
        if args.discovered_only:
            # Get sites discovered by AI
            sites_query = {"discovery_method": "ai_openai"}
        else:
            # Get all validated sites
            sites_query = {"confidence_score": {"$gte": 0.5}}
        
        # Convert MongoDB results to AIDiscoveredSite objects
        discovered_sites = []
        for site_doc in db.discovered_sites.find(sites_query).limit(args.limit_sites or 10):
            # Create AIDiscoveredSite from database document
            from src.discovery.ai_discovery_service import AIDiscoveredSite
            ai_site = AIDiscoveredSite(
                company_name=site_doc['name'],
                url=site_doc['url'],
                description=site_doc.get('description', ''),
                confidence_level=int(site_doc['confidence_score'] * 10),
                robotics_focus=', '.join(site_doc.get('robotics_keywords', [])),
                ai_analysis=site_doc.get('ai_analysis', {}),
                discovered_at=site_doc.get('discovered_at')
            )
            discovered_sites.append(ai_site)
        
        if not discovered_sites:
            print("❌ No discovered sites found. Run 'ai-discover' first.")
            return
        
        print(f"🎯 Found {len(discovered_sites)} sites to crawl")
        
        # Initialize multi-site crawler
        crawler = MultiSiteCrawler(db)
        
        # Crawl sites
        results = crawler.crawl_discovered_sites(
            sites=discovered_sites,
            max_products_per_site=args.max_products_per_site or 50,
            delay_between_requests=2.0
        )
        
        print(f"\n✅ Multi-site crawling completed!")
        print(f"📊 Results:")
        print(f"   Sites crawled: {results['sites_crawled']}")
        print(f"   Sites successful: {results['sites_successful']}")
        print(f"   Total products found: {results['total_products_found']}")
        print(f"   Duration: {results['duration_seconds']:.1f}s")
        
        # Show per-site results
        print(f"\n🏭 Per-Site Results:")
        for site_url, site_result in results['site_results'].items():
            status = "✅" if site_result['success'] else "❌"
            print(f"   {status} {site_result['company_name']}: {site_result['products_found']} products")
        
        # Sync to WordPress if requested
        if args.sync_to_wordpress and results['total_products_found'] > 0:
            print(f"\n🔄 Syncing {results['total_products_found']} products to WordPress...")
            
            # Extract all products from results
            all_products = []
            for site_result in results['site_results'].values():
                if site_result['success']:
                    for product_dict in site_result['products']:
                        # Convert back to CrawledProduct
                        from src.crawlers.multi_site_crawler import CrawledProduct
                        product = CrawledProduct(**product_dict)
                        all_products.append(product)
            
            # Sync to WordPress
            sync_results = crawler.sync_products_to_wordpress(all_products, dry_run=False)
            
            print(f"   ✅ WordPress sync completed!")
            print(f"   Created: {sync_results['created']} products")
            print(f"   Updated: {sync_results['updated']} products")
            print(f"   Failed: {sync_results['failed']} products")
        
        db.close()
        
    except Exception as e:
        print(f"❌ AI Crawling failed: {e}")


def cmd_full_ai_pipeline(args):
    """Run complete AI discovery and crawling pipeline"""
    
    print("🚀 Starting Full AI Robotics Discovery Pipeline...")
    print("This will: 1) Discover sites with AI, 2) Crawl products, 3) Sync to WordPress")
    
    try:
        # Step 1: AI Discovery
        print("\n🤖 Step 1: AI Site Discovery")
        ai_discover_args = type('Args', (), {
            'prompt': args.prompt,
            'no_analysis': False,
            'auto_crawl': False,
            'limit_sites': None,
            'max_products': None,
            'no_sync': True
        })()
        cmd_ai_discover(ai_discover_args)
        
        # Step 2: AI Crawling
        print("\n🕷️ Step 2: Multi-Site Crawling")
        ai_crawl_args = type('Args', (), {
            'discovered_only': True,
            'limit_sites': args.limit_sites or 10,
            'max_products_per_site': args.max_products or 30,
            'sync_to_wordpress': args.sync_to_wordpress
        })()
        cmd_ai_crawl(ai_crawl_args)
        
        print("\n🎉 Full AI Pipeline Completed!")
        print("Check your WordPress site for the new robot products.")
        
    except Exception as e:
        print(f"❌ AI Pipeline failed: {e}")


def main():
    """Main CLI entry point"""
    
    parser = argparse.ArgumentParser(description='Robot Catalog Management System')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Site Discovery Commands
    discover_parser = subparsers.add_parser('discover', help='Discover new robotics sites')
    discover_parser.add_argument('--methods', help='Discovery methods (comma-separated): search_engines,industry_directories,competitor_analysis')
    discover_parser.add_argument('--limit', type=int, default=50, help='Max sites per method')
    discover_parser.set_defaults(func=cmd_discover_sites)
    
    # Site Management Commands
    manage_parser = subparsers.add_parser('manage', help='Manage discovered sites')
    manage_parser.add_argument('action', choices=['status', 'auto-transition', 'validate', 'ready', 'cleanup'], 
                              help='Management action')
    manage_parser.add_argument('--limit', type=int, help='Limit results')
    manage_parser.add_argument('--days', type=int, help='Days for cleanup')
    manage_parser.set_defaults(func=cmd_manage_sites)
    
    # Site Validation Commands
    validate_parser = subparsers.add_parser('validate', help='Manually validate a site')
    validate_parser.add_argument('domain', help='Site domain to validate')
    validate_parser.add_argument('--valid', action='store_true', help='Mark as valid')
    validate_parser.add_argument('--invalid', dest='valid', action='store_false', help='Mark as invalid')
    validate_parser.add_argument('--validator', help='Validator name')
    validate_parser.add_argument('--reason', help='Validation reason')
    validate_parser.add_argument('--confidence', type=float, help='Confidence score (0-1)')
    validate_parser.set_defaults(func=cmd_validate_site)
    
    # Crawling Commands
    crawl_parser = subparsers.add_parser('crawl', help='Crawl robots from manufacturers')
    crawl_parser.add_argument('--manufacturers', help='Comma-separated list of manufacturers')
    crawl_parser.add_argument('--discovered', action='store_true', help='Crawl from discovered sites')
    crawl_parser.add_argument('--limit', type=int, help='Limit robots per manufacturer')
    crawl_parser.add_argument('--test', action='store_true', help='Test mode (no WordPress sync)')
    crawl_parser.set_defaults(func=cmd_crawl)
    
    # Sync Commands
    sync_parser = subparsers.add_parser('sync', help='Sync robot data to WordPress')
    sync_parser.add_argument('--manufacturer', help='Specific manufacturer to sync')
    sync_parser.add_argument('--limit', type=int, help='Limit robots to sync')
    sync_parser.set_defaults(func=cmd_sync)
    
    # Statistics Commands
    stats_parser = subparsers.add_parser('stats', help='Show system statistics')
    stats_parser.set_defaults(func=cmd_stats)
    
    # AI Commands
    ai_parser = subparsers.add_parser('ai-discover', help='AI-powered robotics site discovery using OpenAI')
    ai_parser.add_argument('--prompt', help='Custom prompt for AI discovery')
    ai_parser.add_argument('--no-analysis', action='store_true', help='Skip site analysis')
    ai_parser.add_argument('--auto-crawl', action='store_true', help='Auto-crawl discovered sites')
    ai_parser.add_argument('--limit-sites', type=int, help='Limit sites to crawl')
    ai_parser.add_argument('--max-products', type=int, help='Limit products per site')
    ai_parser.add_argument('--no-sync', action='store_true', help='Skip sync to WordPress')
    ai_parser.set_defaults(func=cmd_ai_discover)
    
    ai_crawl_parser = subparsers.add_parser('ai-crawl', help='Crawl robotics products from AI-discovered sites')
    ai_crawl_parser.add_argument('--discovered-only', action='store_true', help='Crawl only discovered sites')
    ai_crawl_parser.add_argument('--limit-sites', type=int, help='Limit sites to crawl')
    ai_crawl_parser.add_argument('--max-products-per-site', type=int, help='Limit products per site')
    ai_crawl_parser.add_argument('--sync-to-wordpress', action='store_true', help='Sync to WordPress')
    ai_crawl_parser.set_defaults(func=cmd_ai_crawl)
    
    ai_full_pipeline_parser = subparsers.add_parser('ai-full-pipeline', help='Run complete AI discovery and crawling pipeline')
    ai_full_pipeline_parser.add_argument('--prompt', help='Custom prompt for AI discovery')
    ai_full_pipeline_parser.add_argument('--no-analysis', action='store_true', help='Skip site analysis')
    ai_full_pipeline_parser.add_argument('--auto-crawl', action='store_true', help='Auto-crawl discovered sites')
    ai_full_pipeline_parser.add_argument('--limit-sites', type=int, help='Limit sites to crawl')
    ai_full_pipeline_parser.add_argument('--max-products', type=int, help='Limit products per site')
    ai_full_pipeline_parser.add_argument('--sync-to-wordpress', action='store_true', help='Sync to WordPress')
    ai_full_pipeline_parser.set_defaults(func=cmd_full_ai_pipeline)
    
    # Legacy commands (existing functionality)
    # ... existing command parsers would go here ...
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n⏹️  Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main() 