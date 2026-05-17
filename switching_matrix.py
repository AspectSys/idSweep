from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from keithley707a import Device as Keithley707A


SWITCH_PORT_ADDRESS = "GPIB0::18::INSTR"
MATRIX_SETTLING_SECONDS = 0.3


@dataclass(frozen=True)
class MatrixRoute:
    measured_pin: str
    matrix_config: str


class MatrixPortWrapper:
    """Wrap a PyVISA resource with the port API expected by the 707A driver."""

    def __init__(self, resource, timeout_ms: int = 10000) -> None:
        self.port = resource
        self.port.write_termination = "\r"
        self.port.read_termination = None
        self.port.timeout = timeout_ms

    def write(self, command: str) -> None:
        self.port.write(command)

    def read(self) -> str:
        return self.port.read()

    def query(self, command: str) -> str:
        return self.port.query(command)


class SwitchingMatrix707A:
    def __init__(self, resource, settling_seconds: float = MATRIX_SETTLING_SECONDS, dry_run: bool = False) -> None:
        self.settling_seconds = settling_seconds
        self.dry_run = dry_run
        self.driver = Keithley707A()
        self.driver.port = MatrixPortWrapper(resource)

    def initialize(self) -> None:
        self.driver.initialize()
        self.driver.configure()

    def apply_route(self, matrix_config: str) -> str:
        normalized_matrix_config = normalize_matrix_config(matrix_config)
        self.open_all()
        self.driver.close_crosspoints_by_string(normalized_matrix_config.replace(";", ","))
        self.wait_for_settling()
        return normalized_matrix_config

    def open_all(self) -> None:
        self.driver.open_all_crosspoints()

    def wait_for_settling(self) -> None:
        print(f"Waiting {self.settling_seconds:.1f} s: Matrix relay settling")
        if self.dry_run:
            return
        time.sleep(self.settling_seconds)

    def shutdown(self) -> None:
        self.open_all()


def connect_707a_matrix(resource_manager, address: str = SWITCH_PORT_ADDRESS, settling_seconds: float = MATRIX_SETTLING_SECONDS, dry_run: bool = False) -> SwitchingMatrix707A:
    resource = resource_manager.open_resource(address)
    matrix = SwitchingMatrix707A(resource, settling_seconds=settling_seconds, dry_run=dry_run)
    matrix.initialize()
    return matrix


def normalize_matrix_config(matrix_config: str) -> str:
    crosspoints = [crosspoint.strip().upper() for crosspoint in str(matrix_config).replace(",", ";").split(";")]
    crosspoints = [crosspoint for crosspoint in crosspoints if crosspoint]
    if not crosspoints:
        raise ValueError("Matrix Config is empty after normalization.")
    return ";".join(crosspoints)


def route_from_values(measured_pin: object, matrix_config: object) -> Optional[MatrixRoute]:
    measured_pin_text = str(measured_pin).strip()
    matrix_config_text = normalize_matrix_config(str(matrix_config))
    if not measured_pin_text:
        return None
    return MatrixRoute(measured_pin=measured_pin_text, matrix_config=matrix_config_text)