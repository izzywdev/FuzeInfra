"""
Reads LOG_CTX env var and .github/crit-ignore-patterns.json.
Filters out log lines matching suppressed patterns.

Outputs to GITHUB_OUTPUT:
  skip=true           — all log lines are suppressed; skip Claude
  skip=false          — at least one unsuppressed line remains
  filtered_logs=...   — only non-suppressed lines (for Claude)
  suppressed_count=N  — how many lines were suppressed
"""
import os, json, re, sys, pathlib

patterns_file = pathlib.Path('.github/crit-ignore-patterns.json')
try:
    patterns = json.loads(patterns_file.read_text())
except (FileNotFoundError, json.JSONDecodeError):
    patterns = []

log_ctx = os.environ.get('LOG_CTX', '').strip()
github_output = os.environ.get('GITHUB_OUTPUT', '/dev/null')

log_lines = [l for l in log_ctx.splitlines() if l.strip()]

# No logs or fetch failed — let Claude see it
if not log_lines or log_ctx.startswith('(could not retrieve'):
    with open(github_output, 'a') as f:
        f.write('skip=false\n')
        f.write(f'filtered_logs<<FL_EOF\n{log_ctx}\nFL_EOF\n')
        f.write('suppressed_count=0\n')
    print('No logs to filter — proceeding.')
    sys.exit(0)


def is_suppressed(line):
    for p in patterns:
        pat = p.get('pattern', '')
        if not pat:
            continue
        try:
            if re.search(pat, line, re.IGNORECASE):
                return True
        except re.error:
            if pat.lower() in line.lower():
                return True
    return False


remaining = [l for l in log_lines if not is_suppressed(l)]
suppressed_count = len(log_lines) - len(remaining)

print(f'Suppression: {suppressed_count}/{len(log_lines)} lines matched ignore patterns.')

with open(github_output, 'a') as f:
    if remaining:
        filtered = '\n'.join(remaining)
        f.write('skip=false\n')
        f.write(f'filtered_logs<<FL_EOF\n{filtered}\nFL_EOF\n')
    else:
        f.write('skip=true\n')
        f.write('filtered_logs=\n')
    f.write(f'suppressed_count={suppressed_count}\n')
