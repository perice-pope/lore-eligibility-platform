"""PII vault client (Skyflow-pattern).

This is the application-side abstraction for tokenizing/detokenizing PII. It is built
to swap providers — Skyflow today, Very Good Security or self-hosted Vault tomorrow —
without rewriting callers.
"""
from .client import PIIVaultClient, TokenizeRequest, DetokenizeRequest, PolicyDeniedError  # noqa: F401
