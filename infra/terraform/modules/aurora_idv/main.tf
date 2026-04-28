variable "vpc_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "kms_key_arn" { type = string }
# Stub — Aurora Postgres + RDS Proxy. See ../README.md.
output "writer_endpoint" { value = "idv-aurora.cluster-stub.us-east-1.rds.amazonaws.com" }
