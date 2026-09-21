#!/usr/bin/env python3
"""Generate a versioned MCU-9 supplier-performance release."""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

from supplier_comparison.supplier_history import generate_supplier_history


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("data/purchase_orders.csv"))
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/generated/supplier_history/mcu9"),
    )
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--as-of-date", required=True)
    args = parser.parse_args()
    content, manifest, content_hash, manifest_hash = generate_supplier_history(
        args.source,
        args.output_root,
        dataset_version=args.dataset_version,
        generated_at=datetime.fromisoformat(args.generated_at),
        as_of_date=date.fromisoformat(args.as_of_date),
    )
    print(f"content={content} sha256={content_hash}")
    print(f"manifest={manifest} sha256={manifest_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
