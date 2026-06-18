"""High-level workflow controller for the Accretech UF series prober.

``ProberController`` replaces the SweepMe! ``Device`` state machine (``initialize`` /
``configure`` / ``apply`` / ``unconfigure``). The monolithic ``apply()`` logic is broken
into atomic, scriptable methods (:meth:`load_wafer`, :meth:`move_to_die`,
:meth:`move_to_subsite`, ...) while preserving the wafer load/preload sequencing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .exceptions import AccretechError, WaferNotLoadedError

if TYPE_CHECKING:
    from .core import AccretechProber

logger = logging.getLogger(__name__)

# Maximum allowed deviation (in position units) between requested and measured subsite.
DEFAULT_SUBSITE_TOLERANCE = 5


@dataclass
class ProberInfo:
    """Static identification info about the prober."""

    prober_id: str
    prober_type: str
    system_version: str


@dataclass
class ProberStatus:
    """Snapshot of the prober's current status."""

    wafer_id: str
    wafer_status: str
    cassette_status: str
    prober_status: str
    prober_status_message: str
    cassette_lock_status: int
    chuck_contacted: bool
    wafer_on_chuck: bool
    alarm: bool
    error_code: str
    error_message: str


@dataclass
class DieInfo:
    """Information about the current die / position."""

    cassette: int
    slot: int
    die_x: int
    die_y: int
    chuck_contacted: bool
    position: tuple[float, float]


