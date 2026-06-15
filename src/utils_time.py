from __future__ import annotations

from datetime import date, datetime, time
from typing import Optional

import pandas as pd

_DATE_GROUP_CACHE: dict[int, dict[date, pd.DataFrame]] = {}


def register_market_date_groups(market_df: pd.DataFrame, date_groups: dict[date, pd.DataFrame]) -> None:
    _DATE_GROUP_CACHE[id(market_df)] = date_groups


def parse_datetime(value) -> pd.Timestamp:
    """Parse one value into pandas Timestamp, returning NaT on failure."""
    return pd.to_datetime(value, errors="coerce")


def normalize_datetime_series(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def normalize_date(value) -> Optional[date]:
    ts = parse_datetime(value)
    if pd.isna(ts):
        return None
    return ts.date()


def combine_date_time(day: date, hhmm: str) -> pd.Timestamp:
    parsed = datetime.strptime(hhmm, "%H:%M").time()
    return pd.Timestamp(datetime.combine(day, parsed))


def row_to_dict(row: Optional[pd.Series]) -> Optional[dict]:
    if row is None:
        return None
    return row.to_dict()


def filter_by_date(market_df: pd.DataFrame, day: date) -> pd.DataFrame:
    if market_df.empty or "datetime" not in market_df.columns:
        return pd.DataFrame(columns=market_df.columns)
    date_groups = _DATE_GROUP_CACHE.get(id(market_df))
    if isinstance(date_groups, dict):
        grouped = date_groups.get(day)
        if grouped is None:
            return pd.DataFrame(columns=market_df.columns)
        return grouped
    if "date" in market_df.columns:
        mask = market_df["date"] == day
    else:
        mask = market_df["datetime"].dt.date == day
    return market_df.loc[mask].sort_values("datetime")


def first_available(day_df: pd.DataFrame) -> Optional[pd.Series]:
    if day_df.empty:
        return None
    return day_df.sort_values("datetime").iloc[0]


def last_available(day_df: pd.DataFrame) -> Optional[pd.Series]:
    if day_df.empty:
        return None
    return day_df.sort_values("datetime").iloc[-1]


def last_before(day_df: pd.DataFrame, target_dt: pd.Timestamp) -> Optional[pd.Series]:
    if day_df.empty:
        return None
    subset = day_df.loc[day_df["datetime"] <= target_dt].sort_values("datetime")
    if subset.empty:
        return None
    return subset.iloc[-1]


def first_after(day_df: pd.DataFrame, target_dt: pd.Timestamp) -> Optional[pd.Series]:
    if day_df.empty:
        return None
    subset = day_df.loc[day_df["datetime"] >= target_dt].sort_values("datetime")
    if subset.empty:
        return None
    return subset.iloc[0]


def nearest_to(day_df: pd.DataFrame, target_dt: pd.Timestamp) -> Optional[pd.Series]:
    if day_df.empty:
        return None
    work = day_df.copy()
    work["_abs_delta"] = (work["datetime"] - target_dt).abs()
    work = work.sort_values(["_abs_delta", "datetime"])
    if work.empty:
        return None
    row = work.iloc[0].drop(labels=["_abs_delta"])
    return row


def safe_date_str(value) -> str:
    ts = parse_datetime(value)
    if pd.isna(ts):
        return ""
    return ts.strftime("%Y-%m-%d")


def safe_month_str(value) -> str:
    ts = parse_datetime(value)
    if pd.isna(ts):
        return ""
    return ts.strftime("%Y-%m")
