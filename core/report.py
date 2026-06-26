from __future__ import annotations

import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

# Hardcoded test limits matching the existing post-processing script:
# (low, high, scale_to_display_unit, fail_bin)
_LIMITS: Dict[str, Tuple[float, float, float, int]] = {
    "guard_0v": (0.0, 0.1, 1e9,  7),   # A  -> nA
    "guard_2v": (0.0, 0.1, 1e9,  7),   # A  -> nA
    "dc_rev":   (0.0, 0.1, 1e3,  8),   # A  -> mA  (at -1.25 V)
    "dc_fwd":   (0.0, 0.1, 1e12, 8),   # A  -> pA  (at  2.5 V)
    "rs":       (0.0, 0.1, 1.0,  5),   # Ohm unchanged
    "cap":      (0.0, 0.1, 1e12, 6),   # F  -> pF
}


class ReportWriter:
    """Transform the four measurement CSVs into the company result txt format."""

    def __init__(
        self,
        guard_leakage_csv: Path,
        dark_current_csv: Path,
        capacitance_csv: Path,
        series_resistance_csv: Path,
        run_info: Dict[str, str],
    ) -> None:
        self.gl  = pd.read_csv(guard_leakage_csv,     sep=";", decimal=",")
        self.dc  = pd.read_csv(dark_current_csv,      sep=";", decimal=",")
        self.cap = pd.read_csv(capacitance_csv,       sep=";", decimal=",")
        self.rs  = pd.read_csv(series_resistance_csv, sep=";", decimal=",")
        self.run_info = run_info
        self._soft_bin = 1

    def run(self, output_path: Optional[Path] = None) -> Path:
        lines: List[str] = list(self._header().splitlines(keepends=True))

        # Guard leakage -- device-level, 2 rows (step 0 = 0 V, step 1 = 2.5 V).
        # NOTE: the engine's global "Step Index" is the flattened sweep-nest combo
        # index. It equals a single channel's step index only while at most one
        # channel is multi-step (the case for every current config). A genuine
        # multi-axis sweep would make these "Step Index == 0/1" filters select a
        # slice of the nest; switch to per-channel "<Label> Step Index" columns then.
        gl_0v = float(self.gl.loc[self.gl["Step Index"] == 0, "Guard Current A"].iloc[0])
        gl_2v = float(self.gl.loc[self.gl["Step Index"] == 1, "Guard Current A"].iloc[0])

        # Per-pin lookup tables indexed by Measured Pin
        dc0     = self.dc[self.dc["Step Index"] == 0].set_index("Measured Pin")
        dc1     = self.dc[self.dc["Step Index"] == 1].set_index("Measured Pin")
        rs_idx  = self.rs.set_index("Measured Pin")
        cap_idx = self.cap.set_index("Measured Pin")

        # Pin order from dark current step 0
        pins = self.dc[self.dc["Step Index"] == 0]["Measured Pin"].tolist()

        for pin_seq, pin in enumerate(pins, start=1):
            if pin_seq == 1:
                lines += self._entry(1, 1, "Guard Leakage 0V [nA]",   gl_0v, _LIMITS["guard_0v"])
                lines += self._entry(1, 2, "Guard Leakage 2.5V [nA]", gl_2v, _LIMITS["guard_2v"])

            dc_rev  = float(dc0.at[pin, "Anode Current A"])
            dc_fwd  = float(dc1.at[pin, "Anode Current A"])
            rs_val  = float(rs_idx.at[pin,  "Rs Ohm"])        if pin in rs_idx.index  else float("nan")
            cap_val = float(cap_idx.at[pin, "Capacitance F"])  if pin in cap_idx.index else float("nan")

            lines += self._entry(pin_seq, 3, f"Pin {pin} Dark Current -1.25V [mA]", dc_rev,  _LIMITS["dc_rev"])
            lines += self._entry(pin_seq, 4, f"Pin {pin} Dark Current 2.5V [pA]",   dc_fwd,  _LIMITS["dc_fwd"])
            lines += self._entry(pin_seq, 5, f"Pin {pin} Series Resistance [Ohm]",  rs_val,  _LIMITS["rs"])
            lines += self._entry(pin_seq, 6, f"Pin {pin} Capacitance [pF]",         cap_val, _LIMITS["cap"])

        lines += self._footer().splitlines(keepends=True)

        if output_path is None:
            device_no = int(self.run_info.get("device_no", 0))
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            wafer_id = self.run_info.get("wafer_id", "")
            pos_x = self.run_info.get("tester_device_pos_x", "")
            pos_y = self.run_info.get("tester_device_pos_y", "")
            output_path = Path("results") / f"result_{ts}_wafer{wafer_id}_x{pos_x}_y{pos_y}_dev{device_no:03d}.txt"

        output_path.parent.mkdir(exist_ok=True)
        output_path.write_text("".join(lines), encoding="utf-8")
        print(f"Report written to {output_path}")
        return output_path

    # ------------------------------------------------------------------ #

    @staticmethod
    def _dec(value: object) -> str:
        """Format a value with a comma decimal separator for the result txt."""
        return str(value).replace(".", ",")

    def _header(self) -> str:
        ri = self.run_info
        now = datetime.datetime.now().strftime("%d.%m.%y %H:%M:%S")
        return (
            f"Product Name\t{ri.get('product_name', '')}\n"
            f"Time\t{now}\n"
            f"Operator Name\t{ri.get('operator', '')}\n"
            f"Test Station\t{ri.get('test_station', 'aS 2')}\n"
            f"LOT ID\t{ri.get('lot_id', '')}\n"
            f"Wafer ID\t{ri.get('wafer_id', '')}\n"
            f"Tester Device Pos X\t{ri.get('tester_device_pos_x', '')}\n"
            f"Tester Device Pos Y\t{ri.get('tester_device_pos_y', '')}\n"
            f"Temperature\t{self._dec(ri.get('temperature', ''))}\n"
            f"Device No\t{ri.get('device_no', '')}\n"
        )

    def _entry(
        self,
        pin_seq: int,
        test_num: int,
        name: str,
        raw_value: float,
        limits: Tuple[float, float, float, int],
    ) -> List[str]:
        low, high, scale, fail_bin = limits
        value = raw_value * scale
        # NaN comparisons always return False -- correctly treated as fail
        pass_fail = 1 if low <= value <= high else 0
        bin_ = 1 if pass_fail else fail_bin
        if bin_ > self._soft_bin:
            self._soft_bin = bin_
        line = (
            f"{pin_seq:02d}{test_num:02d}\t{name}\t{bin_}\t{pass_fail}\t"
            f"{self._dec(low)}\t{self._dec(value)}\t{self._dec(high)}\n"
        )
        return line.splitlines(keepends=True)

    def _footer(self) -> str:
        try:
            all_ts = pd.to_datetime(pd.concat([
                self.gl["Timestamp"],
                self.dc["Timestamp"],
                self.cap["Timestamp"],
                self.rs["Timestamp"],
            ]))
            elapsed = int((all_ts.max() - all_ts.min()).total_seconds())
            total_time = str(datetime.timedelta(seconds=elapsed))
        except Exception:
            total_time = ""
        passed = 1 if self._soft_bin == 1 else 0
        return (
            f"Total Time\t{total_time}\n"
            f"Soft Bin\t{self._soft_bin}\n"
            f"Pass\t{passed}\n"
        )
