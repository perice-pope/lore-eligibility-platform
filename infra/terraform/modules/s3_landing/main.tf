# One landing bucket per partner. Object Lock in COMPLIANCE mode for the audit
# trail (raw landed files; 7-year retention).
#
# Why one bucket per partner: blast-radius isolation, simpler IAM, partner-scoped
# encryption with their CMK, easier offboarding (delete the bucket).

variable "partner_ids" { type = list(string) }
variable "kms_keys" { type = map(string) } # partner_id -> KMS key ARN

resource "aws_s3_bucket" "raw" {
  for_each      = toset(var.partner_ids)
  bucket        = "lore-eligibility-raw-${each.key}-${data.aws_caller_identity.this.account_id}"
  force_destroy = false # never auto-delete a HIPAA bucket; explicit two-step

  object_lock_enabled = true
}

resource "aws_s3_bucket_object_lock_configuration" "raw" {
  for_each = aws_s3_bucket.raw
  bucket   = each.value.id
  rule {
    default_retention {
      mode  = "COMPLIANCE"
      years = 7
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  for_each = aws_s3_bucket.raw
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_keys[each.key]
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "raw" {
  for_each = aws_s3_bucket.raw
  bucket   = each.value.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_public_access_block" "raw" {
  for_each                = aws_s3_bucket.raw
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  for_each = aws_s3_bucket.raw
  bucket   = each.value.id
  rule {
    id     = "tier_to_ia_after_90d"
    status = "Enabled"
    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 365
      storage_class = "GLACIER_IR"
    }
    expiration { days = 7 * 365 } # 7 year retention
  }
}

# Bronze, silver, gold buckets — single platform-scoped each (Iceberg)
resource "aws_s3_bucket" "tier" {
  for_each      = toset(["bronze", "silver", "gold"])
  bucket        = "lore-eligibility-${each.key}-${data.aws_caller_identity.this.account_id}"
  force_destroy = false
}

data "aws_caller_identity" "this" {}

output "bucket_names" { value = { for k, v in aws_s3_bucket.raw : k => v.bucket } }
output "bucket_arns" { value = concat([for v in aws_s3_bucket.raw : v.arn], [for v in aws_s3_bucket.tier : v.arn]) }
