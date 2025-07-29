#!/usr/bin/env python3
"""
Atlassian API Integration for Tunnel Manager
Handles Jira and Confluence webhook creation, updates, and management.
"""

import os
import requests
from typing import Dict, List, Optional, Any
import json
import base64
from urllib.parse import urlparse

class AtlassianWebhookManager:
    """Manages Atlassian (Jira/Confluence) webhooks via REST API."""
    
    def __init__(self, instance_url: str = None, email: str = None, api_token: str = None):
        """Initialize Atlassian webhook manager."""
        self.instance_url = instance_url or os.getenv("ATLASSIAN_INSTANCE")
        self.email = email or os.getenv("ATLASSIAN_EMAIL")
        self.api_token = api_token or os.getenv("ATLASSIAN_API_TOKEN")
        
        if not all([self.instance_url, self.email, self.api_token]):
            raise ValueError("Atlassian instance URL, email, and API token are required.")
        
        # Ensure instance URL has proper format
        if not self.instance_url.startswith("https://"):
            if ".atlassian.net" not in self.instance_url:
                self.instance_url = f"https://{self.instance_url}.atlassian.net"
            else:
                self.instance_url = f"https://{self.instance_url}"
        
        # Create basic auth header
        auth_string = f"{self.email}:{self.api_token}"
        auth_bytes = base64.b64encode(auth_string.encode()).decode()
        
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Basic {auth_bytes}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "FuzeInfra-TunnelManager/1.0"
        })
        
        # Detect if this is a Jira or Confluence instance
        self.is_jira = self._detect_service_type()
    
    def _detect_service_type(self) -> bool:
        """Detect if the instance is Jira (True) or Confluence (False)."""
        try:
            # Try Jira endpoint first
            response = self.session.get(f"{self.instance_url}/rest/api/2/serverInfo", timeout=10)
            if response.status_code == 200:
                return True
            
            # Try Confluence endpoint
            response = self.session.get(f"{self.instance_url}/rest/api/space", timeout=10)
            if response.status_code == 200:
                return False
            
            # Default to Jira if both fail
            return True
            
        except:
            return True  # Default to Jira
    
    def create_webhook(self, webhook_url: str, events: List[str] = None, 
                      name: str = None, enabled: bool = True) -> Dict:
        """Create a new webhook."""
        name = name or f"FuzeInfra Webhook - {webhook_url.split('//')[1].split('/')[0]}"
        
        if self.is_jira:
            return self._create_jira_webhook(webhook_url, events, name, enabled)
        else:
            return self._create_confluence_webhook(webhook_url, events, name, enabled)
    
    def _create_jira_webhook(self, webhook_url: str, events: List[str] = None, 
                           name: str = None, enabled: bool = True) -> Dict:
        """Create a Jira webhook."""
        events = events or [
            "jira:issue_created",
            "jira:issue_updated", 
            "jira:issue_deleted",
            "comment_created",
            "comment_updated"
        ]
        
        webhook_data = {
            "name": name,
            "url": webhook_url,
            "events": events,
            "enabled": enabled,
            "excludeBody": False
        }
        
        try:
            response = self.session.post(
                f"{self.instance_url}/rest/webhooks/1.0/webhook",
                json=webhook_data,
                timeout=30
            )
            
            if response.status_code == 201:
                webhook_result = response.json()
                return {
                    "success": True,
                    "webhook_id": webhook_result["self"].split("/")[-1],
                    "name": webhook_result["name"],
                    "url": webhook_result["url"],
                    "events": webhook_result["events"],
                    "enabled": webhook_result["enabled"],
                    "service": "jira"
                }
            else:
                error_data = response.json() if response.content else {}
                return {
                    "success": False,
                    "error": error_data.get("message", f"HTTP {response.status_code}"),
                    "status_code": response.status_code,
                    "service": "jira"
                }
                
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Request failed: {str(e)}",
                "service": "jira"
            }
    
    def _create_confluence_webhook(self, webhook_url: str, events: List[str] = None, 
                                 name: str = None, enabled: bool = True) -> Dict:
        """Create a Confluence webhook."""
        events = events or [
            "page_created",
            "page_updated",
            "page_removed",
            "comment_created",
            "comment_updated"
        ]
        
        webhook_data = {
            "name": name,
            "url": webhook_url,
            "events": events,
            "active": enabled
        }
        
        try:
            response = self.session.post(
                f"{self.instance_url}/rest/api/webhooks",
                json=webhook_data,
                timeout=30
            )
            
            if response.status_code == 201:
                webhook_result = response.json()
                return {
                    "success": True,
                    "webhook_id": webhook_result["id"],
                    "name": webhook_result["name"],
                    "url": webhook_result["url"],
                    "events": webhook_result["events"],
                    "enabled": webhook_result["active"],
                    "service": "confluence"
                }
            else:
                error_data = response.json() if response.content else {}
                return {
                    "success": False,
                    "error": error_data.get("message", f"HTTP {response.status_code}"),
                    "status_code": response.status_code,
                    "service": "confluence"
                }
                
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Request failed: {str(e)}",
                "service": "confluence"
            }
    
    def update_webhook(self, webhook_id: str, new_webhook_url: str, 
                      events: List[str] = None, name: str = None, 
                      enabled: bool = True) -> Dict:
        """Update an existing webhook."""
        if self.is_jira:
            return self._update_jira_webhook(webhook_id, new_webhook_url, events, name, enabled)
        else:
            return self._update_confluence_webhook(webhook_id, new_webhook_url, events, name, enabled)
    
    def _update_jira_webhook(self, webhook_id: str, new_webhook_url: str, 
                           events: List[str] = None, name: str = None, 
                           enabled: bool = True) -> Dict:
        """Update a Jira webhook."""
        # Get current webhook to preserve existing values
        current = self.get_webhook(webhook_id)
        if not current["success"]:
            return current
        
        webhook_data = {
            "name": name or current["name"],
            "url": new_webhook_url,
            "events": events or current["events"],
            "enabled": enabled,
            "excludeBody": False
        }
        
        try:
            response = self.session.put(
                f"{self.instance_url}/rest/webhooks/1.0/webhook/{webhook_id}",
                json=webhook_data,
                timeout=30
            )
            
            if response.status_code == 200:
                webhook_result = response.json()
                return {
                    "success": True,
                    "webhook_id": webhook_id,
                    "old_url": current.get("url"),
                    "new_url": webhook_result["url"],
                    "name": webhook_result["name"],
                    "events": webhook_result["events"],
                    "enabled": webhook_result["enabled"],
                    "service": "jira"
                }
            else:
                error_data = response.json() if response.content else {}
                return {
                    "success": False,
                    "error": error_data.get("message", f"HTTP {response.status_code}"),
                    "status_code": response.status_code,
                    "service": "jira"
                }
                
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Request failed: {str(e)}",
                "service": "jira"
            }
    
    def _update_confluence_webhook(self, webhook_id: str, new_webhook_url: str, 
                                 events: List[str] = None, name: str = None, 
                                 enabled: bool = True) -> Dict:
        """Update a Confluence webhook."""
        # Get current webhook to preserve existing values
        current = self.get_webhook(webhook_id)
        if not current["success"]:
            return current
        
        webhook_data = {
            "name": name or current["name"],
            "url": new_webhook_url,
            "events": events or current["events"],
            "active": enabled
        }
        
        try:
            response = self.session.put(
                f"{self.instance_url}/rest/api/webhooks/{webhook_id}",
                json=webhook_data,
                timeout=30
            )
            
            if response.status_code == 200:
                webhook_result = response.json()
                return {
                    "success": True,
                    "webhook_id": webhook_id,
                    "old_url": current.get("url"),
                    "new_url": webhook_result["url"],
                    "name": webhook_result["name"],
                    "events": webhook_result["events"],
                    "enabled": webhook_result["active"],
                    "service": "confluence"
                }
            else:
                error_data = response.json() if response.content else {}
                return {
                    "success": False,
                    "error": error_data.get("message", f"HTTP {response.status_code}"),
                    "status_code": response.status_code,
                    "service": "confluence"
                }
                
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Request failed: {str(e)}",
                "service": "confluence"
            }
    
    def get_webhook(self, webhook_id: str) -> Dict:
        """Get webhook details by ID."""
        try:
            if self.is_jira:
                endpoint = f"{self.instance_url}/rest/webhooks/1.0/webhook/{webhook_id}"
            else:
                endpoint = f"{self.instance_url}/rest/api/webhooks/{webhook_id}"
            
            response = self.session.get(endpoint, timeout=30)
            
            if response.status_code == 200:
                webhook_data = response.json()
                
                # Normalize response format between Jira and Confluence
                if self.is_jira:
                    return {
                        "success": True,
                        "webhook_id": webhook_id,
                        "name": webhook_data["name"],
                        "url": webhook_data["url"],
                        "events": webhook_data["events"],
                        "enabled": webhook_data["enabled"],
                        "service": "jira"
                    }
                else:
                    return {
                        "success": True,
                        "webhook_id": webhook_data["id"],
                        "name": webhook_data["name"],
                        "url": webhook_data["url"],
                        "events": webhook_data["events"],
                        "enabled": webhook_data["active"],
                        "service": "confluence"
                    }
            else:
                error_data = response.json() if response.content else {}
                return {
                    "success": False,
                    "error": error_data.get("message", f"HTTP {response.status_code}"),
                    "status_code": response.status_code,
                    "service": "jira" if self.is_jira else "confluence"
                }
                
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Request failed: {str(e)}",
                "service": "jira" if self.is_jira else "confluence"
            }
    
    def list_webhooks(self) -> Dict:
        """List all webhooks."""
        try:
            if self.is_jira:
                endpoint = f"{self.instance_url}/rest/webhooks/1.0/webhook"
            else:
                endpoint = f"{self.instance_url}/rest/api/webhooks"
            
            response = self.session.get(endpoint, timeout=30)
            
            if response.status_code == 200:
                webhooks_data = response.json()
                webhooks = []
                
                if self.is_jira:
                    # Jira returns a different structure
                    for webhook in webhooks_data:
                        webhooks.append({
                            "webhook_id": webhook["self"].split("/")[-1],
                            "name": webhook["name"],
                            "url": webhook["url"],
                            "events": webhook["events"],
                            "enabled": webhook["enabled"]
                        })
                else:
                    # Confluence structure
                    for webhook in webhooks_data:
                        webhooks.append({
                            "webhook_id": webhook["id"],
                            "name": webhook["name"],
                            "url": webhook["url"],
                            "events": webhook["events"],
                            "enabled": webhook["active"]
                        })
                
                return {
                    "success": True,
                    "webhooks": webhooks,
                    "count": len(webhooks),
                    "service": "jira" if self.is_jira else "confluence"
                }
            else:
                error_data = response.json() if response.content else {}
                return {
                    "success": False,
                    "error": error_data.get("message", f"HTTP {response.status_code}"),
                    "status_code": response.status_code,
                    "service": "jira" if self.is_jira else "confluence"
                }
                
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Request failed: {str(e)}",
                "service": "jira" if self.is_jira else "confluence"
            }
    
    def delete_webhook(self, webhook_id: str) -> Dict:
        """Delete a webhook."""
        try:
            if self.is_jira:
                endpoint = f"{self.instance_url}/rest/webhooks/1.0/webhook/{webhook_id}"
            else:
                endpoint = f"{self.instance_url}/rest/api/webhooks/{webhook_id}"
            
            response = self.session.delete(endpoint, timeout=30)
            
            if response.status_code == 204:
                return {
                    "success": True,
                    "webhook_id": webhook_id,
                    "message": "Webhook deleted successfully",
                    "service": "jira" if self.is_jira else "confluence"
                }
            else:
                error_data = response.json() if response.content else {}
                return {
                    "success": False,
                    "error": error_data.get("message", f"HTTP {response.status_code}"),
                    "status_code": response.status_code,
                    "service": "jira" if self.is_jira else "confluence"
                }
                
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Request failed: {str(e)}",
                "service": "jira" if self.is_jira else "confluence"
            }
    
    def find_webhook_by_url(self, url_pattern: str) -> Dict:
        """Find webhook by URL pattern."""
        webhooks_result = self.list_webhooks()
        
        if not webhooks_result["success"]:
            return webhooks_result
        
        matching_webhooks = []
        for webhook in webhooks_result["webhooks"]:
            if webhook["url"] and url_pattern in webhook["url"]:
                matching_webhooks.append(webhook)
        
        return {
            "success": True,
            "matching_webhooks": matching_webhooks,
            "count": len(matching_webhooks),
            "service": "jira" if self.is_jira else "confluence"
        }
    
    def get_instance_info(self) -> Dict:
        """Get basic instance information."""
        try:
            if self.is_jira:
                response = self.session.get(f"{self.instance_url}/rest/api/2/serverInfo", timeout=30)
            else:
                response = self.session.get(f"{self.instance_url}/rest/api/space", timeout=30)
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "instance_url": self.instance_url,
                    "service": "jira" if self.is_jira else "confluence",
                    "accessible": True
                }
            else:
                return {
                    "success": False,
                    "error": f"Instance not accessible: HTTP {response.status_code}",
                    "instance_url": self.instance_url
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": f"Connection failed: {str(e)}",
                "instance_url": self.instance_url
            }

