# =============================================================================
# GENERATED — materialized consumer declarations. DO NOT HAND-EDIT.
#
# This file is the ONLY place consumer-specific data enters FuzeInfra's
# terraform. The bare setup (../*.tf) is consumer-free by invariant; CI loads
# this file via -var-file. Source of truth is each consumer repo's own
# declaration (deploy/cf/hosts.yaml); entries are materialized here by the
# consumer-dispatch flow (repository_dispatch → validate against policy →
# auto-PR → saved-plan gate → deliberate merge applies).
#
# Policy reminders: labels must not collide, no wildcards, bypass only for
# hosts whose app owns its own auth — admin UIs get gated Access apps in
# cloudflare.tf instead.
# =============================================================================

public_app_hosts = {
  # izzywdev/FuzeKeys (FuzeInfra#136 / #140)
  "keys"     = "izzywdev/FuzeKeys" # frontend    — keys.prod.fuzefront.com
  "api.keys" = "izzywdev/FuzeKeys" # API backend — api.keys.prod.fuzefront.com

  # MFE product apps — registered as FuzeFront micro-frontends
  "fuzesales"   = "izzywdev/FuzeSales"   # fuzesales.prod.fuzefront.com
  "fuzecontact" = "izzywdev/FuzeContact" # fuzecontact.prod.fuzefront.com
  "fuzeservice" = "izzywdev/FuzeService" # fuzeservice.prod.fuzefront.com
}
