#!/usr/bin/env python3
"""
Tunnel Manager for FuzeInfra Local Development Orchestrator
Manages Cloudflare tunnels and automatically updates webhook URLs across services.
"""

import os
import sys
import json
import yaml
import argparse
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import requests
from tinydb import TinyDB, Query
from cryptography.fernet import Fernet
import base64
import hashlib

class TunnelManager:
    """Manages Cloudflare tunnels and webhook URL synchronization."""
    
    def __init__(self, config_path: str = "config.yaml"):
        """Initialize the tunnel manager with configuration."""
        self.config_path = config_path
        self.config = self._load_config()
        self.db = TinyDB(self.config["registry"]["file_path"])
        self.cipher = self._init_encryption()
        
    def _load_config(self) -> Dict:
        """Load configuration from YAML file."""
        config_file = Path(__file__).parent / self.config_path
        
        # Default configuration
        default_config = {
            "cloudflare": {
                "tunnel_name": os.getenv("CLOUDFLARE_TUNNEL_NAME", "fuzeinfra-tunnel"),
                "domain": os.getenv("CLOUDFLARE_TUNNEL_DOMAIN", "your-domain.com"),
                "container_name": "fuzeinfra-cloudflare-tunnel",
                "config_path": "/etc/cloudflared/config.yml"
            },
            "url_patterns": {
                "github_webhook": "{project}.webhook.{domain}/github",
                "atlassian_webhook": "{project}.webhook.{domain}/atlassian",
                "oauth_callback": "{project}.auth.{domain}/oauth/{provider}/callback",
                "generic_webhook": "{project}.webhook.{domain}/{service}"
            },
            "apis": {
                "github": {
                    "base_url": "https://api.github.com",
                    "timeout": 30,
                    "retries": 3
                },
                "atlassian": {
                    "timeout": 30,
                    "retries": 3
                }
            },
            "registry": {
                "file_path": "webhook_registry.json",
                "backup_enabled": True,
                "backup_retention_days": 30
            },
            "monitoring": {
                "check_interval_seconds": 60,
                "tunnel_health_timeout": 10,
                "webhook_test_timeout": 5,
                "max_consecutive_failures": 3
            }
        }
        
        if config_file.exists():
            try:
                with open(config_file, 'r') as f:
                    config = yaml.safe_load(f)
                return {**default_config, **config}
            except Exception as e:
                print(f"Warning: Could not load config file {config_file}: {e}")
                print("Using default configuration")
        
        return default_config
    
    def _init_encryption(self) -> Fernet:
        """Initialize encryption for sensitive data."""
        registry_key = os.getenv("WEBHOOK_REGISTRY_KEY")
        if not registry_key:
            # Generate a key from a combination of environment variables
            base_data = f"{os.getenv('CLOUDFLARE_TUNNEL_ID', 'default')}{os.getenv('GITHUB_TOKEN', 'default')}"
            key = base64.urlsafe_b64encode(hashlib.sha256(base_data.encode()).digest())
        else:
            key = base64.urlsafe_b64encode(hashlib.sha256(registry_key.encode()).digest())
        
        return Fernet(key)
    
    def _encrypt_data(self, data: str) -> str:
        """Encrypt sensitive data."""
        return self.cipher.encrypt(data.encode()).decode()
    
    def _decrypt_data(self, encrypted_data: str) -> str:
        """Decrypt sensitive data."""
        return self.cipher.decrypt(encrypted_data.encode()).decode()
    
    def _check_tunnel_health(self) -> Dict:
        """Check if Cloudflare tunnel is running and healthy."""
        try:
            # Check if container is running
            result = subprocess.run([
                "docker", "ps", "--filter", f"name={self.config['cloudflare']['container_name']}", 
                "--format", "{{.Names}}"
            ], capture_output=True, text=True, check=True)
            
            is_running = self.config["cloudflare"]["container_name"] in result.stdout
            
            if not is_running:
                return {
                    "healthy": False,
                    "error": "Tunnel container is not running"
                }
            
            # Check tunnel connectivity
            tunnel_info = subprocess.run([
                "docker", "exec", self.config["cloudflare"]["container_name"],
                "cloudflared", "tunnel", "info", self.config["cloudflare"]["tunnel_name"]
            ], capture_output=True, text=True, timeout=self.config["monitoring"]["tunnel_health_timeout"])
            
            return {
                "healthy": tunnel_info.returncode == 0,
                "container_running": is_running,
                "info": tunnel_info.stdout if tunnel_info.returncode == 0 else tunnel_info.stderr
            }
            
        except subprocess.TimeoutExpired:
            return {
                "healthy": False,
                "error": "Tunnel health check timed out"
            }
        except Exception as e:
            return {
                "healthy": False,
                "error": f"Failed to check tunnel health: {str(e)}"
            }
    
    def generate_webhook_url(self, project: str, service: str, webhook_type: str = "generic") -> str:
        """Generate webhook URL for a project and service."""
        domain = self.config["cloudflare"]["domain"]
        
        if webhook_type == "github":
            pattern = self.config["url_patterns"]["github_webhook"]
            return f"https://{pattern.format(project=project, domain=domain)}"
        elif webhook_type == "atlassian":
            pattern = self.config["url_patterns"]["atlassian_webhook"]
            return f"https://{pattern.format(project=project, domain=domain)}"
        elif webhook_type == "oauth":
            pattern = self.config["url_patterns"]["oauth_callback"]
            return f"https://{pattern.format(project=project, domain=domain, provider=service)}"
        else:
            pattern = self.config["url_patterns"]["generic_webhook"]
            return f"https://{pattern.format(project=project, domain=domain, service=service)}"
    
    def register_webhook(self, project: str, service: str, webhook_type: str, 
                        external_id: str = None, metadata: Dict = None) -> Dict:
        """Register a webhook in the local registry."""
        webhook_url = self.generate_webhook_url(project, service, webhook_type)
        
        webhook_record = {
            "project": project,
            "service": service,
            "webhook_type": webhook_type,
            "url": webhook_url,
            "external_id": external_id,  # GitHub webhook ID, Atlassian webhook ID, etc.
            "metadata": metadata or {},
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "last_sync": None,
            "sync_failures": 0,
            "active": True
        }
        
        # Encrypt sensitive metadata
        if metadata and "api_token" in metadata:
            webhook_record["metadata"]["api_token"] = self._encrypt_data(metadata["api_token"])
        
        # Insert or update record
        Webhook = Query()
        existing = self.db.search((Webhook.project == project) & 
                                 (Webhook.service == service) & 
                                 (Webhook.webhook_type == webhook_type))
        
        if existing:
            self.db.update(webhook_record, (Webhook.project == project) & 
                          (Webhook.service == service) & 
                          (Webhook.webhook_type == webhook_type))
            action = "updated"
        else:
            self.db.insert(webhook_record)
            action = "created"
        
        return {
            "success": True,
            "action": action,
            "webhook_url": webhook_url,
            "project": project,
            "service": service,
            "webhook_type": webhook_type
        }
    
    def list_webhooks(self, project: str = None, active_only: bool = True) -> List[Dict]:
        """List registered webhooks."""
        Webhook = Query()
        
        if project:
            if active_only:
                results = self.db.search((Webhook.project == project) & (Webhook.active == True))
            else:
                results = self.db.search(Webhook.project == project)
        else:
            if active_only:
                results = self.db.search(Webhook.active == True)
            else:
                results = self.db.all()
        
        # Decrypt sensitive data for display (but mask it)
        for webhook in results:
            if "metadata" in webhook and "api_token" in webhook["metadata"]:
                webhook["metadata"]["api_token"] = "***encrypted***"
        
        return results
    
    def remove_webhook(self, project: str, service: str, webhook_type: str) -> Dict:
        """Remove webhook from registry."""
        Webhook = Query()
        removed = self.db.remove((Webhook.project == project) & 
                                (Webhook.service == service) & 
                                (Webhook.webhook_type == webhook_type))
        
        return {
            "success": len(removed) > 0,
            "removed_count": len(removed),
            "project": project,
            "service": service,
            "webhook_type": webhook_type
        }
    
    def get_tunnel_status(self) -> Dict:
        """Get comprehensive tunnel and webhook status."""
        tunnel_health = self._check_tunnel_health()
        webhooks = self.list_webhooks()
        
        # Count webhooks by type
        webhook_stats = {
            "total": len(webhooks),
            "by_type": {},
            "by_project": {},
            "sync_failures": 0
        }
        
        for webhook in webhooks:
            webhook_type = webhook["webhook_type"]
            project = webhook["project"]
            
            webhook_stats["by_type"][webhook_type] = webhook_stats["by_type"].get(webhook_type, 0) + 1
            webhook_stats["by_project"][project] = webhook_stats["by_project"].get(project, 0) + 1
            webhook_stats["sync_failures"] += webhook.get("sync_failures", 0)
        
        return {
            "tunnel": tunnel_health,
            "domain": self.config["cloudflare"]["domain"],
            "tunnel_name": self.config["cloudflare"]["tunnel_name"],
            "webhooks": webhook_stats,
            "timestamp": datetime.now().isoformat()
        }

