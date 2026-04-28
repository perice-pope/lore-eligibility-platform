"""AI-assisted partner-file schema inference and PII classification.

This package detects column semantics and PII tier in unknown partner files using
Amazon Bedrock (Claude). The output is a draft data contract reviewed by a human
before promotion.
"""
from .inference import infer_schema, SchemaInferenceResult, ColumnInference  # noqa: F401
