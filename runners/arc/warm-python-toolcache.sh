#!/usr/bin/env bash
# Populate /opt/hostedtoolcache with the actions/python-versions relocatable
# builds for the requested minor series, so actions/setup-python@v5 resolves
# offline. Invoked from runners/arc/Dockerfile. Args: minor series (e.g. 3.10 3.11)
set -eux

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
done

chown -R 1001:1001 /opt/hostedtoolcache
