terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.80" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  # Remote state (recommended): copy backend.tf.example -> backend.tf and run `terraform init -migrate-state`.
}

provider "aws" {
  region = var.region
  default_tags {
    tags = merge({ Project = var.project, Environment = var.environment, ManagedBy = "terraform", DataClass = "synthetic" }, var.tags)
  }
}
