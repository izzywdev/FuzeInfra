#!/usr/bin/env python3
"""
Version Manager for Mendys Robot Scraper Platform

This script provides semantic versioning management with the following features:
- Semantic versioning (MAJOR.MINOR.PATCH)
- Pre-release versions (alpha, beta, rc)
- Build metadata tracking
- Git integration for tagging
- Component version synchronization
- Release notes generation
- Version validation and comparison

Usage:
    python version_manager.py current
    python version_manager.py bump patch
    python version_manager.py bump minor --pre-release alpha
    python version_manager.py bump major
    python version_manager.py tag --push
    python version_manager.py info
    python version_manager.py validate

Author: Mendys Robot Scraper Platform Team
"""

import os
import sys
import json
import argparse
import subprocess
import datetime
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Union
from dataclasses import dataclass


@dataclass
class Version:
    """Represents a semantic version with all components."""
    major: int
    minor: int
    patch: int
    pre_release: Optional[str] = None
    pre_release_number: Optional[int] = None
    build_metadata: Optional[str] = None

    def __str__(self) -> str:
        """Return the version string in semver format."""
        version = f"{self.major}.{self.minor}.{self.patch}"
        
        if self.pre_release:
            version += f"-{self.pre_release}"
            if self.pre_release_number is not None:
                version += f".{self.pre_release_number}"
        
        if self.build_metadata:
            version += f"+{self.build_metadata}"
            
        return version

    @classmethod
    def parse(cls, version_string: str) -> 'Version':
        """Parse a version string into a Version object."""
        # Regex for semantic versioning
        pattern = r'^(\d+)\.(\d+)\.(\d+)(?:-([a-zA-Z]+)(?:\.(\d+))?)?(?:\+(.+))?$'
        match = re.match(pattern, version_string)
        
        if not match:
            raise ValueError(f"Invalid version string: {version_string}")
        
        major, minor, patch, pre_release, pre_release_num, build_metadata = match.groups()
        
        return cls(
            major=int(major),
            minor=int(minor),
            patch=int(patch),
            pre_release=pre_release,
            pre_release_number=int(pre_release_num) if pre_release_num else None,
            build_metadata=build_metadata
        )

    def compare(self, other: 'Version') -> int:
        """Compare two versions. Returns -1, 0, or 1."""
        # Compare major.minor.patch
        for self_val, other_val in [(self.major, other.major), 
                                   (self.minor, other.minor), 
                                   (self.patch, other.patch)]:
            if self_val < other_val:
                return -1
            elif self_val > other_val:
                return 1
        
        # Handle pre-release comparison
        if self.pre_release is None and other.pre_release is not None:
            return 1  # Release version is greater than pre-release
        elif self.pre_release is not None and other.pre_release is None:
            return -1  # Pre-release is less than release
        elif self.pre_release is not None and other.pre_release is not None:
            # Compare pre-release types (alpha < beta < rc)
            pre_release_order = {'alpha': 1, 'beta': 2, 'rc': 3}
            self_order = pre_release_order.get(self.pre_release, 0)
            other_order = pre_release_order.get(other.pre_release, 0)
            
            if self_order != other_order:
                return -1 if self_order < other_order else 1
            
            # Compare pre-release numbers
            self_num = self.pre_release_number or 0
            other_num = other.pre_release_number or 0
            if self_num < other_num:
                return -1
            elif self_num > other_num:
                return 1
        
        return 0


