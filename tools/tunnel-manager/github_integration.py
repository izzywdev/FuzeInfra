#!/usr/bin/env python3
"""
GitHub API Integration for Tunnel Manager
Handles GitHub webhook creation, updates, and management.
"""

import os
import requests
from typing import Dict, List, Optional, Any
from github import Github
import json

class GitHubWebhookManager:
    """Manages GitHub webhooks via REST API."""
    
    def __init__(self, token: str = None):
        """Initialize GitHub webhook manager."""
        self.token = token or os.getenv("GITHUB_TOKEN")
        if not self.token:
            raise ValueError("GitHub token is required. Set GITHUB_TOKEN environment variable.")
        
        self.github = Github(self.token)
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "FuzeInfra-TunnelManager/1.0"
        })
        self.base_url = "https://api.github.com"
    
    def create_webhook(self, repo_owner: str, repo_name: str, webhook_url: str, 
                      events: List[str] = None, secret: str = None) -> Dict:
        """Create a new webhook for a GitHub repository."""
        events = events or ["push", "pull_request", "issues"]
        secret = secret or os.getenv("GITHUB_WEBHOOK_SECRET", "")
        
        webhook_config = {
            "name": "web",
            "active": True,
            "events": events,
            "config": {
                "url": webhook_url,
                "content_type": "json",
                "insecure_ssl": "0"
            }
        }
        
        if secret:
            webhook_config["config"]["secret"] = secret
        
        try:
            response = self.session.post(
                f"{self.base_url}/repos/{repo_owner}/{repo_name}/hooks",
                json=webhook_config,
                timeout=30
            )
            
            if response.status_code == 201:
                webhook_data = response.json()
                return {
                    "success": True,
                    "webhook_id": webhook_data["id"],
                    "webhook_url": webhook_data["config"]["url"],
                    "events": webhook_data["events"],
                    "active": webhook_data["active"],
                    "created_at": webhook_data["created_at"],
                    "ping_url": webhook_data["ping_url"]
                }
            else:
                error_data = response.json() if response.content else {}
                return {
                    "success": False,
                    "error": error_data.get("message", f"HTTP {response.status_code}"),
                    "status_code": response.status_code
                }
                
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Request failed: {str(e)}"
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Unexpected error: {str(e)}"
            }
    
    def update_webhook(self, repo_owner: str, repo_name: str, webhook_id: int, 
                      new_webhook_url: str, events: List[str] = None, 
                      secret: str = None) -> Dict:
        """Update an existing webhook URL and configuration."""
        current_webhook = self.get_webhook(repo_owner, repo_name, webhook_id)
        if not current_webhook["success"]:
            return current_webhook
        
        # Preserve existing configuration if not specified
        existing_config = current_webhook["config"]
        events = events or current_webhook["events"]
        secret = secret or existing_config.get("secret", os.getenv("GITHUB_WEBHOOK_SECRET", ""))
        
        webhook_config = {
            "active": True,
            "events": events,
            "config": {
                "url": new_webhook_url,
                "content_type": existing_config.get("content_type", "json"),
                "insecure_ssl": existing_config.get("insecure_ssl", "0")
            }
        }
        
        if secret:
            webhook_config["config"]["secret"] = secret
        
        try:
            response = self.session.patch(
                f"{self.base_url}/repos/{repo_owner}/{repo_name}/hooks/{webhook_id}",
                json=webhook_config,
                timeout=30
            )
            
            if response.status_code == 200:
                webhook_data = response.json()
                return {
                    "success": True,
                    "webhook_id": webhook_data["id"],
                    "old_url": existing_config.get("url"),
                    "new_url": webhook_data["config"]["url"],
                    "events": webhook_data["events"],
                    "updated_at": webhook_data["updated_at"]
                }
            else:
                error_data = response.json() if response.content else {}
                return {
                    "success": False,
                    "error": error_data.get("message", f"HTTP {response.status_code}"),
                    "status_code": response.status_code
                }
                
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Request failed: {str(e)}"
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Unexpected error: {str(e)}"
            }
    
    def get_webhook(self, repo_owner: str, repo_name: str, webhook_id: int) -> Dict:
        """Get webhook details by ID."""
        try:
            response = self.session.get(
                f"{self.base_url}/repos/{repo_owner}/{repo_name}/hooks/{webhook_id}",
                timeout=30
            )
            
            if response.status_code == 200:
                webhook_data = response.json()
                return {
                    "success": True,
                    "webhook_id": webhook_data["id"],
                    "name": webhook_data["name"],
                    "active": webhook_data["active"],
                    "events": webhook_data["events"],
                    "config": webhook_data["config"],
                    "created_at": webhook_data["created_at"],
                    "updated_at": webhook_data["updated_at"],
                    "ping_url": webhook_data.get("ping_url")
                }
            else:
                error_data = response.json() if response.content else {}
                return {
                    "success": False,
                    "error": error_data.get("message", f"HTTP {response.status_code}"),
                    "status_code": response.status_code
                }
                
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Request failed: {str(e)}"
            }
    
    def list_webhooks(self, repo_owner: str, repo_name: str) -> Dict:
        """List all webhooks for a repository."""
        try:
            response = self.session.get(
                f"{self.base_url}/repos/{repo_owner}/{repo_name}/hooks",
                timeout=30
            )
            
            if response.status_code == 200:
                webhooks_data = response.json()
                webhooks = []
                
                for webhook in webhooks_data:
                    webhooks.append({
                        "webhook_id": webhook["id"],
                        "name": webhook["name"],
                        "active": webhook["active"],
                        "events": webhook["events"],
                        "url": webhook["config"].get("url"),
                        "created_at": webhook["created_at"],
                        "updated_at": webhook["updated_at"]
                    })
                
                return {
                    "success": True,
                    "webhooks": webhooks,
                    "count": len(webhooks)
                }
            else:
                error_data = response.json() if response.content else {}
                return {
                    "success": False,
                    "error": error_data.get("message", f"HTTP {response.status_code}"),
                    "status_code": response.status_code
                }
                
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Request failed: {str(e)}"
            }
    
    def delete_webhook(self, repo_owner: str, repo_name: str, webhook_id: int) -> Dict:
        """Delete a webhook."""
        try:
            response = self.session.delete(
                f"{self.base_url}/repos/{repo_owner}/{repo_name}/hooks/{webhook_id}",
                timeout=30
            )
            
            if response.status_code == 204:
                return {
                    "success": True,
                    "webhook_id": webhook_id,
                    "message": "Webhook deleted successfully"
                }
            else:
                error_data = response.json() if response.content else {}
                return {
                    "success": False,
                    "error": error_data.get("message", f"HTTP {response.status_code}"),
                    "status_code": response.status_code
                }
                
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Request failed: {str(e)}"
            }
    
    def test_webhook(self, repo_owner: str, repo_name: str, webhook_id: int) -> Dict:
        """Test a webhook by sending a ping."""
        try:
            response = self.session.post(
                f"{self.base_url}/repos/{repo_owner}/{repo_name}/hooks/{webhook_id}/pings",
                timeout=30
            )
            
            if response.status_code == 204:
                return {
                    "success": True,
                    "webhook_id": webhook_id,
                    "message": "Ping sent successfully"
                }
            else:
                error_data = response.json() if response.content else {}
                return {
                    "success": False,
                    "error": error_data.get("message", f"HTTP {response.status_code}"),
                    "status_code": response.status_code
                }
                
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Request failed: {str(e)}"
            }
    
    def find_webhook_by_url(self, repo_owner: str, repo_name: str, url_pattern: str) -> Dict:
        """Find webhook by URL pattern."""
        webhooks_result = self.list_webhooks(repo_owner, repo_name)
        
        if not webhooks_result["success"]:
            return webhooks_result
        
        matching_webhooks = []
        for webhook in webhooks_result["webhooks"]:
            if webhook["url"] and url_pattern in webhook["url"]:
                matching_webhooks.append(webhook)
        
        return {
            "success": True,
            "matching_webhooks": matching_webhooks,
            "count": len(matching_webhooks)
        }
    
    def get_repository_info(self, repo_owner: str, repo_name: str) -> Dict:
        """Get basic repository information."""
        try:
            repo = self.github.get_repo(f"{repo_owner}/{repo_name}")
            return {
                "success": True,
                "full_name": repo.full_name,
                "private": repo.private,
                "owner": repo.owner.login,
                "default_branch": repo.default_branch,
                "clone_url": repo.clone_url,
                "webhooks_url": f"{self.base_url}/repos/{repo_owner}/{repo_name}/hooks"
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Repository not found or access denied: {str(e)}"
            }

