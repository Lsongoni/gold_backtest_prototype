from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import FUTURES_PERIODS, MARKET_SUMMARY_COLUMNS

logger = logging.getLogger(__name__)


@dataclass
class MarketDataset:
    symbol: str
    period: int
    timeframe: str
    file_path: Path
    data: pd.DataFrame


def _warning(warning_type: str, source: str, message: str, context: str = "") -> dict:
    return {
        "warning_type": warning_type,
        "source": source,
        "message": message,
        "context": context,
    }


def _normalize_col(value: object) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("\n", "").replace("\r", "").replace("\t", "")
    text = text.replace("（", "(").replace("）", ")").replace("／", "/")
    text = re.sub(r"\s+", "", text)
    return text


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    normalized = {_normalize_col(col): col for col in df.columns}
    for candidate in candidates:
        key = _normalize_col(candidate)
        if key in normalized:
            return normalized[key]
    for col in df.columns:
        col_key = _normalize_col(col)
        for candidate in candidates:
            cand_key = _normalize_col(candidate)
            if cand_key and (cand_key in col_key or col_key in cand_key):
                return col
    return None


def _find_exact_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    normalized = {_normalize_col(col): col for col in df.columns}
    for candidate in candidates:
        key = _normalize_col(candidate)
        if key in normalized:
            return normalized[key]
    return None


def parse_futures_filename(path: Path) -> tuple[str, int] | None:
    match = re.match(r"gold_futures_(.+)_(\d+)min\.csv$", path.name)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def standardize_market_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["datetime", "close"])

    datetime_col = _find_exact_col(df, ["datetime", "日期时间", "交易时间"])
    date_col = _find_exact_col(df, ["date", "日期"])
    time_col = _find_exact_col(df, ["time", "时间"])
    close_col = _find_col(df, ["close", "收盘", "收盘价", "最新价", "价格"])

    if datetime_col is not None:
        dt = pd.to_datetime(df[datetime_col], errors="coerce")
    elif date_col is not None and time_col is not None:
        dt = pd.to_datetime(df[date_col].astype(str) + " " + df[time_col].astype(str), errors="coerce")
    elif date_col is not None:
        dt = pd.to_datetime(df[date_col], errors="coerce")
    else:
        dt = pd.Series(pd.NaT, index=df.index)

    out = pd.DataFrame(index=df.index)
    out["datetime"] = dt
    if close_col is None:
        out["close"] = pd.NA
    else:
        out["close"] = pd.to_numeric(df[close_col], errors="coerce")

    for optional_col in ["open", "high", "low", "volume", "hold", "position"]:
        source_col = _find_col(df, [optional_col, optional_col.upper(), optional_col.capitalize()])
        if source_col is not None:
            out[optional_col] = pd.to_numeric(df[source_col], errors="coerce")

    out = out.dropna(subset=["datetime", "close"]).sort_values("datetime").reset_index(drop=True)
    return out


def summarize_existing_market_data(data_dir: str | Path) -> pd.DataFrame:
    data_path = Path(data_dir)
    rows: list[dict] = []
    if not data_path.exists():
        return pd.DataFrame(columns=MARKET_SUMMARY_COLUMNS)

    sge_file = data_path / "sge_au9999_daily.csv"
    if sge_file.exists():
        try:
            raw = pd.read_csv(sge_file)
            std = standardize_market_frame(raw)
            rows.append(
                {
                    "source": "csv_sge_spot",
                    "symbol": "Au99.99",
                    "period": "daily",
                    "rows": len(raw),
                    "start_datetime": std["datetime"].min() if not std.empty else "",
                    "end_datetime": std["datetime"].max() if not std.empty else "",
                    "file_path": str(sge_file),
                    "status": "existing",
                    "error_message": "",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "source": "csv_sge_spot",
                    "symbol": "Au99.99",
                    "period": "daily",
                    "rows": 0,
                    "start_datetime": "",
                    "end_datetime": "",
                    "file_path": str(sge_file),
                    "status": "failed",
                    "error_message": str(exc),
                }
            )

    for file_path in sorted(data_path.glob("gold_futures_*_*min.csv")):
        parsed = parse_futures_filename(file_path)
        if parsed is None:
            continue
        symbol, period = parsed
        try:
            raw = pd.read_csv(file_path)
            std = standardize_market_frame(raw)
            rows.append(
                {
                    "source": "csv_futures_minute",
                    "symbol": symbol,
                    "period": str(period),
                    "rows": len(std),
                    "start_datetime": std["datetime"].min() if not std.empty else "",
                    "end_datetime": std["datetime"].max() if not std.empty else "",
                    "file_path": str(file_path),
                    "status": "existing" if not std.empty else "empty",
                    "error_message": "",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "source": "csv_futures_minute",
                    "symbol": symbol,
                    "period": str(period),
                    "rows": 0,
                    "start_datetime": "",
                    "end_datetime": "",
                    "file_path": str(file_path),
                    "status": "failed",
                    "error_message": str(exc),
                }
            )
    return pd.DataFrame(rows, columns=MARKET_SUMMARY_COLUMNS)


def load_market_datasets(
    data_dir: str | Path,
    symbol: str | None = None,
    period: int | None = None,
) -> tuple[list[MarketDataset], list[dict]]:
    data_path = Path(data_dir)
    warnings: list[dict] = []
    datasets: list[MarketDataset] = []

    if not data_path.exists():
        warnings.append(_warning("missing_data_dir", "market_loader", "Market data directory does not exist", str(data_path)))
        return datasets, warnings

    files = sorted(data_path.glob("gold_futures_*_*min.csv"))
    for file_path in files:
        parsed = parse_futures_filename(file_path)
        if parsed is None:
            continue
        file_symbol, file_period = parsed
        if symbol and file_symbol.lower() != symbol.lower():
            continue
        if period and file_period != int(period):
            continue
        if file_period not in FUTURES_PERIODS:
            continue

        try:
            raw = pd.read_csv(file_path)
            data = standardize_market_frame(raw)
            if data.empty:
                warnings.append(
                    _warning(
                        "empty_market_data",
                        "market_loader",
                        "Market CSV had no usable datetime/close rows",
                        str(file_path),
                    )
                )
                continue
            datasets.append(
                MarketDataset(
                    symbol=file_symbol,
                    period=file_period,
                    timeframe=f"{file_period}min",
                    file_path=file_path,
                    data=data,
                )
            )
            logger.info("Loaded market dataset %s rows=%s", file_path, len(data))
        except Exception as exc:
            warnings.append(
                _warning("market_csv_load_failed", "market_loader", str(exc), str(file_path))
            )

    if not datasets:
        warnings.append(
            _warning(
                "no_usable_market_data",
                "market_loader",
                "No usable futures minute CSV files were found",
                f"data_dir={data_path}, symbol={symbol}, period={period}",
            )
        )

    return datasets, warnings
