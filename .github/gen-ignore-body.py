"""Writes the ignore-candidate issue body to stdout.
The IGNORE-PATTERN HTML comment is machine-read by update-ignore-list.py."""
import os, json, pathlib

r = json.loads(pathlib.Path('/tmp/claude_result.json').read_text())
pattern      = r.get('pattern', '')
reason       = r.get('reason', '')
fired_at     = os.environ.get('FIRED_AT', '')
log_ctx      = os.environ.get('LOG_CTX', '')
run_url      = os.environ.get('RUN_URL', '')
workflow_url = os.environ.get('WORKFLOW_URL', '')

# The log excerpt is attacker-influenceable (anyone who can write to the logs).
# Neutralise HTML-comment markers so it cannot forge the IGNORE-PATTERN trailer
# that update-ignore-list.py reads. (That reader also anchors the marker to the
# end of the body; this is belt-and-suspenders.)
log_ctx = log_ctx.replace('<!--', '<!- -').replace('-->', '- ->')

print(f"""## 🔕 Claude suggests ignoring this error

**Pattern:** `{pattern}`

**Reason:** {reason}

---

### What this means

Claude analysed the log output and determined this error is **benign or expected** — not worth paging on. To permanently suppress future alerts for this pattern:

**→ Close this issue** (counts as your approval).

If you want to keep receiving alerts for this error, leave this issue open and remove the `ignore-candidate` label. The `crit-autofix` label keeps the dedup gate closed in the meantime.

---

**Log excerpt that triggered this alert:**
```
{log_ctx}
```

_Auto-opened by [grafana-crit-fix]({workflow_url}) at {fired_at} · [GHA run]({run_url})_

<!-- IGNORE-PATTERN: {pattern} -->""")
