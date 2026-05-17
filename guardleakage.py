from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import pyvisa

from keithley4200 import Device as Keithley4200SCS
from parameter_matrix import WORKBOOK_PATH, load_parameter_rows
from switching_matrix import MATRIX_SETTLING_SECONDS, SWITCH_PORT_ADDRESS, connect_707a_matrix, normalize_matrix_config


GUARD_LEAKAGE_SHEET = "GuardLeakage"
GUARD_LEAKAGE_COLUMNS = ["Matrix Config", "Measured Pin"]
SMU_PORT_ADDRESS = "GPIB0::17::INSTR"
SMU_WRITE_TERMINATION = "\r\n"
SMU_READ_TERMINATION = "\n"
RESULTS_PREFIX = "GuardLeakage"


@dataclass(frozen=True)
class SweepStep:
    voltage: float
    hold_seconds: float


@dataclass(frozen=True)
class GuardLeakageConfig:
    workbook_path: Path = WORKBOOK_PATH
    sheet_name: str = GUARD_LEAKAGE_SHEET
    smu_port_address: str = SMU_PORT_ADDRESS
    switch_port_address: str = SWITCH_PORT_ADDRESS
    output_path: Optional[Path] = None
    matrix_settling_seconds: float = MATRIX_SETTLING_SECONDS
    limit_rows: Optional[int] = None
    dry_run: bool = False
    matrix_only: bool = False
    smu_config_only: bool = False
    smu_zero_only: bool = False
    smu_readback_only: bool = False


class PortWrapper:
    """Wrap PyVISA resources with the port API expected by the SweepMe drivers."""

    def __init__(self, resource, write_termination: str, read_termination: Optional[str] = None, timeout_ms: int = 10000) -> None:
        self.port = resource
        self.port.write_termination = write_termination
        self.port.read_termination = read_termination
        self.port.timeout = timeout_ms

    def write(self, command: str) -> None:
        self.port.write(command)

    def read(self) -> str:
        return self.port.read()

    def query(self, command: str) -> str:
        return self.port.query(command)


class DryRunPort:
    def __init__(self, name: str) -> None:
        self.name = name
        self.timeout = 10000
        self.write_termination = ""
        self.read_termination = None

    def clear(self) -> None:
        print(f"[DRY-RUN] {self.name}: clear")

    def write(self, command: str) -> None:
        print(f"[DRY-RUN] {self.name}: write {command}")

    def read(self) -> str:
        print(f"[DRY-RUN] {self.name}: read")
        return ""

    def query(self, command: str) -> str:
        print(f"[DRY-RUN] {self.name}: query {command}")
        if command.startswith("TV"):
            return "TV 0.000000E+00"
        if command.startswith("TI"):
            return "TI 0.000000E+00"
        return ""


class DryRunResourceManager:
    def open_resource(self, address: str) -> DryRunPort:
        print(f"[DRY-RUN] open_resource {address}")
        return DryRunPort(address)

    def close(self) -> None:
        print("[DRY-RUN] resource manager close")


CATHODE_SWEEP = [
    SweepStep(voltage=0.0, hold_seconds=1.0),
    SweepStep(voltage=2.5, hold_seconds=8.0),
]

GUARD_SWEEP = [
    SweepStep(voltage=0.0, hold_seconds=8.0),
]


