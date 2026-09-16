#!/usr/bin/env bash
# Populate /opt/hostedtoolcache with the actions/python-versions relocatable
# builds for the requested minor series, so actions/setup-python@v5 resolves
# offline. Invoked from runners/arc/Dockerfile. Args: minor series (e.g. 3.10 3.11)
#
# Each warmed interpreter is ALSO seeded with a baseline set of pip packages
# ($TOOLCACHE_PIP_PACKAGES) so the harden-gate self-test suite (scripts/__tests__)
# has them at JOB TIME WITHOUT reaching PyPI. The in-cluster ARC runner pods have
# no PyPI egress, so a job-time `pip install jsonschema` cannot land — gate_manifest
# then silently drops to STRUCTURAL FALLBACK and test_gate_manifest's full-validation
# cases red (observed on gate-identifier / gate-vacuous-check, which discover and run
# the whole scripts/__tests__ suite). Front-loading them here is safe because THIS
# script runs at image-build time, on a hosted runner with full egress. setup-python
# resolves e.g. `python-version: '3.12'` to the manifest-latest 3.12.x — the exact
# version this script warms from the same manifest — so the packages land in the very
# interpreter the gate uses, and the job-time `pip install` is a satisfied no-op.
set -eux

# Baseline packages every warmed interpreter carries. pyyaml + PyNaCl back the
# manifest/secret self-tests; jsonschema is what gate_manifest needs for full
# (non-fallback) validation. Override to EXTEND (never shrink below these).
TOOLCACHE_PIP_PACKAGES="${TOOLCACHE_PIP_PACKAGES:-jsonschema pyyaml PyNaCl}"

manifest="$(curl -fsSL https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json)"

for series in "$@"; do
  ver="$(printf '%s' "$manifest" | jq -r --arg s "$series" '
    [ .[] | select(.stable == true) | select(.version | startswith($s + ".")) ]
    | sort_by(.version | split(".") | map(tonumber)) | reverse | .[0].version')"
  url="$(printf '%s' "$manifest" | jq -r --arg s "$series" '
    [ .[] | select(.stable == true) | select(.version | startswith($s + ".")) ]
    | sort_by(.version | split(".") | map(tonumber)) | reverse
    | .[0].files[]
    | select(.platform == "linux" and .arch == "x64"
        and (.platform_version == "24.04" or .platform_version == null))
    | .download_url' | head -1)"

  if [ -z "$ver" ] || [ -z "$url" ] || [ "$url" = "null" ]; then
    echo "WARNING: no python-versions build found for $series on ubuntu-24.04 x64 — skipping" >&2
    continue
  fi

  echo "Python $series -> $ver ($url)"
  dir="/opt/hostedtoolcache/Python/${ver}/x64"
  mkdir -p "$dir"
  curl -fsSL "$url" | tar -xz -C "$dir"
  ( cd "$dir" && bash ./setup.sh )
  touch "/opt/hostedtoolcache/Python/${ver}/x64.complete"
  "$dir/bin/python3" --version

  # Seed the baseline packages into THIS interpreter so job-time gates need no
  # PyPI. Runs at build time (full egress); the gates' `pip install` then finds
  # them already satisfied and does no network I/O. The import check fails the
  # BUILD loudly if any package did not land, so a broken image never ships.
  # shellcheck disable=SC2086  # deliberate word-splitting of the package list
  "$dir/bin/python3" -m pip install --disable-pip-version-check --no-warn-script-location -q $TOOLCACHE_PIP_PACKAGES
  "$dir/bin/python3" -c 'import jsonschema, yaml, nacl; print("baseline pip deps OK: jsonschema", jsonschema.__version__)'
done

chown -R 1001:1001 /opt/hostedtoolcache