class VersionManager:
    """Manages semantic versioning for the Mendys Robot Scraper Platform."""
    
    def __init__(self, version_file: str = 'version.json'):
        """
        Initialize the version manager.
        
        Args:
            version_file: Path to the version.json file
        """
        self.version_file = Path(version_file)
        self.project_root = self._find_project_root()
        
        if self.project_root:
            self.version_file = self.project_root / version_file
        
        self.version_data = self._load_version_data()
    
    def _find_project_root(self) -> Optional[Path]:
        """Find the project root directory."""
        current = Path.cwd()
        for parent in [current] + list(current.parents):
            if (parent / 'version.json').exists():
                return parent
            if (parent / '.git').exists() and (parent / 'README.md').exists():
                return parent
        return None
    
    def _load_version_data(self) -> Dict:
        """Load version data from version.json."""
        if not self.version_file.exists():
            # Create default version file
            default_data = {
                "version": "0.1.0",
                "name": "Mendys Robot Scraper Platform",
                "description": "AI-powered industrial robot data collection & WordPress e-commerce sync",
                "build": {
                    "number": 1,
                    "date": datetime.datetime.utcnow().isoformat() + "Z",
                    "commit": "",
                    "branch": "main"
                },
                "components": {
                    "backend": "0.1.0",
                    "frontend": "0.1.0",
                    "infrastructure": "0.1.0",
                    "scrapy_framework": "2.11.0",
                    "wordpress_sync": "0.1.0"
                },
                "metadata": {
                    "release_date": datetime.date.today().isoformat(),
                    "compatibility": {
                        "python": ">=3.9",
                        "node": ">=16.0.0",
                        "docker": ">=20.0.0"
                    },
                    "database_schema_version": "0.1.0",
                    "api_version": "v1"
                }
            }
            self._save_version_data(default_data)
            return default_data
        
        try:
            with open(self.version_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Error loading version file: {e}")
            sys.exit(1)
    
    def _save_version_data(self, data: Dict) -> None:
        """Save version data to version.json."""
        try:
            with open(self.version_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"✅ Version file updated: {self.version_file}")
        except Exception as e:
            print(f"❌ Error saving version file: {e}")
            sys.exit(1)
    
    def get_current_version(self) -> Version:
        """Get the current version."""
        return Version.parse(self.version_data['version'])
    
    def get_git_info(self) -> Dict[str, str]:
        """Get current Git information."""
        git_info = {
            "commit": "",
            "branch": "main",
            "dirty": False
        }
        
        try:
            # Get current branch
            result = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], 
                                  capture_output=True, text=True, cwd=self.project_root)
            if result.returncode == 0:
                git_info["branch"] = result.stdout.strip()
            
            # Get current commit hash
            result = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], 
                                  capture_output=True, text=True, cwd=self.project_root)
            if result.returncode == 0:
                git_info["commit"] = result.stdout.strip()
            
            # Check if working directory is dirty
            result = subprocess.run(['git', 'status', '--porcelain'], 
                                  capture_output=True, text=True, cwd=self.project_root)
            if result.returncode == 0:
                git_info["dirty"] = bool(result.stdout.strip())
                
        except FileNotFoundError:
            print("⚠️  Git not found - Git integration disabled")
        except Exception as e:
            print(f"⚠️  Git error: {e}")
        
        return git_info
    
    def bump_version(self, bump_type: str, pre_release: Optional[str] = None, 
                    build_metadata: Optional[str] = None) -> Version:
        """
        Bump the version according to semantic versioning rules.
        
        Args:
            bump_type: 'major', 'minor', 'patch', or 'pre-release'
            pre_release: Pre-release identifier ('alpha', 'beta', 'rc')
            build_metadata: Build metadata string
            
        Returns:
            New Version object
        """
        current = self.get_current_version()
        
        if bump_type == 'major':
            new_version = Version(
                major=current.major + 1,
                minor=0,
                patch=0,
                pre_release=pre_release,
                pre_release_number=1 if pre_release else None,
                build_metadata=build_metadata
            )
        elif bump_type == 'minor':
            new_version = Version(
                major=current.major,
                minor=current.minor + 1,
                patch=0,
                pre_release=pre_release,
                pre_release_number=1 if pre_release else None,
                build_metadata=build_metadata
            )
        elif bump_type == 'patch':
            new_version = Version(
                major=current.major,
                minor=current.minor,
                patch=current.patch + 1,
                pre_release=pre_release,
                pre_release_number=1 if pre_release else None,
                build_metadata=build_metadata
            )
        elif bump_type == 'pre-release':
            if current.pre_release == pre_release:
                # Increment pre-release number
                new_number = (current.pre_release_number or 0) + 1
            else:
                # New pre-release type
                new_number = 1
            
            new_version = Version(
                major=current.major,
                minor=current.minor,
                patch=current.patch,
                pre_release=pre_release or current.pre_release,
                pre_release_number=new_number,
                build_metadata=build_metadata
            )
        else:
            raise ValueError(f"Invalid bump type: {bump_type}")
        
        return new_version
    
    def update_version(self, new_version: Version, update_components: bool = True) -> None:
        """
        Update the version in version.json and optionally sync component versions.
        
        Args:
            new_version: New version to set
            update_components: Whether to update component versions
        """
        git_info = self.get_git_info()
        
        # Update main version data
        self.version_data['version'] = str(new_version)
        self.version_data['build']['number'] += 1
        self.version_data['build']['date'] = datetime.datetime.utcnow().isoformat() + "Z"
        self.version_data['build']['commit'] = git_info['commit']
        self.version_data['build']['branch'] = git_info['branch']
        self.version_data['metadata']['release_date'] = datetime.date.today().isoformat()
        
        # Update component versions if requested
        if update_components:
            base_version = f"{new_version.major}.{new_version.minor}.{new_version.patch}"
            self.version_data['components']['backend'] = base_version
            self.version_data['components']['frontend'] = base_version
            self.version_data['components']['infrastructure'] = base_version
            self.version_data['components']['wordpress_sync'] = base_version
        
        self._save_version_data(self.version_data)
        print(f"🎯 Version updated: {new_version}")
        
        if git_info['dirty']:
            print("⚠️  Working directory has uncommitted changes")
    
    def create_git_tag(self, push: bool = False) -> bool:
        """
        Create a Git tag for the current version.
        
        Args:
            push: Whether to push the tag to remote
            
        Returns:
            True if successful, False otherwise
        """
        try:
            version = self.get_current_version()
            tag_name = f"v{version}"
            
            # Create annotated tag
            subprocess.run([
                'git', 'tag', '-a', tag_name, 
                '-m', f"Release {tag_name} - {self.version_data['description']}"
            ], check=True, cwd=self.project_root)
            
            print(f"✅ Git tag created: {tag_name}")
            
            if push:
                subprocess.run(['git', 'push', 'origin', tag_name], 
                             check=True, cwd=self.project_root)
                print(f"✅ Git tag pushed: {tag_name}")
            
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"❌ Git tag creation failed: {e}")
            return False
        except FileNotFoundError:
            print("❌ Git not found - cannot create tag")
            return False
    
    def validate_version(self) -> bool:
        """Validate the current version configuration."""
        try:
            current = self.get_current_version()
            print(f"✅ Version format valid: {current}")
            
            # Validate component versions
            for component, version_str in self.version_data['components'].items():
                try:
                    comp_version = Version.parse(version_str)
                    print(f"  ✅ {component}: {comp_version}")
                except ValueError as e:
                    print(f"  ❌ {component}: {e}")
                    return False
            
            # Validate metadata
            metadata = self.version_data.get('metadata', {})
            if 'api_version' in metadata:
                print(f"  ✅ API version: {metadata['api_version']}")
            
            return True
            
        except Exception as e:
            print(f"❌ Version validation failed: {e}")
            return False
    
    def get_version_info(self) -> Dict:
        """Get comprehensive version information."""
        current = self.get_current_version()
        git_info = self.get_git_info()
        
        return {
            "version": str(current),
            "parsed": {
                "major": current.major,
                "minor": current.minor,
                "patch": current.patch,
                "pre_release": current.pre_release,
                "pre_release_number": current.pre_release_number,
                "build_metadata": current.build_metadata
            },
            "build": self.version_data['build'],
            "git": git_info,
            "components": self.version_data['components'],
            "metadata": self.version_data['metadata']
        }
    
    def generate_changelog_entry(self, version: Version, changes: List[str] = None) -> str:
        """Generate a changelog entry for the version."""
        date = datetime.date.today().isoformat()
        git_info = self.get_git_info()
        
        entry = f"## [{version}] - {date}\n\n"
        
        if git_info['commit']:
            entry += f"**Build:** {git_info['commit']} ({git_info['branch']})\n\n"
        
        if changes:
            entry += "### Changes\n"
            for change in changes:
                entry += f"- {change}\n"
            entry += "\n"
        else:
            entry += "### Changes\n"
            entry += "- Version bump\n\n"
        
        return entry
    
    def list_git_tags(self) -> List[str]:
        """List all Git tags matching version pattern."""
        try:
            result = subprocess.run(['git', 'tag', '-l', 'v*'], 
                                  capture_output=True, text=True, cwd=self.project_root)
            if result.returncode == 0:
                tags = result.stdout.strip().split('\n')
                return [tag for tag in tags if tag.startswith('v')]
            return []
        except Exception:
            return []


