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

# All measurements in sequence + generate report
python run_all.py
python run_all.py --dry-run --limit-rows 2
```

`--dry-run` simulates without touching hardware (uses `DryRunResourceManager` / `DryRunPort`).  
`--limit-rows N` caps the Excel rows processed per test.

Output CSVs land in `results/` with a timestamped filename.

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

**Primary vs fixed channels**: A channel with more than one `sweep_profile` entry is the *primary* (swept). All others are *fixed* (held at their single setpoint). Exactly one primary channel must exist per config — `load_measurement_spec` raises `ValueError` otherwise.

### Data model — `core/channel.py`, `core/lcr_channel.py`
Frozen dataclasses (`MeasurementSpec`, `ChannelSpec`, `HardwareConfig`, `SweepStep`) are built by `load_measurement_spec()` / `load_lcr_spec()` from the JSON. Nothing mutable lives here.

### Runners — `core/engine.py`, `core/series_resistance_engine.py`, `core/lcr_engine.py`
- `SweepRunner` — generic: for each row, applies matrix, iterates sweep steps, measures all channels, appends one CSV row per step.
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

### Post-processing — `core/post_process.py`
SweepMe!-style script (not called by `sweep.py` / `run_all.py`). Formats per-pin results into a tab-separated `.txt` file consumed by the test station. Depends on `pysweepme` and `ParameterManager`.

## Helper scripts

`helper/` contains standalone diagnostic scripts (`ping_4200.py`, `deep_flush.py`, `bus_reset.py`, `test_4200_*.py`) for verifying GPIB connectivity and instrument state. Run them directly with `python helper/<script>.py`.
