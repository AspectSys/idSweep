from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Dict, Optional

CONFIGS = [
    Path("settings/guard_leakage.json"),
    Path("settings/dark_current.json"),
    Path("settings/capacitance.json"),
    Path("settings/series_resistance.json"),
]

_INTER_TEST_DELAY_S = 4


def run_one(
    config: Path,
    dry_run: bool,
    limit_rows: int | None,
    output_path: Optional[Path] = None,
) -> Path:
    with open(config, encoding="utf-8") as f:
        raw = json.load(f)

    if "lcr" in raw:
        from core.lcr_channel import load_lcr_spec
        from core.lcr_engine import LCRRunner
        return LCRRunner(
            load_lcr_spec(config), dry_run=dry_run, limit_rows=limit_rows, output_path=output_path
        ).run()
    elif "series_resistance" in raw:
        from core.channel import load_measurement_spec
        from core.series_resistance_engine import SeriesResistanceRunner
        return SeriesResistanceRunner(
            load_measurement_spec(config), dry_run=dry_run, limit_rows=limit_rows, output_path=output_path
        ).run()
    else:
        from core.channel import load_measurement_spec
        from core.engine import SweepRunner
        return SweepRunner(
            load_measurement_spec(config), dry_run=dry_run, limit_rows=limit_rows, output_path=output_path
        ).run()


def device_output_dir(results_root: Path, run_info: Dict[str, str]) -> Path:
    """Per-device output folder: results/wafer<id>/dev<NNN>_x<x>_y<y>/."""
    wafer_id = run_info.get("wafer_id", "")
    device_no = int(run_info.get("device_no", 0))
    pos_x = run_info.get("tester_device_pos_x", "")
    pos_y = run_info.get("tester_device_pos_y", "")
    return results_root / f"wafer{wafer_id}" / f"dev{device_no:03d}_x{pos_x}_y{pos_y}"


def run_device(
    run_info: Dict[str, str],
    *,
    dry_run: bool,
    limit_rows: int | None,
    output_dir: Path,
) -> Path:
    """Run all four measurements for one device and write its report.

    Every artifact (4 CSVs + report) is written into ``output_dir`` with fixed,
    collision-free names, so this can be called many times across a wafer. Returns
    the report path.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = []
    for i, config in enumerate(CONFIGS):
        if i > 0:
            print(f"Waiting {_INTER_TEST_DELAY_S}s before next test...")
            if not dry_run:
                time.sleep(_INTER_TEST_DELAY_S)
        print(f"\n{'=' * 60}\nRunning: {config.stem}\n{'=' * 60}")
        paths.append(
            run_one(config, dry_run=dry_run, limit_rows=limit_rows, output_path=output_dir / f"{config.stem}.csv")
        )

    print(f"\n{'=' * 60}\nGenerating report\n{'=' * 60}")
    guard_path, dark_path, cap_path, rs_path = paths
    from core.report import ReportWriter
    device_no = int(run_info.get("device_no", 0))
    return ReportWriter(
        guard_leakage_csv=guard_path,
        dark_current_csv=dark_path,
        capacitance_csv=cap_path,
        series_resistance_csv=rs_path,
        run_info=run_info,
    ).run(output_path=output_dir / f"result_dev{device_no:03d}.txt")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run all measurements for one device (no prober).")
    parser.add_argument("--limit-rows", type=int, default=None, help="Only run the first N Excel rows per test.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without touching hardware.")
    parser.add_argument("--run-info", type=Path, default=Path("settings/run_info.json"), help="Device metadata JSON.")
    parser.add_argument("--results-root", type=Path, default=Path("results"), help="Root folder for outputs.")
    args = parser.parse_args()

    run_info = (
        json.loads(args.run_info.read_text(encoding="utf-8-sig")) if args.run_info.exists() else {}
    )
    output_dir = device_output_dir(args.results_root, run_info)
    report_path = run_device(
        run_info, dry_run=args.dry_run, limit_rows=args.limit_rows, output_dir=output_dir
    )

    print(f"\n{'=' * 60}\nAll measurements complete. Report: {report_path}\n{'=' * 60}")


if __name__ == "__main__":
    main()
