# ca-ssh-cloudinit — elastic SSH via cloud-init (no registered Contabo SSH secret)

## Context / decision

A gated prod-cutover dry-run for the Contabo cluster-autoscaler discovered the
Contabo account has **zero registered SSH-key secrets** (only a
`type=password` secret exists). The provider's `client.go` was unconditionally
sending `sshKeys: [SSH_KEY_ID]` on every instance create, referencing a
registered SSH secret that doesn't exist — so real creates would fail with an
invalid/unregistered secret id.

DECISION: elastic nodes authorize a public key via **cloud-init** instead
(mirroring how the existing baseline nodes get their SSH key —
`modules/contabo-k3s-node/cloud-init.tftpl`'s `users: … ssh_authorized_keys:`
block) — NOT via Contabo's `sshKeys` secret-reference mechanism.

## Changes, file by file

### 1. `cluster-autoscaler/contabo-externalgrpc/internal/contabo/client.go`

- `CreateReq.SSHKeyID` is now documented as optional: `<= 0` means "no
  registered SSH-key secret referenced."
- In `Create()`'s `doCreate` closure, the `sshKeys` field is now built from a
  `var sshKeys []int64` that stays `nil` unless `req.SSHKeyID > 0`:
  ```go
  var sshKeys []int64
  if req.SSHKeyID > 0 {
      sshKeys = []int64{req.SSHKeyID}
  }
  createBody := struct {
      ...
      SSHKeys []int64 `json:"sshKeys,omitempty"`
      ...
  }{ ..., SSHKeys: sshKeys, ... }
  ```
  A nil slice + `omitempty` means `encoding/json` omits the `"sshKeys"` key
  from the marshaled body entirely when `SSHKeyID <= 0` — not `[]` and not
  `[0]`. When `SSHKeyID > 0`, the field is present with exactly that id, same
  as before.
- Added two tests in `client_test.go`:
  - `TestCreate_SSHKeysOmittedWhenZero` — asserts the POST body (captured via
    a real `httptest.Server` + `json.Unmarshal` into `map[string]interface{}`)
    has NO `"sshKeys"` key when `CreateReq.SSHKeyID` is unset.
  - `TestCreate_SSHKeysPresentWhenPositive` — asserts `sshKeys: [777]` is
    present when `SSHKeyID: 777` is set.
  - Existing `TestCreateAndDelete` untouched (it never asserted on `sshKeys`
    shape, so no update was needed there).

### 2. `cluster-autoscaler/contabo-externalgrpc/cmd/server/main.go` (+ `main_test.go`)

- `SSH_KEY_ID` env var changed from required to **optional**, defaulting to
  `0`: `loadConfig` no longer includes it in the required-var fail-closed
  check; instead it's parsed only if non-empty, else `sshKeyID` stays `0`.
  Non-numeric values still error (unchanged behavior).
- Doc comment block updated to explain the default and why (mirrors the
  decision above).
- `main_test.go`: removed `SSH_KEY_ID` from
  `TestLoadConfig_MissingRequiredVar`'s required-keys table (it's no longer
  required); added `TestLoadConfig_SSHKeyIDDefaultsToZeroWhenUnset` (key
  absent) and `TestLoadConfig_SSHKeyIDEmptyStringTreatedAsUnset` (key present
  but empty) — both assert `provCfg.SSHKeyID == 0` and no error.
  `TestLoadConfig_NonNumericSSHKeyID` kept as-is (still errors).

### 3. `cluster-autoscaler/contabo-externalgrpc/internal/provider/server.go`

- Updated the `Config.SSHKeyID` doc comment to describe the same optional/0
  semantics and point at `client.go`'s omission behavior.

### 4. `cluster-autoscaler/contabo-externalgrpc/deploy/elastic-userdata.template`

- Added a top-level cloud-config `users:` block (before `write_files:`),
  mirroring `modules/contabo-k3s-node/cloud-init.tftpl`:
  ```yaml
  users:
    - name: root
      ssh_authorized_keys:
        - __SSH_PUBLIC_KEY__
  ```
- `__SSH_PUBLIC_KEY__` is a literal placeholder token substituted with the
  real `NODE_SSH_PUBLIC_KEY` secret by `ca-cutover.yml` at base64-encode time
  (never committed as a real key).
- The existing Go-template placeholders (`{{.NodeName}}`, `{{.K3SServerURL}}`,
  `{{.K3SNodeToken}}`) and the `--kubelet-arg provider-id=contabo://{{.NodeName}}`
  scale-down-correlation line are untouched.
- Verified the resulting file is valid cloud-config YAML (see Verification).

### 5. `.github/workflows/ca-cutover.yml`

**a. GATE B — non-fatal SSH-key resolution.** The image-exists check is
unchanged (still fail-closed on non-200). The SSH-key-secret lookup against
`GET /v1/secrets` is now:
- still fail-closed if the **LIST call itself** errors (HTTP != 200) — that's
  a genuine API problem, distinct from "no match."
- otherwise, if no `type=ssh` secret matches (including zero `ssh` secrets
  registered at all), it now emits `::warning::` instead of `::error::`, sets
  `SSH_KEY_ID=""`, writes the same non-secret diagnostic inventory to the job
  summary (unchanged), and **proceeds** rather than `exit 1`.
- `steps.gateb.outputs.ssh_key_id` is always set (possibly empty).

