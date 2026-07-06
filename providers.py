from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET


def http_json(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_text(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def google_news_rss(ticker, limit=3):
    query = f"{ticker.upper()} stock OR earnings OR analyst"
    params = urllib.parse.urlencode({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    xml_text = http_text(f"https://news.google.com/rss/search?{params}")
    root = ET.fromstring(xml_text)
    result = []
    for item in root.findall("./channel/item")[:limit]:
        result.append(
            {
                "title": item.findtext("title"),
                "site": item.findtext("source"),
                "publishedDate": item.findtext("pubDate"),
                "url": item.findtext("link"),
                "source_type": "google_news_rss",
            }
        )
    return result


class FMPProvider:
    name = "fmp"

    def __init__(self, api_key=None):
        self.api_key = api_key or os.environ.get("FMP_API_KEY")
        if not self.api_key:
            raise ValueError("FMP_API_KEY is required for provider=fmp")
        self.base = os.environ.get("FMP_BASE_URL", "https://financialmodelingprep.com/stable")

    def _url(self, path, **params):
        if "from_" in params:
            params["from"] = params.pop("from_")
        params["apikey"] = self.api_key
        return f"{self.base}{path}?{urllib.parse.urlencode(params)}"

    def historical_prices(self, ticker, days=650):
        url = self._url("/historical-price-eod/full", symbol=ticker)
        data = http_json(url)
        rows = []
        if isinstance(data, dict):
            historical = data.get("historical", [])
        else:
            historical = data
        historical = sorted(historical, key=lambda x: x.get("date", ""))
        if days:
            historical = historical[-days:]
        for r in historical:
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
        data = http_json(self._url("/quote", symbol=ticker))
        if isinstance(data, list) and data:
            r = data[0]
            return {
                "price": r.get("price") or r.get("close"),
                "change": r.get("change"),
                "changesPercentage": r.get("changesPercentage"),
                "timestamp": r.get("timestamp"),
            }
        return {}

    def news(self, ticker, limit=3):
        try:
            data = http_json(self._url("/news/stock", symbols=ticker, page=0, limit=limit))
            result = []
            for r in data[:limit]:
                result.append(
                    {
                        "title": r.get("title") or r.get("headline"),
                        "site": r.get("site") or r.get("publisher"),
                        "publishedDate": r.get("publishedDate") or r.get("date"),
                        "url": r.get("url") or r.get("link"),
                        "source_type": "fmp",
                    }
                )
            return result
        except Exception:
            return google_news_rss(ticker, limit=limit)

    def earnings_calendar(self, ticker, days=7):
        start = datetime.now(timezone.utc).date()
        end = start + timedelta(days=days)
        data = http_json(self._url("/earnings-calendar", from_=str(start), to=str(end)))
        result = []
        for r in data:
            if str(r.get("symbol", "")).upper() == ticker.upper():
                result.append(
                    {
                        "date": r.get("date") or r.get("fiscalDateEnding"),
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
            data = http_json(self._url("/economic-calendar", from_=str(start), to=str(end)))
        except Exception:
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
