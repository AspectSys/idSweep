"""Example: run a probe plan against an Accretech UF series prober.

This mirrors the target API described in the design doc. It is intentionally minimal:
load a wafer, iterate the dies in an ``.mdf`` probe plan, and (where you see the comment)
perform your own electrical measurements.

Run with::

    uv run python examples/run_probe_plan.py GPIB0::1::INSTR plan.mdf
"""

from __future__ import annotations

import argparse
import logging

import pyvisa

from accretech_prober import AccretechProber, ProberController
from accretech_prober.parsers import read_mdf_probe_plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an Accretech probe plan.")
    parser.add_argument("visa_address", help="VISA address, e.g. GPIB0::1::INSTR")
    parser.add_argument("probe_plan", help="Path to the .mdf control map file")
    parser.add_argument("--cassette", type=int, default=1, help="Cassette id to load from")
    parser.add_argument("--slot", type=int, default=1, help="Slot id to load")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    rm = pyvisa.ResourceManager()
    visa_resource = rm.open_resource(args.visa_address)
    prober_hw = AccretechProber(visa_resource)
    controller = ProberController(prober_hw)

    try:
        controller.initialize_system()
        controller.check_and_sense_wafers()
        #controller.load_wafer(cassette=args.cassette, slot=args.slot)

        for die_x, die_y in read_mdf_probe_plan(args.probe_plan):
            controller.move_to_die(die_x, die_y)

            # ----------------------------------------------------------------
            # Perform your SMU electrical measurements here, e.g.:
            #     current = my_smu.measure_current()
            # ----------------------------------------------------------------

        #controller.unload_wafer()
    except Exception as exc:  # noqa: BLE001
        controller.abort_and_safe_state()
        logging.getLogger(__name__).error("Run aborted: %s", exc)
    finally:
        controller.separate()
        prober_hw.close()


if __name__ == "__main__":
    main()
