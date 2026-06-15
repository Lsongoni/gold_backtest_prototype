from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from src.backtester import run_backtest
from src.config import DEFAULT_DATA_DIR, DEFAULT_OUTPUT_DIR
from src.data_fetcher import fetch_market_data
from src.market_loader import load_market_datasets, summarize_existing_market_data
from src.metrics import build_monthly_summary, build_strategy_ranking
from src.report_writer import write_report
from src.sales_loader import load_sales_orders


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Free-data prototype for online investment gold fixing backtest."
    )
    parser.add_argument(
        "--sales-file",
        default="宝瑞雅投资金销售.xlsx",
        help="Path to the sales Excel workbook.",
    )
    parser.add_argument(
        "--mode",
        choices=["real_orders", "daily_800k"],
        required=True,
        help="Backtest mode.",
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip AKShare fetching and use existing CSV files in data directory.",
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="Use only one downloaded futures symbol, for example AU0.",
    )
    parser.add_argument(
        "--period",
        type=int,
        choices=[5, 15, 30, 60],
        default=None,
        help="Use only one downloaded futures period.",
    )
    parser.add_argument(
        "--data-dir",
        default=str(DEFAULT_DATA_DIR),
        help="Directory for market CSV files.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for output Excel and charts.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level, for example INFO or DEBUG.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _setup_logging(args.log_level)
    logger = logging.getLogger("app")

    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_warnings: list[dict] = []
    logger.info("Starting backtest prototype")
    orders_clean, sales_warnings = load_sales_orders(args.sales_file)
    all_warnings.extend(sales_warnings)

    if args.skip_fetch:
        logger.info("Skipping AKShare fetch; reading existing CSV files from %s", data_dir)
        market_summary = summarize_existing_market_data(data_dir)
    else:
        market_summary, fetch_warnings = fetch_market_data(data_dir)
        all_warnings.extend(fetch_warnings)

    market_datasets, loader_warnings = load_market_datasets(
        data_dir,
        symbol=args.symbol,
        period=args.period,
    )
    all_warnings.extend(loader_warnings)

    if not market_datasets:
        logger.error("No usable market datasets. Report will contain cleaned orders and warnings only.")
        daily_detail = pd.DataFrame()
        monthly_summary = pd.DataFrame()
        strategy_ranking = pd.DataFrame()
    else:
        daily_detail, backtest_warnings = run_backtest(orders_clean, market_datasets, args.mode)
        all_warnings.extend(backtest_warnings)
        monthly_summary = build_monthly_summary(daily_detail)
        strategy_ranking = build_strategy_ranking(daily_detail, monthly_summary)

    excel_path = write_report(
        output_dir=output_dir,
        orders_clean=orders_clean,
        market_data_summary=market_summary,
        daily_detail=daily_detail,
        monthly_summary=monthly_summary,
        strategy_ranking=strategy_ranking,
        warnings=all_warnings,
    )

    logger.info("Backtest complete: %s", excel_path)
    print(f"Backtest report written to: {excel_path}")
    print(f"Charts written to: {output_dir / 'charts'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

