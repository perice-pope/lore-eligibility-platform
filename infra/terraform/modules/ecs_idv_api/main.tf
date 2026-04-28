variable "vpc_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "aurora_endpoint" { type = string }
# Stub — ECS Fargate + ALB + WAF. See ../README.md.
output "alb_dns_name" { value = "idv.lore.co" }
