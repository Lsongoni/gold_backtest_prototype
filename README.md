# 线上投资金点价回测免费数据原型版

这个项目用销售历史 Excel + 免费行情数据，跑通线上投资金点价回测的完整流程。当前支持 AKShare 免费行情，也支持 Kaggle XAU 历史 CSV。第一版重点是流程、输出和可替换性，不追求完全复刻真实 AU9999/mAuT+D 卖一价点价。

## 1. 安装依赖

建议使用 Python 3.10+。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell 可使用：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. 运行方式

真实订单模式：

```bash
python app.py --sales-file "宝瑞雅投资金销售.xlsx" --mode real_orders
```

80 万/天目标压力测试模式：

```bash
python app.py --sales-file "宝瑞雅投资金销售.xlsx" --mode daily_800k
```

如果 `data/` 里已经有行情 CSV，可以跳过 AKShare 拉取：

```bash
python app.py --sales-file "宝瑞雅投资金销售.xlsx" --mode real_orders --skip-fetch
```

指定已下载的行情文件口径：

```bash
python app.py --sales-file "宝瑞雅投资金销售.xlsx" --mode real_orders --skip-fetch --symbol AU0 --period 5
```

真实订单模式，使用 Kaggle 5 分钟 XAU 数据：

```bash
python app.py --sales-file "宝瑞雅投资金销售.xlsx" --mode real_orders --market-file "data/XAU_5m_data.csv" --market-source kaggle_xau --price-unit xauusd --fx-rate 7.2
```

80 万压力测试，使用 Kaggle 5 分钟 XAU 数据：

```bash
python app.py --sales-file "宝瑞雅投资金销售.xlsx" --mode daily_800k --market-file "data/XAU_5m_data.csv" --market-source kaggle_xau --price-unit xauusd --fx-rate 7.2
```

多个周期一起跑：

```bash
python app.py --sales-file "宝瑞雅投资金销售.xlsx" --mode real_orders --market-files "data/XAU_5m_data.csv,data/XAU_15m_data.csv,data/XAU_30m_data.csv,data/XAU_1h_data.csv" --market-source kaggle_xau --price-unit xauusd --fx-rate 7.2
```

可选时间范围：

```bash
python app.py --sales-file "宝瑞雅投资金销售.xlsx" --mode real_orders --market-file "data/XAU_5m_data.csv" --market-source kaggle_xau --price-unit xauusd --fx-rate 7.2 --start-date 2025-11-28 --end-date 2026-01-30
```

## 3. 数据源说明和限制

当前版本支持两类免费/离线数据源。

AKShare 免费数据：

- `ak.spot_hist_sge(symbol="Au99.99")` 拉取上海黄金交易所 Au99.99 日线，保存为 `data/sge_au9999_daily.csv`。
- `ak.futures_zh_minute_sina(symbol=..., period=...)` 拉取沪金期货分钟数据，尝试 `AU0`、`au0`、`AU888`、`AU88` 和 `5/15/30/60` 分钟周期。

免费分钟数据可能历史很短，接口也可能临时为空或失败。程序会记录 warning 并继续尝试其它 symbol/period。

Kaggle XAU CSV 数据：

- 文件格式为 `Date;Open;High;Low;Close;Volume`，分隔符是英文分号 `;`。
- `Date` 格式为 `YYYY.MM.DD HH:MM`，例如 `2004.06.11 07:15`。
- 支持文件名包括 `XAU_1m_data.csv`、`XAU_5m_data.csv`、`XAU_15m_data.csv`、`XAU_30m_data.csv`、`XAU_1h_data.csv`、`XAU_4h_data.csv`、`XAU_1d_data.csv`、`XAU_1w_data.csv`、`XAU_1Month_data.csv`。
- 默认只跑 `5min`、`15min`、`30min`、`60min`。`1m`、`4h` 等非默认周期只有在明确指定 `--period` 时才会跑，避免数据量过大。
- 如果 `--price-unit xauusd`，程序会按 `xau_price * fx_rate / 31.1035` 转换为人民币/克。
- 如果 `--price-unit cny_per_gram`，程序不做价格换算。

重要限制：

- 当前用沪金期货分钟 close 做盘中择时代理。
- Kaggle XAU 是国际黄金代理数据，不等同于 AU9999/mAuT+D。
- 这不等同于 AU9999/mAuT+D 的真实历史分钟行情。
- 这不等同于真实卖一价点价成交结果。
- 正式版应替换成 AU9999/mAuT+D 历史分钟行情和卖一价 API。
- 当前滑点固定为 `0.5 元/克`，点价手续费为 0。
- 如果销售订单超过所选行情数据时间范围，程序会自动过滤这些订单，并在 `warnings` sheet 写入 `order_outside_market_range`。

