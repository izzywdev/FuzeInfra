"""Extracts the last {"action":...} JSON object from /tmp/claude_raw.txt."""
import sys, json, re

txt = open('/tmp/claude_raw.txt').read()
matches = re.findall(r'\{[^{}]*"action"[^{}]*\}', txt, re.DOTALL)
if matches:
    try:
        print(json.dumps(json.loads(matches[-1])))
        sys.exit(0)
    except Exception:
        pass

print(json.dumps({
    'action': 'issue',
    'title': 'CRIT alert -- Claude analysis inconclusive',
    'body': f'Claude could not determine a fix. Raw output:\n\n```\n{txt[:3000]}\n```',
}))
