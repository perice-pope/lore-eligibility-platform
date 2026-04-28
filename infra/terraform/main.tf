# Lore Eligibility Platform — Terraform root.
# Multi-account topology: prod, staging, dev. This is the prod environment file.
# Sensitive defaults are not hardcoded — they come from terraform.tfvars in a private repo.

terraform {
  required_version = ">= 1.7.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.50"
    }
    snowflake = {
      source  = "Snowflake-Labs/snowflake"
      version = "~> 0.95"
    }
  }
  backend "s3" {
    bucket         = "lore-tf-state-prod"
    key            = "eligibility/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    kms_key_id     = "alias/tf-state"
    dynamodb_table = "lore-tf-locks"
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Owner       = "data-platform"
      Service     = "eligibility-platform"
      Environment = var.environment
      Compliance  = "HIPAA"
      ManagedBy   = "terraform"
      CostCenter  = "data-platform"
    }
  }
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "partner_ids" {
  type    = list(string)
  default = ["acme-corp", "blue-shield-east", "northwind-aco"]
}

module "kms" {
  source      = "./modules/kms"
  partner_ids = var.partner_ids
}

module "s3_landing" {
  source      = "./modules/s3_landing"
  partner_ids = var.partner_ids
  kms_keys    = module.kms.partner_key_arns
}

module "vpc" {
  source = "./modules/vpc"
}

module "transfer_family" {
  source          = "./modules/transfer"
  partner_ids     = var.partner_ids
  landing_buckets = module.s3_landing.bucket_names
}

module "msk" {
  source      = "./modules/msk"
  vpc_id      = module.vpc.vpc_id
  subnet_ids  = module.vpc.private_subnet_ids
  kms_key_arn = module.kms.platform_key_arn
}

module "macie" {
  source      = "./modules/macie"
  bucket_arns = module.s3_landing.bucket_arns
}

module "aurora_idv" {
  source      = "./modules/aurora_idv"
  vpc_id      = module.vpc.vpc_id
  subnet_ids  = module.vpc.database_subnet_ids
  kms_key_arn = module.kms.platform_key_arn
}

module "ecs_idv_api" {
  source          = "./modules/ecs_idv_api"
  vpc_id          = module.vpc.vpc_id
  subnet_ids      = module.vpc.private_subnet_ids
  aurora_endpoint = module.aurora_idv.writer_endpoint
}

module "eks_dagster" {
  source     = "./modules/eks_dagster"
  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnet_ids
}

module "iam" {
  source      = "./modules/iam"
  partner_ids = var.partner_ids
}

module "observability" {
  source = "./modules/observability"
}

output "idv_api_endpoint" {
  value = module.ecs_idv_api.alb_dns_name
}

output "msk_bootstrap_brokers" {
  value     = module.msk.bootstrap_brokers
  sensitive = true
}

output "aurora_writer_endpoint" {
  value = module.aurora_idv.writer_endpoint
}
