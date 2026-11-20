terraform {
  backend "s3" {
    key = "demo/web/terraform.tfstate"
  }
}
