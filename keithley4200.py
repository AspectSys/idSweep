# This Device Class is published under the terms of the MIT License.
# Required Third Party Libraries, which are included in the Device Class
# package for convenience purposes, may have a different license. You can
# find those in the corresponding folders or contact the maintainer.
#
# MIT License
#
# Copyright (c) 2022-2025 SweepMe! GmbH (sweep-me.net)

# SweepMe! driver
# * Module: SMU
# * Instrument: Keithley 4200-SCS
from __future__ import annotations

import contextlib
import ctypes as c
import platform
import time
from pathlib import Path

import numpy as np
from pysweepme import FolderManager
from pysweepme.EmptyDeviceClass import EmptyDevice
from pysweepme.ErrorMessage import debug

FolderManager.addFolderToPATH()

def running_on_device() -> bool:
    """Check if the driver is executed directly on the Keithley 4200-SCS hardware by trying to import the lptlib.dll."""
    dll_path = r"C:\s4200\sys\bin\lptlib.dll"

    # If Clarius is not installed and the dll is not available, the driver is not running on the device
    if not Path(dll_path).exists():
        return False

    # If the dll is available and can be imported, the driver is running on the device
    try:
        _dll = c.WinDLL(dll_path)
    except:
        # if the dll is available but cannot be imported, check the Python interpreter bitness
        if platform.architecture()[0] != "32bit":
            print(
                "Keithley 4200-SCS: Installation of Clarius detected, but lptlib.dll cannot be loaded. Using remote "
                "control via LPTlib server instead. If you are trying to run the driver directly on the device, use "
                "32-Bit version of Python/SweepMe!.",
            )
            return False

        return False

    return True


RUNNING_ON_4200SCS = running_on_device()

if RUNNING_ON_4200SCS:
    from pylptlib import lpt, param
else:
    # --- MODIFIED FOR PURE PYTHON USE ---
    # Bypassed ProxyClass because we are using PyVISA over GPIB
    lpt = None
    param = None


