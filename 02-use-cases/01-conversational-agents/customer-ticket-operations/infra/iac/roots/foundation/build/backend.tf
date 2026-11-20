terraform {
  backend "s3" {
    key = "demo/foundation/build/terraform.tfstate"
  }
}
