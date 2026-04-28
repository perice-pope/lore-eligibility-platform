variable "vpc_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "kms_key_arn" { type = string }
# Stub — see ../README.md.
output "bootstrap_brokers" { value = "b-1.msk-cluster.kafka.us-east-1.amazonaws.com:9098" }
