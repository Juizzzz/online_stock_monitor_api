from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from model import ModelParams, classify_position, compact_zone, generate_levels, nearest_levels, normalize_prices
from providers import get_provider


app = FastAPI(title="Online Stock Point Monitor", version="1.0.0")


@app.exception_handler(Exception)
def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"{type(exc).__name__}: {exc}",
            "path": str(request.url.path),
        },
    )


class StockConfig(BaseModel):
    ticker: str
    grid_step: float = 5
    lookback: int = 252
    min_score: float = 2.2
    current_price: float | None = None
    prices: list[dict[str, Any]] | None = None


class MonitorRequest(BaseModel):
    mode: str = Field("postclose", pattern="^(postclose|premarket|intraday)$")
    provider: str = "fmp"
    stocks: list[StockConfig]
    include_news: bool = True
    include_events: bool = True
    manual_events: list[dict[str, Any]] = Field(default_factory=list)
    lookahead_days: int = 7
    send_discord: bool = False
    discord_webhook_url: str | None = None


def chunk_text(text, limit=1850):
    chunks = []
    current = ""
    for part in text.split("\n\n"):
        candidate = part if not current else current + "\n\n" + part
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            while len(part) > limit:
                chunks.append(part[:limit])
                part = part[limit:]
            current = part
    if current:
        chunks.append(current)
    return chunks


