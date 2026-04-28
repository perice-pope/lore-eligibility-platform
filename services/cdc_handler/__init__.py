"""Debezium CDC consumer.

Consumes per-table change topics from MSK Kafka, performs PII tokenization at the
edge, and writes a normalized eligibility-event stream to an outbound topic that the
silver-layer Spark job consumes.
"""
