from __future__ import annotations

import argparse
from pathlib import Path

from core.channel import load_measurement_spec
from core.engine import SweepRunner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a sweep measurement defined by a JSON config.")
    parser.add_argument("config", type=Path, help="JSON config file (e.g. settings/guard_leakage.json).")
    parser.add_argument("--output", type=Path, default=None, help="CSV output path (default: results/<TestName>_<timestamp>.csv).")
    parser.add_argument("--limit-rows", type=int, default=None, help="Only run the first N Excel rows.")
    parser.add_argument("--dry-run", action="store_true", help="Print sequence without touching hardware or sleeping.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    spec = load_measurement_spec(args.config)
    SweepRunner(
        spec,
        dry_run=args.dry_run,
        limit_rows=args.limit_rows,
        output_path=args.output,
    ).run()



if __name__ == "__main__":
    main()
