"""Low-level command API for the Accretech UF series prober.

``AccretechProber`` maps Python methods onto the prober's GPIB command set. It owns a
:class:`~accretech_prober.communication.VisaTransport` and the status-byte waiting logic,
but contains no high-level workflow state (that lives in
:class:`~accretech_prober.controller.ProberController`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from .communication import VisaTransport
from .constants import (
    CASSETTE_STATUS_CODES,
    ERROR_CODES,
    ERROR_STATUS_CODES,
    PROBER_STATUS_CODES,
    STB_CODES,
    WAFER_STATUS_CODES,
)
from .exceptions import AccretechRecoverableError, HardwareAlarm

if TYPE_CHECKING:
    from pyvisa.resources import MessageBasedResource

logger = logging.getLogger(__name__)

# Callback invoked on a recoverable error (STB 76). It receives the
# AccretechRecoverableError. Return ``True`` to retry the operation, anything falsy to
# abort (which re-raises the error).
ErrorCallback = Callable[[AccretechRecoverableError], bool]

# Status byte indicating an abnormal end of command; always terminates a wait loop.
STB_ABNORMAL_END = 99
# Status byte that signals a recoverable prober error.
STB_RECOVERABLE_ERROR = 76


def _looks_like_transport(obj: object) -> bool:
    """Duck-type check so tests can inject a fake transport."""
    return hasattr(obj, "wait_for_srq") and hasattr(obj, "read_status_byte")


class AccretechProber:
    """Communicate with an Accretech UF series prober.

    Args:
        resource: One of:
            * a :class:`~accretech_prober.communication.VisaTransport`,
            * an open ``pyvisa.resources.MessageBasedResource``,
            * a VISA address string such as ``"GPIB0::1::INSTR"``.
        error_callback: Optional callback invoked when the prober reports a recoverable
            error (status byte 76). It receives an :class:`AccretechRecoverableError`;
            return ``True`` to retry, falsy to abort. If no callback is supplied the error
            is raised instead.
        message_timeout: Timeout in seconds for plain read/write messages (only used when
            ``resource`` is not already a transport).
    """

    def __init__(
        self,
        resource: VisaTransport | MessageBasedResource | str,
        error_callback: ErrorCallback | None = None,
        message_timeout: float = 5.0,
    ) -> None:
        if isinstance(resource, VisaTransport) or _looks_like_transport(resource):
            self.transport: VisaTransport = resource  # type: ignore[assignment]
        else:
            self.transport = VisaTransport(resource, message_timeout=message_timeout)

        self._error_callback = error_callback

        # Expose the static lookup tables as attributes for convenience.
        self.stb_codes = STB_CODES
        self.prober_status_codes = PROBER_STATUS_CODES
        self.cassette_status_codes = CASSETTE_STATUS_CODES
        self.wafer_status_codes = WAFER_STATUS_CODES
        self.error_status_codes = ERROR_STATUS_CODES
        self.error_codes = ERROR_CODES

    # -- Lifecycle --------------------------------------------------------------------

    def close(self) -> None:
        """Unregister SRQ events and close the underlying transport."""
        self.transport.close()

    def unregister_srq_event(self) -> None:
        """Disable the SRQ event mechanism (kept for API parity)."""
        self.transport.unregister_srq_event()

    def __enter__(self) -> AccretechProber:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- Status byte handling ---------------------------------------------------------

    def wait_until_status_byte(self, stb_success: int | tuple[int, ...], timeout: float = 5.0) -> int:
        """Wait until one of ``stb_success`` status bytes is received.

        A status byte of 99 (abnormal end) always terminates the loop.
        """
        if isinstance(stb_success, int):
            stb_success = (stb_success,)

        stb: int | None = None
        while stb not in stb_success:
            stb = self.acquire_status_byte(timeout)
            if stb == STB_ABNORMAL_END:
                break
        return stb

    def acquire_status_byte(self, timeout: float = 10.0) -> int:
        """Wait for an SRQ and read the resulting status byte.

        Handles the recoverable error case (status byte 76) by either invoking the
        configured ``error_callback`` or raising :class:`AccretechRecoverableError`.
        """
        self.transport.wait_for_srq(timeout)
        stb = self.transport.read_status_byte()
        logger.debug("Returned STB: %s (%s)", stb, self.stb_codes.get(stb, "Unknown"))

        if stb == STB_RECOVERABLE_ERROR:
            return self._handle_status_76(timeout)

        return stb

    def _handle_status_76(self, timeout: float) -> int:
        """Resolve a status byte 76 (recoverable / format / execution error)."""
        error_code = self.request_error_code()
        error_status = error_code[0]
        error_type = self.error_status_codes.get(error_status, "Unknown")
        error_message = self.request_error_message()

        logger.error("%s (%s): %s after status byte 76", error_type, error_code, error_message)

        # Codes in ERROR_CODES are unrecoverable (GPIB transmit/receive/timeout problems).
        if error_code in self.error_codes:
            raise HardwareAlarm(
                f"{error_type} ({error_code}): {error_message} after status byte 76",
            )

        exc = AccretechRecoverableError(error_code, error_type, error_message)

        if self._error_callback is not None:
            should_retry = self._error_callback(exc)
            if should_retry:
                # Re-acquire a fresh status byte after the operator fixed the problem.
                return self.acquire_status_byte(timeout)
            raise exc

        raise exc

    def raise_error(self, stb: int) -> None:
        """Raise a :class:`HardwareAlarm` describing an unexpected status byte."""
        stb_message = self.stb_codes.get(stb, "Unknown")
        msg = f"Accretech UF series command did not succeed: STB {stb} ('{stb_message}')"
        raise HardwareAlarm(msg)

    # -- Generic messaging ------------------------------------------------------------

    def query(self, cmd: str) -> str:
        """Write ``cmd`` and read the response, stripping the echoed command prefix."""
        self.transport.write(cmd)
        answer = self.transport.read()
        return answer[len(cmd):]

    @staticmethod
    def get_waferlist_from_status(wafer_status: str) -> list[tuple[int, int, int]]:
        """Parse a wafer status string into ``(cassette, wafer, status)`` tuples."""
        wafer_list: list[tuple[int, int, int]] = []

        for cassette_id, cassette_info in enumerate(wafer_status.split(".")):
            status = cassette_info[0]
            wafers = cassette_info[1:]
            if status != "0":
                for wafer_id, val in enumerate(wafers):
                    if val != "0":
                        wafer_list.append((cassette_id + 1, wafer_id + 1, int(val)))

        return wafer_list

    def get_prober_status_message(self, prober_status: str) -> str:
        """Return the human-readable message for a prober status letter."""
        # The 'ms' response is padded with trailing whitespace (e.g. "R "); normalise it.
        prober_status = prober_status.strip()
        if prober_status in self.prober_status_codes:
            return self.prober_status_codes[prober_status]
        msg = f"Prober status '{prober_status}' unknown."
        raise ValueError(msg)

    # -- Information queries ----------------------------------------------------------

    def request_prober_id(self) -> str:
        return self.query("B")

    def request_wafer_id(self) -> str:
        return self.query("b")

    def request_prober_type(self) -> str:
        return self.query("PV")[:6]

    def request_system_version(self) -> str:
        return self.query("PV")[6:]

    def request_error_code(self) -> str:
        return self.query("E")

    def request_error_message(self) -> str:
        return self.query("e")

    def request_wafer_status(self) -> str:
        """Query the wafer status string (53 characters, two cassettes)."""
        return self.query("w")

    def request_cassette_status(self) -> str:
        return self.query("x")

    def request_prober_status(self) -> str:
        # The 'ms' response is space-padded (e.g. "R "); strip it to a bare status letter.
        return self.query("ms").strip()

    def request_parameter(self, value: int) -> str:
        """Request a device parameter (00 device name, 01 wafer size, 20 card type, ...)."""
        return self.query(f"i{int(value):02d}")

    def request_device_name(self) -> str:
        return self.request_parameter(0)

    def request_wafer_size(self) -> str:
        return self.request_parameter(1)

    def request_card_type(self) -> str:
        return self.request_parameter(20)

    def request_wafer_thickness(self) -> str:
        return self.request_parameter(22)

    def request_contact_height(self) -> str:
        return self.request_parameter(24)

    def request_onwafer_info(self) -> tuple[int, int, int, int, int, int, int, int]:
        """Return the on-wafer status (0/1) for sites 1-8."""
        answer = self.query("O")
        bit_list = tuple(map(int, f"{ord(answer):016b}"))
        return (
            bit_list[7],  # site 1
            bit_list[6],  # site 2
            bit_list[5],  # site 3
            bit_list[4],  # site 4
            bit_list[15],  # site 5
            bit_list[14],  # site 6
            bit_list[13],  # site 7
            bit_list[12],  # site 8
        )

    def request_onwafer_info_with_marking(self) -> str:
        return self.query("o")

    def request_operator_name(self) -> str:
        return self.query("OP")

    def request_die_coordinate(self) -> tuple[int, int]:
        """Return the current die ``(x, y)`` index coordinate."""
        answer = self.query("Q")
        yindex = answer.find("Y")
        xindex = answer.find("X")
        y = int(answer[yindex + 1:xindex])
        x = int(answer[xindex + 1:])
        return x, y

    def request_first_die_coordinate(self) -> tuple[int, int]:
        """Return the first die ``(x, y)`` index coordinate."""
        answer = self.query("q")
        yindex = answer.find("Y")
        xindex = answer.find("X")
        y = int(answer[yindex + 1:xindex])
        x = int(answer[xindex + 1:])
        return x, y

    def request_subdie_coordinate(self) -> tuple[int, int, int]:
        """Return the current subdie ``(x, y, s)`` coordinate."""
        answer = self.query("QS")
        yindex = answer.find("Y")
        xindex = answer.find("X")
        sindex = answer.find("S")
        y = int(answer[yindex + 1:xindex])
        x = int(answer[xindex + 1:sindex])
        s = int(answer[sindex + 1:])
        return x, y, s

    def request_cassette_slot(self) -> tuple[int, int]:
        """Return the ``(cassette, slot)`` of the wafer on the chuck."""
        answer = self.query("X")
        cassette_index = int(answer[2])
        slot_index = int(answer[:2])
        return cassette_index, slot_index

    def request_current_status(self) -> tuple[int, int, int]:
        """Return ``(z_axis_status, wafer_status, alarm_status)`` as 0/1 ints."""
        answer = self.query("S1")
        z_axis_status = int(answer[1])
        wafer_status = int(answer[3])
        alarm_status = int(answer[5])
        return z_axis_status, wafer_status, alarm_status

    def request_position(self) -> tuple[float, float]:
        """Return the current absolute ``(x, y)`` position.

        Unit depends on system settings (Metric -> 0.1 um steps reported here as um).
        The raw ``R`` response is *not* echo-stripped, matching the original protocol.
        """
        self.transport.write("R")
        answer = self.transport.read()
        x = int(answer[-7:]) * 0.1
        y = int(answer[2:9]) * 0.1
        return x, y

    # -- Boolean status helpers -------------------------------------------------------

    def is_chuck_contacted(self) -> bool:
        return bool(self.request_current_status()[0])

    def is_wafer_on_chuck(self) -> bool:
        return bool(self.request_current_status()[1])

    def is_last_wafer_on_chuck(self) -> bool:
        self.query("LIW")
        answer = self.transport.read()
        return answer == "1"

    def is_alarm(self) -> bool:
        return bool(self.request_current_status()[2])

    def get_device_parameters(self) -> str:
        return self.query("ku")

    # -- Movement / process commands --------------------------------------------------

    def check_status_byte(self, status_byte: int) -> int:
        """Send an STB self-test command and wait for the same status byte back."""
        status_byte = int(status_byte)
        self.transport.write(f"STB{status_byte:03d}")
        return self.wait_until_status_byte(status_byte, timeout=10.0)

    def start(self) -> int:
        self.transport.write("st")
        stb = self.wait_until_status_byte((120, 121), timeout=300.0)
        if stb == 121:
            self.raise_error(stb)
        return stb

    def stop(self) -> int:
        """Stop probing (status byte 90)."""
        self.transport.write("K")
        return self.wait_until_status_byte(90, timeout=600.0)

    def terminate_lot_process_forcibly(self) -> int:
        self.transport.write("le")
        stb = self.wait_until_status_byte((98, 99), timeout=180.0)
        if stb == 99:
            self.raise_error(stb)
        return stb

    def terminate_lot_process_immediately(self) -> int:
        self.transport.write("jv")
        stb = self.wait_until_status_byte((94, 99), timeout=180.0)
        if stb == 99:
            self.raise_error(stb)
        return stb

    def unload(self) -> int:
        self.transport.write("U")
        return self.wait_until_status_byte(71, timeout=300.0)

    def unload_all_wafers(self) -> int:
        self.transport.write("U0")
        return self.wait_until_status_byte((71, 94), timeout=300.0)

    def unload_to_inspection_tray(self) -> int:
        self.transport.write("U9")
        return self.wait_until_status_byte(71, timeout=120.0)

    def load_specified_wafer(self, cassette: int, slot: int) -> int:
        """Load a wafer from a cassette (starts a lot process implicitly).

        Use ``cassette=9, slot=99`` to terminate the lot process.
        """
        if int(cassette) == 9 and int(slot) != 99:
            msg = "Accretech UF series: slot id must be 99 if cassette id is 9."
            raise ValueError(msg)
        if int(slot) == 99 and int(cassette) != 9:
            msg = "Accretech UF series: cassette id must be 9 if slot id is 99."
            raise ValueError(msg)

        self.transport.write(f"j2{int(cassette)}{int(slot):02d}")
        return self.wait_until_status_byte((70, 94), timeout=300.0)

    def preload_specified_wafer(self, cassette: int, slot: int) -> int:
        """Preload a wafer onto the subchuck ("j3").

        ``cassette=0, slot=0`` moves the chuck wafer back to the subchuck;
        ``cassette=9, slot=99`` terminates the lot process.
        """
        if int(cassette) == 9 and int(slot) != 99:
            msg = "Accretech UF series: slot id must be 99 if cassette id is 9."
            raise ValueError(msg)
        if int(slot) == 99 and int(cassette) != 9:
            msg = "Accretech UF series: cassette id must be 9 if slot id is 99."
            raise ValueError(msg)
        if int(cassette) == 0 and int(slot) != 0:
            msg = "Accretech UF series: slot id must be 0 if cassette id is 0."
            raise ValueError(msg)

        self.transport.write(f"j3{int(cassette)}{int(slot):02d}")
        return self.wait_until_status_byte((94, 70), timeout=300.0)

    def load_and_preload_specified_wafers(
        self, cassette: int, slot: int, preload_cassette: int, preload_slot: int
    ) -> int:
        """Load a wafer on the chuck and preload another on the subchuck ("j4")."""
        self.transport.write(
            f"j4{int(cassette)}{int(slot):02d}{int(preload_cassette)}{int(preload_slot):02d}"
        )
        return self.wait_until_status_byte(70, timeout=300.0)

    def enable_reexecution(self) -> int:
        """Enable re-execution of a lot process."""
        self.transport.write("ji")
        return self.wait_until_status_byte((98, 99), timeout=60.0)

    def load_wafer_aligned(self) -> int:
        self.transport.write("L")
        return self.wait_until_status_byte((70, 94), timeout=180.0)

    def load_wafer_unaligned(self) -> int:
        self.transport.write("L1")
        return self.wait_until_status_byte(118, timeout=60.0)

    def preload_wafer(self) -> int:
        self.transport.write("L8")
        return self.wait_until_status_byte(118, timeout=60.0)

    def load_inspection_wafer_aligned(self) -> int:
        self.transport.write("LI")
        return self.wait_until_status_byte((70, 94), timeout=300.0)

    def load_inspection_wafer_unaligned(self) -> int:
        self.transport.write("L9")
        return self.wait_until_status_byte(118, timeout=120.0)

    def move_position(self, x: int, y: int) -> int:
        """Relative XY move from the current position."""
        self.transport.write(f"AY{int(y):+07}X{int(x):+07}")
        return self.wait_until_status_byte((65, 67), timeout=30.0)

    def move_next_die(self) -> int:
        self.transport.write("J")
        return self.wait_until_status_byte((67, 66), timeout=30.0)

    def move_specified_die(self, x: int, y: int) -> int:
        """Move to the specified die index coordinate."""
        self.transport.write(f"JY{int(y):04d}X{int(x):04d}")
        stb = self.wait_until_status_byte((66, 67, 74), timeout=30.0)
        if stb == 74:
            self.raise_error(stb)
        return stb

    def move_next_subdie_block(self) -> int:
        self.transport.write("JJ")
        return self.wait_until_status_byte((66, 67), timeout=30.0)

    def move_next_subdie(self) -> int:
        self.transport.write("JS")
        return self.wait_until_status_byte((66, 67), timeout=30.0)

    def move_specified_subdie(self, x: int, y: int, s: int) -> int:
        """Move to the specified subdie block coordinate."""
        self.transport.write(f"JSY{int(y):03d}X{int(x):03d}S{int(s):03d}")
        return self.wait_until_status_byte((66, 67), timeout=10.0)

    def move_contact_position(self, position: int) -> int:
        """Absolute XY travel to a contact position."""
        self.transport.write(f"CM{int(position):02d}X")
        return self.wait_until_status_byte((65, 67, 74), timeout=10.0)

    def sense_wafers(self, cassette: str | int = "") -> int:
        """Sense wafers in all cassettes (or a specific one)."""
        self.transport.write("jw" + str(cassette))
        return self.wait_until_status_byte(98, timeout=30.0)

    def align_wafer(self) -> int:
        self.transport.write("N")
        return self.wait_until_status_byte(70, timeout=60.0)

    def align_wafer_only(self) -> int:
        self.transport.write("N1")
        return self.wait_until_status_byte(113, timeout=60.0)

    def align_needle(self) -> int:
        self.transport.write("N2")
        return self.wait_until_status_byte((114, 115), timeout=60.0)

    def align_wafer_from_inspection_tray(self) -> int:
        self.transport.write("N9")
        return self.wait_until_status_byte(119, timeout=300.0)

    def z_up(self) -> int:
        """Raise the chuck to contact the wafer."""
        self.transport.write("Z")
        return self.wait_until_status_byte(67)

    def z_down(self) -> int:
        """Lower the chuck so it does not contact the wafer."""
        self.transport.write("D")
        return self.wait_until_status_byte(68)

    def set_alarm(self, message: str) -> int:
        if len(message) > 20:
            message = message[:20]
            logger.warning("set_alarm: message truncated to 20 characters.")
        self.transport.write("em" + str(message))
        return self.wait_until_status_byte(101)

    def reset_alarm(self) -> int:
        """Erase the error message and clear the alarm status."""
        self.transport.write("es")
        return self.wait_until_status_byte(119)

    def request_chuck_temperature(self) -> tuple[float, float]:
        """Return ``(current, target)`` chuck temperature in degrees Celsius."""
        answer = self.query("f")
        if answer == "":
            msg = "Accretech UF series: Chuck temperature is not controlled."
            raise HardwareAlarm(msg)
        current_temperature = float(answer[:4]) * 0.1
        target_temperature = float(answer[4:]) * 0.1
        return current_temperature, target_temperature

    def request_hot_chuck_temperature(self) -> float:
        """Return the current hot chuck temperature in degrees Celsius."""
        return float(self.query("f1"))

    def set_chuck_temperature(self, temperature: float) -> None:
        """Set the chuck temperature in degrees Celsius (range -55 to 200)."""
        max_temperature = 200.0
        min_temperature = -55.0
        if temperature > max_temperature or temperature < min_temperature:
            msg = f"Accretech UF series: Temperature must be between {min_temperature} and {max_temperature} C."
            raise ValueError(msg)

        self.transport.write(f"h{int(temperature * 10):04d}")
        answer = self.wait_until_status_byte((93, 99))
        correct_status = 93
        if answer != correct_status:
            self.raise_error(answer)

    def request_device_name_list(self, storage: str = "c") -> str:
        """Request the list of saved device parameter set names."""
        return self.query("d" + storage)

    def request_cassette_lock_status(self) -> int:
        """Return 0 (unlocked) or 1 (locked)."""
        return int(self.query("cls"))
