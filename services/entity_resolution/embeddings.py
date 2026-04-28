"""Embedding generation for entity-resolution feature strings.

Production: Amazon Bedrock Titan Text Embed v2 (1024 dims).
Local fallback: a deterministic char-trigram TF-IDF-style hash embedding so the
matcher works without AWS access. The fallback isn't as good as Titan but it's
*directionally* correct: similar strings produce similar vectors.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from dataclasses import dataclass
from typing import Sequence

log = logging.getLogger(__name__)

DEFAULT_MODEL = "amazon.titan-embed-text-v2:0"
DEFAULT_DIM = 1024
LOCAL_DIM = 256  # smaller is fine for the local mock


@dataclass
class EmbeddingResult:
    vector: list[float]
    dim: int
    model_id: str
    mode: str


class Embedder:
    def __init__(self, *, mode: str | None = None, model_id: str | None = None, region: str | None = None):
        self.mode = (mode or os.environ.get("LORE_EMBED_MODE", "auto")).lower()
        self.model_id = model_id or os.environ.get("LORE_BEDROCK_EMBED_MODEL", DEFAULT_MODEL)
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")
        self._client = None

    def embed(self, text: str) -> EmbeddingResult:
        if self.mode == "local":
            return self._embed_local(text)
        if self.mode == "bedrock":
            return self._embed_bedrock(text)
        try:
            return self._embed_bedrock(text)
        except Exception as exc:
            log.warning("Bedrock embedding failed (%s); falling back to local hash embedding", exc)
            return self._embed_local(text)

    def embed_batch(self, texts: Sequence[str]) -> list[EmbeddingResult]:
        return [self.embed(t) for t in texts]

    # ---------- Bedrock ----------
    def _embed_bedrock(self, text: str) -> EmbeddingResult:
        import boto3  # lazy

        if self._client is None:
            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        body = json.dumps({"inputText": text, "dimensions": DEFAULT_DIM, "normalize": True})
        resp = self._client.invoke_model(
            modelId=self.model_id,
            body=body,
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(resp["body"].read())
        vec = payload["embedding"]
        return EmbeddingResult(vector=vec, dim=len(vec), model_id=self.model_id, mode="bedrock")

    # ---------- Local hash-based mock ----------
    def _embed_local(self, text: str) -> EmbeddingResult:
        vec = [0.0] * LOCAL_DIM
        text = text.lower()
        # char trigrams + token bigrams
        grams = [text[i : i + 3] for i in range(len(text) - 2)]
        tokens = text.split()
        grams += [" ".join(tokens[i : i + 2]) for i in range(len(tokens) - 1)]
        for g in grams:
            h = int.from_bytes(hashlib.sha1(g.encode()).digest()[:4], "big")
            sign = 1.0 if (h >> 31) & 1 else -1.0
            idx = h % LOCAL_DIM
            vec[idx] += sign
        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        vec = [v / norm for v in vec]
        return EmbeddingResult(vector=vec, dim=LOCAL_DIM, model_id="local-trigram-hash", mode="local_mock")


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"vector dim mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)
