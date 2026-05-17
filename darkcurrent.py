from __future__ import annotations

import argparse
from pathlib import Path

from core.channel import load_measurement_spec
from core.engine import SweepRunner

_DEFAULT_CONFIG = Path("settings/dark_current.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the dark current test.")
    parser.add_argument("--config", type=Path, default=_DEFAULT_CONFIG, help="JSON config file.")
    parser.add_argument("--workbook", type=Path, default=None, help="Override Excel workbook path from config.")
    parser.add_argument("--output", type=Path, default=None, help="CSV output path.")
    parser.add_argument("--limit-rows", type=int, default=None, help="Only run the first N Excel rows.")
    parser.add_argument("--dry-run", action="store_true", help="Print sequence without touching hardware or sleeping.")
    parser.add_argument("--matrix-only", action="store_true", help="Only apply matrix routes, do not connect SMUs.")
    parser.add_argument("--smu-config-only", action="store_true", help="Only configure SMUs, then power off.")
    parser.add_argument("--smu-zero-only", action="store_true", help="Apply 0 V to all SMUs, then power off.")
    parser.add_argument("--smu-readback-only", action="store_true", help="Apply 0 V and test TV/TI readback.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    spec = load_measurement_spec(args.config, workbook_path=args.workbook)
    SweepRunner(
        spec,
        dry_run=args.dry_run,
        limit_rows=args.limit_rows,
        output_path=args.output,
        matrix_only=args.matrix_only,
        smu_config_only=args.smu_config_only,
        smu_zero_only=args.smu_zero_only,
        smu_readback_only=args.smu_readback_only,
    ).run()


if __name__ == "__main__":
    main()
