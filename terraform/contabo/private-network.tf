# ===========================================================================
# Private networking — control-plane node attachment
# ===========================================================================
#
# Codifies the EXISTING live Contabo private network that was created
# out-of-band (via the Contabo API) BEFORE it was tracked in Terraform:
#
#     id           : 60932
#     name         : var.private_network_name  (default "FuzeInfra-prod")
#     CIDR         : 10.0.0.0/22          <- Contabo-assigned, READ-ONLY
#     data center  : "European Union 2"   <- Contabo-assigned, READ-ONLY
#
# The `cidr` and `data_center` are computed by Contabo and are NOT settable
# arguments — do not try to pin them here; the provider exposes them as
# read-only attributes only. `region` ("EU") is the settable locator.
#
# ---------------------------------------------------------------------------
# ADOPT the live network into state (one-time, does NOT recreate it):
#
#     terraform import contabo_private_network.prod[0] 60932
#
# (Set var.enable_private_network = true first so the count-indexed resource
#  exists; see variables.tf.)
#
# ===========================================================================
#  ⚠  MANUAL PANEL PURCHASE REQUIRED — TERRAFORM CANNOT ORDER THIS  ⚠
# ===========================================================================
# Contabo private networking depends on a per-instance "VPC / Private
# Networking" ADD-ON that must be BOUGHT PER VPS in the Contabo customer
# panel. Until that add-on is purchased for a given instance, ANY attempt to
# attach that instance to a private network (API or Terraform) fails with:
#
#     HTTP 402  Payment Required
#
# There is NO Contabo API endpoint and NO Terraform resource that can buy the
# add-on — it is a billing action a human must perform manually at
#     https://my.contabo.com  ->  the VPS  ->  "Networking / Private Network"
# Purchase the add-on for the control-plane VPS FIRST, then run the import
# above and `terraform apply`. This TF codifies the network + the intended
# attachment; it can never provision the paid capability behind it.
# ===========================================================================

locals {
  # The k3s config additions (flannel-iface / node-ip / node-external-ip and
  # the private tls-san in provisioning.tf) only activate when private
  # networking is enabled AND a concrete control-plane private IP is supplied.
  # Guarding on the IP prevents writing `node-ip:` with an empty value, which
  # would wedge the k3s server on the next (re)provision.
  private_net_enabled = var.enable_private_network && var.private_node_ip != ""
}

# ---------------------------------------------------------------------------
# The private network resource.
#
# ELASTIC-EXCLUSION INVARIANT (see main.tf): this resource declares ONLY the
# named control-plane VPS as its intended member. The live network 60932 may
# ALSO have elastic / consumer-dispatched instances attached — each of which
# bought its OWN VPC add-on and joined the network out-of-band. Terraform in
# this directory must NEVER enumerate or detach those nodes. Therefore
# `instance_ids` is under `ignore_changes`: attach/detach is a purely
# out-of-band (panel + API) operation, exactly like the elastic pool. Without
# this, a plan that reconciled `instance_ids` back to `[control-plane]` would
# try to DETACH every other member it did not author.
# ---------------------------------------------------------------------------
resource "contabo_private_network" "prod" {
  count = var.enable_private_network ? 1 : 0

  name        = var.private_network_name
  region      = var.private_network_region
  description = "FuzeInfra control-plane private network (10.0.0.0/22, DC 'European Union 2'). Imported from live net 60932."

  # Declared intent: the control-plane VPS is a member. Never reconciled — see
  # the ELASTIC-EXCLUSION note above — so TF never detaches out-of-band members.
  instance_ids = [contabo_instance.prod.id]

  lifecycle {
    ignore_changes = [instance_ids]
  }
}
