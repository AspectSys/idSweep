from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a sweep measurement defined by a JSON config.")
    parser.add_argument("config", type=Path, help="JSON config file (e.g. settings/guard_leakage.json).")
    parser.add_argument("--output", type=Path, default=None, help="CSV output path (default: results/<TestName>_<timestamp>.csv).")
    parser.add_argument("--limit-rows", type=int, default=None, help="Only run the first N Excel rows.")
    parser.add_argument("--dry-run", action="store_true", help="Print sequence without touching hardware or sleeping.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with open(args.config, encoding="utf-8") as f:
        raw = json.load(f)

    if "lcr" in raw:
        from core.lcr_channel import load_lcr_spec
        from core.lcr_engine import LCRRunner
        spec = load_lcr_spec(args.config)
        LCRRunner(
            spec,
            dry_run=args.dry_run,
            limit_rows=args.limit_rows,
            output_path=args.output,
        ).run()
    elif "series_resistance" in raw:
        from core.series_resistance_engine import SeriesResistanceRunner, load_series_resistance_spec
        spec = load_series_resistance_spec(args.config)
        SeriesResistanceRunner(
            spec,
            dry_run=args.dry_run,
            limit_rows=args.limit_rows,
            output_path=args.output,
        ).run()
    else:
        from core.channel import load_measurement_spec
        from core.engine import SweepRunner
        spec = load_measurement_spec(args.config)
        SweepRunner(
            spec,
            dry_run=args.dry_run,
            limit_rows=args.limit_rows,
            output_path=args.output,
        ).run()



if __name__ == "__main__":
    main()
