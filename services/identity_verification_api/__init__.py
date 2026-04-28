"""Identity Verification API.

The hot-path service that the Lore mobile app calls during member sign-up. Reads from
Aurora golden record store + OpenSearch fuzzy index; never reads from Snowflake or S3.
"""
