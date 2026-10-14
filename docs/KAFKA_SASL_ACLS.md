# Kafka SASL/SCRAM + ACLs

FuzeInfra Kafka (`fuzeinfra-kafka.fuzeinfra.svc.cluster.local`, KRaft, single node)
historically exposed only `PLAINTEXT :9092` with no authentication, so any pod that
reaches the broker could produce to any topic (for example a forged
`identity.org.deleted`, which drives hard purges). This adds authentication and ACLs
without breaking existing clients.

Requested by FuzeFront (selection-list-service); corr `7c1f3a52-9d0e-4b6a-8f21-5e4d0c9a1b37`.

## What exists after this change (`kafka.sasl.enabled`, on in `values-contabo.yaml`)

| Listener | Port | Protocol | Use |
|---|---|---|---|
| PLAINTEXT | 9092 | none | every existing client, inter-broker; **kept during the migration window** |
| CONTROLLER | 9093 | none | KRaft quorum (in-pod) |
| SASL | 9094 | SASL_PLAINTEXT, SCRAM-SHA-512 | migrated clients |

SASL_PLAINTEXT means credentials are SCRAM-challenged (never sent in the clear) but the
payload is not encrypted; in-cluster traffic only, no Ingress. TLS is a follow-up.

The `StandardAuthorizer` is on with `allow.everyone.if.no.acl.found=true`, so enabling
it changes nothing for a resource until an ACL exists on it. `User:admin` is the only
super user. `User:ANONYMOUS` (all PLAINTEXT clients) is not.

Principals (`kafka.sasl.users`): `admin`, `selection-list-service`, `identity-producer`,
`kafka-topics-job`. Passwords live in the SealedSecret `fuzeinfra/fuzeinfra-kafka-sasl`
(one key per principal, random 32-char alphanumeric, generated and sealed offline; the
plaintext was never displayed or committed). The `fuzeinfra-kafka-sasl-provision`
PostSync Job upserts the SCRAM users and applies ACLs on every sync, so rotating a
value is a re-seal plus a sync.

## ACL groups (all `enforce: false` today)

An ACL on a resource pattern makes `allow.everyone.if.no.acl.found` stop applying to
that pattern, so **enforcing a group is what closes it to PLAINTEXT clients**. Each
group is therefore its own flag, flipped at its own announced date.

| Group | Effect when enforced |
|---|---|
| `selectionLists` | prefix `selection-lists.`: WRITE/DESCRIBE/CREATE/ALTER/\*_CONFIGS only `selection-list-service`; READ for `readers`; literal `selection-lists.seed.requested`: owner is DENIED write and may READ, WRITE only `seedRequestedWriters`; group prefix `selection-list-service-group` for the owner |
| `identity` | prefix `identity.`: WRITE only `identity-producer`; READ for `readers`; `anonymousRead: true` keeps unmigrated PLAINTEXT consumers reading during migration |
| `topicJob` | CREATE/DESCRIBE/ALTER/DESCRIBE_CONFIGS/ALTER_CONFIGS for `kafka-topics-job` on `selection-lists.` and `identity.`; add `notify.`/`billing.` with `includeNotifyBilling` only at their own cut-over (an ACL there closes them to every PLAINTEXT client) |

DLQ topics named `<base>.dlq` fall under the same prefix as their base topic, so they
are covered by the owning service's WRITE. A cross-prefix `*.dlq` rule is not
expressible (ACL patterns are literal or prefix only).

No ACL is created on the `Cluster` resource: it would also deny Kafka UI and idempotent
producers still on PLAINTEXT.

## Cut-over runbook (per group)

1. Consumer repo receives its credential (credential-handoff, below) and moves its
   client to `bootstrap fuzeinfra-kafka...:9094`, `SASL_PLAINTEXT`, `SCRAM-SHA-512`.
2. Confirm in Kafka UI / `cluster-query.yml` logs that no ANONYMOUS client still
   writes the prefix.
3. Announce the date; flip that group's `enforce: true` in `values-contabo.yaml`; merge.
4. `identity`: the producer must migrate **first**; consumers can follow because of
   `anonymousRead`.

Rollback for any group: set `enforce: false` and delete the ACLs
(`kafka-acls --remove`) via a new provision revision; for the whole feature revert the
PR.

## Final step, not done here: `allow.everyone.if.no.acl.found=false`

Not flippable yet. Inter-broker and controller traffic still run on PLAINTEXT as
ANONYMOUS and would be denied. Before the flip: move the inter-broker listener to SASL
(admin SCRAM credential in the broker JAAS) and verify the controller path, then flip
`kafka.sasl.allowEveryoneIfNoAclFound` and announce the date. Do not flip it early.

## Credential delivery

FuzeFront consumes the three service credentials via credential-handoff
(`docs/CREDENTIAL_HANDOFF.md`): source `fuzeinfra/fuzeinfra-kafka-sasl`, keys
`selection-list-service`, `identity-producer`, `kafka-topics-job`. A
`governance/credential-handoff.json` entry per credential is added once FuzeFront names
the target Secret and manifest path (that entry is the grant).

## Why it is off in prod (incident 2026-10-14)

`kafka.sasl.enabled` was turned on in `values-contabo.yaml` by #1394 and the single
prod broker went into `CrashLoopBackOff`:

    ERROR [StandardAuthorizer 1] Failed to complete initial ACL load process.
    java.util.concurrent.TimeoutException  (AclPublisher.close)

Cause: the runbook above claimed `allow.everyone.if.no.acl.found=true` makes enabling
the authorizer a no-op. That is wrong for KRaft. `StandardAuthorizer` denies every
request from a non-super-user until it has loaded the ACL snapshot from the metadata
log, and the broker's own registration with the (in-process) controller arrives over
the CONTROLLER/PLAINTEXT listener as `User:ANONYMOUS`. With only `User:admin` as a
super user the node deadlocks against itself. `allow.everyone.if.no.acl.found` is only
consulted after the initial load.

What would make it work (not done here; prove on a kind single-node KRaft first):
1. Add `User:ANONYMOUS` to `super.users`. Boots, but then PLAINTEXT clients are
   super users too, so no ACL can ever restrict them: the migration-window design
   (ACL groups closing PLAINTEXT per prefix) does not work with this option.
2. Authenticate the controller and inter-broker traffic (SASL or mTLS on the
   CONTROLLER and inter-broker listeners, with that principal in `super.users`), then
   ACLs genuinely close PLAINTEXT clients. This is the real prerequisite and it was
   mislabelled as a late "flip" step.

The earlier statement "no ACL group is enforced, so nothing is denied" was true of the
ACL groups but not of enabling the authorizer itself.
