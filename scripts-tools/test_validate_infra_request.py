#!/usr/bin/env python3
"""Unit tests for validate_infra_request.validate()."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from validate_infra_request import validate  # noqa: E402

WHITELIST = {
    "allowed_product_ids": ["V45", "V46"],
    "allowed_regions": ["EU"],
    "allowed_roles": ["workload"],
    "max_nodes_per_request": 3,
    "allowed_repos": ["izzywdev/FuzeFront"],
}


class ValidateTests(unittest.TestCase):
    def test_valid_request_auto_applies(self):
        payload = {"requests": [{"name": "w1", "product_id": "V45", "region": "EU", "role": "workload"}]}
        ok, reasons = validate(WHITELIST, payload, "izzywdev/FuzeFront")
        self.assertTrue(ok, reasons)

    def test_defaults_region_and_role(self):
        payload = {"requests": [{"name": "w1", "product_id": "V46"}]}
        ok, _ = validate(WHITELIST, payload, "izzywdev/FuzeFront")
        self.assertTrue(ok)

    def test_bad_product_region_role_rejected(self):
        payload = {"requests": [{"name": "w1", "product_id": "V99", "region": "US", "role": "server"}]}
        ok, reasons = validate(WHITELIST, payload, "izzywdev/FuzeFront")
        self.assertFalse(ok)
        self.assertEqual(len(reasons), 3)

    def test_unknown_repo_rejected(self):
        payload = {"requests": [{"name": "w1", "product_id": "V45"}]}
        ok, reasons = validate(WHITELIST, payload, "stranger/Repo")
        self.assertFalse(ok)

    def test_too_many_nodes_rejected(self):
        payload = {"requests": [{"name": f"w{i}", "product_id": "V45"} for i in range(4)]}
        ok, _ = validate(WHITELIST, payload, "izzywdev/FuzeFront")
        self.assertFalse(ok)

    def test_empty_requests_rejected(self):
        ok, _ = validate(WHITELIST, {"requests": []}, "izzywdev/FuzeFront")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
