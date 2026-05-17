from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import pyvisa

from core.channel import ChannelSpec, MeasurementSpec
from core.port import DryRunResourceManager, PortWrapper
from keithley4200 import Device as Keithley4200SCS
from parameter_matrix import load_parameter_rows
from switching_matrix import connect_707a_matrix, normalize_matrix_config


class SweepRunner:
    """Generic sweep runner driven entirely by a MeasurementSpec.

    Replaces the measurement-specific GuardLeakageTest class. The sweep loop
    applies channels in the order they appear in spec.channels:
      1. Primary channel voltage + hold
      2. Each fixed channel voltage + hold (in declaration order)
      3. Measure all channels → one CSV row
    """

    _SMU_WRITE_TERMINATION = "\r\n"
    _SMU_READ_TERMINATION = "\n"

    def __init__(
        self,
        spec: MeasurementSpec,
        dry_run: bool = False,
        limit_rows: Optional[int] = None,
        output_path: Optional[Path] = None,
        matrix_only: bool = False,
        smu_config_only: bool = False,
        smu_zero_only: bool = False,
        smu_readback_only: bool = False,
    ) -> None:
        self.spec = spec
        self.dry_run = dry_run
        self.limit_rows = limit_rows
        self.output_path = output_path
        self.matrix_only = matrix_only
        self.smu_config_only = smu_config_only
        self.smu_zero_only = smu_zero_only
        self.smu_readback_only = smu_readback_only

        self.resource_manager = None
        self.matrix = None
        self.smu_drivers: Dict[str, Keithley4200SCS] = {}  # keyed by ch.role
        self.results: List[Dict[str, object]] = []

    # ------------------------------------------------------------------ #
    #  Public entry point                                                  #
    # ------------------------------------------------------------------ #

    def run(self) -> Path:
        if self.smu_config_only:
            self._run_smu_config_only()
            return Path()

        if self.smu_zero_only:
            self._run_smu_zero_only()
            return Path()

        if self.smu_readback_only:
            self._run_smu_readback_only()
            return Path()

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

        if self.matrix_only:
            self._run_matrix_only(rows)
            return Path()

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
    #  Diagnostic modes                                                    #
    # ------------------------------------------------------------------ #

    def _run_matrix_only(self, rows: List[Dict[str, str]]) -> None:
        print("Matrix-only mode: SMUs will not be connected or configured.")
        self._connect_matrix()
        try:
            for row_index, row in enumerate(rows, start=1):
                measured_pin = row["Measured Pin"]
                matrix_config = normalize_matrix_config(row["Matrix Config"])
                print(f"\nMatrix row {row_index}: pin={measured_pin}, matrix={matrix_config}")
                self._apply_matrix_config(matrix_config)
        finally:
            self._safe_shutdown()
        print("Matrix-only check complete. No SMU steps or measurements were executed.")

    def _run_smu_config_only(self) -> None:
        labels = ", ".join(f"{ch.label} {ch.smu}" for ch in self.spec.channels)
        print(f"SMU-config-only mode: configuring {labels}.")
        self._init_resource_manager()
        try:
            self._connect_smus()
            print(f"SMU configuration check complete for {labels}.")
        finally:
            self._safe_shutdown()

    def _run_smu_zero_only(self) -> None:
        labels = ", ".join(f"{ch.label} {ch.smu}" for ch in self.spec.channels)
        print(f"SMU-zero-only mode: applying 0 V to {labels}.")
        self._init_resource_manager()
        try:
            self._connect_smus()
            for ch in self.spec.channels:
                self._apply_voltage(ch, 0.0)
            self._wait(1.0, "SMU zero-voltage stability check")
            print("SMU zero-voltage check complete.")
        finally:
            self._safe_shutdown()

    def _run_smu_readback_only(self) -> None:
        labels = ", ".join(f"{ch.label} {ch.smu}" for ch in self.spec.channels)
        print(f"SMU-readback-only mode: testing TV/TI readback for {labels}.")
        self._init_resource_manager()
        try:
            self._connect_smus()
            for ch in self.spec.channels:
                self._apply_voltage(ch, 0.0)
            self._wait(1.0, "SMU readback stability check")
            for ch in self.spec.channels:
                self._read_smu_value(ch, "voltage")
                self._read_smu_value(ch, "current")
            print("SMU readback check complete.")
        finally:
            self._safe_shutdown()

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
        for index, ch in enumerate(self.spec.channels):
            smu = self._setup_smu(shared_port, ch, initialize_instrument=(index == 0))
            self.smu_drivers[ch.role] = smu

    def _setup_smu(
        self, port: PortWrapper, ch: ChannelSpec, initialize_instrument: bool
    ) -> Keithley4200SCS:
        smu = Keithley4200SCS()
        smu.port = port
        smu.apply_gui_parameters(
            {
                "Port": self.spec.hardware.smu_mainframe,
                "Channel": ch.smu,
                "SweepMode": "Voltage in V",
                "Range": ch.range_,
                "Speed": ch.speed,
                "Compliance": ch.compliance_a,
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
    #  Sweep loop                                                          #
    # ------------------------------------------------------------------ #

    def _run_row(self, row_index: int, row: Dict[str, str]) -> None:
        measured_pin = row["Measured Pin"]
        normalized = normalize_matrix_config(row["Matrix Config"])
        print(f"\nRow {row_index}: pin={measured_pin}, matrix={normalized}")
        self._apply_matrix_config(normalized)

        primary = next(ch for ch in self.spec.channels if ch.is_primary)
        fixed = [ch for ch in self.spec.channels if not ch.is_primary]

        for step_index, step in enumerate(primary.sweep_profile):
            # Apply primary channel
            self._apply_voltage(primary, step.voltage)
            self._wait(step.hold_s, f"{primary.label} hold at {step.voltage} V")

            # Apply each fixed channel in declaration order
            for ch in fixed:
                fixed_step = ch.sweep_profile[0]
                self._apply_voltage(ch, fixed_step.voltage)
                self._wait(fixed_step.hold_s, f"{ch.label} hold at {fixed_step.voltage} V")

            # Measure all channels
            measurements: Dict[str, Tuple[float, float]] = {}
            for ch in self.spec.channels:
                v, i = self._measure(ch)
                measurements[ch.role] = (v, i)

            # Build CSV row with dynamic column names from channel roles
            result: Dict[str, object] = {
                "Timestamp": datetime.now().isoformat(timespec="seconds"),
                "Measured Pin": measured_pin,
                "Matrix Config": normalized,
                "Step Index": step_index,
            }
            for ch in self.spec.channels:
                target_v = step.voltage if ch.is_primary else ch.sweep_profile[0].voltage
                hold_s = step.hold_s if ch.is_primary else ch.sweep_profile[0].hold_s
                v, i = measurements[ch.role]
                result[f"{ch.label} Channel"] = ch.smu
                result[f"{ch.label} Target V"] = target_v
                result[f"{ch.label} Hold s"] = hold_s
                result[f"{ch.label} Measured V"] = v
                result[f"{ch.label} Current A"] = i

            self.results.append(result)

            summary = ", ".join(
                f"{ch.label}={measurements[ch.role][1]:.3e} A"
                for ch in self.spec.channels
            )
            print(f"Step {step_index}: {summary}")

    # ------------------------------------------------------------------ #
    #  Device control helpers                                              #
    # ------------------------------------------------------------------ #

    def _apply_matrix_config(self, matrix_config: str) -> None:
        if self.matrix is None:
            raise RuntimeError("Matrix is not connected.")
        self.matrix.apply_route(matrix_config)

    def _apply_voltage(self, ch: ChannelSpec, voltage: float) -> None:
        smu = self.smu_drivers[ch.role]
        smu.value = voltage
        smu.apply()
        print(f"Applied {ch.label} {ch.smu} = {voltage:.3f} V")

    def _measure(self, ch: ChannelSpec) -> Tuple[float, float]:
        smu = self.smu_drivers[ch.role]
        smu.measure()
        measured_voltage, measured_current = smu.call()
        print(
            f"Measured {ch.label} {ch.smu}: "
            f"{float(measured_voltage):.6g} V, {float(measured_current):.6g} A"
        )
        return float(measured_voltage), float(measured_current)

    def _read_smu_value(self, ch: ChannelSpec, value_type: str) -> Optional[float]:
        smu = self.smu_drivers[ch.role]
        channel_digit = ch.smu[-1]
        cmd = f"T{'V' if value_type == 'voltage' else 'I'}{channel_digit}"
        print(f"Reading {ch.label} {ch.smu} {value_type} with {cmd}...")
        try:
            value = (
                smu.get_voltage(channel_digit)
                if value_type == "voltage"
                else smu.get_current(channel_digit)
            )
        except Exception as error:
            print(f"FAILED {cmd} for {ch.label} {ch.smu}: {error}")
            return None
        unit = "V" if value_type == "voltage" else "A"
        print(f"SUCCESS {cmd} for {ch.label} {ch.smu}: {float(value):.6g} {unit}")
        return float(value)

    def _wait(self, seconds: float, reason: str) -> None:
        if seconds <= 0.0:
            return
        print(f"Waiting {seconds:.1f} s: {reason}")
        if not self.dry_run:
            time.sleep(seconds)

    def _safe_shutdown(self) -> None:
        print("\nSafe shutdown: powering off SMUs, then opening matrix.")
        for ch in self.spec.channels:
            smu = self.smu_drivers.get(ch.role)
            if smu is None:
                continue
            try:
                smu.poweroff()
                smu.unconfigure()
                smu.deinitialize()
                print(f"Powered off {ch.label} {ch.smu}.")
            except Exception as error:
                print(f"WARNING: Could not fully shut down {ch.label} {ch.smu}: {error}")

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
        return Path(f"{prefix}_{timestamp}.csv")
