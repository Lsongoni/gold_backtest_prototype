from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from .config import LATE_FIX_TIME, MA_WINDOWS, MORNING_FIX_TIME
from .utils_time import combine_date_time, filter_by_date, first_available, last_available, last_before


@dataclass
class FixingDecision:
    trend_signal: str
    fixing_time: str
    fixing_price: float | None
    data_quality_flag: str


def add_moving_averages(market_df: pd.DataFrame, windows: list[int] | None = None) -> pd.DataFrame:
    work = market_df.sort_values("datetime").reset_index(drop=True).copy()
    for window in windows or MA_WINDOWS:
        work[f"ma_{window}"] = work["close"].rolling(window=window, min_periods=window).mean()
    return work


def determine_trend(market_df: pd.DataFrame, day: date, ma_window: int) -> tuple[str, str]:
    day_df = filter_by_date(market_df, day)
    first_row = first_available(day_df)
    if first_row is None:
        return "missing", "no_market_data"

    ma_col = f"ma_{ma_window}"
    ma_value = first_row.get(ma_col)
    close = first_row.get("close")
    if pd.isna(ma_value) or pd.isna(close):
        return "sideways", "ma_insufficient"
    if close > ma_value:
        return "uptrend", "ok"
    if close < ma_value:
        return "downtrend", "ok"
    return "sideways", "ok"


def _format_time(row: pd.Series | None) -> str:
    if row is None or pd.isna(row.get("datetime")):
        return ""
    return pd.Timestamp(row["datetime"]).strftime("%Y-%m-%d %H:%M:%S")


def _price(row: pd.Series | None) -> float | None:
    if row is None:
        return None
    value = row.get("close")
    if pd.isna(value):
        return None
    return float(value)


def _early_row(day_df: pd.DataFrame, day: date) -> tuple[pd.Series | None, str]:
    target_dt = combine_date_time(day, MORNING_FIX_TIME)
    row = last_before(day_df, target_dt)
    if row is not None:
        return row, "ok"
    return first_available(day_df), "early_fallback_first"


def _late_row(day_df: pd.DataFrame, day: date) -> tuple[pd.Series | None, str]:
    target_dt = combine_date_time(day, LATE_FIX_TIME)
    row = last_before(day_df, target_dt)
    if row is not None:
        return row, "ok"
    return last_available(day_df), "late_fallback_last"


def _split_fix(day_df: pd.DataFrame, day: date) -> tuple[str, float | None, str]:
    early, early_flag = _early_row(day_df, day)
    late, late_flag = _late_row(day_df, day)
    early_price = _price(early)
    late_price = _price(late)
    if early_price is None and late_price is None:
        return "", None, "missing_fixing_price"
    if early_price is None:
        return _format_time(late), late_price, f"split_missing_early;{late_flag}"
    if late_price is None:
        return _format_time(early), early_price, f"split_missing_late;{early_flag}"
    fixing_price = early_price * 0.5 + late_price * 0.5
    fixing_time = f"{_format_time(early)} / {_format_time(late)}"
    flags = [flag for flag in [early_flag, late_flag] if flag != "ok"]
    return fixing_time, fixing_price, ";".join(flags) if flags else "ok"


def select_fixing(
    market_df: pd.DataFrame,
    day: date,
    strategy_kind: str,
    ma_window: int | None = None,
) -> FixingDecision:
    day_df = filter_by_date(market_df, day)
    if day_df.empty:
        return FixingDecision("missing", "", None, "no_market_data")

    trend_signal = "baseline"
    quality_flags: list[str] = []
    effective_kind = strategy_kind

    if strategy_kind == "trend":
        if ma_window is None:
            raise ValueError("ma_window is required for trend strategy")
        trend_signal, trend_flag = determine_trend(market_df, day, ma_window)
        if trend_flag != "ok":
            quality_flags.append(trend_flag)
        if trend_signal == "uptrend":
            effective_kind = "early"
        elif trend_signal == "downtrend":
            effective_kind = "late"
        else:
            effective_kind = "split"
    elif strategy_kind == "baseline_early":
        effective_kind = "first"
    elif strategy_kind == "baseline_late":
        effective_kind = "last"
    elif strategy_kind == "baseline_split":
        effective_kind = "split"

    if effective_kind == "early":
        row, flag = _early_row(day_df, day)
        fixing_time = _format_time(row)
        fixing_price = _price(row)
        if flag != "ok":
            quality_flags.append(flag)
    elif effective_kind == "late":
        row, flag = _late_row(day_df, day)
        fixing_time = _format_time(row)
        fixing_price = _price(row)
        if flag != "ok":
            quality_flags.append(flag)
    elif effective_kind == "first":
        row = first_available(day_df)
        fixing_time = _format_time(row)
        fixing_price = _price(row)
    elif effective_kind == "last":
        row = last_available(day_df)
        fixing_time = _format_time(row)
        fixing_price = _price(row)
    elif effective_kind == "split":
        fixing_time, fixing_price, flag = _split_fix(day_df, day)
        if flag != "ok":
            quality_flags.append(flag)
    else:
        raise ValueError(f"Unknown strategy kind: {strategy_kind}")

    if fixing_price is None:
        quality_flags.append("missing_fixing_price")

    return FixingDecision(
        trend_signal=trend_signal,
        fixing_time=fixing_time,
        fixing_price=fixing_price,
        data_quality_flag=";".join(dict.fromkeys(quality_flags)) if quality_flags else "ok",
    )

