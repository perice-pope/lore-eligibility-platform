"""Entity resolution.

Three-stage matcher: deterministic blocking → embedding-based candidate retrieval →
LLM adjudication for borderline cases. See docs/architecture.md §3.5.
"""
from .matcher import EntityResolver, MatchDecision, ResolverConfig  # noqa: F401
