from __future__ import annotations

from typing import Optional


class PortWrapper:
    """Wrap a PyVISA resource with the port API expected by the SweepMe drivers."""

    def __init__(
        self,
        resource,
        write_termination: str,
        read_termination: Optional[str] = None,
        timeout_ms: int = 10000,
    ) -> None:
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
    """Mock port that prints all driver commands instead of sending them to hardware."""

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
    """Mock VISA resource manager that returns DryRunPort instances."""

    def open_resource(self, address: str) -> DryRunPort:
        print(f"[DRY-RUN] open_resource {address}")
        return DryRunPort(address)

    def close(self) -> None:
        print("[DRY-RUN] resource manager close")
