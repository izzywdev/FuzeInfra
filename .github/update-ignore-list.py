"""
Extracts the IGNORE-PATTERN from the closed issue body and appends it
to .github/crit-ignore-patterns.json.

Env vars (set by the GHA workflow):
  ISSUE_BODY    — raw issue body text
  ISSUE_URL     — URL of the closed issue
  ISSUE_NUMBER  — issue number
  TODAY         — YYYY-MM-DD
"""
import os, json, re, sys, pathlib

issue_body   = os.environ.get('ISSUE_BODY', '')
issue_url    = os.environ.get('ISSUE_URL', '')
today        = os.environ.get('TODAY', '')

# Extract pattern from HTML comment: <!-- IGNORE-PATTERN: ... -->
m = re.search(r'<!--\s*IGNORE-PATTERN:\s*(.+?)\s*-->', issue_body, re.DOTALL)
if not m:
    print('No IGNORE-PATTERN comment found in issue body. Nothing to do.')
    sys.exit(0)

pattern = m.group(1).strip()
print(f'Extracted pattern: {pattern!r}')

f = pathlib.Path('.github/crit-ignore-patterns.json')
try:
    patterns = json.loads(f.read_text())
except (FileNotFoundError, json.JSONDecodeError):
    patterns = []

if any(p.get('pattern') == pattern for p in patterns):
    print('Pattern already in ignore list. Nothing to do.')
    sys.exit(0)

patterns.append({
    'pattern': pattern,
    'reason': f'Approved via {issue_url}',
    'added_at': today,
    'issue_url': issue_url,
})

f.write_text(json.dumps(patterns, indent=2) + '\n')
print(f'Added pattern to ignore list. Total: {len(patterns)} pattern(s).')
