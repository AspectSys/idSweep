# accretech-prober

A standalone Python library for controlling **Accretech UF series wafer probers** over
standard [PyVISA](https://pyvisa.readthedocs.io/). It was extracted from a driver that was
tightly coupled to the *SweepMe!* software ecosystem and refactored into a clean, layered,
scriptable API.

## Architecture

The package is split into distinct layers:

| Module | Layer | Responsibility |
| --- | --- | --- |
| `communication.py` | Transport | Pure PyVISA interactions: open/write/read, SRQ events, status byte. |
| `core.py` | Low-level API | `AccretechProber`: maps Python methods to machine commands (`z_up()`, `move_specified_die()`, ...). |
| `controller.py` | High-level workflow | `ProberController`: state machine for wafers/dies/subsites. |
| `parsers.py` | Utility | Reads `.mdf` probe-plan (control map) files. |
| `exceptions.py` | Errors | Custom exception hierarchy that replaces GUI message boxes. |
| `constants.py` | Data | Status-byte codes, error codes and status dictionaries. |

## Installation

This project is managed with [uv](https://docs.astral.sh/uv/).

```bash
uv sync                      # install runtime + dev dependencies
uv sync --extra backend      # also install the pure-Python pyvisa-py backend
```

You also need a VISA backend. Either install a vendor implementation (e.g. NI-VISA), or use
the pure-Python `pyvisa-py` backend via the `backend` extra above.

## Quick start

```python
import pyvisa
from accretech_prober import AccretechProber, ProberController
from accretech_prober.parsers import read_mdf_probe_plan

rm = pyvisa.ResourceManager()
visa_resource = rm.open_resource("GPIB0::1::INSTR")
prober_hw = AccretechProber(visa_resource)

controller = ProberController(prober_hw)
try:
    controller.initialize_system()
    controller.check_and_sense_wafers()
    controller.load_wafer(cassette=1, slot=1)

    for die_x, die_y in read_mdf_probe_plan("plan.mdf"):
        controller.move_to_die(die_x, die_y)
        # ... perform your electrical measurements here ...

    controller.unload_wafer()
except Exception as exc:  # noqa: BLE001
    controller.abort_and_safe_state()
    print(f"Error: {exc}")
finally:
    prober_hw.close()
```

See [`examples/run_probe_plan.py`](examples/run_probe_plan.py) for a runnable example.

## Logging

The library uses the standard `logging` module under the `accretech_prober` logger.
Enable debug output (including per-command status-byte polling) with:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Running tests

```bash
uv run pytest
```

The tests use a fake transport and do not require any hardware.
