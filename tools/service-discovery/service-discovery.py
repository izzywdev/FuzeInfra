#!/usr/bin/env python3
"""
Service Discovery CLI for FuzeInfra
Command-line interface for Consul-based service registration and discovery.
"""

import os
import sys
import json
import argparse
from pathlib import Path
from typing import List, Optional

# Add the current directory to Python path for imports
sys.path.insert(0, str(Path(__file__).parent))

try:
    from consul_helper import ConsulHelper
except ImportError:
    print("Error: consul_helper module not found. Make sure consul-helper.py is in the same directory.")
    sys.exit(1)


class ServiceDiscoveryCLI:
    """Command-line interface for service discovery operations."""
    
    def __init__(self):
        """Initialize the CLI with ConsulHelper."""
        try:
            self.consul_helper = ConsulHelper()
        except Exception as e:
            print(f"Error: Failed to initialize Consul connection: {e}")
            print("Make sure Consul is running and accessible at localhost:8500")
            sys.exit(1)
    
    def register_service(self, args) -> None:
        """Register a service with Consul."""
        try:
            # Parse tags
            tags = []
            if args.tags:
                tags = [tag.strip() for tag in args.tags.split(',')]
            
            # Parse metadata
            meta = {}
            if args.meta:
                for item in args.meta.split(','):
                    if '=' in item:
                        key, value = item.split('=', 1)
                        meta[key.strip()] = value.strip()
            
            result = self.consul_helper.register_service(
                name=args.service_name,
                port=args.port,
                address=args.address,
                health_check_path=args.health_check,
                health_check_interval=args.health_interval,
                health_check_timeout=args.health_timeout,
                tags=tags,
                meta=meta
            )
            
            if result["success"]:
                print(f"✅ Service registered successfully!")
                print(f"   Service: {result['service_name']}")
                print(f"   Address: {result['address']}:{result['port']}")
                print(f"   Domain: {result['domain']}")
                print(f"   Service ID: {result['service_id']}")
                if result["health_check"]:
                    print(f"   Health Check: Enabled")
                if result["tags"]:
                    print(f"   Tags: {', '.join(result['tags'])}")
            else:
                print(f"❌ Failed to register service: {result['error']}")
                sys.exit(1)
                
        except Exception as e:
            print(f"❌ Error registering service: {e}")
            sys.exit(1)
    
    def discover_service(self, args) -> None:
        """Discover a service by name."""
        try:
            result = self.consul_helper.discover_service(
                service_name=args.service_name,
                healthy_only=not args.include_unhealthy
            )
            
            if result["success"]:
                print(f"🔍 Found {result['total_instances']} instance(s) for service '{result['service_name']}':")
                print()
                
                for i, instance in enumerate(result["instances"], 1):
                    status_emoji = "🟢" if instance["health_status"] == "passing" else "🔴"
                    print(f"  {i}. {status_emoji} {instance['service_id']}")
                    print(f"     Address: {instance['address']}:{instance['port']}")
                    print(f"     URL: {instance['url']}")
                    print(f"     Domain: {instance['domain']}")
                    print(f"     Domain URL: {instance['domain_url']}")
                    print(f"     Health: {instance['health_status']} ({instance['health_checks']} checks)")
                    if instance["tags"]:
                        print(f"     Tags: {', '.join(instance['tags'])}")
                    print()
                
                if args.json:
                    print("JSON Output:")
                    print(json.dumps(result, indent=2))
            else:
                print(f"❌ {result['error']}")
                sys.exit(1)
                
        except Exception as e:
            print(f"❌ Error discovering service: {e}")
            sys.exit(1)
    
    def list_services(self, args) -> None:
        """List all registered services."""
        try:
            services = self.consul_helper.list_services()
            
            if not services:
                print("No services registered in Consul.")
                return
            
            print(f"📋 Found {len(services)} registered service instance(s):")
            print()
            
            # Group services by name
            services_by_name = {}
            for service in services:
                name = service["service_name"]
                if name not in services_by_name:
                    services_by_name[name] = []
                services_by_name[name].append(service)
            
            for service_name, instances in services_by_name.items():
                healthy_count = len([s for s in instances if s["health_status"] == "passing"])
                total_count = len(instances)
                
                print(f"🔸 {service_name} ({healthy_count}/{total_count} healthy)")
                
                for instance in instances:
                    status_emoji = "🟢" if instance["health_status"] == "passing" else "🔴"
                    print(f"   {status_emoji} {instance['address']}:{instance['port']} - {instance['domain']}")
                    if instance["tags"]:
                        print(f"      Tags: {', '.join(instance['tags'])}")
                print()
            
            if args.json:
                print("JSON Output:")
                print(json.dumps(services, indent=2))
                
        except Exception as e:
            print(f"❌ Error listing services: {e}")
            sys.exit(1)
    
    def health_check(self, args) -> None:
        """Check health status of a service."""
        try:
            result = self.consul_helper.get_service_health(args.service_name)
            
            if result["success"]:
                status_emoji = "🟢" if result["overall_status"] == "healthy" else "🔴"
                print(f"{status_emoji} Service '{result['service_name']}' - {result['overall_status'].upper()}")
                print(f"   Total Instances: {result['total_instances']}")
                print(f"   Healthy: {result['healthy_instances']}")
                print(f"   Unhealthy: {result['unhealthy_instances']}")
                print()
                
                for i, instance in enumerate(result["instances"], 1):
                    instance_emoji = "🟢" if instance["healthy"] else "🔴"
                    print(f"  {i}. {instance_emoji} {instance['service_id']} ({instance['address']}:{instance['port']})")
                    
                    for check in instance["checks"]:
                        check_emoji = "✅" if check["status"] == "passing" else "❌"
                        print(f"     {check_emoji} {check['name']}: {check['status']}")
                        if check["output"] and args.verbose:
                            print(f"        Output: {check['output']}")
                    print()
                
                if args.json:
                    print("JSON Output:")
                    print(json.dumps(result, indent=2))
            else:
                print(f"❌ {result['error']}")
                sys.exit(1)
                
        except Exception as e:
            print(f"❌ Error checking service health: {e}")
            sys.exit(1)
    
    def deregister_service(self, args) -> None:
        """Deregister a service from Consul."""
        try:
            result = self.consul_helper.deregister_service(
                service_name=args.service_name,
                port=args.port
            )
            
            if result["success"]:
                print(f"✅ {result['message']}")
                if "deregistered_services" in result:
                    for service_id in result["deregistered_services"]:
                        print(f"   - {service_id}")
            else:
                print(f"❌ Failed to deregister service: {result['error']}")
                sys.exit(1)
                
        except Exception as e:
            print(f"❌ Error deregistering service: {e}")
            sys.exit(1)
    
    def status_check(self, args) -> None:
        """Check Consul status."""
        try:
            result = self.consul_helper.check_consul_status()
            
            if result["success"]:
                print("🟢 Consul Status: HEALTHY")
                print(f"   Leader: {result['leader']}")
                print(f"   Cluster Members: {result['cluster_members']}")
                print(f"   Registered Services: {result['registered_services']}")
                print(f"   UI URL: {result['consul_ui_url']}")
                print(f"   Timestamp: {result['timestamp']}")
            else:
                print(f"🔴 Consul Status: UNHEALTHY")
                print(f"   Error: {result['error']}")
                sys.exit(1)
            
            if args.json:
                print("\nJSON Output:")
                print(json.dumps(result, indent=2))
                
        except Exception as e:
            print(f"❌ Error checking Consul status: {e}")
            sys.exit(1)


