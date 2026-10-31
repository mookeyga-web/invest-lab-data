# -*- coding: utf-8 -*-
"""
Invest_Lab weekly data snapshot (runs on GitHub Actions, no computer needed).
Writes:
  data/weekly_prices.json  - same schema as the local OpenBB script (closes, pb, errors)
  data/risk_inputs.json    - inputs for the biweekly "correction vs crash" report
No trading, no API keys. Source: Yahoo Finance (yfinance) + FRED public CSV.
"""
import io, json, sys, datetime as dt
from pathlib import Path
import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1] / "data"
ROOT.mkdir(exist_ok=True)
START = "2026-10-12"  # covers the 16.10 baseline and the 19.10 consensus entry
NOW = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

INDICES = {"^GSPC": "S&P 500", "^TA125.TA": "TA-125"}
BANKS = {"LUMI.TA": "Leumi", "POLI.TA": "Hapoalim", "MZTF.TA": "Mizrahi",
         "DSCT.TA": "Discount", "FIBI.TA": "FIBI"}
CONSENSUS = ["ERIE", "SATS", "FIX", "TTWO", "WYNN", "ANET", "FLEX", "NVDA", "SW", "MPWR",
             "AMZN", "CRH", "DAL", "DXCM", "AVGO", "DVN", "SPGI", "VST", "META", "GOOGL"]
# extra series for the risk report (A2 equal weight, A3 consumer, A4 vol, 10Y, oil, hyperscalers)
RISK_TICKERS = ["RSP", "XLY", "XLP", "^VIX", "^VIX3M", "^TNX", "BZ=F", "QQQ",
                "MSFT", "ORCL"]
FRED = {  # public CSV, no key
    "BAMLH0A0HYM2": "HY OAS (B1)", "BAMLC0A0CM": "IG OAS (B2)", "NFCI": "NFCI (B3)",
    "SAHMREALTIME": "Sahm (B7)", "IC4WSA": "Initial claims 4wk avg (B8)",
    "CCSA": "Continued claims (B9)", "T10Y3M": "10Y-3M (B11)", "DGS10": "10Y yield (A5)",
}


def closes(ticker, start):
    df = yf.download(ticker, start=start, progress=False, auto_adjust=False, threads=False)
    if df is None or df.empty:
        raise ValueError("no data")
    s = df["Close"]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    return s.dropna()


