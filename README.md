# Online Stock Monitor API

这是“全联网版”的股票点位监控服务。n8n 不再执行本地脚本，也不依赖 `/Users/yolo/...` 路径；n8n 只需要定时调用这个 API，然后把返回的 `discord_messages` 发到 Discord Webhook。

## 架构

```text
n8n Schedule
  -> HTTP Request 调用 /monitor
  -> Split discord_messages
  -> Discord Webhook
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
- Stock news
- Earnings calendar
- Economic calendar

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

需要改两处：

1. 把 `https://YOUR_STOCK_MONITOR_API/monitor` 改成你的 API 地址。
2. 把 `https://YOUR_DISCORD_WEBHOOK_URL` 改成你的 Discord Webhook，并启用 Discord 节点。

模板包含三条触发：

- 上海时间 06:45：收盘后复盘
- 上海时间 21:00：盘前预案
- 美股交易时段：盘中 30 分钟监控

## 请求字段

```json
{
  "mode": "postclose | premarket | intraday",
  "provider": "fmp",
  "include_news": true,
  "include_events": true,
  "lookahead_days": 7,
  "send_discord": false,
  "stocks": [
    {
      "ticker": "NVDA",
      "grid_step": 5,
      "lookback": 252,
      "min_score": 2.2,
      "current_price": 198.5
    }
  ]
}
```

`current_price` 可选。如果不传，API 会用行情源 quote 的当前价；如果你在 n8n 里有更好的盘前/盘中报价，可以传进来覆盖。

## 免责声明

这是策略监控和信息提醒工具，不构成投资建议。
