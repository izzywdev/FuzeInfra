"""Writes the crit-autofix issue body to stdout. All values from env vars."""
import os, json, pathlib

r = json.loads(pathlib.Path('/tmp/claude_result.json').read_text())
body         = r.get('body', '')
fired_at     = os.environ.get('FIRED_AT', '')
log_ctx      = os.environ.get('LOG_CTX', '')
workflow_url = os.environ.get('WORKFLOW_URL', '')

print(f"""{body}

---
**Log excerpt:**
```
{log_ctx}
```

_Auto-opened by [grafana-crit-fix]({workflow_url}) at {fired_at}_""")
