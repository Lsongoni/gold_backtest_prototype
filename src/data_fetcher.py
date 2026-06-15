from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from .config import FUTURES_PERIODS, FUTURES_SYMBOLS, MARKET_SUMMARY_COLUMNS, SGE_SYMBOL

logger = logging.getLogger(__name__)


def _warning(warning_type: str, source: str, message: str, context: str = "") -> dict:
    return {
        "warning_type": warning_type,
        "source": source,
        "message": message,
        "context": context,
    }


def _extract_datetime_bounds(df: pd.DataFrame) -> tuple[str, str]:
    if df.empty:
        return "", ""

    candidates = [
        "datetime",
        "date",
        "日期",
        "时间",
        "交易时间",
        "Date",
        "time",
    ]
    for col in candidates:
        if col in df.columns:
            dt = pd.to_datetime(df[col], errors="coerce")
            if dt.notna().any():
                return str(dt.min()), str(dt.max())
    return "", ""


def _summary_row(
    source: str,
    symbol: str,
    period: str | int | None,
    rows: int,
    file_path: str,
    status: str,
    error_message: str = "",
    start_datetime: str = "",
    end_datetime: str = "",
) -> dict:
    return {
        "source": source,
        "symbol": symbol,
        "period": "" if period is None else str(period),
        "rows": rows,
        "start_datetime": start_datetime,
        "end_datetime": end_datetime,
        "file_path": file_path,
        "status": status,
        "error_message": error_message,
    }


def fetch_market_data(
    data_dir: str | Path,
    symbols: list[str] | None = None,
    periods: list[int] | None = None,
) -> tuple[pd.DataFrame, list[dict]]:
    """Fetch free AKShare market data and save raw CSV files."""
    data_path = Path(data_dir)
    data_path.mkdir(parents=True, exist_ok=True)
    symbols = symbols or FUTURES_SYMBOLS
    periods = periods or FUTURES_PERIODS
    summary: list[dict] = []
    warnings: list[dict] = []

    try:
        import akshare as ak
    except Exception as exc:  # pragma: no cover - depends on local env
        message = f"Could not import akshare: {exc}"
        logger.warning(message)
        warnings.append(_warning("akshare_import_failed", "data_fetcher", message))
        summary.append(_summary_row("akshare_sge_spot", SGE_SYMBOL, "daily", 0, "", "failed", message))
        for symbol in symbols:
            for period in periods:
                summary.append(_summary_row("akshare_sina_futures_minute", symbol, period, 0, "", "failed", message))
        return pd.DataFrame(summary, columns=MARKET_SUMMARY_COLUMNS), warnings

    logger.info("Fetching SGE spot daily data for %s", SGE_SYMBOL)
    try:
        sge_df = ak.spot_hist_sge(symbol=SGE_SYMBOL)
        file_path = data_path / "sge_au9999_daily.csv"
        if sge_df is None or sge_df.empty:
            message = "SGE spot daily API returned empty data"
            logger.warning(message)
            warnings.append(_warning("empty_market_data", "data_fetcher", message, f"symbol={SGE_SYMBOL}"))
            summary.append(_summary_row("akshare_sge_spot", SGE_SYMBOL, "daily", 0, str(file_path), "empty", message))
        else:
            sge_df.to_csv(file_path, index=False, encoding="utf-8-sig")
            start_dt, end_dt = _extract_datetime_bounds(sge_df)
            logger.info("Saved %s SGE rows to %s", len(sge_df), file_path)
            summary.append(
                _summary_row(
                    "akshare_sge_spot",
                    SGE_SYMBOL,
                    "daily",
                    len(sge_df),
                    str(file_path),
                    "success",
                    "",
                    start_dt,
                    end_dt,
                )
            )
    except Exception as exc:  # pragma: no cover - network/API dependent
        message = str(exc)
        logger.warning("SGE spot fetch failed: %s", message)
        warnings.append(_warning("market_fetch_failed", "data_fetcher", message, f"symbol={SGE_SYMBOL}"))
        summary.append(_summary_row("akshare_sge_spot", SGE_SYMBOL, "daily", 0, "", "failed", message))

    for symbol in symbols:
        for period in periods:
            logger.info("Fetching futures minute data: symbol=%s period=%s", symbol, period)
            file_path = data_path / f"gold_futures_{symbol}_{period}min.csv"
            try:
                futures_df = ak.futures_zh_minute_sina(symbol=symbol, period=str(period))
                if futures_df is None or futures_df.empty:
                    message = "Futures minute API returned empty data"
                    logger.warning("%s for symbol=%s period=%s", message, symbol, period)
                    warnings.append(
                        _warning(
                            "empty_market_data",
                            "data_fetcher",
                            message,
                            f"symbol={symbol}, period={period}",
                        )
                    )
                    summary.append(
                        _summary_row(
                            "akshare_sina_futures_minute",
                            symbol,
                            period,
                            0,
                            str(file_path),
                            "empty",
                            message,
                        )
                    )
                    continue

                futures_df.to_csv(file_path, index=False, encoding="utf-8-sig")
                start_dt, end_dt = _extract_datetime_bounds(futures_df)
                logger.info("Saved %s futures rows to %s", len(futures_df), file_path)
                summary.append(
                    _summary_row(
                        "akshare_sina_futures_minute",
                        symbol,
                        period,
                        len(futures_df),
                        str(file_path),
                        "success",
                        "",
                        start_dt,
                        end_dt,
                    )
                )
            except Exception as exc:  # pragma: no cover - network/API dependent
                message = str(exc)
                logger.warning("Futures fetch failed: symbol=%s period=%s error=%s", symbol, period, message)
                warnings.append(
                    _warning(
                        "market_fetch_failed",
                        "data_fetcher",
                        message,
                        f"symbol={symbol}, period={period}",
                    )
                )
                summary.append(
                    _summary_row(
                        "akshare_sina_futures_minute",
                        symbol,
                        period,
                        0,
                        str(file_path),
                        "failed",
                        message,
                    )
                )

    return pd.DataFrame(summary, columns=MARKET_SUMMARY_COLUMNS), warnings

