from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional

import pandas as pd


WORKBOOK_PATH = Path(__file__).resolve().parent / "settings" / "ParameterMatrix_DF.xlsx"


DEFAULT_COLUMN_ALIASES = {
    "matrixconfig": "Matrix Config",
    "matrix config": "Matrix Config",
    "measuredpin": "Measured Pin",
    "measured pin": "Measured Pin",
}


def load_workbook(workbook_path: Path = WORKBOOK_PATH) -> Dict[str, pd.DataFrame]:
    if not workbook_path.exists():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")
    return pd.read_excel(workbook_path, sheet_name=None)


def load_parameter_sheet(workbook_path: Path, sheet_name: str, column_aliases: Optional[Mapping[str, str]] = None) -> pd.DataFrame:
    workbook = load_workbook(workbook_path)
    if sheet_name not in workbook:
        available_sheets = ", ".join(workbook.keys())
        raise ValueError(f"Sheet '{sheet_name}' not found in {workbook_path}. Available sheets: {available_sheets}")
    return normalize_columns(workbook[sheet_name], column_aliases=column_aliases)


def load_parameter_rows(
    workbook_path: Path,
    sheet_name: str,
    required_columns: Iterable[str],
    column_aliases: Optional[Mapping[str, str]] = None,
) -> List[Dict[str, str]]:
    sheet = load_parameter_sheet(workbook_path, sheet_name, column_aliases=column_aliases)
    required_column_list = list(required_columns)
    missing_columns = [column for column in required_column_list if column not in sheet.columns]
    if missing_columns:
        raise ValueError(f"Sheet '{sheet_name}' is missing required column(s): {', '.join(missing_columns)}")

    rows: List[Dict[str, str]] = []
    for row_number, row in sheet.iterrows():
        cleaned_row: Dict[str, str] = {}
        skip_reasons = []

        for column in required_column_list:
            value = row.get(column)
            if pd.isna(value) or str(value).strip() == "":
                skip_reasons.append(f"empty {column}")
            else:
                cleaned_row[column] = str(value).strip()

        if skip_reasons:
            print(f"Skipping {sheet_name} row {row_number + 2}: {', '.join(skip_reasons)}")
            continue

        for column in sheet.columns:
            if column in cleaned_row:
                continue
            value = row.get(column)
            if not pd.isna(value) and str(value).strip() != "":
                cleaned_row[str(column).strip()] = str(value).strip()

        rows.append(cleaned_row)

    return rows


def normalize_columns(sheet: pd.DataFrame, column_aliases: Optional[Mapping[str, str]] = None) -> pd.DataFrame:
    aliases = dict(DEFAULT_COLUMN_ALIASES)
    if column_aliases:
        aliases.update({normalize_column_key(key): value for key, value in column_aliases.items()})

    renamed_columns = {}
    for column in sheet.columns:
        normalized_name = normalize_column_key(column)
        renamed_columns[column] = aliases.get(normalized_name, str(column).strip())
    return sheet.rename(columns=renamed_columns)


def normalize_column_key(column: object) -> str:
    return " ".join(str(column).strip().split()).lower()
