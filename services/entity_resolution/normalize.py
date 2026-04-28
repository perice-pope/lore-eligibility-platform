"""Lossless normalization for entity-resolution feature strings.

We do NOT mutate source records here — we build a *normalized projection* used purely
for matching. The original PII (or its tokens) stays in the silver layer.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any

# Common nicknames → canonical form. Not exhaustive; a real implementation would
# import a curated dataset (e.g., the SecondString or Beider-Morse phonetic libraries).
_NICKNAMES = {
    "bob": "robert",
    "rob": "robert",
    "robbie": "robert",
    "bobby": "robert",
    "bill": "william",
    "billy": "william",
    "will": "william",
    "willy": "william",
    "liz": "elizabeth",
    "beth": "elizabeth",
    "betsy": "elizabeth",
    "lizzie": "elizabeth",
    "kate": "katherine",
    "kathy": "katherine",
    "katie": "katherine",
    "cathy": "katherine",
    "mike": "michael",
    "mick": "michael",
    "mikey": "michael",
    "dave": "david",
    "davey": "david",
    "jim": "james",
    "jimmy": "james",
    "jamie": "james",
    "tom": "thomas",
    "tommy": "thomas",
    "rick": "richard",
    "ricky": "richard",
    "dick": "richard",
    "rich": "richard",
    "jen": "jennifer",
    "jenny": "jennifer",
    "jenn": "jennifer",
    "nick": "nicholas",
    "nicky": "nicholas",
    "tony": "anthony",
    "joe": "joseph",
    "joey": "joseph",
}

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    # Unicode-normalize, lowercase, strip diacritics for matching only
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    # Strip punctuation except hyphen and apostrophe (which are name-significant)
    n = re.sub(r"[^a-z\-' ]", " ", n)
    parts = [p for p in n.split() if p and p not in _SUFFIXES]
    parts = [_NICKNAMES.get(p, p) for p in parts]
    return " ".join(parts)


def normalize_dob(value: Any) -> str:
    """Coerce many DOB representations to ISO yyyy-mm-dd, or '' if unparseable."""
    if value is None or value == "":
        return ""
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def normalize_zip(value: Any) -> str:
    if not value:
        return ""
    s = re.sub(r"[^\d]", "", str(value))[:5]
    return s.rjust(5, "0") if 1 <= len(s) <= 5 else ""


def soundex(s: str) -> str:
    """American Soundex. Sufficient for blocking on last_name."""
    if not s:
        return ""
    s = s.upper()
    s = re.sub(r"[^A-Z]", "", s)
    if not s:
        return ""
    first = s[0]
    code_map = {
        **{c: "1" for c in "BFPV"},
        **{c: "2" for c in "CGJKQSXZ"},
        **{c: "3" for c in "DT"},
        **{c: "4" for c in "L"},
        **{c: "5" for c in "MN"},
        **{c: "6" for c in "R"},
    }
    encoded = first
    prev = code_map.get(first, "")
    for ch in s[1:]:
        code = code_map.get(ch, "")
        if code and code != prev:
            encoded += code
        if code:
            prev = code
        else:
            prev = ""
    encoded = (encoded + "000")[:4]
    return encoded


def feature_string(record: dict) -> str:
    """Render a record as a stable string for embedding.

    Field order is fixed and important — embeddings see this as a sentence; consistent
    structure means similar records produce similar embeddings.
    """
    fname = normalize_name(record.get("first_name"))
    mname = normalize_name(record.get("middle_name"))
    lname = normalize_name(record.get("last_name"))
    dob = normalize_dob(record.get("dob"))
    addr = (record.get("address_line_1") or "").strip().lower()
    city = (record.get("city") or "").strip().lower()
    state = (record.get("state") or "").strip().upper()
    zipc = normalize_zip(record.get("zip"))
    # Email and SSN are token-only at this layer; we use last 4 of SSN if present
    ssn4 = record.get("ssn_last4") or ""
    return (
        f"NAME: {fname} {mname} {lname} | DOB: {dob} | "
        f"ADDR: {addr}, {city}, {state} {zipc} | SSN4: {ssn4}"
    )


def blocking_keys(record: dict) -> list[str]:
    """A record may produce multiple blocking keys. Records that share ANY key are
    candidates for comparison. We emit multiple keys to handle:
      - hyphenated/compound surnames ("Garcia-Lopez" → keys for "Garcia" AND "Lopez")
      - ZIP changes within the same metro (we widen to zip3)

    Tradeoff: more keys = more comparisons but better recall. We bound it at ~4 keys
    per record by only splitting compound names on hyphen and space.
    """
    dob = normalize_dob(record.get("dob"))
    year = dob[:4] if dob else "????"
    zipc = normalize_zip(record.get("zip"))
    zip3 = zipc[:3] if zipc else "???"

    last = (record.get("last_name") or "").strip()
    parts = []
    if last:
        parts.append(last)
        for sep in ("-", " "):
            if sep in last:
                parts.extend(p for p in last.split(sep) if p)

    # de-dupe soundex codes
    codes = []
    seen = set()
    for p in parts:
        sx = soundex(p)
        if sx and sx not in seen:
            seen.add(sx)
            codes.append(sx)
    if not codes:
        codes = [""]
    return [f"{year}|{c}|{zip3}" for c in codes]


def blocking_key(record: dict) -> str:
    """Backward-compat: returns the primary (first) blocking key."""
    keys = blocking_keys(record)
    return keys[0] if keys else "????||???"