class GuardLeakageTest:
    def __init__(self, config: GuardLeakageConfig) -> None:
        self.config = config
        self.resource_manager = None
        self.matrix = None
        self.cathode_smu = None
        self.guard_smu = None
        self.results: List[Dict[str, object]] = []

    def run(self) -> Path:
        if self.config.smu_config_only:
            self._run_smu_config_only()
            return Path()

        if self.config.smu_zero_only:
            self._run_smu_zero_only()
            return Path()

        if self.config.smu_readback_only:
            self._run_smu_readback_only()
            return Path()

        rows = load_parameter_rows(
            self.config.workbook_path,
            self.config.sheet_name,
            required_columns=GUARD_LEAKAGE_COLUMNS,
        )
        if self.config.limit_rows is not None:
            rows = rows[: self.config.limit_rows]

        if not rows:
            raise ValueError("No guard leakage rows found in the workbook.")

        print(f"Loaded {len(rows)} guard leakage row(s) from {self.config.workbook_path}")

        if self.config.matrix_only:
            self._run_matrix_only(rows)
            return Path()

        self._connect_instruments()

        try:
            for row_index, row in enumerate(rows, start=1):
                self._run_row(row_index, row)
        finally:
            self._safe_shutdown()

        output_path = self.config.output_path or default_output_path()
        pd.DataFrame(self.results).to_csv(output_path, index=False)
        print(f"Saved {len(self.results)} measurement row(s) to {output_path}")
        return output_path

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
        print("SMU-config-only mode: matrix will not be connected, no voltage will be applied, and no measurements will run.")

        if self.config.dry_run:
            self.resource_manager = DryRunResourceManager()
        else:
            self.resource_manager = pyvisa.ResourceManager()

        try:
            self._connect_smus()
            print("SMU configuration check complete for Cathode SMU1 and Guard SMU3.")
        finally:
            self._safe_shutdown()

    def _run_smu_zero_only(self) -> None:
        print("SMU-zero-only mode: matrix will not be connected and no measurements will run.")

        if self.config.dry_run:
            self.resource_manager = DryRunResourceManager()
        else:
            self.resource_manager = pyvisa.ResourceManager()

        try:
            self._connect_smus()
            self._apply_voltage(self.cathode_smu, "Cathode SMU1", 0.0)
            self._apply_voltage(self.guard_smu, "Guard SMU3", 0.0)
            self._wait(1.0, "SMU zero-voltage stability check")
            print("SMU zero-voltage check complete for Cathode SMU1 and Guard SMU3.")
        finally:
            self._safe_shutdown()

    def _run_smu_readback_only(self) -> None:
        print("SMU-readback-only mode: matrix will not be connected and no sweep will run.")

        if self.config.dry_run:
            self.resource_manager = DryRunResourceManager()
        else:
            self.resource_manager = pyvisa.ResourceManager()

        try:
            self._connect_smus()
            self._apply_voltage(self.cathode_smu, "Cathode SMU1", 0.0)
            self._apply_voltage(self.guard_smu, "Guard SMU3", 0.0)
            self._wait(1.0, "SMU readback stability check")

            self._read_smu_value(self.cathode_smu, "Cathode SMU1", "voltage")
            self._read_smu_value(self.cathode_smu, "Cathode SMU1", "current")
            self._read_smu_value(self.guard_smu, "Guard SMU3", "voltage")
            self._read_smu_value(self.guard_smu, "Guard SMU3", "current")
            print("SMU readback check complete.")
        finally:
            self._safe_shutdown()

    def _connect_instruments(self) -> None:
        self._connect_matrix()
        self._connect_smus()

    def _connect_smus(self) -> None:
        if self.resource_manager is None:
            raise RuntimeError("Resource manager is not initialized.")

        smu_resource = self.resource_manager.open_resource(self.config.smu_port_address)
        shared_smu_port = PortWrapper(
            smu_resource,
            write_termination=SMU_WRITE_TERMINATION,
            read_termination=SMU_READ_TERMINATION,
            timeout_ms=10000,
        )
        self.cathode_smu = setup_smu(shared_smu_port, self.config.smu_port_address, "SMU1", average="1", initialize_instrument=True)
        self.guard_smu = setup_smu(shared_smu_port, self.config.smu_port_address, "SMU3", average="3", initialize_instrument=False)

    def _connect_matrix(self) -> None:
        if self.config.dry_run:
            self.resource_manager = DryRunResourceManager()
        else:
            self.resource_manager = pyvisa.ResourceManager()

        self.matrix = connect_707a_matrix(
            self.resource_manager,
            address=self.config.switch_port_address,
            settling_seconds=self.config.matrix_settling_seconds,
            dry_run=self.config.dry_run,
        )

    def _run_row(self, row_index: int, row: Dict[str, str]) -> None:
        measured_pin = row["Measured Pin"]
        matrix_config = row["Matrix Config"]
        normalized_matrix_config = normalize_matrix_config(matrix_config)

        print(f"\nRow {row_index}: pin={measured_pin}, matrix={normalized_matrix_config}")
        self._apply_matrix_config(normalized_matrix_config)

        for cathode_index, cathode_step in enumerate(CATHODE_SWEEP):
            self._apply_voltage(self.cathode_smu, "Cathode SMU1", cathode_step.voltage)
            self._wait(cathode_step.hold_seconds, f"Cathode hold at {cathode_step.voltage} V")

            for guard_index, guard_step in enumerate(GUARD_SWEEP):
                self._apply_voltage(self.guard_smu, "Guard SMU3", guard_step.voltage)
                self._wait(guard_step.hold_seconds, f"Guard hold at {guard_step.voltage} V")

                guard_voltage, guard_current = self._measure(self.guard_smu, "Guard SMU3")
                cathode_voltage, cathode_current = self._measure(self.cathode_smu, "Cathode SMU1")
                is_primary_leakage = cathode_index == 1 and guard_index == 0

                self.results.append(
                    {
                        "Timestamp": datetime.now().isoformat(timespec="seconds"),
                        "Measured Pin": measured_pin,
                        "Matrix Config": normalized_matrix_config,
                        "Cathode Channel": "SMU1",
                        "Guard Channel": "SMU3",
                        "Cathode Step Index": cathode_index,
                        "Guard Step Index": guard_index,
                        "Cathode Target V": cathode_step.voltage,
                        "Cathode Hold s": cathode_step.hold_seconds,
                        "Guard Target V": guard_step.voltage,
                        "Guard Hold s": guard_step.hold_seconds,
                        "Cathode Measured V": cathode_voltage,
                        "Cathode Current A": cathode_current,
                        "Guard Measured V": guard_voltage,
                        "Guard Leakage A": guard_current,
                        "Primary Leakage": is_primary_leakage,
                    }
                )

                print(
                    "Measurement "
                    f"cathode={cathode_step.voltage:.3f} V, "
                    f"guard={guard_step.voltage:.3f} V, "
                    f"guard leakage={guard_current:.3e} A, "
                    f"primary={is_primary_leakage}"
                )

    def _apply_matrix_config(self, matrix_config: str) -> None:
        if self.matrix is None:
            raise RuntimeError("Matrix is not connected.")

        self.matrix.apply_route(matrix_config)

    def _apply_voltage(self, smu: Keithley4200SCS, label: str, voltage: float) -> None:
        smu.value = voltage
        smu.apply()
        print(f"Applied {label} = {voltage:.3f} V")

    def _measure(self, smu: Keithley4200SCS, label: str) -> Tuple[float, float]:
        smu.measure()
        measured_voltage, measured_current = smu.call()
        print(f"Measured {label}: {measured_voltage:.6g} V, {measured_current:.6g} A")
        return float(measured_voltage), float(measured_current)

    def _read_smu_value(self, smu: Keithley4200SCS, label: str, value_type: str) -> Optional[float]:
        channel = smu.card_name[-1]
        command = f"T{'V' if value_type == 'voltage' else 'I'}{channel}"
        print(f"Reading {label} {value_type} with {command}...")
        try:
            if value_type == "voltage":
                value = smu.get_voltage(channel)
            elif value_type == "current":
                value = smu.get_current(channel)
            else:
                raise ValueError(f"Unknown readback type: {value_type}")
        except Exception as error:
            print(f"FAILED {command} for {label}: {error}")
            return None

        unit = "V" if value_type == "voltage" else "A"
        print(f"SUCCESS {command} for {label}: {value:.6g} {unit}")
        return float(value)

    def _wait(self, seconds: float, reason: str) -> None:
        print(f"Waiting {seconds:.1f} s: {reason}")
        if self.config.dry_run:
            return
        time.sleep(seconds)

    def _safe_shutdown(self) -> None:
        print("\nSafe shutdown: powering off configured SMUs, then opening matrix.")

        for label, smu in (("Cathode SMU1", self.cathode_smu), ("Guard SMU3", self.guard_smu)):
            if smu is None:
                continue
            try:
                smu.poweroff()
                smu.unconfigure()
                smu.deinitialize()
                print(f"Powered off {label}.")
            except Exception as error:
                print(f"WARNING: Could not fully shut down {label}: {error}")

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


