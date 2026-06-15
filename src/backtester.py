from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

import numpy as np
import pandas as pd

from .config import (
    DAILY_PLAN_TIME,
    DAILY_TARGET_SALES_AMOUNT,
    DETAIL_COLUMNS,
    MA_WINDOWS,
    SLIPPAGE_PER_GRAM,
    TARGET_SPREAD_PER_GRAM,
)
from .market_loader import MarketDataset
from .strategy import add_moving_averages, select_fixing
from .utils_time import combine_date_time, filter_by_date, first_available, nearest_to, register_market_date_groups

logger = logging.getLogger(__name__)


@dataclass
class WarningCollector:
    rows: list[dict] = field(default_factory=list)
    seen: set[tuple[str, str, str, str]] = field(default_factory=set)

    def add(self, warning_type: str, source: str, message: str, context: str = "") -> None:
        key = (warning_type, source, message, context)
        if key in self.seen:
            return
        self.seen.add(key)
        self.rows.append(
            {
                "warning_type": warning_type,
                "source": source,
                "message": message,
                "context": context,
            }
        )


def _is_valid_number(value) -> bool:
    return value is not None and not pd.isna(value)


def _daily_reference_from_sales(day_orders: pd.DataFrame) -> tuple[float | None, str]:
    market_values = pd.to_numeric(day_orders["market_price"], errors="coerce").dropna()
    if not market_values.empty:
        return float(market_values.mean()), "sales_market_price"
    zhaojin_values = pd.to_numeric(day_orders["zhaojin_price"], errors="coerce").dropna()
    if not zhaojin_values.empty:
        return float(zhaojin_values.mean()), "sales_zhaojin_price"
    return None, "missing_reference_price"


def _reference_for_row(
    row: pd.Series,
    day_df: pd.DataFrame,
    warning_collector: WarningCollector,
    dataset: MarketDataset,
) -> tuple[float | None, str]:
    if _is_valid_number(row.get("market_price")):
        reference_source = row.get("reference_source")
        if isinstance(reference_source, str) and reference_source:
            return float(row["market_price"]), reference_source
        return float(row["market_price"]), "sales_market_price"
    if _is_valid_number(row.get("zhaojin_price")):
        return float(row["zhaojin_price"]), "sales_zhaojin_price"

    order_dt = row.get("order_datetime")
    if pd.notna(order_dt) and not day_df.empty:
        near = nearest_to(day_df, pd.Timestamp(order_dt))
        if near is not None and _is_valid_number(near.get("close")):
            return float(near["close"]), "nearest_market_close"

    first_row = first_available(day_df)
    if first_row is not None and _is_valid_number(first_row.get("close")):
        return float(first_row["close"]), "day_first_market_close"

    warning_collector.add(
        "missing_reference_price",
        "backtester",
        "Could not determine reference price",
        f"dataset={dataset.symbol}_{dataset.timeframe}, order_id={row.get('order_id')}, date={row.get('date')}",
    )
    return None, "missing_reference_price"


def _parse_date_bound(value: str | None, end_of_day: bool = False) -> pd.Timestamp | None:
    if not value:
        return None
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    if end_of_day:
        return ts.normalize() + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    return ts.normalize()


def _market_datetime_bounds(market_df: pd.DataFrame) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    if market_df.empty or "datetime" not in market_df.columns:
        return None, None
    return market_df["datetime"].min(), market_df["datetime"].max()


