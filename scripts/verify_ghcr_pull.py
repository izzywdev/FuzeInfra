"""Verify an existing dockerconfigjson against one private GHCR image.

Only status codes and a manifest digest leave this process. Credentials and
registry response bodies must never reach logs, argv, artifacts or commits.
"""
import argparse
import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request


class ProbeError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def parse_image(image):
    match = re.fullmatch(
        r"ghcr\.io/(izzywdev/[a-z0-9._-]+(?:/[a-z0-9._-]+)*):([A-Za-z0-9_][A-Za-z0-9_.-]{0,127})",
        image,
    )
    if not match:
        raise ProbeError("Image must be ghcr.io/izzywdev/<package>:<tag>")
    return match.groups()


def basic_auth(config):
    try:
        entry = config["auths"]["ghcr.io"]
        if entry.get("auth"):
            raw = base64.b64decode(entry["auth"], validate=True)
        else:
            raw = (entry["username"] + ":" + entry["password"]).encode()
        user, password = raw.split(b":", 1)
        if not user or not password or b"\n" in raw or b"\r" in raw:
            raise ValueError()
        return "Basic " + base64.b64encode(raw).decode("ascii")
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ProbeError("Source has no usable ghcr.io credential") from None


def verify(config, image, opener=None):
    package, tag = parse_image(image)
    auth = basic_auth(config)
    opener = opener or urllib.request.build_opener(NoRedirect())

    def request(url, headers, stage):
        try:
            return opener.open(urllib.request.Request(url, headers=headers), timeout=30)
        except urllib.error.HTTPError as error:
            raise ProbeError(f"GHCR {stage} rejected: HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ProbeError(f"GHCR {stage} could not be reached") from None

    query = urllib.parse.urlencode({"service": "ghcr.io", "scope": f"repository:{package}:pull"})
    with request("https://ghcr.io/token?" + query, {"Authorization": auth}, "authorization") as response:
        try:
            payload = json.load(response)
            token = payload.get("token") or payload.get("access_token")
            if not isinstance(token, str) or not token or "\n" in token or "\r" in token:
                raise ValueError()
        except (ValueError, AttributeError, TypeError):
            raise ProbeError("GHCR authorization returned an invalid token response") from None

    # Token issuance alone does not establish pull access. GHCR can issue a
    # token with no pull grant; the exact manifest must also be retrievable.
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": ", ".join([
            "application/vnd.oci.image.index.v1+json",
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.docker.distribution.manifest.v2+json",
        ]),
    }
    with request(f"https://ghcr.io/v2/{package}/manifests/{tag}", headers, "manifest") as response:
        digest = response.headers.get("Docker-Content-Digest", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ProbeError("GHCR manifest returned no valid digest")
        return digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dockerconfig", required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    try:
        parse_image(args.image)
        with open(args.dockerconfig, encoding="utf-8") as source:
            config = json.load(source)
        digest = verify(config, args.image)
    except ProbeError as error:
        print(f"::error::{error}")
        return 1
    except (OSError, ValueError):
        print("::error::Source dockerconfig could not be read")
        return 1
    print(f"Pull authorization verified: {args.image} digest={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