**b. `userDataTemplateB64` build step** (renamed step: "Fill
clusterAutoscaler.provider values…"): before base64-encoding
`deploy/elastic-userdata.template`, a Python3 heredoc (`python3 - ... <<'PYEOF'`)
does a literal string replace of `__SSH_PUBLIC_KEY__` → the real
`NODE_SSH_PUBLIC_KEY` secret value (read from `os.environ`, never argv) into a
`0600` temp file created under `umask 077`. sed was deliberately avoided
because SSH public-key base64 bodies contain `/` and `+`, which collide with
common sed delimiters. The base64 round-trip sanity check now diffs against
the **rendered** (post-substitution) file, not the original template, since
that's what's actually written into `userDataTemplateB64`. The temp file is
removed via `trap ... EXIT` and an explicit `rm -f` + `trap -` after use.

**c. Sealed-secret step:** `SSH_KEY_ID` is now optional. The `seal-secret.sh`
invocation was refactored from a fixed argument list to a `SEAL_PAIRS` bash
array; `"SSH_KEY_ID=@${TMPDIR}/SSH_KEY_ID"` is appended to that array (and the
temp file written) only `if [ -n "$SSH_KEY_ID" ]`. When GATE B didn't resolve
a key, the sealed Secret simply won't contain an `SSH_KEY_ID` key at all —
confirmed `provider-deployment.yaml` pulls the whole Secret via
`envFrom: secretRef`, so a missing key just means the env var is unset in the
container, which `main.go`'s `loadConfig` now tolerates (defaults to 0).

**Additional hardening beyond the 3 requested edits:**
- Updated the header comment block, the commit message, and the PR body text
  to describe GATE B's non-fatal SSH resolution instead of claiming an
  unconditional "PASS."
- The "Flip enabled flags" step's `GITHUB_STEP_SUMMARY` diff of
  `values-contabo.yaml` now `grep -v`s the `userDataTemplateB64` line before
  printing — that field now embeds the (double-base64) `NODE_SSH_PUBLIC_KEY`
  value, and even though it's a public key, we don't print it to any log per
  the task's secret-safety requirement.

## Go build/test output (via Docker; D: drive wasn't bind-mountable in this
environment — see note below — so the module was copied to the scratch dir
under C:\ and built/tested from there)

```
$ go build ./...
(no output — success)

$ go test ./... -race -count=1
ok  	.../cmd/server	1.030s
ok  	.../internal/contabo	1.059s
?   	.../internal/protos	[no test files]
ok  	.../internal/provider	1.041s

$ go test ./internal/contabo/... ./cmd/server/... -run SSH -v -count=1
=== RUN   TestCreate_SSHKeysOmittedWhenZero        --- PASS
=== RUN   TestCreate_SSHKeysPresentWhenPositive     --- PASS
=== RUN   TestLoadConfig_NonNumericSSHKeyID         --- PASS
=== RUN   TestLoadConfig_SSHKeyIDDefaultsToZeroWhenUnset      --- PASS
=== RUN   TestLoadConfig_SSHKeyIDEmptyStringTreatedAsUnset    --- PASS

$ go vet ./...   (clean)
```

Note: `gofmt -l .` flags every file in the module, including files untouched
by this change (e.g. `memclient.go`) — `gofmt -d` on an untouched file shows a
full-file rewrite, which is the signature of CRLF line endings from the
Windows checkout, not a real formatting defect. Pre-existing, out of scope.

## YAML / shell verification

- `python3 -c "import yaml; yaml.safe_load(open('ca-cutover.yml'))"` → OK,
  15 steps parsed under `jobs.cutover.steps`.
- Extracted every step's `run:` script and ran `bash -n` on each — all 13
  scripts with a `run:` block, including the Python-heredoc-embedded "Fill
  clusterAutoscaler.provider values" step, pass syntax checking.
- `elastic-userdata.template`: stripped the `#cloud-config` header and
  `yaml.safe_load`'d the body — parses cleanly; `users` key confirmed present
  as `[{'name': 'root', 'ssh_authorized_keys': ['__SSH_PUBLIC_KEY__']}]`.

## Secret-safety grep-audit

`grep -n "NODE_SSH_PUBLIC_KEY" ca-cutover.yml` — every occurrence is either:
an env-var declaration (`NODE_SSH_PUBLIC_KEY: ${{ secrets.NODE_SSH_PUBLIC_KEY }}`),
a presence check (`[ -n "$NODE_SSH_PUBLIC_KEY" ]`), a variable-name mention
inside a human-readable warning/summary string, or consumption into
`WANT_BLOB`/`os.environ["NODE_SSH_PUBLIC_KEY"]` for matching/substitution.
No occurrence echoes, prints, or writes the raw value to stdout/log/summary.
The one place a derived artifact *would* contain the key
(`userDataTemplateB64` in the `values-contabo.yaml` diff printed to
`GITHUB_STEP_SUMMARY`) is now explicitly filtered out via `grep -v`.

## Docker/D:-drive note (environment quirk, not a code issue)

The exact Docker invocation given in the task
(`-v "D:/source/FuzeInfra/.claude/worktrees/musing-roentgen-52a749:/src"`)
failed in this sandbox with `mkdir /run/desktop/mnt/host/d: file exists` —
Docker Desktop's WSL2 file-sharing for the `D:` drive appears stale/broken in
this environment (a `C:`-drive bind mount worked fine, confirming Docker
itself is healthy). Worked around by copying the
`cluster-autoscaler/contabo-externalgrpc` Go module (self-contained, has its
own `go.mod`/`go.sum`) into the session scratchpad under `C:\Users\...\Temp\...`
and running the same `golang:1.25` image against that copy instead. No source
files were modified by this workaround — it's purely how the verification
commands were executed.

## Commit

Branch `claude/ca-ssh-cloudinit`, created off `origin/main` (fetched first).
Committed but NOT pushed, per instructions.