def main():
    """Main entry point for the service discovery CLI."""
    parser = argparse.ArgumentParser(
        description="FuzeInfra Service Discovery CLI - Consul-based service registration and discovery",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Register a service
  python service-discovery.py register fuzeagent --port 3000 --health-check /api/health --tags api,webhook

  # Discover a service
  python service-discovery.py discover fuzeagent

  # List all services
  python service-discovery.py list

  # Check service health
  python service-discovery.py health fuzeagent

  # Check Consul status
  python service-discovery.py status

  # Deregister a service
  python service-discovery.py deregister fuzeagent --port 3000
        """
    )
    
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Register command
    register_parser = subparsers.add_parser("register", help="Register a service")
    register_parser.add_argument("service_name", help="Name of the service")
    register_parser.add_argument("--port", "-p", type=int, required=True, help="Service port")
    register_parser.add_argument("--address", "-a", help="Service address (defaults to service name)")
    register_parser.add_argument("--health-check", help="Health check path (e.g., /health)")
    register_parser.add_argument("--health-interval", default="10s", help="Health check interval")
    register_parser.add_argument("--health-timeout", default="5s", help="Health check timeout")
    register_parser.add_argument("--tags", help="Comma-separated tags")
    register_parser.add_argument("--meta", help="Comma-separated key=value metadata")
    
    # Discover command
    discover_parser = subparsers.add_parser("discover", help="Discover a service")
    discover_parser.add_argument("service_name", help="Name of the service to discover")
    discover_parser.add_argument("--include-unhealthy", action="store_true", help="Include unhealthy instances")
    
    # List command
    list_parser = subparsers.add_parser("list", help="List all services")
    
    # Health command
    health_parser = subparsers.add_parser("health", help="Check service health")
    health_parser.add_argument("service_name", help="Name of the service to check")
    
    # Deregister command
    deregister_parser = subparsers.add_parser("deregister", help="Deregister a service")
    deregister_parser.add_argument("service_name", help="Name of the service to deregister")
    deregister_parser.add_argument("--port", "-p", type=int, help="Specific port (deregister all if not specified)")
    
    # Status command
    status_parser = subparsers.add_parser("status", help="Check Consul status")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Initialize CLI
    cli = ServiceDiscoveryCLI()
    
    # Execute command
    try:
        if args.command == "register":
            cli.register_service(args)
        elif args.command == "discover":
            cli.discover_service(args)
        elif args.command == "list":
            cli.list_services(args)
        elif args.command == "health":
            cli.health_check(args)
        elif args.command == "deregister":
            cli.deregister_service(args)
        elif args.command == "status":
            cli.status_check(args)
    except KeyboardInterrupt:
        print("\n⚠️  Operation cancelled by user")
        sys.exit(1)


if __name__ == "__main__":
    main()