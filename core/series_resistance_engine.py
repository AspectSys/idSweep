from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import pyvisa

from core.channel import HardwareConfig
from core.port import DryRunResourceManager, PortWrapper
from keithley4200 import Device as Keithley4200SCS
from parameter_matrix import load_parameter_rows
from switching_matrix import connect_707a_matrix, normalize_matrix_config


# ------------------------------------------------------------------ #
#  Spec dataclasses                                                    #
# ------------------------------------------------------------------ #

@dataclass(frozen=True)
class SrStep:
    """Mode-neutral sweep step: setpoint is amps for current-source channels, volts for voltage-source channels."""
    setpoint: float
    hold_s: float


@dataclass(frozen=True)
class SrSmuSpec:
    """Unified SMU spec for Series Resistance — covers both current-source and voltage-source channels."""
    role: str
    smu: str
    sweep_mode: str       # "Voltage in V" or "Current in A"
    compliance: float     # amps for voltage mode; volts for current mode
    speed: str
    range_: str
    average: int
    sweep_profile: Tuple[SrStep, ...]


@dataclass(frozen=True)
class SeriesResistanceSpec:
    test_name: str
    excel_sheet: str
    hardware: HardwareConfig
    cathode: SrSmuSpec         # exactly 1, sweep_mode="Current in A", 2 profile entries
    fixed_smus: Tuple[SrSmuSpec, ...]
    workbook_path: Path


# ------------------------------------------------------------------ #
#  Spec loader                                                         #
# ------------------------------------------------------------------ #

def load_series_resistance_spec(
    path: Path,
    workbook_path: Optional[Path] = None,
) -> SeriesResistanceSpec:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    hardware = HardwareConfig(
        switch_matrix=data["instruments"]["switch_matrix"],
        smu_mainframe=data["instruments"]["smu_mainframe"],
        matrix_settling_s=data["instruments"].get("matrix_settling_s", 0.3),
    )

    all_smus: List[SrSmuSpec] = []
    for key, cfg in data["smus"].items():
        role, smu = key.rsplit("_", 1)
        is_current_mode = cfg.get("sweep_mode", "Voltage in V") == "Current in A"
        sweep_profile = tuple(
            SrStep(
                setpoint=s["current"] if is_current_mode else s["voltage"],
                hold_s=s["hold_s"],
            )
            for s in cfg["sweep_profile"]
        )
        all_smus.append(
            SrSmuSpec(
                role=role,
                smu=smu,
                sweep_mode=cfg.get("sweep_mode", "Voltage in V"),
                compliance=float(cfg["compliance_A"]),
                speed=cfg["speed"],
                range_=cfg.get("range", "Auto"),
                average=int(cfg.get("average", 1)),
                sweep_profile=sweep_profile,
            )
        )

    cathodes = [s for s in all_smus if s.sweep_mode == "Current in A"]
    fixed = [s for s in all_smus if s.sweep_mode != "Current in A"]

    if len(cathodes) != 1:
        raise ValueError(
            f"Expected exactly 1 SMU with sweep_mode 'Current in A', found {len(cathodes)}."
        )
    cathode = cathodes[0]
    if len(cathode.sweep_profile) != 2:
        raise ValueError(
            f"Cathode '{cathode.role}' sweep_profile must have exactly 2 current points, "
            f"found {len(cathode.sweep_profile)}."
        )

    if workbook_path is None:
        json_workbook = data["instruments"].get("workbook")
        if json_workbook is not None:
            workbook_path = Path(json_workbook)
        else:
            from parameter_matrix import WORKBOOK_PATH as _DEFAULT_WORKBOOK  # noqa: PLC0415
            workbook_path = _DEFAULT_WORKBOOK

    return SeriesResistanceSpec(
        test_name=data["test_name"],
        excel_sheet=data["excel_sheet"],
        hardware=hardware,
        cathode=cathode,
        fixed_smus=tuple(fixed),
        workbook_path=workbook_path,
    )


# ------------------------------------------------------------------ #
#  Runner                                                              #
# ------------------------------------------------------------------ #