def post_discord(webhook_url, text):
    for chunk in chunk_text(text):
        data = json.dumps({"content": chunk}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()


def mode_title(mode):
    return {"postclose": "收盘后点位监控", "premarket": "盘前交易预案", "intraday": "盘中点位监控"}[mode]


def mode_plan(mode, reference_price, filtered):
    nearest_below, nearest_above = nearest_levels(reference_price, filtered)
    lines = []
    if nearest_below:
        lines.append(f"下方最近节点: {compact_zone(nearest_below)} 分数 {nearest_below['score']:.2f}")
    if nearest_above:
        lines.append(f"上方最近节点: {compact_zone(nearest_above)} 分数 {nearest_above['score']:.2f}")
    if nearest_below and nearest_above:
        lines.append(f"近期震荡区间: {nearest_below['zone_low']:.2f}-{nearest_above['zone_high']:.2f}")
    if nearest_above and reference_price >= nearest_above["zone_low"]:
        verb = "盘中动作" if mode == "intraday" else "盘前预案"
        lines.append(f"{verb}: 贴近阻力，偏高抛/T 出；放量站稳后再上移目标。")
    elif nearest_below and reference_price <= nearest_below["zone_high"]:
        verb = "盘中动作" if mode == "intraday" else "盘前预案"
        lines.append(f"{verb}: 贴近支撑，偏低吸/T 买回；跌破下沿不抢，等下一层。")
    else:
        verb = "盘中动作" if mode == "intraday" else "盘前预案"
        lines.append(f"{verb}: 区间中部，减少操作频率，等靠近上下沿。")
    return lines


def render_stock(mode, stock, provider):
    ticker = stock.ticker.upper()
    try:
        if stock.prices:
            raw_prices = stock.prices
        else:
            raw_prices = provider.historical_prices(ticker)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{ticker}: historical prices failed: {type(exc).__name__}: {exc}")
    if len(raw_prices) < 220:
        raise HTTPException(status_code=400, detail=f"{ticker}: not enough historical price rows")
    try:
        rows = normalize_prices(raw_prices)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{ticker}: price normalization failed: {type(exc).__name__}: {exc}")
    try:
        quote = provider.quote(ticker) if provider else {}
    except Exception as exc:
        quote = {"error": f"{type(exc).__name__}: {exc}"}
    reference_price = stock.current_price or quote.get("price") or rows[-1]["Close"]
    params = ModelParams(grid_step=stock.grid_step, lookback=stock.lookback, min_score=stock.min_score)
    levels, meta = generate_levels(rows, params=params)
    filtered = [x for x in levels if x["score"] >= params.min_score]
    supports = [x for x in filtered if x["role"] == "support"]
    resistances = [x for x in filtered if x["role"] == "resistance"]
    status, action, nearest_support, nearest_resistance = classify_position(reference_price, supports, resistances)
    buy_zones = supports[:4]
    sell_zones = resistances[:4]
    lines = [
        f"【{ticker} {mode_title(mode)}】",
        f"数据日: {meta['trade_date']}  收盘: {meta['close']:.2f}  参考价: {reference_price:.2f}  ATR14: {meta['atr14']:.2f}",
        f"状态: {status}",
        f"建议: {action}",
        "",
        "做T低吸区: " + " / ".join(compact_zone(x) for x in buy_zones),
        "做T高抛区: " + " / ".join(compact_zone(x) for x in sell_zones),
    ]
    if mode in ("premarket", "intraday"):
        lines.extend(mode_plan(mode, reference_price, filtered))
    if nearest_support:
        lines.append(f"最近支撑: {compact_zone(nearest_support)} 分数 {nearest_support['score']:.2f}")
    if nearest_resistance:
        lines.append(f"最近阻力: {compact_zone(nearest_resistance)} 分数 {nearest_resistance['score']:.2f}")
    record = {
        "ticker": ticker,
        "trade_date": meta["trade_date"],
        "close": meta["close"],
        "reference_price": reference_price,
        "status": status,
        "supports": buy_zones,
        "resistances": sell_zones,
        "quote": quote,
        "meta": meta,
    }
    return lines, record


@app.get("/health")
def health():
    return {"ok": True, "time": datetime.utcnow().isoformat() + "Z"}


@app.get("/debug/provider/{ticker}")
def debug_provider(ticker: str, provider: str = "fmp"):
    data_provider = get_provider(provider)
    result = {"ok": True, "provider": provider, "ticker": ticker.upper()}
    try:
        prices = data_provider.historical_prices(ticker.upper(), days=260)
        result.update(
            {
                "historical_ok": True,
                "price_rows": len(prices),
                "first_date": prices[0]["date"] if prices else None,
                "last_date": prices[-1]["date"] if prices else None,
            }
        )
    except Exception as exc:
        result.update({"ok": False, "historical_ok": False, "historical_error": f"{type(exc).__name__}: {exc}"})
    try:
        result.update({"quote_ok": True, "quote": data_provider.quote(ticker.upper())})
    except Exception as exc:
        result.update({"ok": False, "quote_ok": False, "quote_error": f"{type(exc).__name__}: {exc}"})
    try:
        result.update({"news_ok": True, "news_sample": data_provider.news(ticker.upper(), limit=1)})
    except Exception as exc:
        result.update({"news_ok": False, "news_error": f"{type(exc).__name__}: {exc}"})
    try:
        result.update({"economic_calendar_ok": True, "economic_calendar_sample": data_provider.economic_calendar(7)[:3]})
    except Exception as exc:
        result.update({"economic_calendar_ok": False, "economic_calendar_error": f"{type(exc).__name__}: {exc}"})
    return result


@app.post("/monitor")
def monitor(req: MonitorRequest):
    try:
        provider = get_provider(req.provider)
    except Exception as exc:
        if any(s.prices for s in req.stocks):
            provider = None
        else:
            raise HTTPException(status_code=400, detail=str(exc))

    all_events = list(req.manual_events)
    if req.include_events and provider:
        try:
            all_events.extend(provider.economic_calendar(req.lookahead_days))
        except Exception as exc:
            all_events.append({"title": f"事件日历读取失败: {exc}", "tickers": ["ALL"]})

    messages = []
    records = []
    for stock in req.stocks:
        lines, record = render_stock(req.mode, stock, provider)
        ticker = stock.ticker.upper()
        events = list(all_events)
        if req.include_events and provider:
            try:
                events.extend(provider.earnings_calendar(ticker, req.lookahead_days))
            except Exception:
                pass
        if events:
            lines.append("")
            lines.append("未来一周事件:")
            for e in events[:8]:
                lines.append(f"- {e.get('date', '')} {e.get('time', '')} {e.get('title', '')} {e.get('impact', '')}".strip())
        news = []
        if req.include_news and provider:
            try:
                news = provider.news(ticker, limit=3)
            except Exception:
                news = []
        if news:
            lines.append("")
            lines.append("近期新闻:")
            for n in news[:3]:
                suffix = f" {n.get('url')}" if n.get("url") else ""
                lines.append(f"- {n.get('title')}{suffix}")
        messages.append("\n".join(lines))
        record["events"] = events
        record["news"] = news
        records.append(record)

    text = f"{mode_title(req.mode)} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n" + "\n\n---\n\n".join(messages)
    if req.send_discord:
        webhook = req.discord_webhook_url or os.environ.get("DISCORD_WEBHOOK_URL")
        if not webhook:
            raise HTTPException(status_code=400, detail="DISCORD_WEBHOOK_URL is required")
        post_discord(webhook, text)
    return {"text": text, "discord_messages": chunk_text(text), "records": records}
