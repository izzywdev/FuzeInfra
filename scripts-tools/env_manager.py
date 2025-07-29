#!/usr/bin/env python3
"""
Environment Variable Manager for Mendys Robot Scraper Platform

This script provides safe operations for managing .env files:
- Add new environment variables
- Modify existing values
- Remove variables
- Create automatic backups
- Validate changes
- Support multiple .env files

Usage:
    python env_manager.py add KEY=VALUE
    python env_manager.py modify KEY=NEW_VALUE
    python env_manager.py remove KEY
    python env_manager.py backup
    python env_manager.py restore BACKUP_FILE
    python env_manager.py list
    python env_manager.py validate

Author: Mendys Robot Scraper Platform Team
"""

import os
import sys
import argparse
import shutil
import datetime
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional


class EnvManager:
    """Manages .env files with safe operations and automatic backups."""
    
    def __init__(self, env_file: str = '.env', backup_dir: str = 'backups/env'):
        """
        Initialize the environment manager.
        
        Args:
            env_file: Path to the .env file (default: '.env')
            backup_dir: Directory for backup files (default: 'backups/env')
        """
        self.env_file = Path(env_file)
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        # Ensure we're working from the project root
        if not self.env_file.exists() and env_file == '.env':
            # Try to find .env file in parent directories
            current = Path.cwd()
            for parent in [current] + list(current.parents):
                potential_env = parent / '.env'
                if potential_env.exists():
                    self.env_file = potential_env
                    break
    
    def create_backup(self, suffix: str = None) -> Path:
        """
        Create a backup of the current .env file.
        
        Args:
            suffix: Optional suffix for the backup filename
            
        Returns:
            Path to the created backup file
        """
        if not self.env_file.exists():
            print(f"❌ Warning: {self.env_file} does not exist, cannot create backup")
            return None
            
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        if suffix:
            backup_name = f".env.backup.{suffix}.{timestamp}"
        else:
            backup_name = f".env.backup.{timestamp}"
            
        backup_path = self.backup_dir / backup_name
        
        try:
            shutil.copy2(self.env_file, backup_path)
            print(f"✅ Backup created: {backup_path}")
            return backup_path
        except Exception as e:
            print(f"❌ Error creating backup: {e}")
            return None
    
    def read_env_file(self) -> List[str]:
        """
        Read the .env file and return lines.
        
        Returns:
            List of lines from the .env file
        """
        if not self.env_file.exists():
            print(f"📝 Creating new .env file: {self.env_file}")
            return []
            
        try:
            with open(self.env_file, 'r', encoding='utf-8') as f:
                return f.readlines()
        except Exception as e:
            print(f"❌ Error reading {self.env_file}: {e}")
            return []
    
    def write_env_file(self, lines: List[str]) -> bool:
        """
        Write lines to the .env file.
        
        Args:
            lines: List of lines to write
            
        Returns:
            True if successful, False otherwise
        """
        try:
            with open(self.env_file, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            print(f"✅ Successfully updated {self.env_file}")
            return True
        except Exception as e:
            print(f"❌ Error writing {self.env_file}: {e}")
            return False
    
    def parse_env_lines(self, lines: List[str]) -> Dict[str, Tuple[str, int]]:
        """
        Parse .env lines and extract key-value pairs with line numbers.
        
        Args:
            lines: List of lines from .env file
            
        Returns:
            Dictionary mapping keys to (value, line_number) tuples
        """
        env_vars = {}
        for i, line in enumerate(lines):
            line = line.strip()
            if line and not line.startswith('#'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = (value.strip(), i)
        return env_vars
    
    def add_variable(self, key: str, value: str, comment: str = None) -> bool:
        """
        Add a new environment variable.
        
        Args:
            key: Environment variable key
            value: Environment variable value
            comment: Optional comment to add above the variable
            
        Returns:
            True if successful, False otherwise
        """
        lines = self.read_env_file()
        env_vars = self.parse_env_lines(lines)
        
        if key in env_vars:
            print(f"⚠️  Variable {key} already exists. Use 'modify' to change it.")
            return False
        
        # Create backup before making changes
        self.create_backup(suffix="before_add")
        
        # Add the new variable at the end
        if lines and not lines[-1].endswith('\n'):
            lines.append('\n')
        
        if comment:
            lines.append(f"# {comment}\n")
        
        lines.append(f"{key}={value}\n")
        
        return self.write_env_file(lines)
    
    def modify_variable(self, key: str, value: str) -> bool:
        """
        Modify an existing environment variable.
        
        Args:
            key: Environment variable key
            value: New environment variable value
            
        Returns:
            True if successful, False otherwise
        """
        lines = self.read_env_file()
        env_vars = self.parse_env_lines(lines)
        
        if key not in env_vars:
            print(f"⚠️  Variable {key} does not exist. Use 'add' to create it.")
            return False
        
        # Create backup before making changes
        self.create_backup(suffix="before_modify")
        
        # Update the line
        old_value, line_num = env_vars[key]
        lines[line_num] = f"{key}={value}\n"
        
        print(f"🔄 Changing {key}: '{old_value}' → '{value}'")
        return self.write_env_file(lines)
    
    def remove_variable(self, key: str) -> bool:
        """
        Remove an environment variable.
        
        Args:
            key: Environment variable key to remove
            
        Returns:
            True if successful, False otherwise
        """
        lines = self.read_env_file()
        env_vars = self.parse_env_lines(lines)
        
        if key not in env_vars:
            print(f"⚠️  Variable {key} does not exist.")
            return False
        
        # Create backup before making changes
        self.create_backup(suffix="before_remove")
        
        # Remove the line
        old_value, line_num = env_vars[key]
        lines.pop(line_num)
        
        print(f"🗑️  Removed {key}={old_value}")
        return self.write_env_file(lines)
    
    def list_variables(self) -> None:
        """List all environment variables in the .env file."""
        lines = self.read_env_file()
        env_vars = self.parse_env_lines(lines)
        
        if not env_vars:
            print(f"📝 No environment variables found in {self.env_file}")
            return
        
        print(f"📋 Environment variables in {self.env_file}:")
        print("=" * 50)
        
        for key, (value, _) in sorted(env_vars.items()):
            # Mask sensitive values
            if any(sensitive in key.lower() for sensitive in ['password', 'secret', 'key', 'token']):
                masked_value = value[:4] + '*' * (len(value) - 4) if len(value) > 4 else '***'
                print(f"{key}={masked_value}")
            else:
                print(f"{key}={value}")
    
    def validate_env_file(self) -> bool:
        """
        Validate the .env file for common issues.
        
        Returns:
            True if validation passes, False otherwise
        """
        if not self.env_file.exists():
            print(f"❌ {self.env_file} does not exist")
            return False
        
        lines = self.read_env_file()
        issues = []
        
        for i, line in enumerate(lines, 1):
            line = line.strip()
            if line and not line.startswith('#'):
                if '=' not in line:
                    issues.append(f"Line {i}: Missing '=' in '{line}'")
                elif line.startswith('='):
                    issues.append(f"Line {i}: Key cannot be empty in '{line}'")
                elif line.count('=') > 1 and not (line.count('=') == 2 and '==' in line):
                    # Allow == for comparison in comments but warn about multiple =
                    if not line.startswith('#'):
                        issues.append(f"Line {i}: Multiple '=' found in '{line}' (use quotes if needed)")
        
        if issues:
            print("❌ Validation failed:")
            for issue in issues:
                print(f"   {issue}")
            return False
        else:
            print("✅ .env file validation passed")
            return True
    
    def restore_backup(self, backup_file: str) -> bool:
        """
        Restore from a backup file.
        
        Args:
            backup_file: Path to the backup file
            
        Returns:
            True if successful, False otherwise
        """
        backup_path = Path(backup_file)
        
        # Check if it's a relative path in backup directory
        if not backup_path.exists():
            backup_path = self.backup_dir / backup_file
        
        if not backup_path.exists():
            print(f"❌ Backup file not found: {backup_file}")
            return False
        
        # Create backup of current state before restoring
        self.create_backup(suffix="before_restore")
        
        try:
            shutil.copy2(backup_path, self.env_file)
            print(f"✅ Restored from backup: {backup_path}")
            return True
        except Exception as e:
            print(f"❌ Error restoring from backup: {e}")
            return False
    
    def list_backups(self) -> None:
        """List available backup files."""
        backup_files = list(self.backup_dir.glob(".env.backup.*"))
        
        if not backup_files:
            print(f"📝 No backup files found in {self.backup_dir}")
            return
        
        print(f"📋 Available backup files in {self.backup_dir}:")
        print("=" * 60)
        
        backup_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        
        for backup_file in backup_files:
            mtime = datetime.datetime.fromtimestamp(backup_file.stat().st_mtime)
            size = backup_file.stat().st_size
            print(f"{backup_file.name:<30} {mtime.strftime('%Y-%m-%d %H:%M:%S')} ({size} bytes)")


def main():
    """Main CLI interface for the environment manager."""
    parser = argparse.ArgumentParser(
        description="Manage .env files safely with automatic backups",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python env_manager.py add DATABASE_URL=postgresql://localhost:5432/mydb
  python env_manager.py modify OPENAI_API_KEY=sk-new-key-here
  python env_manager.py remove OLD_VARIABLE
  python env_manager.py list
  python env_manager.py backup
  python env_manager.py restore .env.backup.20231201_120000
  python env_manager.py validate
        """
    )
    
    parser.add_argument(
        'action',
        choices=['add', 'modify', 'remove', 'list', 'backup', 'restore', 'validate', 'list-backups'],
        help='Action to perform'
    )
    
    parser.add_argument(
        'target',
        nargs='?',
        help='Target for the action (KEY=VALUE for add/modify, KEY for remove, backup_file for restore)'
    )
    
    parser.add_argument(
        '--env-file',
        default='.env',
        help='Path to .env file (default: .env)'
    )
    
    parser.add_argument(
        '--backup-dir',
        default='backups/env',
        help='Backup directory (default: backups/env)'
    )
    
    parser.add_argument(
        '--comment',
        help='Comment to add above new variable (for add action)'
    )
    
    args = parser.parse_args()
    
    # Initialize the environment manager
    env_manager = EnvManager(args.env_file, args.backup_dir)
    
    # Execute the requested action
    try:
        if args.action == 'add':
            if not args.target or '=' not in args.target:
                print("❌ Error: add requires KEY=VALUE format")
                sys.exit(1)
            
            key, value = args.target.split('=', 1)
            success = env_manager.add_variable(key.strip(), value.strip(), args.comment)
            sys.exit(0 if success else 1)
        
        elif args.action == 'modify':
            if not args.target or '=' not in args.target:
                print("❌ Error: modify requires KEY=VALUE format")
                sys.exit(1)
            
            key, value = args.target.split('=', 1)
            success = env_manager.modify_variable(key.strip(), value.strip())
            sys.exit(0 if success else 1)
        
        elif args.action == 'remove':
            if not args.target:
                print("❌ Error: remove requires KEY")
                sys.exit(1)
            
            success = env_manager.remove_variable(args.target.strip())
            sys.exit(0 if success else 1)
        
        elif args.action == 'list':
            env_manager.list_variables()
        
        elif args.action == 'backup':
            backup_path = env_manager.create_backup()
            sys.exit(0 if backup_path else 1)
        
        elif args.action == 'restore':
            if not args.target:
                print("❌ Error: restore requires backup filename")
                sys.exit(1)
            
            success = env_manager.restore_backup(args.target)
            sys.exit(0 if success else 1)
        
        elif args.action == 'validate':
            success = env_manager.validate_env_file()
            sys.exit(0 if success else 1)
        
        elif args.action == 'list-backups':
            env_manager.list_backups()
    
    except KeyboardInterrupt:
        print("\n🛑 Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main() 