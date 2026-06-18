from __future__ import annotations

import itertools
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import pyvisa

from core.channel import ChannelSpec, MeasurementSpec, SweepStep
from core.port import DryRunResourceManager, PortWrapper
from keithley4200 import Device as Keithley4200SCS
from parameter_matrix import load_parameter_rows
from switching_matrix import connect_707a_matrix, normalize_matrix_config


class SweepRunner:
    """Generic sweep runner driven entirely by a MeasurementSpec.

    Each channel's sweep_profile is a loop axis. For every Excel row the runner
    sweeps the Cartesian product of all profiles in declaration order, with the
    first channel as the outermost (slowest-varying) loop and the last channel
    as the innermost. At each combination it:
      1. Applies every channel's voltage + hold (in declaration order)
      2. Measures all channels → one CSV row
    A channel with a single sweep_profile entry is simply held at that value, so
    a config where only one channel is multi-step reproduces a plain 1-D sweep.
    """

    _SMU_WRITE_TERMINATION = "\r\n"
    _SMU_READ_TERMINATION = "\n"

    def __init__(
        self,
        spec: MeasurementSpec,
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
        self.smu_drivers: Dict[str, Keithley4200SCS] = {}  # keyed by ch.role
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
        smu_resource.clear()  # GPIB Device Clear: flushes 4200 state between tests
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
                "SweepMode": ch.sweep_mode,
                "Range": ch.range_,
                "Speed": ch.speed,
                "Compliance": ch.compliance_a,
                "Average": str(ch.average),
            }
        )
        if initialize_instrument:
            smu.device_communication.pop(smu.identifier, None)
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

    @staticmethod
    def _build_combos(
        channels: Tuple[ChannelSpec, ...],
    ) -> List[Tuple[Tuple[int, SweepStep], ...]]:
        """Enumerate the sweep nest as a Cartesian product of channel profiles.

        Returns the list of combinations in iteration order. Each combination is
        a tuple aligned by index with ``channels``; element ``k`` is the
        ``(step_index, step)`` pair for channel ``k`` at that point in the nest.

        ``itertools.product`` advances the first iterable slowest, so the first
        channel is the outermost loop and the last channel the innermost — i.e.
        JSON declaration order == loop nesting order.
        """
        indexed = [list(enumerate(ch.sweep_profile)) for ch in channels]
        return list(itertools.product(*indexed))

    def _run_row(self, row_index: int, row: Dict[str, str]) -> None:
        measured_pin = row["Measured Pin"]
        normalized = normalize_matrix_config(row["Matrix Config"])
        print(f"\nRow {row_index}: pin={measured_pin}, matrix={normalized}")
        self._apply_matrix_config(normalized)

        channels = self.spec.channels
        for combo_index, combo in enumerate(self._build_combos(channels)):
            # Apply every channel in declaration order. Holds are honored on
            # every combination (no change-detection) so the timing is exactly
            # what the JSON declares.
            for ch, (_, step) in zip(channels, combo):
                self._apply_voltage(ch, step.voltage)
                self._wait(step.hold_s, f"{ch.label} hold at {step.voltage} V")

            # Measure all channels
            measurements: Dict[str, Tuple[float, float]] = {}
            for ch in channels:
                v, i = self._measure(ch)
                measurements[ch.role] = (v, i)

            # Build CSV row with dynamic column names from channel roles.
            # "Step Index" is the flattened combo index; for a single-axis sweep
            # it equals that channel's step index (see report.py).
            result: Dict[str, object] = {
                "Timestamp": datetime.now().isoformat(timespec="seconds"),
                "Measured Pin": measured_pin,
                "Matrix Config": normalized,
                "Step Index": combo_index,
            }
            for ch, (step_index, step) in zip(channels, combo):
                v, i = measurements[ch.role]
                result[f"{ch.label} Channel"] = ch.smu
                result[f"{ch.label} Step Index"] = step_index
                result[f"{ch.label} Target V"] = step.voltage
                result[f"{ch.label} Hold s"] = step.hold_s
                result[f"{ch.label} Measured V"] = v
                result[f"{ch.label} Current A"] = i

            self.results.append(result)

            summary = ", ".join(
                f"{ch.label}={measurements[ch.role][1]:.3e} A"
                for ch in channels
            )
            print(f"Step {combo_index}: {summary}")

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
        unit = "A" if ch.sweep_mode == "Current in A" else "V"
        print(f"Applied {ch.label} {ch.smu} = {voltage:.3f} {unit}")

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
        output_dir = Path("results")
        output_dir.mkdir(exist_ok=True)
        return output_dir / f"{prefix}_{timestamp}.csv"