def weekly_prices():
    out = {"generated_at": NOW, "source": "yfinance via GitHub Actions", "start": START,
           "end": dt.date.today().isoformat(), "closes": {}, "pb": {}, "errors": {}}
    for t in list(INDICES) + list(BANKS) + CONSENSUS:
        try:
            s = closes(t, START)
            div = 100.0 if (t.endswith(".TA") and not t.startswith("^")) else 1.0  # agorot -> ILS
            out["closes"][t] = [{"date": str(i)[:10], "close": round(float(v) / div, 4)} for i, v in s.items()]
        except Exception as e:
            out["errors"][t] = str(e)[:300]
    for t in BANKS:
        try:
            pb = yf.Ticker(t).info.get("priceToBook")
            out["pb"][t] = None if pb is None else round(float(pb), 3)
        except Exception as e:
            out["pb"][t] = None
            out["errors"][t + ":pb"] = str(e)[:300]
    (ROOT / "weekly_prices.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def fred(series):
    """FRED public CSV. One short attempt: FRED often blocks cloud runners, and prices must not wait for it."""
    cosd = (dt.date.today() - dt.timedelta(days=730)).isoformat()
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={cosd}"
    r = requests.get(url, timeout=(10, 30), headers={"User-Agent": "Mozilla/5.0 (invest-lab-data; research)"})
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna().tail(130)


def risk_inputs():
    out = {"generated_at": NOW, "source": "yfinance + FRED via GitHub Actions",
           "fred": {}, "prices": {}, "derived": {}, "errors": {}}
    for sid, name in FRED.items():
        try:
            df = fred(sid)
            out["fred"][sid] = {"name": name, "last_date": df["date"].iloc[-1],
                                "last": float(df["value"].iloc[-1]),
                                "history": df.tail(60).to_dict("records")}
        except Exception as e:
            out["errors"][sid] = str(e)[:300]
            if not out["fred"]:  # first series failed -> FRED unreachable; skip the rest
                for rest in FRED:
                    out["errors"].setdefault(rest, "skipped: FRED unreachable from runner")
                break
    start = (dt.date.today() - dt.timedelta(days=420)).isoformat()
    series = {}
    for t in ["^GSPC"] + RISK_TICKERS:
        try:
            s = closes(t, start)
            series[t] = s
            out["prices"][t] = {"last_date": str(s.index[-1])[:10], "last": round(float(s.iloc[-1]), 4),
                                "ret_3m": round(float(s.iloc[-1] / s.iloc[-64] - 1) * 100, 2) if len(s) > 64 else None}
        except Exception as e:
            out["errors"][t] = str(e)[:300]
    try:  # A10: S&P 500 vs 10-month average of monthly closes
        m = series["^GSPC"].resample("ME").last()
        last_full = m.iloc[:-1] if m.index[-1].month == dt.date.today().month else m
        ma10 = float(last_full.tail(10).mean())
        last = float(series["^GSPC"].iloc[-1])
        out["derived"]["A10_spx_vs_ma10_pct"] = round((last / ma10 - 1) * 100, 2)
        out["derived"]["A10_ma10"] = round(ma10, 2)
    except Exception as e:
        out["errors"]["A10"] = str(e)[:300]
    try:  # A2: S&P 500 vs equal weight (RSP), 3-month gap in points
        out["derived"]["A2_spx_minus_rsp_3m"] = round(out["prices"]["^GSPC"]["ret_3m"] - out["prices"]["RSP"]["ret_3m"], 2)
    except Exception as e:
        out["errors"]["A2"] = str(e)[:300]
    try:  # A3: XLY vs S&P 3m, and XLY/XLP vs its 200-day average
        out["derived"]["A3_xly_minus_spx_3m"] = round(out["prices"]["XLY"]["ret_3m"] - out["prices"]["^GSPC"]["ret_3m"], 2)
        ratio = (series["XLY"] / series["XLP"]).dropna()
        out["derived"]["A3_xly_xlp_vs_ma200_pct"] = round(float(ratio.iloc[-1] / ratio.tail(200).mean() - 1) * 100, 2)
    except Exception as e:
        out["errors"]["A3"] = str(e)[:300]
    try:  # A4: VIX term structure
        out["derived"]["A4_vix_minus_vix3m"] = round(out["prices"]["^VIX"]["last"] - out["prices"]["^VIX3M"]["last"], 2)
    except Exception as e:
        out["errors"]["A4"] = str(e)[:300]
    (ROOT / "risk_inputs.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return out


if __name__ == "__main__":
    w = weekly_prices()
    r = risk_inputs()
    print(f"weekly: ok={len(w['closes'])} errors={len(w['errors'])} | risk: fred={len(r['fred'])} prices={len(r['prices'])} errors={len(r['errors'])}")
    sys.exit(0 if w["closes"] else 1)
# -*- coding: utf-8 -*-
"""
Invest_Lab weekly data snapshot (runs on GitHub Actions, no computer needed).
Writes:
  data/weekly_prices.json  - same schema as the local OpenBB script (closes, pb, errors)
  data/risk_inputs.json    - inputs for the biweekly "correction vs crash" report
No trading, no API keys. Source: Yahoo Finance (yfinance) + FRED public CSV.
"""
import io, json, sys, datetime as dt
from pathlib import Path
import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1] / "data"
ROOT.mkdir(exist_ok=True)
START = "2026-10-12"  # covers the 16.10 baseline and the 19.10 consensus entry
NOW = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

INDICES = {"^GSPC": "S&P 500", "^TA125.TA": "TA-125"}
BANKS = {"LUMI.TA": "Leumi", "POLI.TA": "Hapoalim", "MZTF.TA": "Mizrahi",
         "DSCT.TA": "Discount", "FIBI.TA": "FIBI"}
CONSENSUS = ["ERIE", "SATS", "FIX", "TTWO", "WYNN", "ANET", "FLEX", "NVDA", "SW", "MPWR",
             "AMZN", "CRH", "DAL", "DXCM", "AVGO", "DVN", "SPGI", "VST", "META", "GOOGL"]
# extra series for the risk report (A2 equal weight, A3 consumer, A4 vol, 10Y, oil, hyperscalers)
RISK_TICKERS = ["RSP", "XLY", "XLP", "^VIX", "^VIX3M", "^TNX", "BZ=F", "QQQ",
                "MSFT", "ORCL"]
FRED = {  # public CSV, no key
    "BAMLH0A0HYM2": "HY OAS (B1)", "BAMLC0A0CM": "IG OAS (B2)", "NFCI": "NFCI (B3)",
    "SAHMREALTIME": "Sahm (B7)", "IC4WSA": "Initial claims 4wk avg (B8)",
    "CCSA": "Continued claims (B9)", "T10Y3M": "10Y-3M (B11)", "DGS10": "10Y yield (A5)",
}


def closes(ticker, start):
    df = yf.download(ticker, start=start, progress=False, auto_adjust=False, threads=False)
    if df is None or df.empty:
        raise ValueError("no data")
    s = df["Close"]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    return s.dropna()


def weekly_prices():
    out = {"generated_at": NOW, "source": "yfinance via GitHub Actions", "start": START,
           "end": dt.date.today().isoformat(), "closes": {}, "pb": {}, "errors": {}}
    for t in list(INDICES) + list(BANKS) + CONSENSUS:
        try:
            s = closes(t, START)
            div = 100.0 if (t.endswith(".TA") and not t.startswith("^")) else 1.0  # agorot -> ILS
            out["closes"][t] = [{"date": str(i)[:10], "close": round(float(v) / div, 4)} for i, v in s.items()]
        except Exception as e:
            out["errors"][t] = str(e)[:300]
    for t in BANKS:
        try:
            pb = yf.Ticker(t).info.get("priceToBook")
            out["pb"][t] = None if pb is None else round(float(pb), 3)
        except Exception as e:
            out["pb"][t] = None
            out["errors"][t + ":pb"] = str(e)[:300]
    (ROOT / "weekly_prices.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def fred(series):
    """FRED public CSV, limited to the last ~2 years, with retries (FRED can be slow from cloud runners)."""
    import time
    cosd = (dt.date.today() - dt.timedelta(days=730)).isoformat()
    urls = [f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={cosd}",
            f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"]
    last = None
    for attempt in range(4):
        url = urls[min(attempt, 1)]
        try:
            r = requests.get(url, timeout=(15, 120), headers={
                "User-Agent": "Mozilla/5.0 (invest-lab-data; research)", "Accept": "text/csv"})
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = ["date", "value"]
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            return df.dropna().tail(130)
        except Exception as e:
            last = e
            time.sleep(10 * (attempt + 1))
    raise last


def risk_inputs():
    out = {"generated_at": NOW, "source": "yfinance + FRED via GitHub Actions",
           "fred": {}, "prices": {}, "derived": {}, "errors": {}}
    for sid, name in FRED.items():
        try:
            df = fred(sid)
            out["fred"][sid] = {"name": name, "last_date": df["date"].iloc[-1],
                                "last": float(df["value"].iloc[-1]),
                                "history": df.tail(60).to_dict("records")}
        except Exception as e:
            out["errors"][sid] = str(e)[:300]
    start = (dt.date.today() - dt.timedelta(days=420)).isoformat()
    series = {}
    for t in ["^GSPC"] + RISK_TICKERS:
        try:
            s = closes(t, start)
            series[t] = s
            out["prices"][t] = {"last_date": str(s.index[-1])[:10], "last": round(float(s.iloc[-1]), 4),
                                "ret_3m": round(float(s.iloc[-1] / s.iloc[-64] - 1) * 100, 2) if len(s) > 64 else None}
        except Exception as e:
            out["errors"][t] = str(e)[:300]
    try:  # A10: S&P 500 vs 10-month average of monthly closes
        m = series["^GSPC"].resample("ME").last()
        last_full = m.iloc[:-1] if m.index[-1].month == dt.date.today().month else m
        ma10 = float(last_full.tail(10).mean())
        last = float(series["^GSPC"].iloc[-1])
        out["derived"]["A10_spx_vs_ma10_pct"] = round((last / ma10 - 1) * 100, 2)
        out["derived"]["A10_ma10"] = round(ma10, 2)
    except Exception as e:
        out["errors"]["A10"] = str(e)[:300]
    try:  # A2: S&P 500 vs equal weight (RSP), 3-month gap in points
        out["derived"]["A2_spx_minus_rsp_3m"] = round(out["prices"]["^GSPC"]["ret_3m"] - out["prices"]["RSP"]["ret_3m"], 2)
    except Exception as e:
        out["errors"]["A2"] = str(e)[:300]
    try:  # A3: XLY vs S&P 3m, and XLY/XLP vs its 200-day average
        out["derived"]["A3_xly_minus_spx_3m"] = round(out["prices"]["XLY"]["ret_3m"] - out["prices"]["^GSPC"]["ret_3m"], 2)
        ratio = (series["XLY"] / series["XLP"]).dropna()
        out["derived"]["A3_xly_xlp_vs_ma200_pct"] = round(float(ratio.iloc[-1] / ratio.tail(200).mean() - 1) * 100, 2)
    except Exception as e:
        out["errors"]["A3"] = str(e)[:300]
    try:  # A4: VIX term structure
        out["derived"]["A4_vix_minus_vix3m"] = round(out["prices"]["^VIX"]["last"] - out["prices"]["^VIX3M"]["last"], 2)
    except Exception as e:
        out["errors"]["A4"] = str(e)[:300]
    (ROOT / "risk_inputs.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return out


if __name__ == "__main__":
    w = weekly_prices()
    r = risk_inputs()
    print(f"weekly: ok={len(w['closes'])} errors={len(w['errors'])} | risk: fred={len(r['fred'])} prices={len(r['prices'])} errors={len(r['errors'])}")
    sys.exit(0 if w["closes"] else 1)
