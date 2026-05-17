# Keithley 4200-SCS autorange patch

## Problem

The SweepMe driver mapped `Range = "Auto"` to this KXCI command:

```text
RG <channel>, 0
```

Live KXCI observation showed that the 4200-SCS rejects this command with:

```text
ERROR: GPIB argument error. (-993)
```

According to the manual, `RG` sets the lower limit for autoranging and its argument must be a valid current range in amperes. It is not the command that enables autoranging, and `0` is not a valid range value.

## Patch

In `keithley4200.py`, `Range = "Auto"` is now handled as a no-op during `configure()`. The driver no longer sends `RG <channel>, 0`.

Limited ranges still use `RG <channel>, <valid current>`, for example:

```text
RG 1, 0.01
```

Fixed ranges still use `RI <channel>, <range>, <compliance>`.

## Verification approach

Use the standalone diagnostic script in dry-run mode first:

```powershell
uv run python test_4200_range_config.py --channel SMU1 --range Auto
```

Expected result: setup commands are printed, but no `RG 1, 0` command appears.