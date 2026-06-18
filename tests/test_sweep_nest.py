"""Unit tests for the nested-loop ("sweep nest") engine.

These run without VISA hardware or an Excel workbook: they exercise the pure
``SweepRunner._build_combos`` helper and the Step Index contracts that
``core/report.py`` depends on.

Run directly:  ``python tests/test_sweep_nest.py``
Or with pytest if available:  ``pytest tests/test_sweep_nest.py``
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow ``python tests/test_sweep_nest.py`` from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.channel import ChannelSpec, SweepStep  # noqa: E402
from core.engine import SweepRunner  # noqa: E402


def _channel(role: str, smu: str, voltages) -> ChannelSpec:
    return ChannelSpec(
        role=role,
        smu=smu,
        compliance_a=0.1,
        speed="Slow",
        range_="Auto",
        average=1,
        sweep_profile=tuple(SweepStep(voltage=v, hold_s=0.0) for v in voltages),
    )


def test_product_outermost_is_first_channel():
    """First channel varies slowest, last channel fastest."""
    a = _channel("a", "SMU1", [0.0, 1.0])       # 2 steps
    b = _channel("b", "SMU2", [0.0, 1.0, 2.0])  # 3 steps

    combos = SweepRunner._build_combos((a, b))
    # Reduce each combo to (a_step_index, b_step_index).
    order = [(combo[0][0], combo[1][0]) for combo in combos]

    assert order == [
        (0, 0), (0, 1), (0, 2),
        (1, 0), (1, 1), (1, 2),
    ], order


def test_global_step_index_equals_sole_multistep_index():
    """With one multi-step channel, the flattened combo index equals that
    channel's per-channel step index — the guarantee report.py relies on."""
    fixed_before = _channel("anode", "SMU2", [0.0])
    swept = _channel("cathode", "SMU1", [0.0, 2.5, 5.0])  # the only multi-step axis
    fixed_after = _channel("guard", "SMU3", [0.0])

    combos = SweepRunner._build_combos((fixed_before, swept, fixed_after))
    assert len(combos) == 3, len(combos)

    for combo_index, combo in enumerate(combos):
        swept_step_index = combo[1][0]  # index 1 == the swept channel
        assert combo_index == swept_step_index, (combo_index, swept_step_index)


def test_single_step_channels_yield_one_combo():
    """All channels single-step (e.g. dark_current.json) → one static row."""
    chans = (
        _channel("cathode", "SMU1", [2.5]),
        _channel("anode", "SMU2", [0.0]),
        _channel("guard", "SMU3", [0.0]),
        _channel("group", "SMU4", [0.0]),
    )
    combos = SweepRunner._build_combos(chans)
    assert len(combos) == 1, len(combos)


def test_multi_axis_row_count_is_product():
    """A genuine multi-axis nest produces rows = product of step counts."""
    chans = (
        _channel("a", "SMU1", [0.0, 1.0]),       # 2
        _channel("b", "SMU2", [0.0, 1.0, 2.0]),  # 3
        _channel("c", "SMU3", [0.0, 1.0]),       # 2
    )
    combos = SweepRunner._build_combos(chans)
    assert len(combos) == 2 * 3 * 2, len(combos)


def _run_all():
    tests = [
        test_product_outermost_is_first_channel,
        test_global_step_index_equals_sole_multistep_index,
        test_single_step_channels_yield_one_combo,
        test_multi_axis_row_count_is_product,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
