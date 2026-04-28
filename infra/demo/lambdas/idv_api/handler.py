"""AWS Lambda handler for the IDV API.

This is the existing FastAPI app from `services/identity_verification_api/main.py`,
wrapped with Mangum so it runs on Lambda behind API Gateway HTTP API. The DynamoDB
backend is engaged via the `LORE_IDV_STORE_BACKEND=dynamodb` environment variable
that Terraform sets on this function.

The build script copies `services/` into this directory before zipping, so the
runtime has the full package available on the import path.
"""

from mangum import Mangum

from services.identity_verification_api.main import app

# Mangum translates API Gateway HTTP API v2 events <-> ASGI for FastAPI.
# `lifespan="off"` is intentional: we want lifespan startup to run on each cold
# start, but Mangum's native lifespan support is best-effort on Lambda. The
# FastAPI lifespan handler builds the DDB-backed store synchronously, so it's
# fine to run inline.
handler = Mangum(app, lifespan="auto")
