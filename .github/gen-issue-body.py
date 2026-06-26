"""Writes the crit-autofix issue body to stdout. All values from env vars."""
import os, json, pathlib

r = json.loads(pathlib.Path('/tmp/claude_result.json').read_text())
body         = r.get('body', '')
fired_at     = os.environ.get('FIRED_AT', '')
log_ctx      = os.environ.get('LOG_CTX', '')
workflow_url = os.environ.get('WORKFLOW_URL', '')
run_url      = os.environ.get('RUN_URL', '')
owner_repo   = os.environ.get('OWNER_REPO', '')

routed = f" · routed to `{owner_repo}`" if owner_repo else ""

print(f"""{body}

**Handling run:** {run_url}

---
**Log excerpt:**
```
{log_ctx}
```

_Auto-opened by [grafana-crit-fix]({workflow_url}) · [run]({run_url}) at {fired_at}{routed}_""")
