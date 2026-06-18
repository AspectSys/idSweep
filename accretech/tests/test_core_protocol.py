"""Protocol-level tests for AccretechProber using a fake transport (no hardware)."""

from __future__ import annotations

import pytest

from accretech_prober.core import AccretechProber
from accretech_prober.exceptions import AccretechRecoverableError, HardwareAlarm


class FakeTransport:
    """A minimal stand-in for VisaTransport.

    ``reads`` are returned in order by :meth:`read`; ``stbs`` by :meth:`read_status_byte`.
    All writes are recorded in ``writes``.
    """

    def __init__(self, reads: list[str] | None = None, stbs: list[int] | None = None) -> None:
        self.writes: list[str] = []
        self._reads = list(reads or [])
        self._stbs = list(stbs or [])
        self.closed = False

    def write(self, command: str) -> None:
        self.writes.append(command)

    def read(self) -> str:
        return self._reads.pop(0)

    def wait_for_srq(self, timeout: float) -> None:  # noqa: ARG002
        return None

    def read_status_byte(self) -> int:
        return self._stbs.pop(0)

    def unregister_srq_event(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def test_query_strips_command_echo() -> None:
    # The prober echoes the command; query() must strip the "B" prefix.
    transport = FakeTransport(reads=["BUF3000"])
    prober = AccretechProber(transport)
    assert prober.request_prober_id() == "UF3000"


def test_request_die_coordinate_parsing() -> None:
    transport = FakeTransport(reads=["QY0003X0001"])
    prober = AccretechProber(transport)
    assert prober.request_die_coordinate() == (1, 3)


def test_request_position_is_not_echo_stripped() -> None:
    # request_position reads the raw R response without stripping the command.
    transport = FakeTransport(reads=["RR0000500GG0000300"])
    prober = AccretechProber(transport)
    x, y = prober.request_position()
    assert (x, y) == (30.0, 50.0)


def test_get_waferlist_from_status() -> None:
    # Cassette 1 status '1', wafers: slot1=3, slot3=1; cassette 2 absent ('0').
    status = "1" + "301" + "0" * 22 + "." + "0" + "0" * 25
    result = AccretechProber.get_waferlist_from_status(status)
    assert (1, 1, 3) in result
    assert (1, 3, 1) in result


def test_wait_until_status_byte_success() -> None:
    transport = FakeTransport(stbs=[64, 65])
    prober = AccretechProber(transport)
    assert prober.wait_until_status_byte((65, 67)) == 65


def test_wait_until_status_byte_breaks_on_abnormal_end() -> None:
    transport = FakeTransport(stbs=[99])
    prober = AccretechProber(transport)
    # 99 is not in the success set but must terminate the loop.
    assert prober.wait_until_status_byte(70) == 99


def test_status_76_raises_recoverable_error_by_default() -> None:
    # STB 76 -> request_error_code ("E") then request_error_message ("e").
    transport = FakeTransport(reads=["EW0042", "eSome problem"], stbs=[76])
    prober = AccretechProber(transport)
    with pytest.raises(AccretechRecoverableError) as exc_info:
        prober.acquire_status_byte()
    assert exc_info.value.error_code == "W0042"
    assert exc_info.value.error_message == "Some problem"


def test_status_76_unrecoverable_code_raises_hardware_alarm() -> None:
    # O0661 is in the unrecoverable ERROR_CODES table.
    transport = FakeTransport(reads=["EO0661", "eExecution error"], stbs=[76])
    prober = AccretechProber(transport)
    with pytest.raises(HardwareAlarm):
        prober.acquire_status_byte()


def test_status_76_error_callback_can_retry() -> None:
    # First STB is 76; after the callback chooses to retry, a fresh 65 is returned.
    transport = FakeTransport(reads=["EW0042", "eSome problem"], stbs=[76, 65])
    calls: list[AccretechRecoverableError] = []

    def callback(exc: AccretechRecoverableError) -> bool:
        calls.append(exc)
        return True

    prober = AccretechProber(transport, error_callback=callback)
    assert prober.acquire_status_byte() == 65
    assert len(calls) == 1


def test_request_prober_status_strips_padding() -> None:
    # The prober echoes the command and pads the value: "ms" + "R " -> "R".
    transport = FakeTransport(reads=["msR "])
    prober = AccretechProber(transport)
    assert prober.request_prober_status() == "R"


def test_get_prober_status_message_tolerates_padding() -> None:
    prober = AccretechProber(FakeTransport())
    assert prober.get_prober_status_message("R ") == "Performing lot process"


def test_close_delegates_to_transport() -> None:
    transport = FakeTransport()
    prober = AccretechProber(transport)
    prober.close()
    assert transport.closed is True
