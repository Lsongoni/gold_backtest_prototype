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
from .utils_time import combine_date_time, filter_by_date, first_available, nearest_to

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
        return float(market_values.mean()), "sales_market_price_daily_avg"
    zhaojin_values = pd.to_numeric(day_orders["zhaojin_price"], errors="coerce").dropna()
    if not zhaojin_values.empty:
        return float(zhaojin_values.mean()), "sales_zhaojin_price_daily_avg"
    return None, "missing_sales_reference_price"


def _reference_for_row(
    row: pd.Series,
    day_df: pd.DataFrame,
    warning_collector: WarningCollector,
    dataset: MarketDataset,
) -> tuple[float | None, str]:
    if _is_valid_number(row.get("market_price")):
        reference_flag = row.get("reference_flag")
        if isinstance(reference_flag, str) and reference_flag:
            return float(row["market_price"]), reference_flag
        return float(row["market_price"]), "reference_sales_market_price"
    if _is_valid_number(row.get("zhaojin_price")):
        return float(row["zhaojin_price"]), "reference_sales_zhaojin_price"

    order_dt = row.get("order_datetime")
    if pd.notna(order_dt) and not day_df.empty:
        near = nearest_to(day_df, pd.Timestamp(order_dt))
        if near is not None and _is_valid_number(near.get("close")):
            return float(near["close"]), "reference_nearest_market_close"

    first_row = first_available(day_df)
    if first_row is not None and _is_valid_number(first_row.get("close")):
        return float(first_row["close"]), "reference_day_first_market_close"

    warning_collector.add(
        "missing_reference_price",
        "backtester",
        "Could not determine reference price",
        f"dataset={dataset.symbol}_{dataset.timeframe}, order_id={row.get('order_id')}, date={row.get('date')}",
    )
    return None, "missing_reference_price"


def _build_real_order_plan(orders: pd.DataFrame) -> pd.DataFrame:
    plan = orders.copy()
    plan["date"] = plan["order_datetime"].dt.date
    return plan


def _build_daily_800k_plan(
    orders: pd.DataFrame,
    market_df: pd.DataFrame,
    warning_collector: WarningCollector,
    dataset: MarketDataset,
) -> pd.DataFrame:
    rows: list[dict] = []
    work = orders.copy()
    work["date"] = work["order_datetime"].dt.date
    for day, day_orders in work.groupby("date", sort=True):
        reference_price, reference_flag = _daily_reference_from_sales(day_orders)
        day_df = filter_by_date(market_df, day)
        if reference_price is None:
            first_row = first_available(day_df)
            if first_row is not None and _is_valid_number(first_row.get("close")):
                reference_price = float(first_row["close"])
                reference_flag = "reference_day_first_market_close"
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
                "reference_flag": reference_flag,
            }
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
) -> tuple[pd.DataFrame, list[dict]]:
    warning_collector = WarningCollector()
    records: list[dict] = []

    for dataset in market_datasets:
        logger.info("Running backtest for dataset=%s_%s mode=%s", dataset.symbol, dataset.timeframe, mode)
        market_df = add_moving_averages(dataset.data)
        if mode == "real_orders":
            plan = _build_real_order_plan(orders)
        elif mode == "daily_800k":
            plan = _build_daily_800k_plan(orders, market_df, warning_collector, dataset)
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
                reference_price, reference_flag = _reference_for_row(row, day_df, warning_collector, dataset)
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

                quality_flag = _combine_quality_flags(reference_flag, fixing.data_quality_flag)
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
                        "trend_signal": fixing.trend_signal,
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
