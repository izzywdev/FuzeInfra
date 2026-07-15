This directory holds the handoff MCP id-state as ConfigMap source (helm/fuzeinfra/templates/handoff-mcp.yaml renders `handoff-state` from *.json here).

These are NON-secret ids (agent/env/vault/memory), produced by the provision job (agent-templates/providers/provision.py -> sync/.state/*.json). Populate them (download the "managed-agents-state" artifact from the Provision Managed Agents workflow, or run provision locally) and commit them here in the SAME PR that flips handoffMcp.enabled=true and lands deploy/sealed-secrets/handoff-mcp-secret.yaml.
