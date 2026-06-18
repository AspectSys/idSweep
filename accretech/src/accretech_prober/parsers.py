"""Parsing utilities for Accretech probe-plan (control map) files.

Control map files (``.mdf``) can be exported using the Device Commander software. They are
INI-like and **tab-delimited**: lines often carry trailing tabs, sections are bare
``[SECTION]`` headers (e.g. ``[BASICINFO]``, ``[DEVICEINFO]``, ``[DIEINFO]``), and entries
are ``KEY=VALUE`` lines. Within the ``[DIEINFO]`` section, each die is classified as
``MARK``, ``PROB``, ``SKIP`` or ``INSP``, with the coordinate stored as ``y,x``::

    [DIEINFO]
    PROB=9,11
    PROB=10,10

Header sections (``[BASICINFO]`` etc.) are ignored when reading the probe plan.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

# Die classifications recognised inside the [DIEINFO] section.
_DIE_CLASSES = ("MARK", "PROB", "SKIP", "INSP")
_DIEINFO_SECTION = "[DIEINFO]"


def read_mdf_die_assignment(path: str | Path) -> dict[str, list[str]]:
    """Read a control map file and return all die classifications.

    Robust to the real export format: trailing tabs/whitespace on any line, tabs around
    keys/values, and ``#`` comment lines are all tolerated.

    Args:
        path: Path to the control map ``.mdf`` file.

    Returns:
        A dict mapping each die class (``MARK``, ``PROB``, ``SKIP``, ``INSP``) to a list of
        raw ``"<y>,<x>"`` coordinate strings (as stored in the file).
    """
    die_assignment: dict[str, list[str]] = {cls: [] for cls in _DIE_CLASSES}

    last_section: str | None = None
    with Path(path).open("r") as mdf_file:
        for raw_line in mdf_file:
            # Strip trailing tabs/whitespace and line endings present in the export.
            line = raw_line.strip()

            if not line or line.startswith("#"):
                continue

            # Section headers are bare bracketed names, possibly with trailing tabs.
            if line.startswith("[") and line.endswith("]"):
                last_section = line
                continue

            if last_section != _DIEINFO_SECTION or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key in die_assignment:
                die_assignment[key].append(value)

    return die_assignment


def read_mdf_probe_plan(path: str | Path) -> Iterator[tuple[int, int]]:
    """Yield the dies to probe from a control map file as ``(x, y)`` int tuples.

    This makes it easy to write a plain Python loop::

        for die_x, die_y in read_mdf_probe_plan("plan.mdf"):
            controller.move_to_die(die_x, die_y)

    Args:
        path: Path to the control map ``.mdf`` file.

    Yields:
        ``(x, y)`` integer die index coordinates for every ``PROB`` die, in file order.
    """
    for entry in read_mdf_die_assignment(path)["PROB"]:
        # The .mdf stores each die as "y,x"; we yield it as (x, y).
        y_str, x_str = entry.split(",")
        yield int(x_str.strip()), int(y_str.strip())
