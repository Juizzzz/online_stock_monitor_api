from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone


def http_json(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class FMPProvider:
    name = "fmp"

    def __init__(self, api_key=None):
        self.api_key = api_key or os.environ.get("FMP_API_KEY")
        if not self.api_key:
            raise ValueError("FMP_API_KEY is required for provider=fmp")
        self.base = os.environ.get("FMP_BASE_URL", "https://financialmodelingprep.com/api/v3")

    def _url(self, path, **params):
        if "from_" in params:
            params["from"] = params.pop("from_")
        params["apikey"] = self.api_key
        return f"{self.base}{path}?{urllib.parse.urlencode(params)}"

    def historical_prices(self, ticker, days=650):
        url = self._url(f"/historical-price-full/{ticker}", timeseries=days)
        data = http_json(url)
        rows = []
        for r in data.get("historical", []):
            rows.append(
                {
                    "date": r["date"],
                    "open": r["open"],
                    "high": r["high"],
                    "low": r["low"],
                    "close": r["close"],
                    "volume": r.get("volume", 0),
                }
            )
        rows.sort(key=lambda x: x["date"])
        return rows

    def quote(self, ticker):
        data = http_json(self._url(f"/quote/{ticker}"))
        if isinstance(data, list) and data:
            r = data[0]
            return {
                "price": r.get("price"),
                "change": r.get("change"),
                "changesPercentage": r.get("changesPercentage"),
                "timestamp": r.get("timestamp"),
            }
        return {}

    def news(self, ticker, limit=3):
        data = http_json(self._url("/stock_news", tickers=ticker, limit=limit))
        result = []
        for r in data[:limit]:
            result.append(
                {
                    "title": r.get("title"),
                    "site": r.get("site"),
                    "publishedDate": r.get("publishedDate"),
                    "url": r.get("url"),
                }
            )
        return result

    def earnings_calendar(self, ticker, days=7):
        start = datetime.now(timezone.utc).date()
        end = start + timedelta(days=days)
        data = http_json(self._url("/earning_calendar", from_=str(start), to=str(end)))
        result = []
        for r in data:
            if str(r.get("symbol", "")).upper() == ticker.upper():
                result.append(
                    {
                        "date": r.get("date"),
                        "title": f"{ticker.upper()} earnings",
                        "impact": "high",
                        "tickers": [ticker.upper()],
                    }
                )
        return result

    def economic_calendar(self, days=7):
        start = datetime.now(timezone.utc).date()
        end = start + timedelta(days=days)
        try:
            data = http_json(self._url("/economic_calendar", from_=str(start), to=str(end)))
        except Exception:
            return []
        result = []
        keywords = ("FOMC", "Nonfarm", "Payroll", "CPI", "PPI", "Jobless", "ISM", "PMI", "GDP", "Retail")
        for r in data:
            event = r.get("event") or r.get("title") or ""
            if any(k.lower() in event.lower() for k in keywords):
                result.append(
                    {
                        "date": str(r.get("date", ""))[:10],
                        "time": str(r.get("date", ""))[11:16],
                        "title": event,
                        "impact": r.get("impact") or "macro",
                        "tickers": ["ALL"],
                    }
                )
        return result[:12]


def get_provider(name):
    if name == "fmp":
        return FMPProvider()
    raise ValueError(f"Unknown provider: {name}")