def main():
    """Command line interface for Atlassian webhook management."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Atlassian Webhook Manager")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Create webhook
    create_parser = subparsers.add_parser("create", help="Create a new webhook")
    create_parser.add_argument("url", help="Webhook URL")
    create_parser.add_argument("--name", help="Webhook name")
    create_parser.add_argument("--events", nargs="+", help="Webhook events")
    
    # Update webhook
    update_parser = subparsers.add_parser("update", help="Update existing webhook")
    update_parser.add_argument("webhook_id", help="Webhook ID")
    update_parser.add_argument("url", help="New webhook URL")
    
    # List webhooks
    list_parser = subparsers.add_parser("list", help="List webhooks")
    
    # Get webhook
    get_parser = subparsers.add_parser("get", help="Get webhook details")
    get_parser.add_argument("webhook_id", help="Webhook ID")
    
    # Delete webhook
    delete_parser = subparsers.add_parser("delete", help="Delete webhook")
    delete_parser.add_argument("webhook_id", help="Webhook ID")
    
    # Instance info
    info_parser = subparsers.add_parser("info", help="Get instance information")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    try:
        atlassian_manager = AtlassianWebhookManager()
        
        if args.command == "create":
            result = atlassian_manager.create_webhook(args.url, args.events, args.name)
        elif args.command == "update":
            result = atlassian_manager.update_webhook(args.webhook_id, args.url)
        elif args.command == "list":
            result = atlassian_manager.list_webhooks()
        elif args.command == "get":
            result = atlassian_manager.get_webhook(args.webhook_id)
        elif args.command == "delete":
            result = atlassian_manager.delete_webhook(args.webhook_id)
        elif args.command == "info":
            result = atlassian_manager.get_instance_info()
        
        print(json.dumps(result, indent=2))
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()