"""
Extracts the IGNORE-PATTERN from the closed issue body and appends it
to .github/crit-ignore-patterns.json.

Env vars (set by the GHA workflow):
  ISSUE_BODY    — raw issue body text
  ISSUE_URL     — URL of the closed issue
  ISSUE_NUMBER  — issue number
  ISSUE_AUTHOR  — login of the user who opened the issue
  TODAY         — YYYY-MM-DD
"""
import os, json, re, sys, pathlib

issue_body   = os.environ.get('ISSUE_BODY', '')
issue_url    = os.environ.get('ISSUE_URL', '')
issue_author = os.environ.get('ISSUE_AUTHOR', '')
today        = os.environ.get('TODAY', '')

# Defense in depth: only honour ignore-candidate issues that the workflow bot
# opened itself. Without this, anyone who can open (or get auto-opened) an issue
# carrying the ignore-candidate label could have a suppression pattern approved
# on close. The candidate issues are created with the default GITHUB_TOKEN, so
# their author is github-actions[bot].
ALLOWED_AUTHORS = {'github-actions[bot]'}
if issue_author not in ALLOWED_AUTHORS:
    print(f'Issue author {issue_author!r} is not an approved bot author '
          f'(expected one of {sorted(ALLOWED_AUTHORS)}); refusing to update ignore list.')
    sys.exit(0)

# Extract the pattern from the trailing HTML comment: <!-- IGNORE-PATTERN: ... -->
# The marker MUST be the last non-whitespace content in the body (anchored to \Z)
# and may not span newlines. The rendered log excerpt is attacker-influenceable and
# appears earlier in the body; anchoring + dropping re.DOTALL stops an injected
# marker there from being matched instead of the real bot-written trailer.
m = re.search(r'<!--\s*IGNORE-PATTERN:\s*([^\n]+?)\s*-->\s*\Z', issue_body)
if not m:
    print('No trailing IGNORE-PATTERN comment found in issue body. Nothing to do.')
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
