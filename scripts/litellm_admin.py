#!/usr/bin/env python3
"""On-demand LiteLLM gateway admin/introspection, run INSIDE the litellm pod.

WHY THIS EXISTS — it is a debugging-latency fix, not a feature.
Every hypothesis about the gateway's virtual keys used to cost a full
commit -> PR -> merge -> deploy-prod -> Argo sync -> PostSync Job -> read Job
logs round trip: ~15 minutes, and the answer was usually "that image doesn't
have curl". Four of those cycles were spent on questions ("does the key have a
models restriction?", "what does /key/list actually return?") that this script
answers in about 60 seconds via `kubectl exec` into the already-running pod.

It is shipped INTO the pod by .github/workflows/litellm-admin.yml
(`kubectl exec deploy/litellm -- python3 -c "$(cat this-file)" <action> [arg]`).
Running there is what makes it cheap and safe:
  * the pod already holds LITELLM_MASTER_KEY in its env, so no credential is
    read out of the cluster, put on a command line, or handed to a runner;
  * it reaches the gateway over localhost, so it needs no ingress, no
    Cloudflare Access service token, and no NetworkPolicy change;
  * stdlib only, so there is nothing to install (the failure mode that broke
    the PostSync hooks twice — see helm/litellm/templates/job-sync-key-models.yaml).

SECRET DISCIPLINE — FuzeInfra job logs are PUBLIC.
Nothing here ever prints a credential. `/key/list` returns the HASHED token and
only its first 8 characters are logged, which is enough to tell two key rows
apart and useless as a credential. `test-model` mints a short-lived key, holds
it in memory only, and deletes it in a finally block; that value is never
printed, never written to disk, and never returned.

Actions:
  list-keys              show every virtual key: alias, model-restriction count,
                         hashed-token prefix
  list-models            show every model the gateway currently serves
  clear-key-models       set models=[] ("all models") on every restricted key
  test-model <name>      prove a model is reachable BY A VIRTUAL KEY: mint a
                         temporary models=[] key, call the model with it, delete
                         it, and report which model actually served the request
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:" + os.environ.get("LITELLM_PORT", "4000")
MASTER = os.environ.get("LITELLM_MASTER_KEY", "")
# Defence in depth: the workflow already passes this as a distinct argv element
# (kubectl exec uses exec form, so no shell ever sees it), but a model name is
# still untrusted input, so constrain it to the shape LiteLLM model names take.
SAFE_NAME = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_/:")


def api(path, payload=None, key=None, timeout=120):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=body,
        method="POST" if payload is not None else "GET",
    )
    req.add_header("Authorization", "Bearer " + (key or MASTER))
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "null")


def http_err(e):
    try:
        detail = e.read().decode()[:600]
    except Exception:
        detail = ""
    return "HTTP " + str(e.code) + " " + detail


def fetch_keys():
    resp = api("/key/list?return_full_object=true&size=100")
    raw = resp.get("keys") or resp.get("data") or [] if isinstance(resp, dict) else (resp or [])
    out = []
    for k in raw:
        if isinstance(k, str):
            out.append({"token": k, "models": None, "key_alias": None})
        elif isinstance(k, dict):
            out.append(k)
    return out


def show_keys(keys):
    print("Virtual keys: " + str(len(keys)))
    for k in keys:
        tok = k.get("token") or k.get("key") or ""
        models = k.get("models")
        alias = k.get("key_alias") or k.get("key_name") or "(no alias)"
        if isinstance(models, list):
            state = "ALL MODELS" if not models else str(len(models)) + " allowed: " + ",".join(models[:12])
        else:
            state = "unknown"
        print("  - alias=" + str(alias) + "  token_prefix=" + tok[:8] + "  " + state)


def cmd_list_keys():
    show_keys(fetch_keys())
    return 0


def cmd_list_models():
    info = api("/model/info")
    names = sorted({m.get("model_name") for m in (info or {}).get("data", []) if m.get("model_name")})
    print("Models served: " + str(len(names)))
    for n in names:
        print("  - " + n)
    return 0


def cmd_clear_key_models():
    keys = fetch_keys()
    show_keys(keys)
    restricted = [
        (k.get("token") or k.get("key") or "", k.get("key_alias") or k.get("key_name") or "(no alias)")
        for k in keys
        if isinstance(k.get("models"), list) and k.get("models")
    ]
    if not restricted:
        print("\nNothing to do: every key already has models=[] (all models).")
        return 0
    print("\nClearing restrictions on " + str(len(restricted)) + " key(s)...")
    failures = 0
    for tok, alias in restricted:
        if not tok:
            continue
        try:
            api("/key/update", {"key": tok, "models": []})
            print("  OK  alias=" + str(alias) + " token_prefix=" + tok[:8] + " -> ALL MODELS")
        except urllib.error.HTTPError as e:
            failures += 1
            print("  ERR alias=" + str(alias) + " " + http_err(e))
        except Exception as e:
            failures += 1
            print("  ERR alias=" + str(alias) + " " + repr(e))
    print("\nVerifying...")
    show_keys(fetch_keys())
    return 1 if failures else 0


def cmd_test_model(name):
    """Prove a model is reachable by a NON-master virtual key.

    Uses a throwaway key rather than a real one so the test is read-only with
    respect to fleet credentials, and so it works without any key value ever
    being handled outside this process.
    """
    if not name or any(c not in SAFE_NAME for c in name):
        print("ERROR: invalid model name")
        return 2
    print("Minting a temporary models=[] key to test '" + name + "'...")
    temp = api("/key/generate", {
        "models": [],
        "duration": "10m",
        "key_alias": "ci-selftest-transient",
        "metadata": {"purpose": "litellm-admin test-model; safe to delete"},
    })
    # NEVER print or persist this value.
    secret = temp.get("key")
    token = temp.get("token") or ""
    if not secret:
        print("ERROR: /key/generate returned no key")
        return 1
    print("  temp key minted (token_prefix=" + token[:8] + ") — value not logged")
    try:
        try:
            resp = api("/chat/completions", {
                "model": name,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 4,
            }, key=secret)
        except urllib.error.HTTPError as e:
            print("\nRESULT: FAIL — " + http_err(e))
            return 1
        except Exception as e:
            print("\nRESULT: FAIL — " + repr(e))
            return 1
        served = resp.get("model", "(unreported)")
        print("\nRESULT: OK — request accepted for '" + name + "'")
        print("  served by: " + str(served))
        if served and name and str(served).split("/")[-1] != name:
            print("  NOTE: a fallback hop served this, not the primary. The key")
            print("        can reach the model, but the primary provider errored.")
        return 0
    finally:
        try:
            api("/key/delete", {"keys": [token or secret]})
            print("  temp key deleted")
        except Exception as e:
            print("  WARNING: could not delete temp key (expires in 10m): " + repr(e))


def main(argv):
    if not MASTER:
        print("ERROR: LITELLM_MASTER_KEY not present in the pod environment")
        return 2
    action = argv[1] if len(argv) > 1 else "list-keys"
    arg = argv[2] if len(argv) > 2 else ""
    print("=== litellm-admin: " + action + " ===")
    try:
        if action == "list-keys":
            return cmd_list_keys()
        if action == "list-models":
            return cmd_list_models()
        if action == "clear-key-models":
            return cmd_clear_key_models()
        if action == "test-model":
            return cmd_test_model(arg)
        print("ERROR: unknown action '" + action + "'")
        return 2
    except urllib.error.HTTPError as e:
        print("ERROR: " + http_err(e))
        return 1
    except Exception as e:
        print("ERROR: " + repr(e))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
