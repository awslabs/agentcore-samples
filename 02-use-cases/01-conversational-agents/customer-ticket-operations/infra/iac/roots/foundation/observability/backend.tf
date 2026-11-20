terraform {
  backend "s3" {
    key = "demo/foundation/observability/terraform.tfstate"
  }
}
