from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

import pyvisa


SMU_PORT_ADDRESS = "GPIB0::17::INSTR"


@dataclass(frozen=True)
class ReadTerminationConfig:
    smu_port_address: str
    timeout_ms: int
    do_tv_test: bool
    execute: bool


def log_write(resource, command: str) -> None:
    print(f"WRITE  {command}")
    resource.write(command)


def log_query(resource, command: str) -> str:
    print(f"QUERY  {command}")
    response = resource.query(command)
    print(f"REPLY  {response.strip()!r}")
    return response


def run_diagnostic(config: ReadTerminationConfig) -> None:
    if not config.execute:
        print("DRY-RUN: this script only makes sense against real hardware. Add --execute to run it.")
        print("Planned sequence:")
        print("  open GPIB0::17::INSTR")
        print("  set write_termination = '\\r\\n'")
        print("  set read_termination  = '\\n'")
        print(f"  set timeout = {config.timeout_ms} ms")
        print("  device clear")
        print("  query: ID")
        if config.do_tv_test:
            print("  write: US")
            print("  write: DR0")
            print("  write: RS 7")
            print("  write: IT3")
            print("  write: DV1, 0, 0.0, 0.1")
            print("  query: TV1")
            print("  write: DV1   (power off)")
        return

    rm = pyvisa.ResourceManager()
    print(f"Opening {config.smu_port_address}")
    resource = rm.open_resource(config.smu_port_address)

    try:
        resource.write_termination = "\r\n"
        resource.read_termination = "\n"
        resource.timeout = config.timeout_ms

        print("Sending Device Clear (DCL)")
        resource.clear()
        time.sleep(0.5)

        try:
            log_query(resource, "ID")
        except Exception as error:
            print(f"ID query failed: {error}")
            print("Aborting before any further commands. Bus may already be wedged.")
            return

        if not config.do_tv_test:
            print("ID query succeeded. Stop here because --no-tv-test was passed.")
            return

        log_write(resource, "US")
        log_write(resource, "DR0")
        log_write(resource, "RS 7")
        log_write(resource, "IT3")
        log_write(resource, "DV1, 0, 0.0, 0.1")
        time.sleep(0.5)

        try:
            log_query(resource, "TV1")
            print("TV1 read succeeded with read_termination='\\n'.")
        except Exception as error:
            print(f"TV1 read failed: {error}")
        finally:
            try:
                log_write(resource, "DV1")
                print("Powered off SMU1.")
            except Exception as error:
                print(f"WARNING: Could not power off SMU1: {error}")
    finally:
        try:
            resource.close()
        except Exception:
            pass
        try:
            rm.close()
        except Exception:
            pass


def parse_args() -> ReadTerminationConfig:
    parser = argparse.ArgumentParser(description="Minimal read-termination diagnostic for the Keithley 4200-SCS over KXCI/GPIB.")
    parser.add_argument("--smu-port", default=SMU_PORT_ADDRESS, help="Keithley 4200-SCS VISA address.")
    parser.add_argument("--timeout-ms", type=int, default=5000, help="VISA timeout in milliseconds.")
    parser.add_argument("--no-tv-test", action="store_true", help="Stop after the ID ping. Do not configure or read TV1.")
    parser.add_argument("--execute", action="store_true", help="Actually open the VISA resource and send commands. Omitted means dry-run.")
    args = parser.parse_args()

    return ReadTerminationConfig(
        smu_port_address=args.smu_port,
        timeout_ms=args.timeout_ms,
        do_tv_test=not args.no_tv_test,
        execute=args.execute,
    )


def main() -> None:
    run_diagnostic(parse_args())


if __name__ == "__main__":
    main()
