# Terraform modules

Two modules are fleshed out in this prototype:

- [`kms`](kms/) — per-partner customer-managed keys with encryption-context-bound IAM
- [`s3_landing`](s3_landing/) — per-partner landing buckets with Object Lock + lifecycle

The remaining modules referenced from the root `main.tf` are stubbed below — each is a
brief description of the contained resources so the architecture is auditable from this
file. In a real Lore environment each would be ~50–200 lines of Terraform.

| Module | Resources | Notes |
|---|---|---|
| `vpc` | VPC + 3-AZ public/private/database subnets + NAT + flow logs | Flow logs to S3 + CloudWatch; gateway endpoints for S3 and DynamoDB; interface endpoints for KMS, Secrets Manager, ECR |
| `transfer` | AWS Transfer Family SFTP server per partner; SSH keys in Secrets Manager; S3 backing | Custom IDP via Lambda for partner-scoped IAM; CloudWatch logging; PrivateLink for partner egress (optional) |
| `msk` | Amazon MSK cluster (3 brokers, kafka.m5.large), client TLS, IAM auth, encryption-at-rest with platform CMK | MSK Connect for Debezium connectors; Schema Registry via Glue or Confluent Cloud; broker logs to S3 |
| `macie` | Macie account enabled; classification jobs targeting raw landing buckets | Custom data identifiers for Lore-specific patterns; findings to EventBridge → SNS → PagerDuty |
| `aurora_idv` | Aurora PostgreSQL cluster (writer + 2 readers, r6g.xlarge), RDS Proxy, parameter group with pg_trgm | Multi-AZ, 7-day backup retention, encrypted with platform CMK; performance insights enabled |
| `ecs_idv_api` | ECS Fargate cluster, service with ≥3 tasks across AZs, ALB with TLS 1.3, WAF, target group health checks | Auto-scaling on CPU + request count; CloudWatch logs; X-Ray tracing |
| `eks_dagster` | EKS cluster for Dagster hybrid agent + Spark on Kubernetes | Bottlerocket nodes; Cluster Autoscaler; IRSA for service accounts; CNI VPC IP exhaustion mitigation |
| `iam` | Roles for ingest, dbt, IDV, dagster, ECS task, observability; least-privilege | All roles use `Condition` blocks; no admin grants; Access Analyzer enabled |
| `observability` | CloudWatch dashboards, alarms; OpenLineage backend ECS task; Datadog forwarder | Alarm topic per severity; SLO burn-rate alerts; quarterly review of alert noise |

Each module exposes the minimum outputs the root or other modules depend on. Module
interfaces are versioned via Terraform module registry; major-version bumps require an
ADR.
