"""Reads grafana-crit-prompt.txt and substitutes env-var placeholders."""
import os, pathlib

t = pathlib.Path('.github/grafana-crit-prompt.txt').read_text()
for k, v in {
    '{{ALERT_SUMMARY}}': os.environ.get('ALERT_SUMMARY', ''),
    '{{ALERT_DESC}}':    os.environ.get('ALERT_DESC', ''),
    '{{ALERT_LABELS}}':  os.environ.get('ALERT_LABELS', ''),
    '{{FIRED_AT}}':      os.environ.get('FIRED_AT', ''),
    '{{LOG_CTX}}':       os.environ.get('LOG_CTX', ''),
}.items():
    t = t.replace(k, v)
print(t, end='')
