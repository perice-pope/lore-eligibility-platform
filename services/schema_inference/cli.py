"""CLI for schema inference.

Usage:
    python -m services.schema_inference.cli <path-to-sample-file> [--mode bedrock|local|auto]

Outputs the draft data contract YAML to stdout. Designed for the partner-onboarding flow:
the engineer runs this on a sample, eyeballs the output, edits if needed, commits to
`schemas/data_contracts/`.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .inference import infer_schema


def load_sample(path: Path, max_rows: int = 50) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        delim = "\t" if suffix == ".tsv" else ","
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=delim)
            return [row for _, row in zip(range(max_rows), reader)]
    if suffix in {".json", ".jsonl", ".ndjson"}:
        with path.open(encoding="utf-8") as f:
            text = f.read().strip()
        if suffix == ".json":
            data = json.loads(text)
            if isinstance(data, dict) and "members" in data:
                data = data["members"]
            return data[:max_rows]
        return [json.loads(line) for line in text.splitlines()[:max_rows] if line.strip()]
    raise ValueError(f"Unsupported sample format for CLI: {suffix} (the production pipeline handles EDI/fixed-width)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Infer a draft data contract from a partner sample file.")
    parser.add_argument("path", help="Path to sample file (CSV / JSON)")
    parser.add_argument(
        "--mode", choices=["bedrock", "local", "auto"], default="auto",
        help="Inference backend. 'auto' tries Bedrock and falls back to local heuristics.",
    )
    parser.add_argument(
        "--format", choices=["yaml", "json"], default="yaml",
        help="Output format.",
    )
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 2

    sample = load_sample(path)
    if not sample:
        print(f"No rows parsed from {path}", file=sys.stderr)
        return 3

    result = infer_schema(path.name, sample, mode=args.mode)

    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(result.to_data_contract_yaml())

    print(f"\n# mode={result.mode} risk={result.overall_quality_risk} columns={len(result.columns)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
