from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import FUTURES_PERIODS, KAGGLE_DEFAULT_PERIODS, MARKET_SUMMARY_COLUMNS

logger = logging.getLogger(__name__)

XAU_TROY_OUNCE_GRAMS = 31.1035
KAGGLE_TIMEFRAME_MAP = {
    "1m": (1, "1min"),
    "5m": (5, "5min"),
    "15m": (15, "15min"),
    "30m": (30, "30min"),
    "1h": (60, "60min"),
    "4h": (240, "4h"),
    "1d": (1440, "1d"),
    "1w": (10080, "1w"),
    "1Month": (43200, "1M"),
}


@dataclass
class MarketDataset:
    symbol: str
    period: int
    timeframe: str
    file_path: Path
    data: pd.DataFrame
    source: str = "akshare"
    original_unit: str = "cny_per_gram"
    converted_unit: str = "cny_per_gram"
    fx_rate: float | None = None


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


def parse_kaggle_xau_filename(path: Path) -> tuple[int, str] | None:
    match = re.match(r"XAU_(.+)_data\.csv$", path.name)
    if not match:
        return None
    return KAGGLE_TIMEFRAME_MAP.get(match.group(1))


def _market_summary_row(
    source: str,
    symbol: str,
    period: str | int | None,
    timeframe: str,
    rows: int,
    start_datetime,
    end_datetime,
    file_path: str,
    status: str,
    error_message: str = "",
    original_unit: str = "",
    converted_unit: str = "",
    fx_rate: float | None = None,
) -> dict:
    return {
        "source": source,
        "symbol": symbol,
        "period": "" if period is None else str(period),
        "timeframe": timeframe,
        "rows": rows,
        "start_datetime": start_datetime,
        "end_datetime": end_datetime,
        "file_path": file_path,
        "status": status,
        "error_message": error_message,
        "original_unit": original_unit,
        "converted_unit": converted_unit,
        "fx_rate": fx_rate,
    }


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
    out["date"] = out["datetime"].dt.date
    return out


def _to_numeric(series: pd.Series) -> pd.Series:
    if series.dtype == object:
        series = series.astype(str).str.replace(",", ".", regex=False)
    return pd.to_numeric(series, errors="coerce")


def load_kaggle_xau_file(
    file_path: str | Path,
    price_unit: str = "cny_per_gram",
    fx_rate: float = 7.2,
) -> MarketDataset:
    path = Path(file_path)
    parsed = parse_kaggle_xau_filename(path)
    if parsed is None:
        raise ValueError(f"Could not infer Kaggle XAU timeframe from file name: {path.name}")
    period, timeframe = parsed

    raw = pd.read_csv(path, sep=";")
    required = ["Date", "Open", "High", "Low", "Close", "Volume"]
    missing = [col for col in required if col not in raw.columns]
    if missing:
        raise ValueError(f"Missing required Kaggle columns: {missing}")

    data = pd.DataFrame()
    data["datetime"] = pd.to_datetime(raw["Date"], format="%Y.%m.%d %H:%M", errors="coerce")
    for source_col, target_col in [
        ("Open", "open"),
        ("High", "high"),
        ("Low", "low"),
        ("Close", "close"),
        ("Volume", "volume"),
    ]:
        data[target_col] = _to_numeric(raw[source_col])

    if price_unit == "xauusd":
        factor = fx_rate / XAU_TROY_OUNCE_GRAMS
        for col in ["open", "high", "low", "close"]:
            data[col] = data[col] * factor
        converted_unit = "cny_per_gram"
    elif price_unit == "cny_per_gram":
        converted_unit = "cny_per_gram"
    else:
        raise ValueError(f"Unsupported price_unit: {price_unit}")

    data = data.dropna(subset=["datetime", "close"]).sort_values("datetime").reset_index(drop=True)
    data["date"] = data["datetime"].dt.date
    logger.info(
        "Loaded Kaggle XAU file=%s rows=%s range=%s to %s unit=%s converted=%s",
        path,
        len(data),
        data["datetime"].min() if not data.empty else "",
        data["datetime"].max() if not data.empty else "",
        price_unit,
        converted_unit,
    )
    return MarketDataset(
        symbol="XAU",
        period=period,
        timeframe=timeframe,
        file_path=path,
        data=data,
        source="kaggle_xau",
        original_unit=price_unit,
        converted_unit=converted_unit,
        fx_rate=fx_rate,
    )