def main():
    """Command line interface for GitHub webhook management."""
    import argparse
    
    parser = argparse.ArgumentParser(description="GitHub Webhook Manager")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Create webhook
    create_parser = subparsers.add_parser("create", help="Create a new webhook")
    create_parser.add_argument("repo", help="Repository (owner/name)")
    create_parser.add_argument("url", help="Webhook URL")
    create_parser.add_argument("--events", nargs="+", default=["push", "pull_request"], help="Webhook events")
    
    # Update webhook
    update_parser = subparsers.add_parser("update", help="Update existing webhook")
    update_parser.add_argument("repo", help="Repository (owner/name)")
    update_parser.add_argument("webhook_id", type=int, help="Webhook ID")
    update_parser.add_argument("url", help="New webhook URL")
    
    # List webhooks
    list_parser = subparsers.add_parser("list", help="List repository webhooks")
    list_parser.add_argument("repo", help="Repository (owner/name)")
    
    # Test webhook
    test_parser = subparsers.add_parser("test", help="Test webhook with ping")
    test_parser.add_argument("repo", help="Repository (owner/name)")
    test_parser.add_argument("webhook_id", type=int, help="Webhook ID")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    try:
        github_manager = GitHubWebhookManager()
        
        if args.command == "create":
            owner, name = args.repo.split("/", 1)
            result = github_manager.create_webhook(owner, name, args.url, args.events)
        elif args.command == "update":
            owner, name = args.repo.split("/", 1)
            result = github_manager.update_webhook(owner, name, args.webhook_id, args.url)
        elif args.command == "list":
            owner, name = args.repo.split("/", 1)
            result = github_manager.list_webhooks(owner, name)
        elif args.command == "test":
            owner, name = args.repo.split("/", 1)
            result = github_manager.test_webhook(owner, name, args.webhook_id)
        
        print(json.dumps(result, indent=2))
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()