from __future__ import annotations

import numpy as np
import pandas as pd

from .config import MONTHLY_SUMMARY_COLUMNS, STRATEGY_RANKING_COLUMNS, TARGET_SPREAD_PER_GRAM
from .utils_time import safe_month_str


def _weighted_average_spread(group: pd.DataFrame) -> float:
    valid = group.dropna(subset=["spread_per_gram", "sales_gram"])
    valid = valid.loc[valid["sales_gram"] > 0]
    if valid.empty:
        return np.nan
    return float((valid["spread_per_gram"] * valid["sales_gram"]).sum() / valid["sales_gram"].sum())


def build_monthly_summary(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame(columns=MONTHLY_SUMMARY_COLUMNS)

    work = detail.copy()
    work["month"] = work["date"].apply(safe_month_str)
    rows: list[dict] = []

    for (strategy_id, mode, month), group in work.groupby(["strategy_id", "mode", "month"], dropna=False):
        valid_spread = group["spread_per_gram"].dropna()
        monthly_pnl = group["pnl_amount"].dropna().sum() if group["pnl_amount"].notna().any() else np.nan
        monthly_sales_gram = group["sales_gram"].dropna().sum() if group["sales_gram"].notna().any() else np.nan
        avg_spread = _weighted_average_spread(group)
        valid_target = group.loc[group["spread_per_gram"].notna(), "target_met"]
        rows.append(
            {
                "strategy_id": strategy_id,
                "mode": mode,
                "month": month,
                "monthly_sales_amount": group["sales_amount"].dropna().sum()
                if group["sales_amount"].notna().any()
                else np.nan,
                "monthly_sales_gram": monthly_sales_gram,
                "avg_spread_per_gram": avg_spread,
                "monthly_pnl": monthly_pnl,
                "target_met_monthly": bool(avg_spread >= TARGET_SPREAD_PER_GRAM)
                if not pd.isna(avg_spread)
                else False,
                "daily_or_order_target_ratio": float(valid_target.mean()) if not valid_target.empty else np.nan,
                "worst_spread": float(valid_spread.min()) if not valid_spread.empty else np.nan,
                "best_spread": float(valid_spread.max()) if not valid_spread.empty else np.nan,
                "count_records": len(group),
            }
        )

    return pd.DataFrame(rows, columns=MONTHLY_SUMMARY_COLUMNS)


def _max_drawdown(group: pd.DataFrame) -> float:
    if group.empty:
        return np.nan
    work = group.sort_values(["date", "order_datetime"]).copy()
    cumulative = work["pnl_amount"].fillna(0).cumsum()
    if cumulative.empty:
        return np.nan
    running_max = cumulative.cummax()
    drawdown = cumulative - running_max
    return float(drawdown.min())


def build_strategy_ranking(detail: pd.DataFrame, monthly_summary: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame(columns=STRATEGY_RANKING_COLUMNS)

    month_ratio = {}
    if not monthly_summary.empty:
        for (strategy_id, mode), group in monthly_summary.groupby(["strategy_id", "mode"], dropna=False):
            if group.empty:
                month_ratio[(strategy_id, mode)] = np.nan
            else:
                month_ratio[(strategy_id, mode)] = float(group["target_met_monthly"].mean())

    rows: list[dict] = []
    for (strategy_id, mode, timeframe, ma_window), group in detail.groupby(
        ["strategy_id", "mode", "timeframe", "ma_window"], dropna=False
    ):
        avg_spread = _weighted_average_spread(group)
        total_pnl = group["pnl_amount"].dropna().sum() if group["pnl_amount"].notna().any() else np.nan
        valid_target = group.loc[group["spread_per_gram"].notna(), "target_met"]
        record_ratio = float(valid_target.mean()) if not valid_target.empty else np.nan
        valid_spread = group["spread_per_gram"].dropna()
        worst_spread = float(valid_spread.min()) if not valid_spread.empty else np.nan
        monthly_target_ratio = month_ratio.get((strategy_id, mode), np.nan)
        drawdown = _max_drawdown(group)

        if pd.isna(avg_spread) or pd.isna(monthly_target_ratio):
            score = np.nan
        else:
            penalty = abs(min(0.0, worst_spread)) * 0.2 if not pd.isna(worst_spread) else 0.0
            score = avg_spread * 0.5 + monthly_target_ratio * 10 - penalty

        rows.append(
            {
                "strategy_id": strategy_id,
                "mode": mode,
                "timeframe": timeframe,
                "ma_window": ma_window,
                "avg_spread_per_gram": avg_spread,
                "total_pnl": total_pnl,
                "monthly_target_ratio": monthly_target_ratio,
                "record_target_ratio": record_ratio,
                "worst_spread": worst_spread,
                "max_drawdown": drawdown,
                "score": score,
            }
        )

    ranking = pd.DataFrame(rows, columns=STRATEGY_RANKING_COLUMNS)
    if not ranking.empty:
        ranking = ranking.sort_values(["score", "avg_spread_per_gram"], ascending=[False, False], na_position="last")
    return ranking.reset_index(drop=True)

