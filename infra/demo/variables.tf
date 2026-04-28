variable "aws_region" {
  type        = string
  default     = "us-east-1"
  description = "AWS region. us-east-1 has the broadest Bedrock model availability."
}

variable "project" {
  type        = string
  default     = "lore-elig-demo"
  description = "Resource-name prefix. Keep lowercase, alphanumeric, and dashes."
}

variable "bedrock_model_id" {
  type        = string
  default     = "us.anthropic.claude-sonnet-4-6"
  description = "Cross-region inference profile for the LLM. Override per the available list."
}

variable "embedding_model_id" {
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
  description = "Embedding model. Titan v2 returns 1024-dim vectors."
}

variable "log_retention_days" {
  type    = number
  default = 7
}
