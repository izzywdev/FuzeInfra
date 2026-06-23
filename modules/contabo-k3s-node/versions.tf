# Provider requirements for the contabo-k3s-node module.
#
# This module is consumer-agnostic: a calling root module (the infra-request
# handler in CI) supplies the credentials and k3s join parameters as variables —
# nothing is hardcoded here.
terraform {
  required_version = ">= 1.6"

  required_providers {
    contabo = {
      source  = "contabo/contabo"
      version = "~> 0.1"
    }
  }
}
