# =============================================================================
# Lore Eligibility — Cloud Demo
# A scoped subset of the architecture deployable to a free-tier AWS account.
# Total cost during a 1-hour panel demo: under $1. Idle: ~$3-8/month.
# Tear down with `./teardown.sh` after the panel.
# =============================================================================

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
      Demo      = "true"
    }
  }
}

data "aws_caller_identity" "this" {}
data "aws_region" "this" {}

locals {
  account_id       = data.aws_caller_identity.this.account_id
  bucket_raw       = "${var.project}-raw-${local.account_id}"
  bucket_bronze    = "${var.project}-bronze-${local.account_id}"
  ddb_table        = "${var.project}-golden-records"
  log_group_idv    = "/aws/lambda/${var.project}-idv-api"
  log_group_proc   = "/aws/lambda/${var.project}-file-processor"
  log_group_schema = "/aws/lambda/${var.project}-schema-inference"
}

# =============================================================================
# DynamoDB — golden records (Aurora analog for the demo)
# =============================================================================
resource "aws_dynamodb_table" "golden_records" {
  name         = local.ddb_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "golden_record_id"

  attribute {
    name = "golden_record_id"
    type = "S"
  }
  attribute {
    name = "lookup_key"
    type = "S"
  }

  global_secondary_index {
    name            = "lookup_key_index"
    hash_key        = "lookup_key"
    projection_type = "ALL"
  }

  server_side_encryption { enabled = true }
  point_in_time_recovery { enabled = false }
}

# =============================================================================
# S3 — raw landing + bronze (Iceberg-ready)
# =============================================================================
resource "aws_s3_bucket" "raw" {
  bucket        = local.bucket_raw
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "raw" {
  bucket                  = aws_s3_bucket.raw.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule {
    id     = "expire-after-7-days"
    status = "Enabled"
    filter {}
    expiration { days = 7 }
  }
}

resource "aws_s3_bucket" "bronze" {
  bucket        = local.bucket_bronze
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "bronze" {
  bucket                  = aws_s3_bucket.bronze.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "bronze" {
  bucket = aws_s3_bucket.bronze.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# =============================================================================
# IAM — Lambda execution role
# =============================================================================
resource "aws_iam_role" "lambda" {
  name = "${var.project}-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_inline" {
  name = "${var.project}-lambda-inline"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan",
          "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:BatchWriteItem",
          "dynamodb:DescribeTable",
        ]
        Resource = [
          aws_dynamodb_table.golden_records.arn,
          "${aws_dynamodb_table.golden_records.arn}/index/*",
        ]
      },
      {
        Sid    = "S3Access"
        Effect = "Allow"
        Action = [
          "s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.raw.arn,
          "${aws_s3_bucket.raw.arn}/*",
          aws_s3_bucket.bronze.arn,
          "${aws_s3_bucket.bronze.arn}/*",
        ]
      },
      {
        Sid    = "BedrockInvoke"
        Effect = "Allow"
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        # Inference profiles span regions; allow the profile and the foundation models it routes to.
        Resource = [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:*:${local.account_id}:inference-profile/*",
          "arn:aws:bedrock:*:*:inference-profile/*",
        ]
      },
    ]
  })
}

# =============================================================================
# CloudWatch log groups (created up-front so retention is enforced)
# =============================================================================
resource "aws_cloudwatch_log_group" "idv" {
  name              = local.log_group_idv
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "proc" {
  name              = local.log_group_proc
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "schema" {
  name              = local.log_group_schema
  retention_in_days = var.log_retention_days
}

# =============================================================================
# Lambda packaging — zips built outside Terraform by deploy.sh
# =============================================================================
data "archive_file" "idv_api_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambdas/idv_api/build"
  output_path = "${path.module}/lambdas/idv_api/idv_api.zip"
  depends_on  = []
}

data "archive_file" "file_processor_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambdas/file_processor/build"
  output_path = "${path.module}/lambdas/file_processor/file_processor.zip"
}

data "archive_file" "schema_inference_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambdas/schema_inference/build"
  output_path = "${path.module}/lambdas/schema_inference/schema_inference.zip"
}

# =============================================================================
# Lambda functions
# =============================================================================
resource "aws_lambda_function" "idv_api" {
  function_name    = "${var.project}-idv-api"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.idv_api_zip.output_path
  source_code_hash = data.archive_file.idv_api_zip.output_base64sha256
  timeout          = 15
  memory_size      = 512

  environment {
    variables = {
      LORE_IDV_STORE_BACKEND   = "dynamodb"
      LORE_IDV_DDB_TABLE       = aws_dynamodb_table.golden_records.name
      LORE_BEDROCK_MODEL       = var.bedrock_model_id
      LORE_BEDROCK_EMBED_MODEL = var.embedding_model_id
      AWS_LWA_INVOKE_MODE      = "response_stream"
    }
  }

  depends_on = [aws_cloudwatch_log_group.idv]
}

resource "aws_lambda_function" "file_processor" {
  function_name    = "${var.project}-file-processor"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.file_processor_zip.output_path
  source_code_hash = data.archive_file.file_processor_zip.output_base64sha256
  timeout          = 60
  memory_size      = 512

  environment {
    variables = {
      LORE_IDV_DDB_TABLE = aws_dynamodb_table.golden_records.name
      BRONZE_BUCKET      = aws_s3_bucket.bronze.bucket
    }
  }

  depends_on = [aws_cloudwatch_log_group.proc]
}

resource "aws_lambda_function" "schema_inference" {
  function_name    = "${var.project}-schema-inference"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.schema_inference_zip.output_path
  source_code_hash = data.archive_file.schema_inference_zip.output_base64sha256
  timeout          = 30
  memory_size      = 512

  environment {
    variables = {
      LORE_BEDROCK_MODEL = var.bedrock_model_id
    }
  }

  depends_on = [aws_cloudwatch_log_group.schema]
}

# =============================================================================
# S3 -> file_processor trigger
# =============================================================================
resource "aws_lambda_permission" "s3_invoke_processor" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.file_processor.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.raw.arn
}

resource "aws_s3_bucket_notification" "raw_to_processor" {
  bucket = aws_s3_bucket.raw.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.file_processor.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "inbox/"
  }

  depends_on = [aws_lambda_permission.s3_invoke_processor]
}

# =============================================================================
# API Gateway HTTP API in front of the IDV Lambda
# =============================================================================
resource "aws_apigatewayv2_api" "idv" {
  name          = "${var.project}-idv-api"
  protocol_type = "HTTP"
  cors_configuration {
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["content-type", "authorization", "x-correlation-id"]
    allow_origins = ["*"]
  }
}

resource "aws_apigatewayv2_integration" "idv" {
  api_id                 = aws_apigatewayv2_api.idv.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.idv_api.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "idv_default" {
  api_id    = aws_apigatewayv2_api.idv.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.idv.id}"
}

resource "aws_apigatewayv2_stage" "idv" {
  api_id      = aws_apigatewayv2_api.idv.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigw_invoke_idv" {
  statement_id  = "AllowAPIGwInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.idv_api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.idv.execution_arn}/*/*"
}
