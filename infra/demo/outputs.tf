output "idv_api_url" {
  value       = aws_apigatewayv2_api.idv.api_endpoint
  description = "Live HTTPS endpoint for the IDV API. Append /v1/verify, /healthz, etc."
}

output "ddb_table" {
  value       = aws_dynamodb_table.golden_records.name
  description = "DynamoDB table holding golden records."
}

output "raw_bucket" {
  value       = aws_s3_bucket.raw.bucket
  description = "Drop a partner file at s3://<this>/inbox/<file> to trigger ingest."
}

output "bronze_bucket" {
  value       = aws_s3_bucket.bronze.bucket
  description = "Bronze (Iceberg-ready) tier."
}

output "schema_inference_lambda" {
  value       = aws_lambda_function.schema_inference.function_name
  description = "Invoke directly: aws lambda invoke --function-name <this> ..."
}

output "log_groups" {
  value = {
    idv              = aws_cloudwatch_log_group.idv.name
    file_processor   = aws_cloudwatch_log_group.proc.name
    schema_inference = aws_cloudwatch_log_group.schema.name
  }
}

output "demo_console_links" {
  value = {
    api_test           = "${aws_apigatewayv2_api.idv.api_endpoint}/healthz"
    ddb_console        = "https://${var.aws_region}.console.aws.amazon.com/dynamodbv2/home?region=${var.aws_region}#item-explorer?table=${aws_dynamodb_table.golden_records.name}"
    raw_bucket_console = "https://${var.aws_region}.console.aws.amazon.com/s3/buckets/${aws_s3_bucket.raw.bucket}"
    cloudwatch_logs    = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#logsV2:log-groups"
  }
  description = "Console URLs to bring up during the demo."
}
