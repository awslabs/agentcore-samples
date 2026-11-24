terraform {
  backend "s3" {
    key = "demo/app/terraform.tfstate"
  }
}
