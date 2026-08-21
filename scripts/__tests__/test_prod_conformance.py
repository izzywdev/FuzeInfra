"""Tests for prod_conformance.

ASSERTS THE PROBE REPORTS FAILURE, not merely that it renders a table. The defect this
replaces is a measurement that goes green when it could not measure: "no apps registered"
and "could not ask the portal" rendered identically, so a missing credential looked like
a real answer. Every test below pins one of the distinctions that stops that recurring.
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import prod_conformance as pc  # noqa: E402


def svc(name, port=80):
    return {"metadata": {"name": name}, "spec": {"ports": [{"port": port, "protocol": "TCP"}]}}


class TestClassify(unittest.TestCase):
    def test_mcp_beats_frontend_on_a_name_containing_both(self):
        # `fuzeservice-fuzeservice-mcp` must not be read as a frontend: an MCP gateway
        # probed as a frontend would be asked for remoteEntry.js and reported as a
        # broken remote rather than a working gateway.
        self.assertEqual(pc.classify(svc("fuzeservice-fuzeservice-mcp")), "mcp")

    def test_a2a(self):
        self.assertEqual(pc.classify(svc("a2a-shared")), "a2a")

    def test_datastores_are_not_probed_as_backends(self):
        for n in ("postgres", "redis", "rabbitmq", "fuzeinfra-mongodb", "fuzeinfra-kafka"):
            self.assertEqual(pc.classify(svc(n)), "datastore", n)

    def test_plain_api_is_a_backend(self):
        self.assertEqual(pc.classify(svc("fuzekeys-backend")), "backend")

    def test_udp_only_service_yields_no_port(self):
        s = {"metadata": {"name": "x"}, "spec": {"ports": [{"port": 53, "protocol": "UDP"}]}}
        self.assertIsNone(pc.http_port(s))


class TestRemoteIsNotJustA200(unittest.TestCase):
    """An SPA catch-all returns index.html for /assets/remoteEntry.js with status 200.
    Treating that as a loadable remote is exactly how a remote the host cannot mount
    still looks healthy."""

    def test_html_at_the_remote_path_is_not_a_federation_container(self):
        body = "<!doctype html><html><body><div id=root></div></body></html>"
        self.assertFalse(any(m in body for m in pc.MF_MARKERS))

    def test_a_real_container_is_recognised(self):
        body = 'var x={get:function(){},init:function(){}};__federation_shared__'
        self.assertTrue(any(m in body for m in pc.MF_MARKERS))


class TestPortalUnverifiedIsNotZero(unittest.TestCase):
    def test_missing_token_returns_a_reason_not_an_empty_answer(self):
        apps, reason = pc.portal("http://x", "")
        self.assertEqual(apps, [])
        self.assertIn("UNVERIFIED", reason)
        self.assertIn("not zero", reason)

    def test_a_failed_query_is_also_a_reason_not_an_empty_answer(self):
        with mock.patch.object(pc.urllib.request, "urlopen", side_effect=OSError("boom")):
            apps, reason = pc.portal("http://x", "tok")
        self.assertEqual(apps, [])
        self.assertTrue(reason.startswith("portal query failed"))


class TestStrictFires(unittest.TestCase):
    """The negative control. A probe only ever seen passing is not evidence."""

    def _run(self, rows, strict):
        with mock.patch.object(pc, "namespaces", return_value=[r["namespace"] for r in rows]), \
             mock.patch.object(pc, "measure", side_effect=lambda ns: next(r for r in rows if r["namespace"] == ns)), \
             mock.patch.object(pc, "portal", return_value=([], "")), \
             mock.patch.object(sys, "argv", ["prod-conformance"] + (["--strict"] if strict else [])):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = pc.main()
        return rc, buf.getvalue()

    BROKEN = [{"namespace": "fuzebroken", "backend": "NO", "backendDetail": "", "swagger": "NO",
               "swaggerDetail": "", "remote": "NO", "remoteDetail": "", "mcp": "NO", "a2a": "NO",
               "public": ["broken.example"], "services": [{"service": "b", "kind": "backend",
                                                           "port": 80, "readyPod": False}]}]
    CLEAN = [{"namespace": "fuzeok", "backend": "YES", "backendDetail": "b/health", "swagger": "YES",
              "swaggerDetail": "", "remote": "YES", "remoteDetail": "", "mcp": "YES", "a2a": "NO",
              "public": [], "services": [{"service": "b", "kind": "backend", "port": 80,
                                          "readyPod": True}]}]

    def test_strict_exits_nonzero_on_a_backend_that_answers_nothing(self):
        rc, out = self._run(self.BROKEN, strict=True)
        self.assertEqual(rc, 1)
        self.assertIn("::error", out)

    def test_without_strict_the_same_tree_reports_but_exits_zero(self):
        rc, out = self._run(self.BROKEN, strict=False)
        self.assertEqual(rc, 0)
        self.assertIn("no health path answered", out)

    def test_strict_passes_a_healthy_tree(self):
        rc, _ = self._run(self.CLEAN, strict=True)
        self.assertEqual(rc, 0)

    def test_a_namespace_with_no_backend_service_is_not_a_strict_failure(self):
        rows = [{"namespace": "fuzefrontendonly", "backend": "NO", "backendDetail": "",
                 "swagger": "NO", "swaggerDetail": "", "remote": "YES", "remoteDetail": "",
                 "mcp": "NO", "a2a": "NO", "public": [],
                 "services": [{"service": "ui", "kind": "frontend", "port": 80, "readyPod": True}]}]
        rc, _ = self._run(rows, strict=True)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