class SeriesResistanceRunner:
    """Series Resistance measurement runner.

    For each row in the workbook the runner:
      1. Applies the matrix configuration.
      2. Sets all fixed voltage SMUs to 0 V.
      3. Sources two current points through the cathode SMU and measures V & I at each.
      4. Computes series resistance: Rs = ((v2-v1) + 2*ln(i2/i1)*26e-3) / (i2-i1)
      5. Appends one CSV row with both measurement points and Rs.
    """

    _SMU_WRITE_TERMINATION = "\r\n"
    _SMU_READ_TERMINATION = "\n"

    def __init__(
        self,
        spec: SeriesResistanceSpec,
        dry_run: bool = False,
        limit_rows: Optional[int] = None,
        output_path: Optional[Path] = None,
    ) -> None:
        self.spec = spec
        self.dry_run = dry_run
        self.limit_rows = limit_rows
        self.output_path = output_path

        self.resource_manager = None
        self.matrix = None
        self.cathode_smu: Optional[Keithley4200SCS] = None
        self.fixed_smu_drivers: Dict[str, Keithley4200SCS] = {}  # keyed by SrSmuSpec.role
        self.results: List[Dict[str, object]] = []

    # ------------------------------------------------------------------ #
    #  Public entry point                                                  #
    # ------------------------------------------------------------------ #

    def run(self) -> Path:
        rows = load_parameter_rows(
            self.spec.workbook_path,
            self.spec.excel_sheet,
            required_columns=["Matrix Config", "Measured Pin"],
        )
        if self.limit_rows is not None:
            rows = rows[: self.limit_rows]

        if not rows:
            raise ValueError(
                f"No rows found in sheet '{self.spec.excel_sheet}' of {self.spec.workbook_path}"
            )

        print(f"Loaded {len(rows)} row(s) from {self.spec.workbook_path} [{self.spec.excel_sheet}]")

        self._connect_instruments()
        try:
            for row_index, row in enumerate(rows, start=1):
                self._run_row(row_index, row)
        finally:
            self._safe_shutdown()

        output_path = self.output_path or self._default_output_path()
        pd.DataFrame(self.results).to_csv(output_path, index=False)
        print(f"Saved {len(self.results)} measurement row(s) to {output_path}")
        return output_path

    # ------------------------------------------------------------------ #
    #  Connection                                                          #
    # ------------------------------------------------------------------ #

    def _init_resource_manager(self) -> None:
        if self.resource_manager is None:
            self.resource_manager = (
                DryRunResourceManager() if self.dry_run else pyvisa.ResourceManager()
            )

    def _connect_instruments(self) -> None:
        self._connect_matrix()
        self._connect_smus()

    def _connect_matrix(self) -> None:
        self._init_resource_manager()
        self.matrix = connect_707a_matrix(
            self.resource_manager,
            address=self.spec.hardware.switch_matrix,
            settling_seconds=self.spec.hardware.matrix_settling_s,
            dry_run=self.dry_run,
        )

    def _connect_smus(self) -> None:
        if self.resource_manager is None:
            raise RuntimeError("Resource manager is not initialized.")

        smu_resource = self.resource_manager.open_resource(self.spec.hardware.smu_mainframe)
        shared_port = PortWrapper(
            smu_resource,
            write_termination=self._SMU_WRITE_TERMINATION,
            read_termination=self._SMU_READ_TERMINATION,
            timeout_ms=10000,
        )

        # Cathode — first SMU on the shared port: full connect + initialize
        self.cathode_smu = self._setup_smu(shared_port, self.spec.cathode, initialize_instrument=True)

        # Fixed voltage SMUs — subsequent: skip connect/initialize
        for fixed in self.spec.fixed_smus:
            smu = self._setup_smu(shared_port, fixed, initialize_instrument=False)
            self.fixed_smu_drivers[fixed.role] = smu

    def _setup_smu(
        self, port: PortWrapper, ch: SrSmuSpec, initialize_instrument: bool
    ) -> Keithley4200SCS:
        smu = Keithley4200SCS()
        smu.port = port
        smu.apply_gui_parameters(
            {
                "Port": self.spec.hardware.smu_mainframe,
                "Channel": ch.smu,
                "SweepMode": ch.sweep_mode,
                "Range": ch.range_,
                "Speed": ch.speed,
                "Compliance": ch.compliance,
                "Average": str(ch.average),
            }
        )
        if initialize_instrument:
            smu.connect()
            smu.initialize()
        else:
            smu.handle_card_name()
            smu.command_set = "US"
        smu.configure()
        return smu

    # ------------------------------------------------------------------ #
    #  Measurement loop                                                    #
    # ------------------------------------------------------------------ #

    def _run_row(self, row_index: int, row: Dict[str, str]) -> None:
        measured_pin = row["Measured Pin"]
        normalized = normalize_matrix_config(row["Matrix Config"])
        print(f"\nRow {row_index}: pin={measured_pin}, matrix={normalized}")

        # 1. Apply matrix routing
        if self.matrix is None:
            raise RuntimeError("Matrix is not connected.")
        self.matrix.apply_route(normalized)

        # 2. Set all fixed voltage SMUs to their configured setpoint
        for fixed in self.spec.fixed_smus:
            smu = self.fixed_smu_drivers[fixed.role]
            setpoint = fixed.sweep_profile[0].setpoint
            smu.value = setpoint
            smu.apply()
            print(f"Applied {fixed.role} {fixed.smu} = {setpoint:.3f} V")

        # 3. Point 1 and Point 2: apply current setpoint, measure V & I
        step1, step2 = self.spec.cathode.sweep_profile
        v1, i1 = self._apply_and_measure(step1.setpoint, step1.hold_s)
        v2, i2 = self._apply_and_measure(step2.setpoint, step2.hold_s)

        # 5. Calculate series resistance
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

        # 6. Save result
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

    def _apply_and_measure(self, setpoint: float, hold_s: float) -> Tuple[float, float]:
        """Apply a setpoint to the cathode SMU, hold, then measure V and I."""
        smu = self.cathode_smu
        smu.value = setpoint
        smu.apply()
        print(f"Applied {self.spec.cathode.role} {self.spec.cathode.smu} = {setpoint:.4g} A")

        self._wait(hold_s, f"{self.spec.cathode.role} hold at {setpoint:.4g} A")

        smu.measure()
        result = smu.call()
        v, i = float(result[0]), float(result[1])
        print(f"Measured {self.spec.cathode.role} {self.spec.cathode.smu}: {v:.6g} V, {i:.6g} A")
        return v, i

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _wait(self, seconds: float, reason: str) -> None:
        if seconds <= 0.0:
            return
        print(f"Waiting {seconds:.1f} s: {reason}")
        if not self.dry_run:
            time.sleep(seconds)

    def _safe_shutdown(self) -> None:
        print("\nSafe shutdown: powering off SMUs, then opening matrix.")

        all_smus: List[Tuple[str, Optional[Keithley4200SCS]]] = [
            (self.spec.cathode.smu, self.cathode_smu)
        ]
        for fixed in self.spec.fixed_smus:
            all_smus.append((fixed.smu, self.fixed_smu_drivers.get(fixed.role)))

        for smu_name, smu in all_smus:
            if smu is None:
                continue
            try:
                smu.poweroff()
                smu.unconfigure()
                smu.deinitialize()
                print(f"Powered off {smu_name}.")
            except Exception as error:
                print(f"WARNING: Could not fully shut down {smu_name}: {error}")

        if self.matrix is not None:
            try:
                self.matrix.shutdown()
            except Exception as error:
                print(f"WARNING: Could not open matrix crosspoints during shutdown: {error}")

        if self.resource_manager is not None and hasattr(self.resource_manager, "close"):
            try:
                self.resource_manager.close()
            except Exception as error:
                print(f"WARNING: Could not close VISA resource manager: {error}")

    def _default_output_path(self) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = self.spec.test_name.replace(" ", "")
        output_dir = Path("results")
        output_dir.mkdir(exist_ok=True)
        return output_dir / f"{prefix}_{timestamp}.csv"