def _build_real_order_plan(
    orders: pd.DataFrame,
    market_df: pd.DataFrame,
    warning_collector: WarningCollector,
    dataset: MarketDataset,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    plan = orders.copy()
    plan["date"] = plan["order_datetime"].dt.date
    user_start = _parse_date_bound(start_date)
    user_end = _parse_date_bound(end_date, end_of_day=True)
    if user_start is not None:
        plan = plan.loc[plan["order_datetime"] >= user_start].copy()
    if user_end is not None:
        plan = plan.loc[plan["order_datetime"] <= user_end].copy()

    market_start, market_end = _market_datetime_bounds(market_df)
    if market_start is None or market_end is None:
        warning_collector.add(
            "no_usable_market_data",
            "backtester",
            "Selected market dataset has no usable datetime range",
            f"dataset={dataset.symbol}_{dataset.timeframe}",
        )
        return plan.iloc[0:0].copy()

    outside = (plan["order_datetime"] < market_start) | (plan["order_datetime"] > market_end)
    if outside.any():
        warning_collector.add(
            "order_outside_market_range",
            "backtester",
            "Order datetime is outside selected market data range",
            (
                f"dataset={dataset.symbol}_{dataset.timeframe}, filtered_count={int(outside.sum())}, "
                f"market_start={market_start}, market_end={market_end}"
            ),
        )
        plan = plan.loc[~outside].copy()

    if plan.empty:
        warning_collector.add(
            "data_range_note",
            "backtester",
            "No orders remain after applying selected market data range",
            f"dataset={dataset.symbol}_{dataset.timeframe}, market_start={market_start}, market_end={market_end}",
        )
    else:
        warning_collector.add(
            "data_range_note",
            "backtester",
            "Actual real_orders backtest order range",
            (
                f"dataset={dataset.symbol}_{dataset.timeframe}, order_start={plan['order_datetime'].min()}, "
                f"order_end={plan['order_datetime'].max()}, market_start={market_start}, market_end={market_end}, "
                f"records={len(plan)}"
            ),
        )
    return plan


def _build_daily_800k_plan(
    orders: pd.DataFrame,
    market_df: pd.DataFrame,
    warning_collector: WarningCollector,
    dataset: MarketDataset,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    rows: list[dict] = []
    work = orders.copy()
    work["date"] = work["order_datetime"].dt.date
    sales_dates = set(work["date"].dropna())
    market_dates = set(market_df["datetime"].dt.date) if not market_df.empty else set()
    market_start, market_end = _market_datetime_bounds(market_df)

    user_start = _parse_date_bound(start_date)
    user_end = _parse_date_bound(end_date)
    if user_start is not None or user_end is not None:
        range_start = (user_start or pd.Timestamp(min(sales_dates or market_dates))).date()
        range_end = (user_end or pd.Timestamp(max(sales_dates or market_dates))).date()
        calendar_days = [d.date() for d in pd.date_range(range_start, range_end, freq="D")]
        missing_market_days = [day for day in calendar_days if day not in market_dates]
        if missing_market_days:
            warning_collector.add(
                "date_no_market_data",
                "backtester",
                "Requested daily_800k date has no market data and was skipped",
                f"dataset={dataset.symbol}_{dataset.timeframe}, count={len(missing_market_days)}, sample={missing_market_days[:10]}",
            )
        plan_days = [day for day in calendar_days if day in market_dates]
    else:
        skipped_sales_dates = sorted(sales_dates - market_dates)
        if skipped_sales_dates:
            warning_collector.add(
                "order_outside_market_range",
                "backtester",
                "Sales date is outside selected market data range and was skipped for daily_800k",
                f"dataset={dataset.symbol}_{dataset.timeframe}, count={len(skipped_sales_dates)}, sample={skipped_sales_dates[:10]}",
            )
        plan_days = sorted(sales_dates & market_dates)

    if not plan_days:
        warning_collector.add(
            "data_range_note",
            "backtester",
            "No daily_800k plan dates remain after applying market date availability",
            f"dataset={dataset.symbol}_{dataset.timeframe}, market_start={market_start}, market_end={market_end}",
        )

    sales_by_date = {day: day_orders for day, day_orders in work.groupby("date", sort=True)}
    for day in plan_days:
        day_orders = sales_by_date.get(day, work.iloc[0:0])
        reference_price, reference_flag = _daily_reference_from_sales(day_orders)
        day_df = filter_by_date(market_df, day)
        if reference_price is None:
            first_row = first_available(day_df)
            if first_row is not None and _is_valid_number(first_row.get("close")):
                reference_price = float(first_row["close"])
                reference_flag = "day_first_market_close"
            else:
                warning_collector.add(
                    "missing_reference_price",
                    "backtester",
                    "Daily plan date has no sales or market reference price",
                    f"dataset={dataset.symbol}_{dataset.timeframe}, date={day}",
                )

        sales_gram = np.nan
        if _is_valid_number(reference_price) and reference_price != 0:
            sales_gram = DAILY_TARGET_SALES_AMOUNT / reference_price

        rows.append(
            {
                "order_id": f"daily-{day}",
                "order_datetime": combine_date_time(day, DAILY_PLAN_TIME),
                "channel": "daily_800k",
                "sales_amount": DAILY_TARGET_SALES_AMOUNT,
                "sales_unit_price": np.nan,
                "zhaojin_price": np.nan,
                "market_price": reference_price,
                "quantity": np.nan,
                "product_gram": np.nan,
                "fee": np.nan,
                "sales_gram": sales_gram,
                "date": day,
                "reference_source": reference_flag,
            }
        )
    if rows:
        warning_collector.add(
            "data_range_note",
            "backtester",
            "Actual daily_800k backtest date range",
            (
                f"dataset={dataset.symbol}_{dataset.timeframe}, date_start={min(plan_days)}, "
                f"date_end={max(plan_days)}, market_start={market_start}, market_end={market_end}, records={len(rows)}"
            ),
        )
    return pd.DataFrame(rows)


def _strategy_specs(symbol: str, timeframe: str) -> list[dict]:
    specs = [
        {
            "strategy_id": f"baseline_early_{symbol}_{timeframe}",
            "kind": "baseline_early",
            "ma_window": np.nan,
        },
        {
            "strategy_id": f"baseline_late_{symbol}_{timeframe}",
            "kind": "baseline_late",
            "ma_window": np.nan,
        },
        {
            "strategy_id": f"baseline_split_{symbol}_{timeframe}",
            "kind": "baseline_split",
            "ma_window": np.nan,
        },
    ]
    for window in MA_WINDOWS:
        specs.append(
            {
                "strategy_id": f"trend_{symbol}_{timeframe}_MA{window}",
                "kind": "trend",
                "ma_window": window,
            }
        )
    return specs


def _combine_quality_flags(*flags: str | None) -> str:
    parts: list[str] = []
    for flag in flags:
        if not flag or pd.isna(flag):
            continue
        for part in str(flag).split(";"):
            if part and part != "ok" and part not in parts:
                parts.append(part)
    return ";".join(parts) if parts else "ok"


def _record_warning_from_flags(
    flags: str,
    warning_collector: WarningCollector,
    dataset: MarketDataset,
    strategy_id: str,
    day: date,
    order_id: str,
) -> None:
    if not flags or flags == "ok":
        return
    for flag in flags.split(";"):
        if flag == "no_market_data":
            warning_collector.add(
                "date_no_market_data",
                "backtester",
                "No market data available for date",
                f"dataset={dataset.symbol}_{dataset.timeframe}, date={day}",
            )
        elif flag == "ma_insufficient":
            warning_collector.add(
                "ma_data_insufficient",
                "backtester",
                "MA history was insufficient at the trend decision point",
                f"dataset={dataset.symbol}_{dataset.timeframe}, strategy={strategy_id}, date={day}",
            )
        elif flag == "missing_fixing_price":
            warning_collector.add(
                "time_matching_failed",
                "backtester",
                "Could not determine fixing price",
                f"dataset={dataset.symbol}_{dataset.timeframe}, strategy={strategy_id}, order_id={order_id}, date={day}",
            )


def run_backtest(
    orders: pd.DataFrame,
    market_datasets: Iterable[MarketDataset],
    mode: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[pd.DataFrame, list[dict]]:
    warning_collector = WarningCollector()
    records: list[dict] = []

    for dataset in market_datasets:
        logger.info("Running backtest for dataset=%s_%s mode=%s", dataset.symbol, dataset.timeframe, mode)
        market_df = add_moving_averages(dataset.data)
        if "date" not in market_df.columns:
            market_df["date"] = market_df["datetime"].dt.date
        register_market_date_groups(
            market_df,
            {
                day: day_df.sort_values("datetime").copy()
                for day, day_df in market_df.groupby("date", sort=False)
            },
        )
        if mode == "real_orders":
            plan = _build_real_order_plan(
                orders,
                market_df,
                warning_collector,
                dataset,
                start_date=start_date,
                end_date=end_date,
            )
        elif mode == "daily_800k":
            plan = _build_daily_800k_plan(
                orders,
                market_df,
                warning_collector,
                dataset,
                start_date=start_date,
                end_date=end_date,
            )
        else:
            raise ValueError(f"Unsupported mode: {mode}")

        specs = _strategy_specs(dataset.symbol, dataset.timeframe)
        for spec in specs:
            strategy_id = spec["strategy_id"]
            kind = spec["kind"]
            ma_window = None if pd.isna(spec["ma_window"]) else int(spec["ma_window"])
            logger.info("Evaluating strategy=%s rows=%s", strategy_id, len(plan))

            for _, row in plan.iterrows():
                day = row.get("date")
                order_id = str(row.get("order_id", ""))
                day_df = filter_by_date(market_df, day)
                reference_price, reference_source = _reference_for_row(row, day_df, warning_collector, dataset)
                fixing = select_fixing(market_df, day, kind, ma_window=ma_window)

                sales_gram = row.get("sales_gram")
                spread = np.nan
                pnl = np.nan
                target_met = False

                if _is_valid_number(reference_price) and _is_valid_number(fixing.fixing_price):
                    spread = float(reference_price) - float(fixing.fixing_price) - SLIPPAGE_PER_GRAM
                    if _is_valid_number(sales_gram):
                        pnl = spread * float(sales_gram)
                    target_met = bool(spread >= TARGET_SPREAD_PER_GRAM)

                if not _is_valid_number(sales_gram):
                    warning_collector.add(
                        "missing_sales_gram",
                        "backtester",
                        "sales_gram is missing, pnl_amount cannot be calculated",
                        f"mode={mode}, order_id={order_id}, date={day}",
                    )

                quality_flag = _combine_quality_flags(fixing.data_quality_flag)
                _record_warning_from_flags(quality_flag, warning_collector, dataset, strategy_id, day, order_id)

                records.append(
                    {
                        "strategy_id": strategy_id,
                        "mode": mode,
                        "order_id": order_id,
                        "date": day,
                        "order_datetime": row.get("order_datetime"),
                        "channel": row.get("channel"),
                        "sales_amount": row.get("sales_amount"),
                        "sales_gram": sales_gram,
                        "reference_price": reference_price,
                        "reference_source": reference_source,
                        "trend_signal": fixing.trend_signal,
                        "trend_decision_time": fixing.trend_decision_time,
                        "timeframe": dataset.timeframe,
                        "ma_window": spec["ma_window"],
                        "fixing_time": fixing.fixing_time,
                        "fixing_price": fixing.fixing_price,
                        "slippage": SLIPPAGE_PER_GRAM,
                        "spread_per_gram": spread,
                        "pnl_amount": pnl,
                        "target_met": target_met,
                        "data_quality_flag": quality_flag,
                    }
                )

    detail = pd.DataFrame(records, columns=DETAIL_COLUMNS)
    return detail, warning_collector.rows
