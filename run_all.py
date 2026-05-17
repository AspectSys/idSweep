from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

CONFIGS = [
    Path("settings/guard_leakage.json"),
    Path("settings/dark_current.json"),
    Path("settings/capacitance.json"),
    Path("settings/series_resistance.json"),
]


def run_one(config: Path, dry_run: bool, limit_rows: int | None) -> Path:
    with open(config, encoding="utf-8") as f:
        raw = json.load(f)

    if "lcr" in raw:
        from core.lcr_channel import load_lcr_spec
        from core.lcr_engine import LCRRunner
        return LCRRunner(load_lcr_spec(config), dry_run=dry_run, limit_rows=limit_rows).run()
    elif "series_resistance" in raw:
        from core.channel import load_measurement_spec
        from core.series_resistance_engine import SeriesResistanceRunner
        return SeriesResistanceRunner(load_measurement_spec(config), dry_run=dry_run, limit_rows=limit_rows).run()
    else:
        from core.channel import load_measurement_spec
        from core.engine import SweepRunner
        return SweepRunner(load_measurement_spec(config), dry_run=dry_run, limit_rows=limit_rows).run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run all measurements in sequence.")
    parser.add_argument("--limit-rows", type=int, default=None, help="Only run the first N Excel rows per test.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without touching hardware.")
    args = parser.parse_args()

    _INTER_TEST_DELAY_S = 4

    paths = []
    for i, config in enumerate(CONFIGS):
        if i > 0:
            print(f"Waiting {_INTER_TEST_DELAY_S}s before next test...")
            if not args.dry_run:
                time.sleep(_INTER_TEST_DELAY_S)
        print(f"\n{'=' * 60}\nRunning: {config.stem}\n{'=' * 60}")
        paths.append(run_one(config, dry_run=args.dry_run, limit_rows=args.limit_rows))

    print(f"\n{'=' * 60}\nAll measurements complete.\n{'=' * 60}")

    print(f"\n{'=' * 60}\nGenerating report\n{'=' * 60}")
    run_info_path = Path("settings/run_info.json")
    run_info = json.loads(run_info_path.read_text(encoding="utf-8-sig")) if run_info_path.exists() else {}
    guard_path, dark_path, cap_path, rs_path = paths
    from core.report import ReportWriter
    ReportWriter(
        guard_leakage_csv=guard_path,
        dark_current_csv=dark_path,
        capacitance_csv=cap_path,
        series_resistance_csv=rs_path,
        run_info=run_info,
    ).run()


if __name__ == "__main__":
    main()
