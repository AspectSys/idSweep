from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


@dataclass(frozen=True)
class SweepStep:
    voltage: float
    hold_s: float


@dataclass(frozen=True)
class ChannelSpec:
    role: str                          # e.g. "cathode"
    smu: str                           # e.g. "SMU1"
    compliance_a: float
    speed: str                         # e.g. "Slow"
    range_: str                        # e.g. "Auto"  (named range_ to avoid shadowing built-in)
    average: int
    sweep_profile: Tuple[SweepStep, ...]

    @property
    def is_primary(self) -> bool:
        """Primary channel: the one with more than one sweep_profile entry."""
        return len(self.sweep_profile) > 1

    @property
    def label(self) -> str:
        """Human-readable role label used in console output and CSV column names."""
        return self.role.capitalize()


@dataclass(frozen=True)
class HardwareConfig:
    switch_matrix: str    # VISA address of the Keithley 707A
    smu_mainframe: str    # VISA address of the Keithley 4200-SCS
    matrix_settling_s: float


@dataclass(frozen=True)
class MeasurementSpec:
    test_name: str
    excel_sheet: str
    hardware: HardwareConfig
    channels: Tuple[ChannelSpec, ...]  # declaration order = application order
    workbook_path: Path


def load_measurement_spec(
    path: Path,
    workbook_path: Optional[Path] = None,
) -> MeasurementSpec:
    """Load a MeasurementSpec from a JSON config file.

    Args:
        path: Path to the JSON config file.
        workbook_path: Override the Excel workbook path. Falls back to the
            WORKBOOK_PATH constant from parameter_matrix if not provided.

    Raises:
        ValueError: If the config does not contain exactly one primary channel
            (i.e. exactly one SMU with more than one sweep_profile entry).
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    hardware = HardwareConfig(
        switch_matrix=data["instruments"]["switch_matrix"],
        smu_mainframe=data["instruments"]["smu_mainframe"],
        matrix_settling_s=data["instruments"].get("matrix_settling_s", 0.3),
    )

    channels = []
    for key, cfg in data["smus"].items():
        # Key format: "cathode_SMU1" → role="cathode", smu="SMU1"
        role, smu = key.rsplit("_", 1)
        sweep_profile = tuple(
            SweepStep(voltage=s["voltage"], hold_s=s["hold_s"])
            for s in cfg["sweep_profile"]
        )
        channels.append(
            ChannelSpec(
                role=role,
                smu=smu,
                compliance_a=cfg["compliance_A"],
                speed=cfg["speed"],
                range_=cfg["range"],
                average=cfg["average"],
                sweep_profile=sweep_profile,
            )
        )

    primary_count = sum(1 for ch in channels if ch.is_primary)
    if primary_count != 1:
        raise ValueError(
            f"Expected exactly 1 primary channel (with >1 sweep_profile entries), "
            f"found {primary_count}. Channels: {[ch.role for ch in channels]}"
        )

    # Resolve workbook path: CLI override → JSON config → hardcoded default
    if workbook_path is None:
        json_workbook = data["instruments"].get("workbook")
        if json_workbook is not None:
            workbook_path = Path(json_workbook)
        else:
            from parameter_matrix import WORKBOOK_PATH as _DEFAULT_WORKBOOK  # noqa: PLC0415
            workbook_path = _DEFAULT_WORKBOOK

    return MeasurementSpec(
        test_name=data["test_name"],
        excel_sheet=data["excel_sheet"],
        hardware=hardware,
        channels=tuple(channels),
        workbook_path=workbook_path,
    )
