"""Transport layer: pure PyVISA interactions with the prober.

This module isolates *all* PyVISA usage so that the rest of the library (and the test
suite) can run against a fake transport. It owns the GPIB connection, the message
read/write termination, and the Service Request (SRQ) / status byte (STB) machinery.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import pyvisa
from pyvisa import constants

from .exceptions import CommunicationTimeout

if TYPE_CHECKING:
    from pyvisa.resources import MessageBasedResource

logger = logging.getLogger(__name__)

# Termination characters used by the Accretech UF GPIB protocol.
TERMINATION = "\r\n"

# Default timeout (seconds) for plain write/read messages. SRQ waits use their own,
# per-command timeouts.
DEFAULT_MESSAGE_TIMEOUT = 5.0


class VisaTransport:
    """Wraps a PyVISA message-based resource for Accretech prober communication.

    The transport can be constructed either from an already-open PyVISA resource or from a
    VISA address string (in which case it opens the resource itself).

    Args:
        resource: An open ``pyvisa.resources.MessageBasedResource`` or a VISA address
            string such as ``"GPIB0::1::INSTR"``.
        resource_manager: Optional :class:`pyvisa.ResourceManager`. Only used when
            ``resource`` is an address string. If omitted, a default one is created.
        message_timeout: Timeout in seconds for plain read/write messages.
    """

    def __init__(
        self,
        resource: MessageBasedResource | str,
        resource_manager: pyvisa.ResourceManager | None = None,
        message_timeout: float = DEFAULT_MESSAGE_TIMEOUT,
    ) -> None:
        self._owns_resource = False

        if isinstance(resource, str):
            rm = resource_manager or pyvisa.ResourceManager()
            self.resource: MessageBasedResource = rm.open_resource(resource)  # type: ignore[assignment]
            self._owns_resource = True
        else:
            self.resource = resource

        if self.resource is None:
            msg = (
                "No connection established with the Accretech UF prober. "
                "Please check the port address / instrument connection."
            )
            raise CommunicationTimeout(msg)

        self.resource.read_termination = TERMINATION
        self.resource.write_termination = TERMINATION
        self.resource.timeout = int(message_timeout * 1000)  # PyVISA expects milliseconds

        self._event_type = constants.EventType.service_request
        self._event_mech = constants.EventMechanism.queue
        self._srq_registered = False

        self.register_srq_event()

    # -- SRQ lifecycle ----------------------------------------------------------------

    def register_srq_event(self) -> None:
        """Enable Service Request (SRQ) events on the queue mechanism."""
        if not self._srq_registered:
            self.resource.enable_event(self._event_type, self._event_mech)
            self._srq_registered = True

    def unregister_srq_event(self) -> None:
        """Disable Service Request (SRQ) events. Safe to call multiple times."""
        if self._srq_registered:
            try:
                self.resource.disable_event(self._event_type, self._event_mech)
            finally:
                self._srq_registered = False

    # -- Plain messaging --------------------------------------------------------------

    def write(self, command: str) -> None:
        """Write a command to the prober."""
        logger.debug("--> write: %r", command)
        self.resource.write(command)

    def read(self) -> str:
        """Read a response line from the prober."""
        answer = self.resource.read()
        logger.debug("<-- read: %r", answer)
        return answer

    # -- Status byte / SRQ ------------------------------------------------------------

    def wait_for_srq(self, timeout: float) -> None:
        """Block until an SRQ event arrives or ``timeout`` (seconds) elapses.

        PyVISA's ``wait_on_event`` raises a ``VisaIOError`` on timeout. To support long
        prober operations without a single huge VISA timeout, we poll in 1 second slices
        until the overall ``timeout`` is exhausted.

        Raises:
            CommunicationTimeout: if no SRQ arrives within ``timeout`` seconds.
        """
        start = time.time()
        while True:
            if time.time() - start >= timeout:
                msg = "Timeout reached while waiting for status byte (SRQ)"
                raise CommunicationTimeout(msg)
            try:
                response = self.resource.wait_on_event(self._event_type, 1000)
            except pyvisa.errors.VisaIOError:
                # 1 second slice timed out; loop and try again until the overall timeout.
                continue
            self._assert_srq_response(response)
            return

    def _assert_srq_response(self, response: object) -> None:
        """Validate a ``wait_on_event`` response across PyVISA versions.

        The ``WaitResponse`` structure changed after PyVISA 1.9.0: newer versions nest the
        event under ``response.event``, while 1.9.0 exposes ``event_type`` directly.
        """
        if hasattr(response, "event"):
            # PyVISA >= ~1.12
            event_type = response.event.event_type  # type: ignore[attr-defined]
        else:
            # PyVISA 1.9.0
            event_type = response.event_type  # type: ignore[attr-defined]

        if event_type != self._event_type:
            msg = "Wrong event type, expected a service request (SRQ) event!"
            raise CommunicationTimeout(msg)

        if getattr(response, "timed_out", False):
            msg = "Timeout expired while waiting for service request (SRQ)!"
            raise CommunicationTimeout(msg)

    def read_status_byte(self) -> int:
        """Read the GPIB status byte from the instrument."""
        return self.resource.read_stb()

    # -- Teardown ---------------------------------------------------------------------

    def close(self) -> None:
        """Unregister SRQ events and (if we opened it) close the resource."""
        self.unregister_srq_event()
        if self._owns_resource and self.resource is not None:
            self.resource.close()

    def __enter__(self) -> VisaTransport:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
