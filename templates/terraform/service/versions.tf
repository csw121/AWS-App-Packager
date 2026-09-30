terraform {
  required_version = ">= 1.9.0, < 2.0.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.14.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = { ManagedBy = "aws-app-packager", Profile = "ecs_fargate_http_service_v1" }
  }
}
