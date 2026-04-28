# One CMK per partner enables cryptographic shredding on offboarding:
# delete the key, all that partner's data at rest is unreadable.

variable "partner_ids" { type = list(string) }

resource "aws_kms_key" "partner" {
  for_each                = toset(var.partner_ids)
  description             = "Eligibility encryption key for partner ${each.key}"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  multi_region            = false # data residency: keep US-only
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Sid       = "EnableRootAccount"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.this.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid    = "AllowEligibilityIngestService"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.this.account_id}:role/eligibility-ingest"
        }
        Action   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource = "*"
        Condition = {
          StringEquals = { "kms:EncryptionContext:partner_id" = each.key }
        }
      }
    ]
  })
  tags = { partner_id = each.key }
}

resource "aws_kms_alias" "partner" {
  for_each      = aws_kms_key.partner
  name          = "alias/lore/eligibility/${each.key}"
  target_key_id = each.value.id
}

resource "aws_kms_key" "platform" {
  description             = "Platform-wide eligibility encryption (non-partner-scoped)"
  deletion_window_in_days = 30
  enable_key_rotation     = true
}

resource "aws_kms_alias" "platform" {
  name          = "alias/lore/eligibility/platform"
  target_key_id = aws_kms_key.platform.id
}

data "aws_caller_identity" "this" {}

output "partner_key_arns" {
  value = { for k, v in aws_kms_key.partner : k => v.arn }
}
output "platform_key_arn" { value = aws_kms_key.platform.arn }