def main():
    """Command line interface for the tunnel manager."""
    parser = argparse.ArgumentParser(description="Tunnel Manager for FuzeInfra Local Development Orchestrator")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Status command
    status_parser = subparsers.add_parser("status", help="Get tunnel and webhook status")
    
    # Register webhook command
    register_parser = subparsers.add_parser("register", help="Register a new webhook")
    register_parser.add_argument("project", help="Project name")
    register_parser.add_argument("service", help="Service name (github, atlassian, oauth, etc.)")
    register_parser.add_argument("--type", default="generic", help="Webhook type")
    register_parser.add_argument("--external-id", help="External webhook ID")
    
    # List webhooks command
    list_parser = subparsers.add_parser("list", help="List registered webhooks")
    list_parser.add_argument("--project", help="Filter by project")
    list_parser.add_argument("--all", action="store_true", help="Include inactive webhooks")
    
    # Remove webhook command  
    remove_parser = subparsers.add_parser("remove", help="Remove a webhook")
    remove_parser.add_argument("project", help="Project name")
    remove_parser.add_argument("service", help="Service name")
    remove_parser.add_argument("--type", default="generic", help="Webhook type")
    
    # Generate URL command
    url_parser = subparsers.add_parser("url", help="Generate webhook URL")
    url_parser.add_argument("project", help="Project name")
    url_parser.add_argument("service", help="Service name")
    url_parser.add_argument("--type", default="generic", help="Webhook type")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    tunnel_manager = TunnelManager()
    
    try:
        if args.command == "status":
            result = tunnel_manager.get_tunnel_status()
        elif args.command == "register":
            result = tunnel_manager.register_webhook(
                args.project, args.service, args.type, args.external_id
            )
        elif args.command == "list":
            result = tunnel_manager.list_webhooks(args.project, not args.all)
        elif args.command == "remove":
            result = tunnel_manager.remove_webhook(args.project, args.service, args.type)
        elif args.command == "url":
            url = tunnel_manager.generate_webhook_url(args.project, args.service, args.type)
            result = {"webhook_url": url}
        
        print(json.dumps(result, indent=2, default=str))
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()