## 4. 回测模式

`real_orders`：

- 使用 Excel 中每一笔真实订单。
- 按订单时间、订单金额、订单克重计算价差和收益。

`daily_800k`：

- 默认按销售数据中出现的日期和行情日期的交集生成每日计划。
- 每天销售额固定为 `800000`。
- 每日克重 = `800000 / reference_price`。
- 默认订单时间为当天 `10:00`。
- 如果提供 `--start-date` / `--end-date`，按指定区间生成计划，但仍只保留有行情的日期；无行情日期会写入 warnings。

## 5. 价格口径

`reference_price` 优先级：

1. 销售表中的 `大盘金价/g`；
2. 销售表中的 `招金发布金价`；
3. 订单时间附近最近行情 close；
4. 订单当天第一个可用行情 close。

`fixing_price` 使用策略指定点价时间对应的分钟 close。

净价差：

```text
spread_per_gram = reference_price - fixing_price - 0.5
```

收益：

```text
pnl_amount = spread_per_gram * sales_gram
```

## 6. 策略

趋势策略测试：

- K 线周期：`5min`、`15min`、`30min`、`60min`
- 均线参数：`MA10`、`MA30`、`MA60`

趋势规则：

- 当前价格 > MA：上涨趋势，尽早点价。
- 当前价格 < MA：下降趋势，尾盘点价。
- 当前价格 == MA 或 MA 不足：横盘，早晚各 50%。

趋势优先用当天 `10:00` 前最后一条行情判断；如果没有，则使用当天第一条行情。均线只使用该判断点及之前的数据，避免未来函数。`daily_detail` 中会输出 `trend_decision_time`。

点价窗口：

- early：优先取 `10:00-10:30` 区间内最后一条行情；没有则回退到 `10:30` 前最后一条；再没有则用当天第一条。
- late：优先取 `20:30-23:15` 区间内最后一条行情；没有夜盘则回退到当天最后一条。
- fallback 信息会写入 `data_quality_flag`。

对照组：

- `baseline_early`：每天第一个可用行情点价。
- `baseline_late`：每天最后一个可用行情点价。
- `baseline_split`：每天第一个和最后一个可用行情各 50%。

## 7. 输出文件

主结果：

```text
outputs/backtest_results.xlsx
```

图表：

```text
outputs/charts/cumulative_pnl_best_strategy.png
outputs/charts/monthly_pnl_best_strategy.png
outputs/charts/spread_distribution_best_strategy.png
```

## 8. Excel sheet 含义

`orders_clean`：

- 清洗后的销售订单表。
- 字段已统一为 `order_id`、`order_datetime`、`channel`、`sales_amount`、`sales_unit_price`、`zhaojin_price`、`market_price`、`quantity`、`product_gram`、`fee`、`sales_gram`。

`market_data_summary`：

- 行情拉取或本地 CSV 读取摘要。
- 包含 source、symbol、period、timeframe、rows、start_datetime、end_datetime、file_path、status、error_message、original_unit、converted_unit、fx_rate。

`daily_detail`：

- 订单级或每日计划级回测明细。
- 包含策略、模式、订单、参考价、`reference_source`、趋势、`trend_decision_time`、点价时间、点价价、滑点、价差、收益、是否达标和数据质量标记。
- `reference_source` 取值包括 `sales_market_price`、`sales_zhaojin_price`、`nearest_market_close`、`day_first_market_close`、`missing_reference_price`。

`monthly_summary`：

- 月度汇总。
- 包含月销售额、月克重、加权平均价差、月收益、月度是否达标、记录达标率、最差/最好价差。

`strategy_ranking`：

- 策略排行榜。
- score 公式：

```text
score = avg_spread_per_gram * 0.5 + monthly_target_ratio * 10 - abs(min(0, worst_spread)) * 0.2
```

`warnings`：

- 数据和回测过程中发现的问题。
- 包括行情为空、订单缺失克重、订单缺失参考价、某日期无行情、MA 数据不足、时间匹配失败等。

## 9. 后续替换正式行情 API

正式版建议优先替换 `src/data_fetcher.py` 和 `src/market_loader.py`：

- 保持输出行情表至少包含 `datetime` 和 `close`。
- 如果有卖一价，建议新增 `ask_price`，并在策略点价处使用卖一价替代 close。
- 其它回测、指标和报表模块可以基本复用。
- 正式生产口径需要替换成 AU9999/mAuT+D 历史分钟行情和卖一价。