class Device(EmptyDevice):
    """Keithley 4200-SCS driver."""

    description = """
        <h3>Keithley 4200-SCS</h3>
        """

    def __init__(self) -> None:
        """Initialize device parameters."""
        EmptyDevice.__init__(self)

        self.variables = ["Voltage", "Current"]
        self.units = ["V", "A"]
        self.plottype = [True, True]  # True to plot data
        self.savetype = [True, True]  # True to save data

        if not RUNNING_ON_4200SCS:
            self.port_types = ["GPIB", "TCPIP"]
            self.port_manager = True

        self.port_properties = {
            "EOL": "\r\n",
            "timeout": 10.0,
            "TCPIP_EOLwrite": "\x00",
            "TCPIP_EOLread": "\x00",
        }

        self.current_range: str = "Auto"
        self.current_ranges = {
            "Auto": None,
            "Fixed 10 mA": 1e-2,
            "Fixed 1 mA": 1e-3,
            "Fixed 100 µA": 1e-4,
            "Fixed 10 µA": 1e-5,
            "Fixed 1 µA": 1e-6,
            "Fixed 100 nA": 1e-7,
            "Fixed 10 nA": 1e-8,
            "Fixed 1 nA": 1e-9,
            "Fixed 100 pA": 1e-10,
            "Fixed 10 pA": 1e-11,
            "Fixed 1 pA": 1e-12,
            "Limited 10 mA": 1e-2,
            "Limited 1 mA": 1e-3,
            "Limited 100 µA": 1e-4,
            "Limited 10 µA": 1e-5,
            "Limited 1 µA": 1e-6,
            "Limited 100 nA": 1e-7,
            "Limited 10 nA": 1e-8,
            "Limited 1 nA": 1e-9,
            "Limited 100 pA": 1e-10,
            "Limited 10 pA": 1e-11,
            "Limited 1 pA": 1e-12,
        }

        self.speed_dict = {
            "Very fast": 0.01,
            "Fast": 0.1,
            "Medium": 1.0,
            "Slow": 10.0,
            "Custom": 10,
        }

        self.port_string: str = "192.168.0.1"
        self.identifier: str = "Keithley_4200-SCS_" + self.port_string
        self.command_set: str = "LPTlib"
        self.card_id: int = 1

        self.lpt: lpt | Proxy | None = None
        self.param: param | Proxy | None = None

        self.route_out: str = "Rear"
        self.source: str = "Voltage in V"
        self.protection: float = 100e-6
        self.channel: str = "SMU1"

        self.speed: str = "Very fast"
        self.delay_factor: str = "0"
        self.filter_factor: str = "0"
        self.ad_aperture_time: str = "0.01"

        self.card_name = "SMU" + self.channel[-1]
        self.pulse_channel = None

        self.measured_voltage: float = 0.0
        self.measured_current: float = 0.0

        self.pulse_master = False
        self.pulse_mode: bool = False
        self.list_master: bool = False
        self.list_receiver: bool = False

    @staticmethod
    def find_ports() -> list[str]:
        return ["LPTlib"] if RUNNING_ON_4200SCS else ["LPTlib via xxx.xxx.xxx.xxx"]

    def update_gui_parameters(self, parameters: dict) -> dict:
        new_parameters = {
            "SweepMode": ["Voltage in V", "Current in A"],
            "RouteOut": ["Rear"],
            "Channel": ["SMU1", "SMU2", "SMU3", "SMU4", "PMU1 - CH1", "PMU1 - CH2"],
            "Speed": list(self.speed_dict.keys()),
            "Compliance": 100e-6,
            "Range": list(self.current_ranges.keys()),
            "Average": "1",
        }
        return new_parameters

    def apply_gui_parameters(self, parameters: dict) -> None:
        self.port_string = parameters.get("Port", "")
        self.identifier = "Keithley_4200-SCS_" + self.port_string
        self.route_out = parameters.get("RouteOut", "")
        self.current_range = parameters.get("Range", "")
        self.source = parameters.get("SweepMode", "")
        self.protection = parameters.get("Compliance", "")
        self.speed = parameters.get("Speed", "")
        self.averages = parameters.get("Average", "1")
        self.channel = parameters.get("Channel", "SMU1")
        self.shortname = "4200-SCS %s" % parameters.get("Channel", "")
        self.port_manager = "lptlib" not in self.port_string.lower()

        if self.speed == "Custom":
            self.delay_factor = parameters.get("Delay factor", "0")
            self.filter_factor = parameters.get("Filter factor", "0")
            self.ad_aperture_time = parameters.get("A/D aperture time", "0.01")

        self.pulse_mode = parameters.get("CheckPulse", False)

    def handle_card_name(self) -> None:
        try:
            if "PMU" in self.channel:
                self.card_name = self.channel.split("-")[0].strip()
                self.pulse_channel = int(self.channel.split("-")[1][-1])
            elif "SMU" in self.channel:
                self.card_name = self.channel.strip()
                self.pulse_channel = None
            else:
                self.card_name = "SMU" + self.channel[-1]
                self.pulse_channel = None
        except Exception as e:
            raise ValueError(f"Unknown channel name: {self.channel}.") from e

    def connect(self) -> None:
        self.handle_card_name()
        if self.port_manager:
            self.command_set = "US"  
            self.port.port.clear()
        else:
            self.command_set = "LPTlib"
            # (Truncated LPT proxy initializations for space as you are using GPIB)
            pass 

    def initialize(self) -> None:
        if self.identifier not in self.device_communication:
            if self.command_set == "US":
                self.get_options()
                self.clear_buffer()
                self.set_to_4200()
                self.set_command_mode("US")
                self.set_data_service()
                self.set_resolution(7)
            self.device_communication[self.identifier] = {}

    def configure(self) -> None:
        if self.command_set == "US":
            if self.speed == "Very fast":
                raise ValueError("Speed of 'Very Fast' is not supported for US command set via GPIB/TCPIP.")

            if self.speed == "Custom":
                self.set_speed_mode(self.speed, float(self.delay_factor), float(self.filter_factor), float(self.ad_aperture_time))
            else:
                self.set_speed_mode(self.speed)

            current_range_float = self.current_ranges[self.current_range]
            if "Limited" in self.current_range:
                self.set_current_range_limited(self.card_name[-1], current_range_float)
            elif self.current_range == "Auto":
                pass
            else:
                self.set_current_range(self.card_name[-1], current_range_float, self.protection)

    def unconfigure(self) -> None:
        pass

    def poweroff(self) -> None:
        if self.command_set == "US":
            self.switch_off(self.card_name[-1])

    def apply(self) -> None:
        self.value = float(self.value)
        if self.command_set == "US":
            voltage_source_range = 0
            current_source_range = 0

            if self.source == "Voltage in V":
                self.set_voltage(self.card_name[-1], voltage_source_range, self.value, float(self.protection))
            elif self.source == "Current in A":
                self.set_current(self.card_name[-1], current_source_range, self.value, float(self.protection))

    def measure(self) -> None:
        pass

    def call(self) -> list:
        if self.command_set == "US":
            averages = int(self.averages)
            voltages = []
            currents = []

            for _ in range(averages):
                voltages.append(self.get_voltage(self.card_name[-1]))
            for _ in range(averages):
                currents.append(self.get_current(self.card_name[-1]))

            self.measured_voltage = np.mean(voltages)
            self.measured_current = np.mean(currents)

        return [self.measured_voltage, self.measured_current]

    # --- Wrapped Functions ---
    def read_tcpip_port(self) -> str:
        answer = ""
        if self.port_string.startswith("TCPIP"):
            answer = self.port.read()
        return answer

    def set_command_mode(self, mode: str) -> str:
        self.port.write(f"{mode}")
        return self.read_tcpip_port()

    def get_identifier(self) -> str:
        self.port.write("*IDN?")
        return self.port.read()

    def get_options(self) -> str:
        self.port.write("*OPT?")
        return self.port.read()

    def set_resolution(self, resolution: int) -> str:
        self.port.write(f"RS {int(resolution)}")
        return self.read_tcpip_port()

    def set_current_range(self, channel: str, current_range: float, compliance: float) -> str:
        self.port.write(f"RI {channel}, {current_range}, {compliance}")
        return self.read_tcpip_port()

    def set_current_range_limited(self, channel: str, current: float) -> str:
        self.port.write(f"RG {channel}, {current}")
        return self.read_tcpip_port()

    def switch_off(self, channel: str) -> str:
        self.port.write(f"DV{channel}")
        return self.read_tcpip_port()

    def clear_buffer(self) -> str:
        self.port.write("BC")
        return self.read_tcpip_port()

    def set_to_4200(self) -> str:
        self.port.write("EM 1,0")
        return self.read_tcpip_port()

    def set_data_service(self) -> str:
        self.port.write("DR0")
        return self.read_tcpip_port()

    def set_speed_mode(self, speed: str, delay_factor: float = 1., filter_factor: float = 1., ad_integration_time: float = 1.) -> str:
        commands = {"fast": "1", "short": "1", "medium": "2", "slow": "3", "long": "3", "custom": "4"}
        if speed.lower() == "custom":
            self.port.write(f"IT4, {delay_factor}, {filter_factor}, {ad_integration_time}")
        else:
            self.port.write("IT" + commands[speed.lower()])
        return self.read_tcpip_port()

    def set_current(self, channel: str, current_range: int, value: float, voltage_compliance: float) -> str:
        self.port.write(f"DI{channel}, {current_range}, {value}, {voltage_compliance}")
        return self.read_tcpip_port()

    def set_voltage(self, channel: str, voltage_range: int, value: float, current_compliance: float) -> str:
        self.port.write(f"DV{channel}, {voltage_range}, {value}, {current_compliance}")
        return self.read_tcpip_port()

    def get_voltage(self, channel: str) -> float:
        answer = self.port.query("TV" + str(channel))
        voltage = float(answer[3:])
        if voltage > 1e37: voltage = float("nan")
        return voltage

    def get_current(self, channel: str) -> float:
        answer = self.port.query("TI" + str(channel))
        current = float(answer[3:])
        if current > 1e37: current = float("nan")
        return current