def setup_smu(port: PortWrapper, port_address: str, channel: str, average: str, initialize_instrument: bool) -> Keithley4200SCS:
    smu = Keithley4200SCS()
    smu.port = port
    smu.apply_gui_parameters(
        {
            "Port": port_address,
            "Channel": channel,
            "SweepMode": "Voltage in V",
            "Range": "Auto",
            "Speed": "Slow",
            "Compliance": 0.1,
            "Average": average,
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


def default_output_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(f"{RESULTS_PREFIX}_{timestamp}.csv")


def parse_args() -> GuardLeakageConfig:
    parser = argparse.ArgumentParser(description="Run the Keithley guard leakage test.")
    parser.add_argument("--workbook", type=Path, default=WORKBOOK_PATH, help="Excel workbook with the GuardLeakage sheet.")
    parser.add_argument("--sheet", default=GUARD_LEAKAGE_SHEET, help="Excel sheet name to use.")
    parser.add_argument("--output", type=Path, default=None, help="CSV output path.")
    parser.add_argument("--limit-rows", type=int, default=None, help="Only run the first N valid Excel rows.")
    parser.add_argument("--dry-run", action="store_true", help="Print the sequence without touching hardware or sleeping.")
    parser.add_argument("--matrix-only", action="store_true", help="Only connect the 707A matrix, apply matrix routes, then open all relays. Do not connect SMUs or measure.")
    parser.add_argument("--smu-config-only", action="store_true", help="Only configure SMU1 and SMU3, then power them off. Do not connect the matrix, apply voltage, or measure.")
    parser.add_argument("--smu-zero-only", action="store_true", help="Only configure SMU1 and SMU3, apply 0 V to both, wait briefly, then power them off. Do not connect the matrix or measure.")
    parser.add_argument("--smu-readback-only", action="store_true", help="Only configure SMU1 and SMU3, apply 0 V, then test TV/TI readback queries. Do not connect the matrix or run the sweep.")
    parser.add_argument("--smu-port", default=SMU_PORT_ADDRESS, help="Keithley 4200-SCS VISA address.")
    parser.add_argument("--switch-port", default=SWITCH_PORT_ADDRESS, help="Keithley 707A VISA address.")
    parser.add_argument("--matrix-settling", type=float, default=MATRIX_SETTLING_SECONDS, help="Matrix relay settling time in seconds.")
    args = parser.parse_args()

    return GuardLeakageConfig(
        workbook_path=args.workbook,
        sheet_name=args.sheet,
        smu_port_address=args.smu_port,
        switch_port_address=args.switch_port,
        output_path=args.output,
        matrix_settling_seconds=args.matrix_settling,
        limit_rows=args.limit_rows,
        dry_run=args.dry_run,
        matrix_only=args.matrix_only,
        smu_config_only=args.smu_config_only,
        smu_zero_only=args.smu_zero_only,
        smu_readback_only=args.smu_readback_only,
    )


def main() -> None:
    GuardLeakageTest(parse_args()).run()


if __name__ == "__main__":
    main()
