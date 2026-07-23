# ---------------------------------------------------------------------------
# Contabo Object Storage (S3) — logs + backups + blobs
#
# Design: docs/design/s3-and-private-networking.md (§2).
#
# WHAT THIS IS FOR (the honest scope): S3 is an HTTP object store — no POSIX
# filesystem, no block writes, no fsync/mmap/locking. Postgres, MongoDB, Redis,
# Neo4j, Elasticsearch, ChromaDB, Kafka and RabbitMQ MUST keep running on
# local-path block PVCs; their only S3 story is backup/snapshot OFFLOAD. The
# genuine S3 consumers are: Loki (native object-store backend, already coded in
# the chart), scheduled DB-backup CronJobs, and app/blob artifacts.
#
# COST GATE: applying contabo_object_storage PURCHASES paid storage
# (~EUR 6.99/mo per 1 TB). Everything here is gated behind
# var.enable_object_storage (default false) so a routine apply of this directory
# never buys storage. Enable it only in an explicit, human-reviewed apply.
#
# ELASTIC-EXCLUSION INVARIANT (see main.tf): this file adds a self-contained
# resource pair. It introduces NO `data "contabo_instance"` and NO for_each over
# the account instance inventory, so it does not weaken that invariant.
#
# S3 endpoint is READ FROM STATE (contabo_object_storage.this[0].s3_url), never
# hardcoded — the region determines the host (EU -> eu2.contabostorage.com).
# ---------------------------------------------------------------------------

resource "contabo_object_storage" "this" {
  count                    = var.enable_object_storage ? 1 : 0
  region                   = var.object_storage_region
  total_purchased_space_tb = var.object_storage_purchased_tb
  display_name             = var.object_storage_display_name

  # Optional growth guardrail: auto-grow purchased quota up to a hard ceiling so
  # a log/backup spike never fails writes, while capping the bill. Off unless
  # var.object_storage_autoscaling_limit_tb > 0.
  dynamic "auto_scaling" {
    for_each = var.object_storage_autoscaling_limit_tb > 0 ? [1] : []
    content {
      state         = "enabled"
      size_limit_tb = var.object_storage_autoscaling_limit_tb
    }
  }

  lifecycle {
    # Purchased quota is a paid, one-way-ish change; never let an unrelated plan
    # silently resize it. Quota changes go through a deliberate, reviewed apply.
    prevent_destroy = true
  }
}

# Three purpose-scoped buckets in the one tenant. Buckets are created via the
# Contabo OAuth2 API (not the S3 API), so no S3 access keys are needed at plan
# time — the same OAuth2 provider block in main.tf covers them.
resource "contabo_object_storage_bucket" "loki" {
  count             = var.enable_object_storage ? 1 : 0
  name              = var.object_storage_bucket_loki
  object_storage_id = contabo_object_storage.this[0].id
  public_sharing    = false
}

resource "contabo_object_storage_bucket" "backups" {
  count             = var.enable_object_storage ? 1 : 0
  name              = var.object_storage_bucket_backups
  object_storage_id = contabo_object_storage.this[0].id
  public_sharing    = false
}

resource "contabo_object_storage_bucket" "blobs" {
  count             = var.enable_object_storage ? 1 : 0
  name              = var.object_storage_bucket_blobs
  object_storage_id = contabo_object_storage.this[0].id
  public_sharing    = false
}
