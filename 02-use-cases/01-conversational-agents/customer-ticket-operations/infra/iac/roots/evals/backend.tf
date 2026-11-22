terraform {
  backend "s3" {
    key = "demo/evals/terraform.tfstate"
  }
}