class ProberController:
    """Scriptable, stateful workflow on top of :class:`AccretechProber`.

    Args:
        prober: A connected :class:`~accretech_prober.core.AccretechProber`.
    """

    def __init__(self, prober: AccretechProber) -> None:
        self.prober = prober

        # Workflow state (formerly initialised in SweepMe's configure()).
        self.last_wafer: tuple[int, int] | None = None
        self.last_wafer_id: str = ""
        self.last_die: tuple[int, int] | None = None
        self.last_sub: tuple[int, int] = (0, 0)

        # Absolute position of the current die's start position.
        self.current_die_position: tuple[float | None, float | None] = (None, None)
        # Last absolute position.
        self.last_position: tuple[float | None, float | None] = (None, None)

    # -- Setup / teardown -------------------------------------------------------------

    def initialize_system(self) -> None:
        """Prepare the prober: clear any alarm and log identification + status."""
        info = self.get_info()
        logger.info(
            "Prober ID=%s, type=%s, system version=%s",
            info.prober_id,
            info.prober_type,
            info.system_version,
        )
        self.prober.reset_alarm()
        self.log_status()

    def check_and_sense_wafers(self) -> None:
        """Sense wafers if no wafer is on the chuck and no cassette has been sensed yet."""
        if self.prober.is_wafer_on_chuck():
            return

        cassette_status = self.prober.request_cassette_status()
        # Last two characters encode the two load-port cassette statuses.
        if cassette_status[-2:] == "00":
            logger.info("No cassette sensed yet; sensing wafers.")
            self.prober.sense_wafers()

    # -- Wafer handling ---------------------------------------------------------------

    def load_wafer(
        self,
        cassette: int,
        slot: int,
        next_cassette: int | None = None,
        next_slot: int | None = None,
    ) -> None:
        """Load the wafer at ``(cassette, slot)`` onto the chuck.

        Preserves the lot-process sequencing of the original ``apply()``:

        * First wafer, no preload -> ``load_specified_wafer`` ("j2").
        * First wafer with a preload -> ``load_and_preload_specified_wafers`` ("j4").
        * Subsequent wafer, no preload -> transfer subchuck wafer to chuck ("j3" 0/0).
        * Subsequent wafer with a preload -> preload the next wafer ("j3").

        Args:
            cassette: Cassette id of the wafer to load.
            slot: Slot id of the wafer to load.
            next_cassette: Optional cassette id of the wafer to preload next.
            next_slot: Optional slot id of the wafer to preload next.
        """
        wafer = (int(cassette), int(slot))
        has_next = next_cassette is not None and next_slot is not None
        preload_wafer = (int(next_cassette), int(next_slot)) if has_next else None

        if wafer == self.last_wafer:
            logger.info("Wafer %s is already on the chuck.", wafer)
            return

        # Always separate before a load operation.
        if self.prober.is_chuck_contacted():
            self.prober.z_down()

        if preload_wafer is None:
            if self.last_wafer is None:
                # First (and possibly only) wafer.
                self.prober.load_specified_wafer(*wafer)
            else:
                # Last wafer of several: transfer the subchuck wafer to the chuck.
                self.prober.preload_specified_wafer(0, 0)
        else:
            # If the wafer to preload is the one currently on the chuck, bring it back
            # first so the load/preload below starts from a clean state.
            if preload_wafer == self.last_wafer:
                self.prober.unload_all_wafers()
                self.last_wafer = None
                self.last_wafer_id = ""

            if self.last_wafer is None:
                self.prober.load_and_preload_specified_wafers(*wafer, *preload_wafer)
            else:
                self.prober.preload_specified_wafer(*preload_wafer)

        self.last_position = (None, None)
        self.last_wafer = wafer
        # A new wafer resets die/subsite tracking.
        self.last_die = None
        self.last_sub = (0, 0)
        self.last_wafer_id = self.prober.request_wafer_id()
        logger.info("Loaded wafer %s (id=%s).", wafer, self.last_wafer_id)

    def unload_wafer(self) -> None:
        """Unload the wafer on the chuck and terminate the lot process."""
        self.prober.reset_alarm()
        if self.prober.is_wafer_on_chuck():
            # Terminate the lot process, bringing all wafers back to their cassettes.
            self.prober.preload_specified_wafer(9, 99)
            self.last_wafer = None
            self.last_wafer_id = ""
            self.last_die = None
            logger.info("Unloaded wafer and terminated lot process.")
        else:
            logger.warning("No wafer on the chuck to unload.")

    def get_wafer_on_chuck(self) -> tuple[int | None, int | None]:
        """Return the ``(cassette, wafer)`` currently on the chuck, or ``(None, None)``."""
        if not self.prober.is_wafer_on_chuck():
            return (None, None)

        wafer_status = self.prober.request_wafer_status()
        wafer_list = self.prober.get_waferlist_from_status(wafer_status)

        wafer_during_test_status = 3
        for cassette, slot, status in wafer_list:
            if status == wafer_during_test_status:
                return (cassette, slot)
        return (None, None)

    # -- Movement ---------------------------------------------------------------------

    def move_to_die(self, x: int, y: int, contact: bool = True) -> None:
        """Move to the die at index ``(x, y)``.

        Args:
            x: Die index in the x direction.
            y: Die index in the y direction.
            contact: If ``True`` (default), bring the chuck up to contact after moving.
        """
        die = (int(x), int(y))

        if die != self.last_die:
            if self.prober.is_chuck_contacted():
                self.prober.z_down()

            self.prober.move_specified_die(*die)

            # Record the absolute position at the die's start; used as the subsite origin.
            self.current_die_position = self.prober.request_position()
            self.last_die = die
            self.last_sub = (0, 0)
            self.last_position = self.current_die_position
            logger.info("Moved to die %s.", die)

        if contact:
            self.contact()

    def move_to_subsite(
        self,
        dx: int,
        dy: int,
        contact: bool = True,
        tolerance: int = DEFAULT_SUBSITE_TOLERANCE,
    ) -> None:
        """Move to a subsite at offset ``(dx, dy)`` relative to the die start position.

        Args:
            dx: Subsite x coordinate relative to the die start.
            dy: Subsite y coordinate relative to the die start.
            contact: If ``True`` (default), contact the wafer after moving.
            tolerance: Max allowed deviation between requested and measured subsite.

        Raises:
            AccretechError: If no die has been selected yet, or the measured position
                deviates from the request by more than ``tolerance``.
        """
        if None in self.current_die_position:
            msg = "move_to_die() must be called before move_to_subsite()."
            raise AccretechError(msg)

        new_sub = (int(dx), int(dy))

        if self.prober.is_chuck_contacted():
            self.prober.z_down()

        # Relative move from the last subsite to the new one.
        xy_move = (new_sub[0] - self.last_sub[0], new_sub[1] - self.last_sub[1])
        self.prober.move_position(*xy_move)
        self.last_sub = new_sub

        position = self.prober.request_position()
        # The A command (move) and R command (read) use opposite coordinate systems, so we
        # subtract the measured position from the die origin to compare with new_sub.
        rel_sub = (
            self.current_die_position[0] - position[0],
            self.current_die_position[1] - position[1],
        )

        if abs(new_sub[0] - rel_sub[0]) > tolerance or abs(new_sub[1] - rel_sub[1]) > tolerance:
            msg = (
                f"Relative subsite position after move {rel_sub} is not in agreement with "
                f"requested subsite position {new_sub}."
            )
            raise AccretechError(msg)

        self.last_position = position
        logger.info("Moved to subsite %s.", new_sub)

        if contact:
            self.contact()

    def contact(self) -> None:
        """Raise the chuck to contact the wafer, if not already contacted."""
        if not self.prober.is_chuck_contacted():
            self.prober.z_up()

    def separate(self) -> None:
        """Lower the chuck to separate from the wafer, if currently contacted."""
        if self.prober.is_chuck_contacted():
            self.prober.z_down()

    def abort_and_safe_state(self) -> None:
        """Bring the prober to a safe state after an error (separate the chuck).

        The wafer is intentionally left on the chuck so the operator can inspect the
        failure, mirroring the original driver's behaviour.
        """
        try:
            self.separate()
        except AccretechError:
            logger.exception("Failed to separate the chuck during abort.")
        logger.warning("Prober brought to safe state (chuck separated).")

    # -- Status / info ----------------------------------------------------------------

    def get_info(self) -> ProberInfo:
        """Return static prober identification info."""
        return ProberInfo(
            prober_id=self.prober.request_prober_id(),
            prober_type=self.prober.request_prober_type(),
            system_version=self.prober.request_system_version(),
        )

    def get_status(self) -> ProberStatus:
        """Return a snapshot of the prober's current status."""
        prober_status = self.prober.request_prober_status()
        return ProberStatus(
            wafer_id=self.prober.request_wafer_id(),
            wafer_status=self.prober.request_wafer_status(),
            cassette_status=self.prober.request_cassette_status(),
            prober_status=prober_status,
            prober_status_message=self.prober.get_prober_status_message(prober_status),
            cassette_lock_status=self.prober.request_cassette_lock_status(),
            chuck_contacted=self.prober.is_chuck_contacted(),
            wafer_on_chuck=self.prober.is_wafer_on_chuck(),
            alarm=self.prober.is_alarm(),
            error_code=self.prober.request_error_code(),
            error_message=self.prober.request_error_message(),
        )

    def get_die_info(self) -> DieInfo:
        """Return information about the current die and position.

        Raises:
            WaferNotLoadedError: If there is no wafer on the chuck.
        """
        if not self.prober.is_wafer_on_chuck():
            msg = "Cannot read die info: no wafer on the chuck."
            raise WaferNotLoadedError(msg)

        cassette, slot = self.prober.request_cassette_slot()
        die_x, die_y = self.prober.request_die_coordinate()
        return DieInfo(
            cassette=cassette,
            slot=slot,
            die_x=die_x,
            die_y=die_y,
            chuck_contacted=self.prober.is_chuck_contacted(),
            position=self.prober.request_position(),
        )

    def log_status(self) -> None:
        """Log the current prober status at INFO level."""
        status = self.get_status()
        logger.info(
            "Status: wafer_id=%s wafer_on_chuck=%s chuck_contacted=%s alarm=%s "
            "prober_status=%s (%s) error=%s",
            status.wafer_id,
            status.wafer_on_chuck,
            status.chuck_contacted,
            status.alarm,
            status.prober_status,
            status.prober_status_message,
            status.error_code,
        )
