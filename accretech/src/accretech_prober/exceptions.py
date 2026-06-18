"""Custom exception hierarchy for the Accretech prober library.

These exceptions replace the GUI message boxes and ``input()`` prompts used by the
original SweepMe! driver so that a standalone library never blocks on user interaction.
"""

from __future__ import annotations


class AccretechError(Exception):
    """Base class for all Accretech prober errors."""


class CommunicationTimeout(AccretechError):
    """Raised when a PyVISA read/write or an SRQ wait times out."""


class HardwareAlarm(AccretechError):
    """Raised when the prober reports an alarm or an abnormal end of a command."""


class WaferNotLoadedError(AccretechError):
    """Raised when an operation requires a wafer on the chuck but none is loaded."""


class AccretechRecoverableError(AccretechError):
    """Raised on a recoverable prober error (status byte 76).

    The original driver paused execution with ``input()`` and let the operator fix the
    problem at the prober before retrying. In a standalone library this is surfaced as an
    exception so the caller can decide how to react (retry, abort, show a GUI, ...).

    Attributes:
        error_code: The raw error code returned by the prober (e.g. ``"O0661"``).
        error_type: Human-readable error category (e.g. ``"Operator call"``).
        error_message: The error message returned by the prober.
    """

    def __init__(self, error_code: str, error_type: str, error_message: str) -> None:
        self.error_code = error_code
        self.error_type = error_type
        self.error_message = error_message
        super().__init__(
            f"{error_type} ({error_code}): {error_message} after status byte 76",
        )
