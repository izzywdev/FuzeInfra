#!/usr/bin/env python3
"""
Webhook Synchronization and Health Monitoring for Tunnel Manager
Automatically updates webhook URLs when tunnel URLs change and monitors health.
"""

import os
import sys
import json
import time
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path

# Import our integration modules
from tunnel_manager import TunnelManager
from github_integration import GitHubWebhookManager
from atlassian_integration import AtlassianWebhookManager

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('webhook_sync.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class WebhookSyncManager:
    """Manages webhook synchronization and health monitoring."""
    
    def __init__(self, config_path: str = "config.yaml"):
        """Initialize the webhook sync manager."""
        self.tunnel_manager = TunnelManager(config_path)
        self.config = self.tunnel_manager.config
        self.running = False
        
        # Initialize API managers
        self.github_manager = None
        self.atlassian_manager = None
        
        try:
            if os.getenv("GITHUB_TOKEN"):
                self.github_manager = GitHubWebhookManager()
                logger.info("GitHub integration initialized")
        except Exception as e:
            logger.warning(f"GitHub integration failed: {e}")
        
        try:
            if all([os.getenv("ATLASSIAN_INSTANCE"), os.getenv("ATLASSIAN_EMAIL"), os.getenv("ATLASSIAN_API_TOKEN")]):
                self.atlassian_manager = AtlassianWebhookManager()
                logger.info(f"Atlassian integration initialized for {self.atlassian_manager.instance_url}")
        except Exception as e:
            logger.warning(f"Atlassian integration failed: {e}")
    
    def sync_webhook(self, webhook_record: Dict) -> Dict:
        """Sync a single webhook to external service."""
        project = webhook_record["project"]
        service = webhook_record["service"]
        webhook_type = webhook_record["webhook_type"]
        external_id = webhook_record.get("external_id")
        metadata = webhook_record.get("metadata", {})
        
        # Generate current webhook URL
        current_url = self.tunnel_manager.generate_webhook_url(project, service, webhook_type)
        stored_url = webhook_record["url"]
        
        # Check if URL has changed
        if current_url == stored_url:
            logger.debug(f"No URL change for {project}/{service} - skipping sync")
            return {
                "success": True,
                "action": "no_change",
                "webhook_url": current_url
            }
        
        logger.info(f"URL changed for {project}/{service}: {stored_url} -> {current_url}")
        
        # Update webhook based on service type
        if webhook_type == "github" and self.github_manager and external_id:
            return self._sync_github_webhook(webhook_record, current_url)
        elif webhook_type == "atlassian" and self.atlassian_manager and external_id:
            return self._sync_atlassian_webhook(webhook_record, current_url)
        else:
            # Manual update required - generate notification
            return self._generate_manual_update_notification(webhook_record, current_url)
    
    def _sync_github_webhook(self, webhook_record: Dict, new_url: str) -> Dict:
        """Sync GitHub webhook URL."""
        try:
            metadata = webhook_record["metadata"]
            repo_owner = metadata.get("repo_owner")
            repo_name = metadata.get("repo_name")
            webhook_id = int(webhook_record["external_id"])
            
            if not all([repo_owner, repo_name]):
                return {
                    "success": False,
                    "error": "Missing repository information in metadata"
                }
            
            # Update GitHub webhook
            result = self.github_manager.update_webhook(
                repo_owner, repo_name, webhook_id, new_url
            )
            
            if result["success"]:
                # Update local registry
                self.tunnel_manager.register_webhook(
                    webhook_record["project"],
                    webhook_record["service"],
                    webhook_record["webhook_type"],
                    webhook_record["external_id"],
                    metadata
                )
                
                logger.info(f"Successfully updated GitHub webhook {webhook_id}: {new_url}")
                return {
                    "success": True,
                    "action": "updated",
                    "service": "github",
                    "webhook_id": webhook_id,
                    "old_url": result.get("old_url"),
                    "new_url": new_url
                }
            else:
                logger.error(f"Failed to update GitHub webhook {webhook_id}: {result.get('error')}")
                return result
                
        except Exception as e:
            logger.error(f"GitHub webhook sync error: {e}")
            return {
                "success": False,
                "error": f"GitHub sync failed: {str(e)}"
            }
    
    def _sync_atlassian_webhook(self, webhook_record: Dict, new_url: str) -> Dict:
        """Sync Atlassian webhook URL."""
        try:
            webhook_id = webhook_record["external_id"]
            
            # Update Atlassian webhook
            result = self.atlassian_manager.update_webhook(webhook_id, new_url)
            
            if result["success"]:
                # Update local registry
                self.tunnel_manager.register_webhook(
                    webhook_record["project"],
                    webhook_record["service"], 
                    webhook_record["webhook_type"],
                    webhook_record["external_id"],
                    webhook_record.get("metadata", {})
                )
                
                logger.info(f"Successfully updated Atlassian webhook {webhook_id}: {new_url}")
                return {
                    "success": True,
                    "action": "updated",
                    "service": result["service"],
                    "webhook_id": webhook_id,
                    "old_url": result.get("old_url"),
                    "new_url": new_url
                }
            else:
                logger.error(f"Failed to update Atlassian webhook {webhook_id}: {result.get('error')}")
                return result
                
        except Exception as e:
            logger.error(f"Atlassian webhook sync error: {e}")
            return {
                "success": False,
                "error": f"Atlassian sync failed: {str(e)}"
            }
    
    def _generate_manual_update_notification(self, webhook_record: Dict, new_url: str) -> Dict:
        """Generate notification for manual webhook updates."""
        notification = {
            "type": "manual_update_required",
            "project": webhook_record["project"],
            "service": webhook_record["service"],
            "webhook_type": webhook_record["webhook_type"],
            "old_url": webhook_record["url"],
            "new_url": new_url,
            "timestamp": datetime.now().isoformat(),
            "instructions": self._get_manual_update_instructions(webhook_record["webhook_type"])
        }
        
        # Log notification
        logger.warning(f"Manual update required for {webhook_record['project']}/{webhook_record['service']}")
        logger.info(f"New URL: {new_url}")
        
        # Save notification to file
        notifications_file = Path("manual_update_notifications.json")
        notifications = []
        
        if notifications_file.exists():
            try:
                with open(notifications_file, 'r') as f:
                    notifications = json.load(f)
            except:
                notifications = []
        
        notifications.append(notification)
        
        with open(notifications_file, 'w') as f:
            json.dump(notifications, f, indent=2)
        
        return {
            "success": True,
            "action": "manual_update_required",
            "notification": notification
        }
    
    def _get_manual_update_instructions(self, webhook_type: str) -> Dict:
        """Get manual update instructions for different webhook types."""
        if webhook_type == "oauth" and "google" in webhook_type.lower():
            return {
                "service": "Google OAuth",
                "steps": [
                    "1. Go to Google Cloud Console (https://console.cloud.google.com/apis/credentials)",
                    "2. Select your OAuth 2.0 Client ID",
                    "3. Update the 'Authorized redirect URIs' section",
                    "4. Add the new URL to the list",
                    "5. Save the changes",
                    "6. Changes may take 5 minutes to a few hours to take effect"
                ],
                "documentation": "https://developers.google.com/identity/protocols/oauth2/web-server#creatingcred"
            }
        else:
            return {
                "service": "Unknown service",
                "steps": [
                    "1. Log into your service's webhook management interface",
                    "2. Find the webhook configuration",
                    "3. Update the webhook URL to the new value",
                    "4. Save the changes"
                ]
            }
    
    def sync_all_webhooks(self) -> Dict:
        """Sync all registered webhooks."""
        webhooks = self.tunnel_manager.list_webhooks(active_only=True)
        results = {
            "total_webhooks": len(webhooks),
            "successful_syncs": 0,
            "failed_syncs": 0,
            "manual_updates_required": 0,
            "no_changes": 0,
            "details": []
        }
        
        for webhook in webhooks:
            try:
                sync_result = self.sync_webhook(webhook)
                results["details"].append({
                    "project": webhook["project"],
                    "service": webhook["service"],
                    "webhook_type": webhook["webhook_type"],
                    "result": sync_result
                })
                
                if sync_result["success"]:
                    if sync_result["action"] == "updated":
                        results["successful_syncs"] += 1
                    elif sync_result["action"] == "manual_update_required":
                        results["manual_updates_required"] += 1
                    else:
                        results["no_changes"] += 1
                else:
                    results["failed_syncs"] += 1
                    
            except Exception as e:
                logger.error(f"Error syncing webhook {webhook['project']}/{webhook['service']}: {e}")
                results["failed_syncs"] += 1
                results["details"].append({
                    "project": webhook["project"],
                    "service": webhook["service"],
                    "webhook_type": webhook["webhook_type"],
                    "result": {
                        "success": False,
                        "error": str(e)
                    }
                })
        
        logger.info(f"Sync completed: {results['successful_syncs']} updated, "
                   f"{results['failed_syncs']} failed, "
                   f"{results['manual_updates_required']} manual updates required")
        
        return results
    
    def monitor_tunnel_health(self) -> Dict:
        """Monitor tunnel health and trigger sync if needed."""
        tunnel_status = self.tunnel_manager.get_tunnel_status()
        
        if not tunnel_status["tunnel"]["healthy"]:
            logger.warning("Tunnel is unhealthy - sync may be needed when restored")
            return {
                "healthy": False,
                "sync_triggered": False,
                "tunnel_status": tunnel_status
            }
        
        # If tunnel is healthy, check if we need to sync
        # This could be triggered by tunnel restart detection
        return {
            "healthy": True,
            "sync_triggered": False,
            "tunnel_status": tunnel_status
        }
    
    def start_monitoring(self, interval_seconds: int = None):
        """Start continuous monitoring and sync."""
        interval_seconds = interval_seconds or self.config["monitoring"]["check_interval_seconds"]
        self.running = True
        
        logger.info(f"Starting webhook monitoring with {interval_seconds}s interval")
        
        try:
            while self.running:
                logger.debug("Running health check and sync...")
                
                # Check tunnel health
                health_result = self.monitor_tunnel_health()
                
                # Sync all webhooks periodically
                sync_result = self.sync_all_webhooks()
                
                if sync_result["failed_syncs"] > 0:
                    logger.warning(f"Some webhooks failed to sync: {sync_result['failed_syncs']}")
                
                # Sleep until next check
                time.sleep(interval_seconds)
                
        except KeyboardInterrupt:
            logger.info("Monitoring stopped by user")
        except Exception as e:
            logger.error(f"Monitoring error: {e}")
        finally:
            self.running = False
    
    def stop_monitoring(self):
        """Stop monitoring."""
        self.running = False
        logger.info("Monitoring stopped")

def main():
    """Command line interface for webhook sync manager."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Webhook Synchronization Manager")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Sync all webhooks
    sync_parser = subparsers.add_parser("sync", help="Sync all webhooks")
    
    # Start monitoring
    monitor_parser = subparsers.add_parser("monitor", help="Start continuous monitoring")
    monitor_parser.add_argument("--interval", type=int, default=60, help="Check interval in seconds")
    
    # Check health
    health_parser = subparsers.add_parser("health", help="Check tunnel health")
    
    # Show manual notifications
    notifications_parser = subparsers.add_parser("notifications", help="Show manual update notifications")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    try:
        sync_manager = WebhookSyncManager()
        
        if args.command == "sync":
            result = sync_manager.sync_all_webhooks()
            print(json.dumps(result, indent=2))
        elif args.command == "monitor":
            sync_manager.start_monitoring(args.interval)
        elif args.command == "health":
            result = sync_manager.monitor_tunnel_health()
            print(json.dumps(result, indent=2))
        elif args.command == "notifications":
            notifications_file = Path("manual_update_notifications.json")
            if notifications_file.exists():
                with open(notifications_file, 'r') as f:
                    notifications = json.load(f)
                print(json.dumps(notifications, indent=2))
            else:
                print("No manual update notifications found")
        
    except Exception as e:
        logger.error(f"Command failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()