def main():
    """Main CLI interface for the version manager."""
    parser = argparse.ArgumentParser(
        description="Semantic version management for Mendys Robot Scraper Platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python version_manager.py current
  python version_manager.py bump patch
  python version_manager.py bump minor --pre-release alpha
  python version_manager.py bump major --build-metadata "hotfix"
  python version_manager.py tag --push
  python version_manager.py info --json
  python version_manager.py validate
  python version_manager.py changelog
        """
    )
    
    parser.add_argument(
        'action',
        choices=['current', 'bump', 'tag', 'info', 'validate', 'changelog', 'list-tags'],
        help='Action to perform'
    )
    
    parser.add_argument(
        'bump_type',
        nargs='?',
        choices=['major', 'minor', 'patch', 'pre-release'],
        help='Version component to bump (for bump action)'
    )
    
    parser.add_argument(
        '--pre-release',
        choices=['alpha', 'beta', 'rc'],
        help='Pre-release identifier'
    )
    
    parser.add_argument(
        '--build-metadata',
        help='Build metadata string'
    )
    
    parser.add_argument(
        '--push',
        action='store_true',
        help='Push Git tag to remote (for tag action)'
    )
    
    parser.add_argument(
        '--json',
        action='store_true',
        help='Output in JSON format (for info action)'
    )
    
    parser.add_argument(
        '--version-file',
        default='version.json',
        help='Path to version.json file (default: version.json)'
    )
    
    args = parser.parse_args()
    
    # Initialize version manager
    version_manager = VersionManager(args.version_file)
    
    try:
        if args.action == 'current':
            current = version_manager.get_current_version()
            print(f"Current version: {current}")
        
        elif args.action == 'bump':
            if not args.bump_type:
                print("❌ Error: bump requires bump_type (major, minor, patch, pre-release)")
                sys.exit(1)
            
            new_version = version_manager.bump_version(
                args.bump_type, 
                args.pre_release, 
                args.build_metadata
            )
            version_manager.update_version(new_version)
            
        elif args.action == 'tag':
            success = version_manager.create_git_tag(args.push)
            sys.exit(0 if success else 1)
        
        elif args.action == 'info':
            info = version_manager.get_version_info()
            if args.json:
                print(json.dumps(info, indent=2))
            else:
                current = version_manager.get_current_version()
                print(f"📦 {version_manager.version_data['name']}")
                print(f"🏷️  Version: {current}")
                print(f"🔨 Build: #{info['build']['number']} ({info['build']['date']})")
                print(f"🌿 Git: {info['git']['commit']} ({info['git']['branch']})")
                print(f"🧩 Components:")
                for comp, ver in info['components'].items():
                    print(f"   • {comp}: {ver}")
        
        elif args.action == 'validate':
            success = version_manager.validate_version()
            sys.exit(0 if success else 1)
        
        elif args.action == 'changelog':
            current = version_manager.get_current_version()
            entry = version_manager.generate_changelog_entry(current)
            print(entry)
        
        elif args.action == 'list-tags':
            tags = version_manager.list_git_tags()
            if tags:
                print("📋 Git version tags:")
                for tag in sorted(tags, reverse=True):
                    print(f"  {tag}")
            else:
                print("📝 No version tags found")
    
    except KeyboardInterrupt:
        print("\n🛑 Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main() 