def load_kaggle_xau_files(
    file_paths: list[str | Path],
    price_unit: str = "cny_per_gram",
    fx_rate: float = 7.2,
    period: int | None = None,
    default_periods: list[int] | None = None,
) -> tuple[list[MarketDataset], pd.DataFrame, list[dict]]:
    datasets: list[MarketDataset] = []
    summary_rows: list[dict] = []
    warnings: list[dict] = []
    allowed_periods = [period] if period is not None else (default_periods or KAGGLE_DEFAULT_PERIODS)

    for raw_path in file_paths:
        path = Path(raw_path)
        parsed = parse_kaggle_xau_filename(path)
        if parsed is None:
            message = "Could not infer Kaggle XAU timeframe from file name"
            warnings.append(_warning("kaggle_file_name_unrecognized", "market_loader", message, str(path)))
            summary_rows.append(
                _market_summary_row(
                    "kaggle_xau",
                    "XAU",
                    "",
                    "",
                    0,
                    "",
                    "",
                    str(path),
                    "failed",
                    message,
                    price_unit,
                    "",
                    fx_rate,
                )
            )
            continue

        file_period, timeframe = parsed
        if file_period not in allowed_periods:
            message = "Kaggle XAU timeframe skipped by period filter"
            warnings.append(
                _warning(
                    "market_file_skipped_by_period",
                    "market_loader",
                    message,
                    f"file={path}, period={file_period}, allowed_periods={allowed_periods}",
                )
            )
            summary_rows.append(
                _market_summary_row(
                    "kaggle_xau",
                    "XAU",
                    file_period,
                    timeframe,
                    0,
                    "",
                    "",
                    str(path),
                    "skipped",
                    message,
                    price_unit,
                    "",
                    fx_rate,
                )
            )
            continue

        try:
            dataset = load_kaggle_xau_file(path, price_unit=price_unit, fx_rate=fx_rate)
            datasets.append(dataset)
            summary_rows.append(
                _market_summary_row(
                    "kaggle_xau",
                    dataset.symbol,
                    dataset.period,
                    dataset.timeframe,
                    len(dataset.data),
                    dataset.data["datetime"].min() if not dataset.data.empty else "",
                    dataset.data["datetime"].max() if not dataset.data.empty else "",
                    str(path),
                    "success" if not dataset.data.empty else "empty",
                    "",
                    dataset.original_unit,
                    dataset.converted_unit,
                    dataset.fx_rate,
                )
            )
            if dataset.data.empty:
                warnings.append(_warning("empty_market_data", "market_loader", "Kaggle XAU file has no usable rows", str(path)))
        except Exception as exc:
            warnings.append(_warning("kaggle_market_load_failed", "market_loader", str(exc), str(path)))
            summary_rows.append(
                _market_summary_row(
                    "kaggle_xau",
                    "XAU",
                    file_period,
                    timeframe,
                    0,
                    "",
                    "",
                    str(path),
                    "failed",
                    str(exc),
                    price_unit,
                    "",
                    fx_rate,
                )
            )

    if not datasets:
        warnings.append(
            _warning(
                "no_usable_market_data",
                "market_loader",
                "No usable Kaggle XAU files were loaded",
                f"files={file_paths}, period={period}, price_unit={price_unit}",
            )
        )

    return datasets, pd.DataFrame(summary_rows, columns=MARKET_SUMMARY_COLUMNS), warnings


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
                _market_summary_row(
                    "csv_sge_spot",
                    "Au99.99",
                    "daily",
                    "1d",
                    len(raw),
                    std["datetime"].min() if not std.empty else "",
                    std["datetime"].max() if not std.empty else "",
                    str(sge_file),
                    "existing",
                    "",
                    "cny_per_gram",
                    "cny_per_gram",
                    None,
                )
            )
        except Exception as exc:
            rows.append(
                _market_summary_row("csv_sge_spot", "Au99.99", "daily", "1d", 0, "", "", str(sge_file), "failed", str(exc))
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
                _market_summary_row(
                    "csv_futures_minute",
                    symbol,
                    period,
                    f"{period}min",
                    len(std),
                    std["datetime"].min() if not std.empty else "",
                    std["datetime"].max() if not std.empty else "",
                    str(file_path),
                    "existing" if not std.empty else "empty",
                    "",
                    "cny_per_gram",
                    "cny_per_gram",
                    None,
                )
            )
        except Exception as exc:
            rows.append(
                _market_summary_row("csv_futures_minute", symbol, period, f"{period}min", 0, "", "", str(file_path), "failed", str(exc))
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
                    source="akshare",
                    original_unit="cny_per_gram",
                    converted_unit="cny_per_gram",
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
