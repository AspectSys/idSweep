"""Tests for the .mdf control-map parser (no hardware required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from accretech_prober.parsers import read_mdf_die_assignment, read_mdf_probe_plan

# Mirrors the real Device Commander export: real section names and trailing tabs on
# header/section lines (see DF.mdf in the repo root).
SAMPLE_MDF = "\t\n".join(
    [
        "[BASICINFO]",
        "TSKMAP=1",
        "STREETX=72556",
        "[DEVICEINFO]",
        "WFSIZE=200",
        "[DIEINFO]",
    ]
) + "\t\n" + "\n".join(
    [
        "MARK=0,0",
        "PROB=1,3",
        "PROB=2,4",
        "SKIP=5,5",
        "INSP=6,6",
        "PROB=-2,7",
        "",
    ]
)


def _write_mdf(tmp_path: Path) -> Path:
    path = tmp_path / "plan.mdf"
    path.write_text(SAMPLE_MDF)
    return path


def test_read_mdf_probe_plan_yields_int_tuples(tmp_path: Path) -> None:
    path = _write_mdf(tmp_path)
    # The file stores "y,x"; the plan yields (x, y), so values are swapped.
    dies = list(read_mdf_probe_plan(path))
    assert dies == [(3, 1), (4, 2), (7, -2)]


def test_read_mdf_probe_plan_is_iterable_in_for_loop(tmp_path: Path) -> None:
    path = _write_mdf(tmp_path)
    collected = []
    for die_x, die_y in read_mdf_probe_plan(path):
        collected.append((die_x, die_y))
    assert collected == [(3, 1), (4, 2), (7, -2)]


def test_read_mdf_die_assignment_classifies_all(tmp_path: Path) -> None:
    path = _write_mdf(tmp_path)
    assignment = read_mdf_die_assignment(path)
    assert assignment["MARK"] == ["0,0"]
    assert assignment["PROB"] == ["1,3", "2,4", "-2,7"]
    assert assignment["SKIP"] == ["5,5"]
    assert assignment["INSP"] == ["6,6"]


def test_comments_and_other_sections_ignored(tmp_path: Path) -> None:
    path = tmp_path / "plan.mdf"
    path.write_text("# only comments\n[OTHER]\nPROB=9,9\n")
    # PROB outside [DIEINFO] must be ignored.
    assert list(read_mdf_probe_plan(path)) == []


def test_header_keys_outside_dieinfo_are_ignored(tmp_path: Path) -> None:
    path = _write_mdf(tmp_path)
    assignment = read_mdf_die_assignment(path)
    # Only the [DIEINFO] PROB entries are collected; header keys are not.
    assert assignment["PROB"] == ["1,3", "2,4", "-2,7"]


REAL_MDF = Path(__file__).resolve().parents[1] / "DF.mdf"


@pytest.mark.skipif(not REAL_MDF.exists(), reason="DF.mdf reference file not present")
def test_reads_real_df_mdf() -> None:
    dies = list(read_mdf_probe_plan(REAL_MDF))
    assert len(dies) == 48
    # File stores "y,x" (PROB=9,11 / PROB=5,1) -> yielded as (x, y).
    assert dies[0] == (11, 9)
    assert dies[-1] == (1, 5)
    assert all(isinstance(x, int) and isinstance(y, int) for x, y in dies)
