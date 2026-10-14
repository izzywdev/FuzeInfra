import base64
import io
import json
import unittest
import urllib.error

from scripts.verify_ghcr_pull import NoRedirect, ProbeError, basic_auth, parse_image, verify


IMAGE = "ghcr.io/izzywdev/fuzeplan-frontend-mfe:914a500db436"
DIGEST = "sha256:" + "a" * 64
CONFIG = {"auths": {"ghcr.io": {"auth": base64.b64encode(b"reader:private-token").decode()}}}


class Response(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(json.dumps(body).encode())
        self.headers = headers or {}


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class PullVerification(unittest.TestCase):
    def test_exact_manifest_required_after_auth(self):
        opener = Opener(Response({"token": "private-bearer"}), Response({}, {"Docker-Content-Digest": DIGEST}))
        self.assertEqual(verify(CONFIG, IMAGE, opener), DIGEST)
        self.assertIn("repository%3Aizzywdev%2Ffuzeplan-frontend-mfe%3Apull", opener.requests[0].full_url)
        self.assertEqual(opener.requests[1].full_url, "https://ghcr.io/v2/izzywdev/fuzeplan-frontend-mfe/manifests/914a500db436")
        self.assertEqual(opener.requests[1].get_header("Authorization"), "Bearer private-bearer")

    def test_auth_403_does_not_disclose_response_or_credential(self):
        opener = Opener(urllib.error.HTTPError("https://ghcr.io/token", 403, "private-token", {}, io.BytesIO(b"private-token")))
        with self.assertRaisesRegex(ProbeError, "authorization rejected: HTTP 403") as error:
            verify(CONFIG, IMAGE, opener)
        self.assertNotIn("private-token", str(error.exception))
        self.assertEqual(len(opener.requests), 1)

    def test_token_without_pull_access_is_not_success(self):
        opener = Opener(Response({"token": "limited-token"}), urllib.error.HTTPError("https://ghcr.io/manifest", 403, "secret-body", {}, None))
        with self.assertRaisesRegex(ProbeError, "manifest rejected: HTTP 403"):
            verify(CONFIG, IMAGE, opener)

    def test_missing_tag_is_distinct_from_auth_failure(self):
        opener = Opener(Response({"token": "token"}), urllib.error.HTTPError("https://ghcr.io/manifest", 404, "missing", {}, None))
        with self.assertRaisesRegex(ProbeError, "manifest rejected: HTTP 404"):
            verify(CONFIG, IMAGE, opener)

    def test_invalid_source_never_sends_request(self):
        for config in ({"auths": {}}, {"auths": {"ghcr.io": {"auth": "bad-value"}}}, {"auths": {"ghcr.io": {"username": "reader", "password": ""}}}):
            with self.subTest(config=config):
                with self.assertRaises(ProbeError):
                    basic_auth(config)

    def test_destinations_are_bounded(self):
        for image in ("https://evil.example/image", "ghcr.io/other/image:tag", IMAGE + "\n", "ghcr.io/izzywdev/image:tag?redirect=evil"):
            with self.subTest(image=image):
                with self.assertRaises(ProbeError):
                    parse_image(image)

    def test_redirects_never_forward_auth(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://evil.example"))

    def test_bad_token_response_and_manifest_fail_closed(self):
        with self.assertRaises(ProbeError):
            verify(CONFIG, IMAGE, Opener(Response({})))
        with self.assertRaises(ProbeError):
            verify(CONFIG, IMAGE, Opener(Response({"token": "token"}), Response({}, {"Docker-Content-Digest": "secret-body"})))


if __name__ == "__main__":
    unittest.main()
