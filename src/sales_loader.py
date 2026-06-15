from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .config import NUMERIC_SALES_FIELDS, SALES_FIELD_ALIASES, SALES_SHEET_NAME
from .utils_time import normalize_datetime_series

logger = logging.getLogger(__name__)


def _normalize_header(value: object) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("\n", "").replace("\r", "").replace("\t", "")
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", "", text)
    text = text.replace("／", "/")
    return text


def _warning(warning_type: str, source: str, message: str, context: str = "") -> dict:
    return {
        "warning_type": warning_type,
        "source": source,
        "message": message,
        "context": context,
    }


def _find_column(columns: Iterable[object], aliases: list[str]) -> object | None:
    normalized = {_normalize_header(col): col for col in columns}
    for alias in aliases:
        key = _normalize_header(alias)
        if key in normalized:
            return normalized[key]

    alias_keys = [_normalize_header(alias) for alias in aliases]
    for col in columns:
        col_key = _normalize_header(col)
        for alias_key in alias_keys:
            if alias_key and (alias_key in col_key or col_key in alias_key):
                return col
    return None


def load_sales_orders(sales_file: str | Path, sheet_name: str = SALES_SHEET_NAME) -> tuple[pd.DataFrame, list[dict]]:
    warnings: list[dict] = []
    sales_path = Path(sales_file)
    logger.info("Reading sales workbook: %s", sales_path)

    raw = pd.read_excel(sales_path, sheet_name=sheet_name)
    raw.columns = [str(col).strip().replace("\n", "").replace("\r", "") for col in raw.columns]
    logger.info("Loaded %s rows from sheet %s", len(raw), sheet_name)

    clean = pd.DataFrame(index=raw.index)
    for target, aliases in SALES_FIELD_ALIASES.items():
        source_col = _find_column(raw.columns, aliases)
        if source_col is None:
            clean[target] = np.nan
            warnings.append(
                _warning(
                    "missing_sales_column",
                    "sales_loader",
                    f"Could not find column for {target}",
                    f"aliases={aliases}",
                )
            )
        else:
            clean[target] = raw[source_col]

    clean["order_id"] = clean["order_id"].astype("string").str.strip()
    clean["channel"] = clean["channel"].astype("string").str.strip()
    clean["order_datetime"] = normalize_datetime_series(clean["order_datetime"])

    invalid_time = clean["order_datetime"].isna()
    if invalid_time.any():
        warnings.append(
            _warning(
                "invalid_order_datetime",
                "sales_loader",
                "Rows with unparseable order_datetime were dropped",
                f"count={int(invalid_time.sum())}",
            )
        )
        clean = clean.loc[~invalid_time].copy()

    for field in NUMERIC_SALES_FIELDS:
        before_non_empty = clean[field].notna().sum()
        clean[field] = pd.to_numeric(clean[field], errors="coerce")
        converted_missing = int(clean[field].isna().sum())
        if before_non_empty and converted_missing:
            warnings.append(
                _warning(
                    "numeric_conversion_missing",
                    "sales_loader",
                    f"Numeric conversion produced missing values for {field}",
                    f"missing_count={converted_missing}",
                )
            )

    missing_sales_gram = clean["sales_gram"].isna()
    can_fill_sales_gram = missing_sales_gram & clean["quantity"].notna() & clean["product_gram"].notna()
    if can_fill_sales_gram.any():
        clean.loc[can_fill_sales_gram, "sales_gram"] = (
            clean.loc[can_fill_sales_gram, "quantity"] * clean.loc[can_fill_sales_gram, "product_gram"]
        )
        warnings.append(
            _warning(
                "sales_gram_filled",
                "sales_loader",
                "Missing sales_gram values were filled from quantity * product_gram",
                f"count={int(can_fill_sales_gram.sum())}",
            )
        )

    still_missing_sales_gram = clean["sales_gram"].isna()
    if still_missing_sales_gram.any():
        warnings.append(
            _warning(
                "missing_sales_gram",
                "sales_loader",
                "Orders with missing sales_gram remain in the cleaned table",
                f"count={int(still_missing_sales_gram.sum())}",
            )
        )

    missing_ref = clean["market_price"].isna() & clean["zhaojin_price"].isna()
    if missing_ref.any():
        warnings.append(
            _warning(
                "missing_sales_reference_price",
                "sales_loader",
                "Orders missing both market_price and zhaojin_price will need market data fallback",
                f"count={int(missing_ref.sum())}",
            )
        )

    clean = clean.sort_values("order_datetime").reset_index(drop=True)
    logger.info("Cleaned sales orders: %s rows", len(clean))
    return clean, warnings

