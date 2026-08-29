from __future__ import annotations

import argparse
from pathlib import Path

from support.r130run_builder import build_synthetic_r130run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--large-csv-mib", type=int, default=0)
    args = parser.parse_args()
    overrides: dict[str, bytes] | None = None
    if args.large_csv_mib > 0:
        row = b"019d3c80-3d21-7a65-8e5a-555555555555,019d3c80-3d21-7a65-8e5a-222222222222\n"
        target = args.large_csv_mib * 1024 * 1024
        repeats = max(1, target // len(row))
        overrides = {"measurements.csv": b"measurement_id,run_id\n" + row * repeats}
    build_synthetic_r130run(args.output, payload_overrides=overrides)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
