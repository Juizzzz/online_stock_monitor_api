# Online Stock Monitor API

这是“全联网版”的股票点位监控服务。n8n 不再执行本地脚本，n8n 只需要定时调用这个 API，然后把返回的 `webhook_messages` 发到对应股票的 Discord Webhook。

## 架构

```text
n8n Schedule
  -> HTTP Request 调用 /monitor
  -> Split webhook_messages
  -> 用每条消息里的 discord_webhook_url 动态推送到 Discord
```

API 负责：

- 拉取历史日线
- 拉取实时/盘前/盘中参考价
- 拉取近期新闻
- 拉取未来一周财报/宏观事件
- 计算支撑区、阻力位、震荡区间、做 T 区间
- 生成 Discord 文本

## 当前数据源

默认实现了 FMP / Financial Modeling Prep：

- 历史日线
- Quote 当前价
- Stock news：优先 FMP；如果套餐不可用，自动兜底 Google News RSS
- Earnings calendar：优先 FMP；如果套餐不可用则跳过
- Economic calendar：优先免费官方日历源，再尝试 FMP；如果套餐不可用也不会中断
  - BLS Online Calendar：CPI、PPI、Employment Situation、JOLTS、ECI 等
  - Federal Reserve Calendar：FOMC、Fed 发言、Beige Book 等
  - `manual_events`：手动补充财报、FOMC、CPI、PPI、非农、GDP 等关键事件

需要环境变量：

```bash
FMP_API_KEY=你的key
DISCORD_WEBHOOK_URL=你的discord webhook，可选
```

## 本地测试

```bash
cd outputs/online_stock_monitor_api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
FMP_API_KEY=你的key uvicorn app:app --reload --port 8080
```

健康检查：

```bash
curl http://127.0.0.1:8080/health
```

调用监控：

```bash
curl -X POST http://127.0.0.1:8080/monitor \
  -H 'Content-Type: application/json' \
  -d '{
    "mode": "premarket",
    "provider": "fmp",
    "include_news": true,
    "include_events": true,
    "stocks": [
      {"ticker": "NVDA", "grid_step": 5, "lookback": 252, "min_score": 2.2},
      {"ticker": "AMZN", "grid_step": 5, "lookback": 252, "min_score": 2.2}
    ]
  }'
```

## 部署

适合部署到：

- Render
- Railway
- Fly.io
- Google Cloud Run
- AWS App Runner
- 自己的 VPS

Docker:

```bash
docker build -t stock-monitor-api .
docker run -p 8080:8080 -e FMP_API_KEY=你的key stock-monitor-api
```

## n8n

导入：

```text
outputs/online_stock_monitor_api/n8n_online_discord_workflow.json
```

需要改两类配置：

1. 把 `https://YOUR_STOCK_MONITOR_API/monitor` 改成你的 API 地址。
2. 在每只股票的配置里填自己的 `discord_webhook_url`。

示例：

```json
"stocks": [
  {
    "ticker": "NVDA",
    "grid_step": 5,
    "lookback": 252,
    "min_score": 2.2,
    "discord_webhook_url": "https://discord.com/api/webhooks/NVDA_WEBHOOK"
  },
  {
    "ticker": "AMZN",
    "grid_step": 5,
    "lookback": 252,
    "min_score": 2.2,
    "discord_webhook_url": "https://discord.com/api/webhooks/AMZN_WEBHOOK"
  }
]
```

n8n 的 Discord HTTP Request 节点使用动态 URL：

```text
{{ $json.discord_webhook_url }}
```

Body 使用：

```text
{{ { "content": $json.content } }}
```

模板包含三条触发：

- 上海时间 06:45：收盘后复盘
- 上海时间 21:00：盘前预案
- 美股交易时段：盘中 30 分钟监控

## 表格维护模式

如果你要长期维护多只股票，推荐不要把股票写死在 n8n 节点里，而是维护两张表：

- `stocks`: 每只股票的模型参数和 Discord webhook。
- `manual_events`: 手动补充的重大事件，例如财报、FOMC、CPI、PPI、非农等。

权限安全建议：

- 最安全省心：用 n8n 自带 Data Table，不需要授权 Google 账号。
- 如果用 Google Sheets：用 Service Account，并且只把这一张表共享给 service account 邮箱。
- 不建议用个人 Google OAuth 授权 n8n 访问 Drive/Sheets。
- 不建议把包含 Discord webhook 的表格 Publish to web。

模板文件：

```text
outputs/online_stock_monitor_api/stocks_watchlist.template.csv
outputs/online_stock_monitor_api/manual_events.template.csv
outputs/online_stock_monitor_api/n8n_google_sheets_watchlist.md
```

长期流程建议：

```text
每只股票独立窗口做点位模型
  -> 把 ticker/grid_step/lookback/min_score/webhook 写入 stocks 表
  -> n8n 定时读取 stocks 表
  -> API 计算点位与事件
  -> 按每只股票自己的 webhook 推送
```

## 请求字段

```json
{
  "mode": "postclose | premarket | intraday",
  "provider": "fmp",
  "include_news": true,
  "include_events": true,
  "manual_events": [
    {"date": "2026-07-08", "time": "14:00 ET", "title": "FOMC Minutes", "impact": "high", "tickers": ["ALL"]}
  ],
  "lookahead_days": 7,
  "send_discord": false,
  "stocks": [
    {
      "ticker": "NVDA",
      "grid_step": 5,
      "lookback": 252,
      "min_score": 2.2,
      "swing_radius": 3,
      "max_distance_from_close": 0.5,
      "touch_bonus_divisor": 8,
      "touch_bonus_cap": 2,
      "atr_zone_multiple": 0.45,
      "min_zone_width": 2,
      "current_price": 198.5,
      "discord_webhook_url": "https://discord.com/api/webhooks/..."
    }
  ]
}
```

`current_price` 可选。如果不传，API 会用行情源 quote 的当前价；如果你在 n8n 里有更好的盘前/盘中报价，可以传进来覆盖。

`discord_webhook_url` 可选。填在每只股票下面时，API 会额外返回 `webhook_messages`，n8n 可以按股票拆分并推送到不同 Discord 频道。老字段 `discord_messages` 仍然保留，用于推送到单一频道。

模型参数可选。不填时使用默认值：

```text
grid_step=5
lookback=252
min_score=2.2
swing_radius=3
max_distance_from_close=0.5
touch_bonus_divisor=8
touch_bonus_cap=2
atr_zone_multiple=0.45
min_zone_width=2
```

`manual_events` 可选。适合在 FMP 套餐没有宏观日历权限时，手动补充未来一周会影响行情的事件，例如 FOMC、CPI、PPI、非农、GDP、NVDA/AMZN 财报等。`tickers` 填 `["ALL"]` 表示所有股票都展示；填 `["NVDA"]` 表示只展示给 NVDA。

## FMP 套餐限制

如果你的 FMP 套餐不能访问新闻或宏观日历，服务仍然可以正常工作：

- 点位模型依赖历史日线和 quote，只要这两个接口可用，支撑/阻力/做 T 区间就能计算。
- 新闻会自动尝试 Google News RSS 兜底。
- 未来一周重大事件会先尝试 BLS 和 Federal Reserve 免费官方日历，再尝试 FMP。
- 宏观日历仍拿不到时不会中断 `/monitor`，需要时用 `manual_events` 补充。
- `/debug/provider/NVDA` 会分项显示 `historical_ok`、`quote_ok`、`news_ok`、`economic_calendar_ok`，方便排查是哪一类数据源不可用。

## 免责声明

这是策略监控和信息提醒工具，不构成投资建议。
