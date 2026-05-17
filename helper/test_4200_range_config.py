from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Optional

import pyvisa

from keithley4200 import Device as Keithley4200SCS


SMU_PORT_ADDRESS = "GPIB0::17::INSTR"
SMU_WRITE_TERMINATION = "\r\n"
SMU_READ_TERMINATION = None
DEFAULT_RANGE = "Limited 10 mA"


class LoggingPortWrapper:
    """Log every command while exposing the port API expected by the SweepMe driver."""

    def __init__(self, resource, write_termination: str, read_termination: Optional[str], timeout_ms: int) -> None:
        self.port = resource
        self.port.write_termination = write_termination
        self.port.read_termination = read_termination
        self.port.timeout = timeout_ms

    def write(self, command: str) -> None:
        print(f"WRITE {command}")
        self.port.write(command)

    def read(self) -> str:
        print("READ")
        return self.port.read()

    def query(self, command: str) -> str:
        print(f"QUERY {command}")
        return self.port.query(command)


class DryRunInnerPort:
    def __init__(self) -> None:
        self.write_termination = ""
        self.read_termination = None
        self.timeout = 10000

    def clear(self) -> None:
        print("DRY-RUN CLEAR")

    def write(self, command: str) -> None:
        print(f"DRY-RUN write payload={command!r}")

    def read(self) -> str:
        print("DRY-RUN read")
        return ""

    def query(self, command: str) -> str:
        print(f"DRY-RUN query payload={command!r}")
        return ""


@dataclass(frozen=True)
class RangeDiagnosticConfig:
    smu_port_address: str
    channel: str
    current_range: str
    compliance: float
    average: str
    speed: str
    timeout_ms: int
    execute: bool


def configure_smu(port: LoggingPortWrapper, config: RangeDiagnosticConfig) -> Keithley4200SCS:
    smu = Keithley4200SCS()
    smu.port = port
    smu.apply_gui_parameters(
        {
            "Port": config.smu_port_address,
            "Channel": config.channel,
            "SweepMode": "Voltage in V",
            "Range": config.current_range,
            "Speed": config.speed,
            "Compliance": config.compliance,
            "Average": config.average,
        }
    )
    smu.connect()
    smu.initialize()
    smu.configure()
    return smu


def run_diagnostic(config: RangeDiagnosticConfig) -> None:
    resource_manager = None
    smu = None

    if config.execute:
        print(f"Opening {config.smu_port_address}")
        resource_manager = pyvisa.ResourceManager()
        resource = resource_manager.open_resource(config.smu_port_address)
    else:
        print("DRY-RUN mode: no VISA resource will be opened. Add --execute to touch hardware.")
        resource = DryRunInnerPort()

    port = LoggingPortWrapper(
        resource,
        write_termination=SMU_WRITE_TERMINATION,
        read_termination=SMU_READ_TERMINATION,
        timeout_ms=config.timeout_ms,
    )

    try:
        print(f"Configuring {config.channel} with Range={config.current_range!r}, Speed={config.speed!r}, Compliance={config.compliance:g} A")
        smu = configure_smu(port, config)
        print("Range configuration completed. Check the KXCI log for GPIB argument errors before running any readback test.")
    finally:
        if smu is not None:
            try:
                smu.poweroff()
                print(f"Powered off {config.channel}.")
            except Exception as error:
                print(f"WARNING: Could not power off {config.channel}: {error}")

        if resource_manager is not None:
            resource_manager.close()


def parse_args() -> RangeDiagnosticConfig:
    parser = argparse.ArgumentParser(description="Safely test the Keithley 4200-SCS current range command emitted by the SweepMe driver.")
    parser.add_argument("--smu-port", default=SMU_PORT_ADDRESS, help="Keithley 4200-SCS VISA address.")
    parser.add_argument("--channel", default="SMU1", choices=["SMU1", "SMU2", "SMU3", "SMU4"], help="SMU channel to configure.")
    parser.add_argument("--range", dest="current_range", default=DEFAULT_RANGE, help="SweepMe driver Range value to test, for example 'Limited 10 mA'.")
    parser.add_argument("--compliance", type=float, default=100e-6, help="Voltage-source current compliance in A. Configure-only mode does not apply voltage.")
    parser.add_argument("--average", default="1", help="Average setting passed to the driver.")
    parser.add_argument("--speed", default="Slow", help="Speed setting passed to the driver.")
    parser.add_argument("--timeout-ms", type=int, default=10000, help="VISA timeout in milliseconds.")
    parser.add_argument("--execute", action="store_true", help="Actually open the VISA resource and send the configuration commands. Omitted means dry-run only.")
    parser.add_argument("--allow-auto", action="store_true", help="Accepted for compatibility with earlier diagnostics; Auto no longer sends RG <channel>, 0.")
    args = parser.parse_args()

    return RangeDiagnosticConfig(
        smu_port_address=args.smu_port,
        channel=args.channel,
        current_range=args.current_range,
        compliance=args.compliance,
        average=args.average,
        speed=args.speed,
        timeout_ms=args.timeout_ms,
        execute=args.execute,
    )


def main() -> None:
    run_diagnostic(parse_args())


if __name__ == "__main__":
    main()