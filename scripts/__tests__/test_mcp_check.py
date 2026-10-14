"""Tests for scripts/mcp_check.py — the deterministic MCP coverage checker.

These prove the checker can FAIL (over-cap, under-floor, drift, bad irreversible guard) and
PASS (in-sync, reconcile-on-write) rather than only asserting the happy path — the same
discipline gate-required-checks enforces on gate scripts.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mcp_check  # noqa: E402


def _spec(paths):
    return {"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "paths": paths}


def _op(op_id, summary="s", **extra):
    d = {"operationId": op_id, "summary": summary}
    d.update(extra)
    return d


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.policy = mcp_check.Policy()

    def _ops(self, spec):
        ops = mcp_check.iter_operations(spec)
        return ops, mcp_check.eligible_operations(ops, self.policy)

    def test_default_coverage_selects_safe_reads(self):
        spec = _spec({
            "/orders": {"get": _op("listOrders"), "post": _op("createOrder")},
            "/orders/{id}": {"get": _op("getOrder"), "delete": _op("deleteOrder")},
        })
        _, eligible = self._ops(spec)
        selected, mode = mcp_check.select_tools(eligible, self.policy)
        self.assertEqual(mode, "default-coverage")
        self.assertEqual(sorted(op.tool_name for op in selected), ["getOrder", "listOrders"])

    def test_explicit_expose_overrides_default(self):
        spec = _spec({
            "/orders": {"get": _op("listOrders"),
                        "post": _op("createOrder", **{"x-mcp-expose": True})},
            "/orders/{id}": {"get": _op("getOrder")},
        })
        _, eligible = self._ops(spec)
        selected, mode = mcp_check.select_tools(eligible, self.policy)
        self.assertEqual(mode, "explicit")
        self.assertEqual([op.tool_name for op in selected], ["createOrder"])

    def test_explicit_false_opts_out_of_default_coverage(self):
        spec = _spec({
            "/orders": {"get": _op("listOrders")},
            "/health": {"get": _op("health", **{"x-mcp-expose": False})},
        })
        _, eligible = self._ops(spec)
        selected, mode = mcp_check.select_tools(eligible, self.policy)
        self.assertEqual([op.tool_name for op in selected], ["listOrders"])

    def test_mutates_from_method(self):
        spec = _spec({"/x": {"get": _op("g"), "post": _op("p"), "delete": _op("d")}})
        ops = mcp_check.iter_operations(spec)
        by = {o.tool_name: o for o in ops}
        self.assertFalse(by["g"].mutates)
        self.assertTrue(by["p"].mutates)
        self.assertTrue(by["d"].mutates)


class ExclusionTests(unittest.TestCase):
    def test_deprecated_excluded_by_default(self):
        spec = _spec({"/a": {"get": _op("a", deprecated=True)}, "/b": {"get": _op("b")}})
        ops = mcp_check.iter_operations(spec)
        eligible = mcp_check.eligible_operations(ops, mcp_check.Policy())
        self.assertEqual([o.tool_name for o in eligible], ["b"])

    def test_path_glob_and_tag_exclusion(self):
        policy = mcp_check.Policy({"exclude": {"pathGlobs": ["/admin/**"], "tags": ["internal"]}})
        spec = _spec({
            "/admin/x": {"get": _op("adminX")},
            "/pub": {"get": _op("pub")},
            "/int": {"get": _op("intOp", tags=["internal"])},
        })
        ops = mcp_check.iter_operations(spec)
        eligible = mcp_check.eligible_operations(ops, policy)
        self.assertEqual([o.tool_name for o in eligible], ["pub"])


class BoundsTests(unittest.TestCase):
    def test_over_cap_violation(self):
        policy = mcp_check.Policy({"maxTools": 2})
        paths = {f"/r{i}": {"get": _op(f"get{i}")} for i in range(5)}
        ops = mcp_check.iter_operations(_spec(paths))
        eligible = mcp_check.eligible_operations(ops, policy)
        selected, _ = mcp_check.select_tools(eligible, policy)
        v = mcp_check.enforce_bounds(selected, len(eligible), policy)
        self.assertTrue(any("over the cap" in m for m in v))

    def test_ranked_trim_respects_priority(self):
        policy = mcp_check.Policy({"maxTools": 2, "mode": "ranked"})
        spec = _spec({
            "/a": {"get": _op("a", **{"x-mcp-priority": 3})},
            "/b": {"get": _op("b", **{"x-mcp-priority": 1})},
            "/c": {"get": _op("c", **{"x-mcp-priority": 2})},
        })
        ops = mcp_check.iter_operations(spec)
        eligible = mcp_check.eligible_operations(ops, policy)
        selected, _ = mcp_check.select_tools(eligible, policy)
        trimmed = mcp_check.apply_ranked_trim(selected, policy)
        self.assertEqual(sorted(op.tool_name for op in trimmed), ["b", "c"])

    def test_under_floor_violation(self):
        # 4 eligible ops, all writes, none annotated -> default-coverage selects 0 safe reads.
        policy = mcp_check.Policy()  # minTools=3
        paths = {f"/r{i}": {"post": _op(f"post{i}")} for i in range(4)}
        ops = mcp_check.iter_operations(_spec(paths))
        eligible = mcp_check.eligible_operations(ops, policy)
        selected, _ = mcp_check.select_tools(eligible, policy)
        v = mcp_check.enforce_bounds(selected, len(eligible), policy)
        self.assertTrue(any("under the floor" in m for m in v))

    def test_floor_scales_down_for_tiny_api(self):
        # 1 eligible safe-read -> effective floor = min(1, 3) = 1, so no violation.
        policy = mcp_check.Policy()
        ops = mcp_check.iter_operations(_spec({"/only": {"get": _op("only")}}))
        eligible = mcp_check.eligible_operations(ops, policy)
        selected, _ = mcp_check.select_tools(eligible, policy)
        self.assertEqual(mcp_check.enforce_bounds(selected, len(eligible), policy), [])


class _Tmp:
    """A tiny repo on disk: manifest + spec (+ optional committed tools.json).

    The spec is written as JSON (valid YAML too, and mcp_check's loader parses either) so these
    tests have no pyyaml dependency and pass in any consuming repo's `unittest discover`."""
    def __init__(self, tmp, mcp_block, spec_paths, tools=None, spec_rel="openapi.json"):
        self.root = tmp
        self.manifest = {"mcp": mcp_block} if mcp_block is not None else {}
        with open(os.path.join(tmp, spec_rel), "w", encoding="utf-8") as f:
            json.dump(_spec(spec_paths), f)
        if tools is not None:
            os.makedirs(os.path.join(tmp, "mcp"), exist_ok=True)
            with open(os.path.join(tmp, "mcp", "tools.json"), "w", encoding="utf-8") as f:
                json.dump(tools, f)


class CheckTests(unittest.TestCase):
    def _gateway_block(self, spec_rel="openapi.json", **extra):
        s = {"name": "gw", "transport": "sse", "image": "ghcr.io/izzywdev/fuze-mcp-gateway",
             "spec": spec_rel, "manifest": "mcp/tools.json"}
        s.update(extra)
        return {"enabled": False, "servers": [s]}

    def test_no_mcp_block_is_no_surface(self):
        with tempfile.TemporaryDirectory() as t:
            r = mcp_check.check({}, t, mcp_check.Policy())
            self.assertEqual(r.status, "no-surface")
            self.assertFalse(r.needs_agent)

    def test_missing_spec_is_violation(self):
        with tempfile.TemporaryDirectory() as t:
            manifest = {"mcp": self._gateway_block(spec_rel="nope.yaml")}
            r = mcp_check.check(manifest, t, mcp_check.Policy())
            self.assertEqual(r.status, "violation")

    def test_drift_then_write_then_in_sync(self):
        with tempfile.TemporaryDirectory() as t:
            fx = _Tmp(t, self._gateway_block(), {"/o": {"get": _op("listO")},
                                                 "/o/{id}": {"get": _op("getO")}})
            # First check: no committed tools.json -> drift.
            r = mcp_check.check(fx.manifest, t, mcp_check.Policy(), write=False)
            self.assertEqual(r.status, "drift")
            # Write it.
            r = mcp_check.check(fx.manifest, t, mcp_check.Policy(), write=True)
            self.assertEqual(r.status, "reconciled")
            self.assertTrue(os.path.isfile(os.path.join(t, "mcp", "tools.json")))
            # Now in-sync.
            r = mcp_check.check(fx.manifest, t, mcp_check.Policy(), write=False)
            self.assertEqual(r.status, "in-sync")

    def test_irreversible_tool_must_resolve_and_mutate(self):
        with tempfile.TemporaryDirectory() as t:
            block = self._gateway_block()
            block["irreversibleTools"] = ["ghost"]
            fx = _Tmp(t, block, {"/o": {"get": _op("listO")}, "/o/{id}": {"get": _op("getO")},
                                 "/x": {"get": _op("getX")}})
            r = mcp_check.check(fx.manifest, t, mcp_check.Policy(), write=True)
            self.assertEqual(r.status, "violation")
            self.assertTrue(any("ghost" in v for v in r.violations))

    def test_irreversible_on_safe_read_is_contradiction(self):
        with tempfile.TemporaryDirectory() as t:
            block = self._gateway_block()
            block["irreversibleTools"] = ["listO"]  # a GET
            fx = _Tmp(t, block, {"/o": {"get": _op("listO")}, "/o/{id}": {"get": _op("getO")},
                                 "/x": {"get": _op("getX")}})
            r = mcp_check.check(fx.manifest, t, mcp_check.Policy(), write=True)
            self.assertEqual(r.status, "violation")

    def test_undocumented_tool_flags_needs_agent(self):
        with tempfile.TemporaryDirectory() as t:
            fx = _Tmp(t, self._gateway_block(),
                      {"/a": {"get": _op("a", summary=None)},
                       "/b": {"get": _op("b", summary="")},
                       "/c": {"get": _op("c")}})
            r = mcp_check.check(fx.manifest, t, mcp_check.Policy(), write=True)
            self.assertTrue(r.needs_agent)
            self.assertTrue(any("no summary/description" in x for x in r.agent_reasons))


if __name__ == "__main__":
    unittest.main()
