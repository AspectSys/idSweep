"""Top-level wafer sweep: drive an Accretech prober over a probe plan, running the
full per-device measurement (guard leakage, dark current, capacitance, series
resistance) + report at each die.

    prober -> move to die -> run_device (run_all) -> next die

The prober API under ``accretech/`` is used as-is (standalone package, no changes).
For a single-device run *without* a prober, use ``run_all.py`` instead.

Run with::

    uv run python run_wafer.py GPIB0::1::INSTR accretech/examples/DF.mdf
    uv run python run_wafer.py GPIB0::1::INSTR accretech/examples/DF.mdf --dry-run --limit-rows 1
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Tuple

from accretech_prober.parsers import read_mdf_probe_plan

from run_all import device_output_dir, run_device

logger = logging.getLogger(__name__)


class NullProberController:
    """No-op stand-in for ProberController used by ``--dry-run`` (no GPIB).

    Implements only the methods the wafer loop calls, logging instead of moving,
    so the whole flow can be exercised with no prober attached.
    """

    def initialize_system(self) -> None:
        logger.info("[dry-run] prober initialize_system()")

    def check_and_sense_wafers(self) -> None:
        logger.info("[dry-run] prober check_and_sense_wafers()")

    def move_to_die(self, x: int, y: int, contact: bool = True) -> None:
        logger.info("[dry-run] would move to die (%d, %d), contact=%s", x, y, contact)

    def separate(self) -> None:
        logger.info("[dry-run] prober separate()")

    def abort_and_safe_state(self) -> None:
        logger.info("[dry-run] prober abort_and_safe_state()")


class _NullProberHardware:
    """Stub for the AccretechProber handle so ``close()`` is a no-op in dry-run."""

    def close(self) -> None:
        logger.info("[dry-run] prober close()")


def _make_prober(dry_run: bool, visa_address: str) -> Tuple[object, object]:
    """Return ``(prober_hw, controller)`` — mocked in dry-run, real otherwise."""
    if dry_run:
        return _NullProberHardware(), NullProberController()

    import pyvisa
    from accretech_prober import AccretechProber, ProberController

    rm = pyvisa.ResourceManager()
    visa_resource = rm.open_resource(visa_address)
    prober_hw = AccretechProber(visa_resource)
    return prober_hw, ProberController(prober_hw)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a wafer probe plan with per-die measurements.")
    parser.add_argument("visa_address", help="Prober VISA address, e.g. GPIB0::1::INSTR")
    parser.add_argument("probe_plan", type=Path, help="Path to the .mdf control map file")
    parser.add_argument("--run-info", type=Path, default=Path("settings/run_info.json"), help="Base device metadata JSON.")
    parser.add_argument("--results-root", type=Path, default=Path("results"), help="Root folder for outputs.")
    parser.add_argument("--limit-rows", type=int, default=None, help="Only run the first N Excel rows per test.")
    parser.add_argument("--dry-run", action="store_true", help="Mock the prober and the SMUs (no hardware).")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    base_run_info = (
        json.loads(args.run_info.read_text(encoding="utf-8-sig")) if args.run_info.exists() else {}
    )

    prober_hw, controller = _make_prober(args.dry_run, args.visa_address)

    try:
        controller.initialize_system()
        controller.check_and_sense_wafers()

        for device_no, (die_x, die_y) in enumerate(read_mdf_probe_plan(args.probe_plan), start=1):
            print(f"\n{'#' * 60}\nDevice {device_no}: die ({die_x}, {die_y})\n{'#' * 60}")
            controller.move_to_die(die_x, die_y)  # contact=True by default

            run_info = {
                **base_run_info,
                "tester_device_pos_x": str(die_x),
                "tester_device_pos_y": str(die_y),
                "device_no": str(device_no),
            }
            output_dir = device_output_dir(args.results_root, run_info)
            report_path = run_device(
                run_info, dry_run=args.dry_run, limit_rows=args.limit_rows, output_dir=output_dir
            )
            print(f"Device {device_no} report: {report_path}")
    except Exception as exc:  # noqa: BLE001
        controller.abort_and_safe_state()
        logger.error("Wafer run aborted: %s", exc)
        raise
    finally:
        controller.separate()
        prober_hw.close()


if __name__ == "__main__":
    main()
