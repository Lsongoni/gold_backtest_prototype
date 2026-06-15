from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"
DEFAULT_CHART_DIR = DEFAULT_OUTPUT_DIR / "charts"

SALES_SHEET_NAME = "经营毛利明细"

SLIPPAGE_PER_GRAM = 0.5
TARGET_SPREAD_PER_GRAM = 6.0
DAILY_TARGET_SALES_AMOUNT = 800_000.0

SGE_SYMBOL = "Au99.99"
FUTURES_SYMBOLS = ["AU0", "au0", "AU888", "AU88"]
FUTURES_PERIODS = [5, 15, 30, 60]
MA_WINDOWS = [10, 30, 60]

MORNING_FIX_TIME = "10:30"
LATE_FIX_TIME = "23:15"
DAILY_PLAN_TIME = "10:00"

SALES_FIELD_ALIASES = {
    "order_id": ["主订单编号", "订单编号", "订单号", "order_id"],
    "order_datetime": ["订单时间", "下单时间", "成交时间", "order_datetime", "order_time"],
    "channel": ["销售渠道", "渠道", "channel"],
    "sales_amount": ["订单应付金额", "应付金额", "订单金额", "sales_amount"],
    "sales_unit_price": ["克销售单价", "克单价", "销售单价", "sales_unit_price"],
    "zhaojin_price": ["招金发布金价", "招金金价", "zhaojin_price"],
    "market_price": ["大盘金价/g", "大盘金价", "市场金价", "market_price"],
    "quantity": ["商品数量", "数量", "quantity"],
    "product_gram": ["规格（克）", "规格(克)", "规格克", "product_gram"],
    "fee": ["工费", "fee"],
    "sales_gram": ["总重量/单", "总重量", "克重", "sales_gram"],
}

NUMERIC_SALES_FIELDS = [
    "sales_amount",
    "sales_unit_price",
    "zhaojin_price",
    "market_price",
    "quantity",
    "product_gram",
    "fee",
    "sales_gram",
]

DETAIL_COLUMNS = [
    "strategy_id",
    "mode",
    "order_id",
    "date",
    "order_datetime",
    "channel",
    "sales_amount",
    "sales_gram",
    "reference_price",
    "trend_signal",
    "timeframe",
    "ma_window",
    "fixing_time",
    "fixing_price",
    "slippage",
    "spread_per_gram",
    "pnl_amount",
    "target_met",
    "data_quality_flag",
]

MARKET_SUMMARY_COLUMNS = [
    "source",
    "symbol",
    "period",
    "rows",
    "start_datetime",
    "end_datetime",
    "file_path",
    "status",
    "error_message",
]

MONTHLY_SUMMARY_COLUMNS = [
    "strategy_id",
    "mode",
    "month",
    "monthly_sales_amount",
    "monthly_sales_gram",
    "avg_spread_per_gram",
    "monthly_pnl",
    "target_met_monthly",
    "daily_or_order_target_ratio",
    "worst_spread",
    "best_spread",
    "count_records",
]

STRATEGY_RANKING_COLUMNS = [
    "strategy_id",
    "mode",
    "timeframe",
    "ma_window",
    "avg_spread_per_gram",
    "total_pnl",
    "monthly_target_ratio",
    "record_target_ratio",
    "worst_spread",
    "max_drawdown",
    "score",
]

WARNING_COLUMNS = [
    "warning_type",
    "source",
    "message",
    "context",
]

