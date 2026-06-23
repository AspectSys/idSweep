# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

Automated diode characterization system that drives Keithley lab instruments (4200-SCS SMU, 590 LCR meter, 707A switching matrix) over GPIB via PyVISA. It iterates over rows in an Excel workbook, applies matrix routing per row, runs a sweep, and writes results to CSV.

Requires Python 3.9.x exactly (`requires-python = "==3.9.*"`). Install with `uv sync`.

## Running measurements

```
# Single measurement (dispatches on config content)
python sweep.py settings/dark_current.json
python sweep.py settings/guard_leakage.json --dry-run
python sweep.py settings/capacitance.json --limit-rows 3

# All measurements + report for ONE device, no prober (today's workflow)
python run_all.py
python run_all.py --dry-run --limit-rows 2

# Full wafer: prober steps to each die, runs run_all per device
python run_wafer.py GPIB0::1::INSTR accretech/examples/DF.mdf
python run_wafer.py GPIB0::1::INSTR accretech/examples/DF.mdf --dry-run --limit-rows 1
```

Three run modes: `sweep.py` (one measurement, no prober), `run_all.py` (all four for one
device, **no prober** — the no-prober path), `run_wafer.py` (prober loop over a wafer; both
`run_all` and `run_wafer` call the same `run_device()`). The wafer prober is the standalone
`accretech-prober` package (under `accretech/`, wired as a uv editable path dependency).

`--dry-run` simulates without touching hardware (`DryRunResourceManager` / `DryRunPort`; plus a
`NullProberController` in `run_wafer.py`). `--limit-rows N` caps the Excel rows per test.

`sweep.py` writes a timestamped CSV in `results/`. `run_all.py` / `run_wafer.py` write a
per-device folder `results/wafer<id>/dev<NNN>_x<x>_y<y>/` holding the 4 CSVs + the report.

## Config dispatch logic

`sweep.py` (and `run_all.py`) pick a runner based on keys in the JSON config:

| Key present | Runner |
|---|---|
| `"lcr"` | `LCRRunner` (Keithley 590) |
| `"series_resistance"` | `SeriesResistanceRunner` |
| neither | `SweepRunner` (generic V/I) |

## Architecture

### Config layer — `settings/*.json`
Each JSON file defines one measurement: VISA addresses, Excel sheet name, and one or more SMU channels. SMU keys use the format `"<role>_<SMU_ID>"` (e.g. `"cathode_SMU1"`); the role becomes the CSV column prefix.

**Sweep nest**: Each channel's `sweep_profile` is one axis of a nested sweep. For each Excel row, `SweepRunner` walks the Cartesian product of all channels' steps; JSON declaration order = nesting order (first channel = outermost/slowest, last = innermost/fastest). A single-step channel is just held at its value, so a config with one multi-step channel is a plain 1-D sweep. Any number of multi-step channels is allowed. (`ChannelSpec.is_primary` = "more than one step" still exists, used by the series-resistance engine.)

### Data model — `core/channel.py`, `core/lcr_channel.py`
Frozen dataclasses (`MeasurementSpec`, `ChannelSpec`, `HardwareConfig`, `SweepStep`) are built by `load_measurement_spec()` / `load_lcr_spec()` from the JSON. Nothing mutable lives here.

### Runners — `core/engine.py`, `core/series_resistance_engine.py`, `core/lcr_engine.py`
- `SweepRunner` — generic: for each row, applies matrix, walks the Cartesian product of all channels' sweep steps (`_build_combos`), measures all channels, appends one CSV row per combination (global `Step Index` + per-channel `<Label> Step Index`).
- `SeriesResistanceRunner(SweepRunner)` — overrides `_run_row` only: applies two current setpoints, computes Rs = (ΔV + 2·ln(I2/I1)·26mV) / ΔI, emits one CSV row per pin.
- `LCRRunner` — standalone (does not extend `SweepRunner`): same matrix loop but drives the Keithley 590 for C/G measurements.

All runners share the same public interface: `Runner(spec, dry_run, limit_rows, output_path).run() → Path`.

### Instrument abstraction — `core/port.py`, `switching_matrix.py`
`PortWrapper` adapts a PyVISA resource to the port API the SweepMe! drivers expect (`.write`, `.read`, `.query`). `DryRunPort` / `DryRunResourceManager` stub out hardware for `--dry-run`.

`SwitchingMatrix707A` in `switching_matrix.py` wraps the 707A driver. `normalize_matrix_config()` canonicalizes crosspoint strings (uppercase, semicolon-separated).

### Excel workbook — `parameter_matrix.py`
`load_parameter_rows()` reads the Excel sheet, normalises column names (case/whitespace tolerant), skips rows with empty required columns, and returns `List[Dict[str, str]]`. Default workbook path: `settings/ParameterMatrix_DF.xlsx`.

### Report — `core/report.py`
`ReportWriter` consumes the four CSVs produced by `run_all.py` plus `settings/run_info.json` to generate a summary report.

### Post-processing — `ref/post_process.py`
SweepMe!-style script kept for reference only (not called by `sweep.py` / `run_all.py`). Formats per-pin results into a tab-separated `.txt` file consumed by the test station. Depends on `pysweepme` and `ParameterManager`.

## Helper scripts

`helper/` contains standalone diagnostic scripts (`ping_4200.py`, `deep_flush.py`, `bus_reset.py`, `test_4200_*.py`) for verifying GPIB connectivity and instrument state. Run them directly with `python helper/<script>.py`.
