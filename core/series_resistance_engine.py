from __future__ import annotations

import math
from datetime import datetime
from typing import Dict, Tuple

from core.engine import SweepRunner
from switching_matrix import normalize_matrix_config


class SeriesResistanceRunner(SweepRunner):
    """Extends SweepRunner for Series Resistance.

    Overrides _run_row to collect both current setpoints, compute Rs from
    the two resulting V & I measurements, and emit one CSV row per pin
    (instead of one per sweep step).
    """

    def _run_row(self, row_index: int, row: Dict[str, str]) -> None:
        measured_pin = row["Measured Pin"]
        normalized = normalize_matrix_config(row["Matrix Config"])
        print(f"\nRow {row_index}: pin={measured_pin}, matrix={normalized}")

        self._apply_matrix_config(normalized)

        primary = next(ch for ch in self.spec.channels if ch.is_primary)
        fixed = [ch for ch in self.spec.channels if not ch.is_primary]

        # Apply fixed channels once at their configured setpoint
        for ch in fixed:
            fixed_step = ch.sweep_profile[0]
            self._apply_voltage(ch, fixed_step.voltage)
            self._wait(fixed_step.hold_s, f"{ch.label} hold at {fixed_step.voltage} V")

        # Measure at each current setpoint
        step_measurements: list[Tuple[float, float]] = []
        for step in primary.sweep_profile:
            self._apply_voltage(primary, step.voltage)
            self._wait(step.hold_s, f"{primary.label} hold at {step.voltage} A")
            step_measurements.append(self._measure(primary))

        (v1, i1), (v2, i2) = step_measurements

        try:
            rs = ((v2 - v1) + 2.0 * math.log(i2 / i1) * 26e-3) / (i2 - i1)
            print(
                f"Rs = {rs:.4f} Ω  "
                f"(v1={v1:.6g} V, i1={i1:.6g} A, v2={v2:.6g} V, i2={i2:.6g} A)"
            )
        except (ZeroDivisionError, ValueError):
            rs = float("nan")
            print(
                f"WARNING: Rs calculation failed — invalid measurements "
                f"(v1={v1:.6g} V, i1={i1:.6g} A, v2={v2:.6g} V, i2={i2:.6g} A)"
            )

        self.results.append(
            {
                "Timestamp": datetime.now().isoformat(timespec="seconds"),
                "Measured Pin": measured_pin,
                "Matrix Config": normalized,
                "V1_V": v1,
                "I1_A": i1,
                "V2_V": v2,
                "I2_A": i2,
                "Rs_Ohm": rs,
            }
        )
