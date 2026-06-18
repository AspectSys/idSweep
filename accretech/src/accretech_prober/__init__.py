"""accretech_prober: a standalone PyVISA library for Accretech UF series wafer probers."""

from __future__ import annotations

import logging

from .communication import VisaTransport
from .controller import DieInfo, ProberController, ProberInfo, ProberStatus
from .core import AccretechProber
from .exceptions import (
    AccretechError,
    AccretechRecoverableError,
    CommunicationTimeout,
    HardwareAlarm,
    WaferNotLoadedError,
)
from .parsers import read_mdf_die_assignment, read_mdf_probe_plan

# Avoid "No handler found" warnings if the application does not configure logging.
logging.getLogger(__name__).addHandler(logging.NullHandler())

__version__ = "0.1.0"

__all__ = [
    "AccretechError",
    "AccretechProber",
    "AccretechRecoverableError",
    "CommunicationTimeout",
    "DieInfo",
    "HardwareAlarm",
    "ProberController",
    "ProberInfo",
    "ProberStatus",
    "VisaTransport",
    "WaferNotLoadedError",
    "read_mdf_die_assignment",
    "read_mdf_probe_plan",
]
