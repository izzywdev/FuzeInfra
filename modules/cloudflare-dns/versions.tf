# Provider requirements for the cloudflare-dns module.
#
# Consumer-agnostic: the calling root module (a consumer's deploy/terraform,
# applied by FuzeInfra's infra-request-handler) configures the cloudflare provider
# with a token injected from FuzeInfra secrets — nothing is hardcoded here.
terraform {
  required_version = ">= 1.6"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4"
    }
  }
}
