from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import pyvisa

from core.lcr_channel import LCRMeasurementSpec
from core.port import DryRunResourceManager, PortWrapper
from keithley590_LCR import Device as Keithley590
from parameter_matrix import load_parameter_rows
from switching_matrix import connect_707a_matrix, normalize_matrix_config


class LCRRunner:
    """One-shot LCR measurement runner driven by an LCRMeasurementSpec.

    For each matrix row in the workbook the runner:
      1. Applies the matrix configuration.
      2. Triggers one LCR measurement.
      3. Appends one row to the results CSV.
    """

    _LCR_WRITE_TERMINATION = "\r"

    def __init__(
        self,
        spec: LCRMeasurementSpec,
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
        self.lcr_resource = None  # the opened VISA resource for the LCR meter
        self.lcr: Optional[Keithley590] = None
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
        pd.DataFrame(self.results).to_csv(output_path, index=False, sep=";", decimal=",")
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
        self._connect_lcr()

    def _connect_matrix(self) -> None:
        self._init_resource_manager()
        self.matrix = connect_707a_matrix(
            self.resource_manager,
            address=self.spec.switch_matrix,
            settling_seconds=self.spec.matrix_settling_s,
            dry_run=self.dry_run,
        )

    def _connect_lcr(self) -> None:
        if self.resource_manager is None:
            raise RuntimeError("Resource manager is not initialized.")

        lcr_resource = self.resource_manager.open_resource(self.spec.lcr_meter)
        self.lcr_resource = lcr_resource
        port = PortWrapper(
            lcr_resource,
            write_termination=self._LCR_WRITE_TERMINATION,
            read_termination=None,
            timeout_ms=10000,
        )
        lcr = Keithley590()
        lcr.port = port
        lcr.get_GUIparameter(
            {
                "Port": self.spec.lcr_meter,
                "SweepMode": "None",
                "ValueTypeBias": "Voltage bias in V:",
                "ValueBias": self.spec.lcr.bias_voltage,
                "Frequency": self.spec.lcr.frequency,
                "Integration": self.spec.lcr.integration,
                "Range": self.spec.lcr.range_,
                "Trigger": self.spec.lcr.trigger,
            }
        )
        lcr.initialize()
        lcr.configure()
        self.lcr = lcr

    # ------------------------------------------------------------------ #
    #  Measurement loop                                                    #
    # ------------------------------------------------------------------ #

    def _run_row(self, row_index: int, row: Dict[str, str]) -> None:
        measured_pin = row["Measured Pin"]
        normalized = normalize_matrix_config(row["Matrix Config"])
        print(f"\nRow {row_index}: pin={measured_pin}, matrix={normalized}")

        if self.matrix is None:
            raise RuntimeError("Matrix is not connected.")
        self.matrix.apply_route(normalized)

        if self.lcr is None:
            raise RuntimeError("LCR meter is not connected.")

        self.lcr.value = self.spec.lcr.bias_voltage
        self.lcr.apply()
        self.lcr.measure()
        capacitance, conductance, dc_bias = self.lcr.call()

        print(f"Measured: C={capacitance:.6g} F, G={conductance:.6g} S, V_bias={dc_bias:.6g} V")

        self.results.append(
            {
                "Timestamp": datetime.now().isoformat(timespec="seconds"),
                "Measured Pin": measured_pin,
                "Matrix Config": normalized,
                "Capacitance F": capacitance,
                "Conductance S": conductance,
                "DC Bias V": dc_bias,
            }
        )

    # ------------------------------------------------------------------ #
    #  Shutdown                                                            #
    # ------------------------------------------------------------------ #

    def _safe_shutdown(self) -> None:
        print("\nSafe shutdown: resetting LCR meter, opening matrix.")
        if self.lcr is not None:
            try:
                self.lcr.unconfigure()
                self.lcr.poweroff()
                self.lcr.deinitialize()
                print("LCR meter shut down.")
            except Exception as error:
                print(f"WARNING: Could not fully shut down LCR meter: {error}")

        if self.matrix is not None:
            try:
                self.matrix.shutdown()
            except Exception as error:
                print(f"WARNING: Could not open matrix crosspoints during shutdown: {error}")

        self._close_resources()

    def _close_resources(self) -> None:
        """Close the VISA resources this runner opened.

        Deliberately does NOT close the shared pyvisa ResourceManager: it is a
        process-wide singleton that other code (e.g. the wafer prober in
        run_wafer.py) may hold open across many measurement runs. Closing it here
        would invalidate those sessions (pyvisa.errors.InvalidSession).
        """
        if self.lcr_resource is not None and hasattr(self.lcr_resource, "close"):
            try:
                self.lcr_resource.close()
            except Exception as error:
                print(f"WARNING: Could not close LCR resource: {error}")
        if self.matrix is not None:
            try:
                self.matrix.close()
            except Exception as error:
                print(f"WARNING: Could not close matrix resource: {error}")

    def _default_output_path(self) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = self.spec.test_name.replace(" ", "")
        output_dir = Path("results")
        output_dir.mkdir(exist_ok=True)
        return output_dir / f"{prefix}_{timestamp}.csv"
