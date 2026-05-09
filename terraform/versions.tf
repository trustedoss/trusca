###############################################################################
# Provider + Terraform version pins.
#
# We pin to specific minor versions to keep `terraform plan` reproducible
# across operator workstations. Bumping these is a deliberate change — a
# point release of the google provider has occasionally changed default
# args for Cloud Run / Cloud SQL.
###############################################################################

terraform {
  required_version = ">= 1.7.0, < 2.0.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.10"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.10"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state in GCS. The bucket name is supplied via -backend-config at
  # `terraform init` time so this file does not bake an org-specific name.
  # See terraform/README.md "First-time init" for the exact init command.
  backend "gcs" {
    # bucket  = "<set via -backend-config>"
    prefix = "trustedoss-portal/demo"
  }
}
