from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class LCRSpec:
    frequency: float      # 1e8 = 100kHz, 1e9 = 1MHz
    integration: str      # e.g. "10 /s"
    range_: str           # e.g. "Auto"
    trigger: str          # e.g. "One-shot, talk"
    bias_voltage: float   # DC bias in V


@dataclass(frozen=True)
class LCRMeasurementSpec:
    test_name: str
    excel_sheet: str
    switch_matrix: str       # VISA address Keithley 707A
    lcr_meter: str           # VISA address Keithley 590
    matrix_settling_s: float
    workbook_path: Path
    lcr: LCRSpec


def load_lcr_spec(
    path: Path,
    workbook_path: Optional[Path] = None,
) -> LCRMeasurementSpec:
    """Load an LCRMeasurementSpec from a JSON config file.

    Args:
        path: Path to the JSON config file.
        workbook_path: Override the Excel workbook path. Falls back to the
            workbook key in instruments block, then to WORKBOOK_PATH from
            parameter_matrix.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    lcr_cfg = data["lcr"]
    lcr = LCRSpec(
        frequency=float(lcr_cfg["frequency"]),
        integration=lcr_cfg["integration"],
        range_=lcr_cfg["range"],
        trigger=lcr_cfg["trigger"],
        bias_voltage=float(lcr_cfg["bias_voltage"]),
    )

    # Resolve workbook path: CLI override → JSON instruments.workbook → hardcoded default
    if workbook_path is None:
        json_workbook = data["instruments"].get("workbook")
        if json_workbook is not None:
            workbook_path = Path(json_workbook)
        else:
            from parameter_matrix import WORKBOOK_PATH as _DEFAULT_WORKBOOK  # noqa: PLC0415
            workbook_path = _DEFAULT_WORKBOOK

    return LCRMeasurementSpec(
        test_name=data["test_name"],
        excel_sheet=data["excel_sheet"],
        switch_matrix=data["instruments"]["switch_matrix"],
        lcr_meter=data["instruments"]["lcr_meter"],
        matrix_settling_s=float(data["instruments"].get("matrix_settling_s", 0.3)),
        workbook_path=workbook_path,
        lcr=lcr,
    )
