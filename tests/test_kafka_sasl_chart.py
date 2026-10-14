"""Offline guards for the Kafka SASL/SCRAM + ACL chart wiring (docs/KAFKA_SASL_ACLS.md)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
CHART = REPO / "helm" / "fuzeinfra"
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def render(*sets: str, contabo: bool = True) -> list[dict]:
    cmd = ["helm", "template", "t", str(CHART), "-n", "fuzeinfra"]
    if contabo:
        cmd += ["-f", str(CHART / "values-contabo.yaml")]
    for s in sets:
        cmd += ["--set", s]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def kafka_env(docs):
    sts = next(d for d in docs if d["kind"] == "StatefulSet" and d["metadata"]["name"] == "fuzeinfra-kafka")
    return {e["name"]: e.get("value") for e in sts["spec"]["template"]["spec"]["containers"][0]["env"]}


def provision_script(docs) -> str:
    job = next(d for d in docs if d["metadata"]["name"] == "fuzeinfra-kafka-sasl-provision")
    return job["spec"]["template"]["spec"]["containers"][0]["args"][0]


def test_sasl_is_disabled_in_prod_until_the_authorizer_boots():
    # 2026-10-14: enabling the authorizer crash-looped the prod broker (admin was
    # the only super user, so the combined KRaft node denied itself). Do not flip
    # this back without proving it on a single-node KRaft broker and updating
    # docs/KAFKA_SASL_ACLS.md.
    docs = render()
    assert "KAFKA_AUTHORIZER_CLASS_NAME" not in kafka_env(docs)
    assert not any(d["metadata"]["name"] == "fuzeinfra-kafka-sasl-provision" for d in docs)


def test_plaintext_listener_survives_and_admin_is_only_super_user():
    env = kafka_env(render("kafka.sasl.enabled=true"))
    assert "PLAINTEXT://0.0.0.0:9092" in env["KAFKA_LISTENERS"]
    assert "SASL://0.0.0.0:9094" in env["KAFKA_LISTENERS"]
    assert env["KAFKA_LISTENER_SECURITY_PROTOCOL_MAP"].count("PLAINTEXT:PLAINTEXT") == 1
    assert env["KAFKA_INTER_BROKER_LISTENER_NAME"] == "PLAINTEXT"
    assert env["KAFKA_SUPER_USERS"] == "User:admin"
    assert env["KAFKA_ALLOW_EVERYONE_IF_NO_ACL_FOUND"] == "true"


def test_disabled_by_default_renders_nothing_new():
    docs = render(contabo=False)
    assert not any(d["metadata"]["name"] == "fuzeinfra-kafka-sasl-provision" for d in docs)
    assert "KAFKA_AUTHORIZER_CLASS_NAME" not in kafka_env(docs)


def test_no_acl_group_enforced_by_default():
    s = provision_script(render("kafka.sasl.enabled=true"))
    assert "kafka-acls" in s  # helper defined
    assert "acl --add" not in s


def test_enforced_groups_render_the_requested_acls():
    s = provision_script(render(
        "kafka.sasl.enabled=true",
        "kafka.sasl.acls.selectionLists.enforce=true",
        "kafka.sasl.acls.identity.enforce=true",
        "kafka.sasl.acls.topicJob.enforce=true",
        "kafka.sasl.acls.selectionLists.seedRequestedWriters={identity-producer}",
    ))
    assert '--deny-principal "User:selection-list-service" --operation Write --topic selection-lists.seed.requested' in s
    assert '"User:identity-producer" --operation Write --operation Describe --topic selection-lists.seed.requested' in s
    assert '"User:identity-producer" --operation Write --operation Describe --topic identity. --resource-pattern-type prefixed' in s
    assert '--group "selection-list-service-group" --resource-pattern-type prefixed' in s
    assert "notify." not in s and "billing." not in s


def test_no_cluster_resource_acl_ever():
    s = provision_script(render(
        "kafka.sasl.enabled=true",
        "kafka.sasl.acls.selectionLists.enforce=true",
        "kafka.sasl.acls.identity.enforce=true",
        "kafka.sasl.acls.topicJob.enforce=true",
        "kafka.sasl.acls.topicJob.includeNotifyBilling=true",
    ))
    assert "--cluster" not in s


def test_unknown_principal_fails_render():
    with pytest.raises(subprocess.CalledProcessError):
        render("kafka.sasl.enabled=true", "kafka.sasl.acls.identity.readers={ghost}")


def test_sealed_secret_has_a_key_per_principal_and_no_plaintext():
    d = yaml.safe_load((REPO / "deploy" / "sealed-secrets" / "fuzeinfra-kafka-sasl.yaml").read_text())
    assert d["kind"] == "SealedSecret" and d["metadata"]["namespace"] == "fuzeinfra"
    users = yaml.safe_load((CHART / "values.yaml").read_text())["kafka"]["sasl"]["users"]
    assert sorted(d["spec"]["encryptedData"]) == sorted(users)
    assert all(v.startswith("Ag") and len(v) > 200 for v in d["spec"]["encryptedData"].values())
