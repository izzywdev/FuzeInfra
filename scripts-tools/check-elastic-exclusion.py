#!/usr/bin/env python3
"""
Static HCL analysis for elastic-exclusion invariant.

Ensures that terraform/contabo/** and modules/contabo-k3s-node/** never:
1. Enumerate Contabo instances broadly (data "contabo_instance" or similar)
2. Self-tag managed instances with fuzeinfra-elastic

This check requires NO credentials, NO terraform init, NO terraform plan.
It runs on every PR and never fails open.

Exit codes:
  0 = clean (invariant holds)
  1 = dangerous pattern detected (invariant violated)
"""

import re
import sys
from pathlib import Path


def strip_comments(hcl_content: str) -> str:
    """
    Remove HCL comments (# to end of line) while preserving string literals.
    This prevents a comment mentioning 'fuzeinfra-elastic' from triggering a false positive.
    """
    lines = []
    in_heredoc = False
    heredoc_delimiter = None

    for line in hcl_content.split('\n'):
        # Simple heredoc detection (<<- or <<)
        if re.search(r'<<-?\s*\w+', line) and not in_heredoc:
            in_heredoc = True
            heredoc_match = re.search(r'<<-?\s*(\w+)', line)
            if heredoc_match:
                heredoc_delimiter = heredoc_match.group(1)
        elif in_heredoc and heredoc_delimiter and line.strip() == heredoc_delimiter:
            in_heredoc = False
            heredoc_delimiter = None

        # If in heredoc, keep the line as-is
        if in_heredoc:
            lines.append(line)
            continue

        # Remove comments from the line (# outside of strings)
        # Simple approach: find the comment after quoted strings
        # Match: # followed by anything until end of line
        # but only if not inside quotes
        comment_pos = line.find('#')
        if comment_pos != -1:
            # Check if the # is inside a quoted string
            before_comment = line[:comment_pos]
            # Count quotes before the #
            if before_comment.count('"') % 2 == 0 and before_comment.count("'") % 2 == 0:
                # Not in a string, safe to strip
                line = before_comment.rstrip()

        lines.append(line)

    return '\n'.join(lines)


def check_no_broad_instance_enumeration(hcl_content: str, file_path: str) -> list:
    """Check for data "contabo_instance" or similar broad enumeration patterns."""
    violations = []

    # Remove comments to avoid false positives
    stripped = strip_comments(hcl_content)

    # Pattern 1: data "contabo_instance" or data "contabo_instances"
    # Allow variations with whitespace
    pattern = r'data\s+"contabo_instances?"\s+'
    matches = list(re.finditer(pattern, stripped))

    for match in matches:
        # Find the line number
        line_num = hcl_content[:match.start()].count('\n') + 1
        violations.append({
            'file': file_path,
            'line': line_num,
            'pattern': 'data "contabo_instance" / "contabo_instances"',
            'text': match.group(0)
        })

    return violations


def check_no_elastic_self_tag(hcl_content: str, file_path: str) -> list:
    """Check for resource "contabo_instance" with fuzeinfra-elastic tag."""
    violations = []

    # Remove comments
    stripped = strip_comments(hcl_content)

    # Find all resource "contabo_instance" blocks
    # Look for the pattern: resource "contabo_instance" ... { ... tags = [...fuzeinfra-elastic...] ... }
    # This is complex in regex, so we'll do a simpler approach:
    # Find all places where "fuzeinfra-elastic" appears in tags context

    # Pattern: tags = [ or tags = { followed by anything containing "fuzeinfra-elastic"
    # More specifically: within a resource "contabo_instance" block, find tags containing elastic

    # Simple regex: find tags = [...] or tags = { } assignments that include fuzeinfra-elastic
    pattern = r'tags\s*=\s*[\[\{]([^\]\}]*fuzeinfra-elastic[^\]\}]*[\]\}])'
    matches = list(re.finditer(pattern, stripped, re.IGNORECASE | re.DOTALL))

    for match in matches:
        # Find the line number
        line_num = hcl_content[:match.start()].count('\n') + 1
        violations.append({
            'file': file_path,
            'line': line_num,
            'pattern': 'tags containing "fuzeinfra-elastic"',
            'text': match.group(0)[:100]  # First 100 chars
        })

    return violations


def main():
    base_path = Path(__file__).parent.parent  # FuzeInfra root

    # Paths to check
    tf_paths = [
        base_path / 'terraform' / 'contabo',
        base_path / 'modules' / 'contabo-k3s-node',
    ]

    all_violations = []

    for tf_path in tf_paths:
        if not tf_path.exists():
            continue

        # Find all .tf files
        for tf_file in tf_path.rglob('*.tf'):
            content = tf_file.read_text(encoding='utf-8')

            # Check both invariants
            violations = check_no_broad_instance_enumeration(content, str(tf_file))
            all_violations.extend(violations)

            violations = check_no_elastic_self_tag(content, str(tf_file))
            all_violations.extend(violations)

    if all_violations:
        print("::error::Elastic-exclusion invariant VIOLATED:", file=sys.stderr)
        print("", file=sys.stderr)
        for v in all_violations:
            print(f"  {v['file']}:{v['line']}", file=sys.stderr)
            print(f"    Pattern: {v['pattern']}", file=sys.stderr)
            print(f"    Match: {v['text']}", file=sys.stderr)
            print("", file=sys.stderr)
        return 1

    print("✓ Elastic-exclusion invariant holds:")
    print("  - No broad instance enumeration (data \"contabo_instance*\")")
    print("  - No elastic self-tagging in managed resources")
    return 0


if __name__ == '__main__':
    sys.exit(main())
