# technical_analysis.py - نسخه سازگار با Runflare / Python 3.11
import time
import pandas as pd
import numpy as np
from binance.client import Client
from datetime import datetime
import jdatetime

CACHE = {}
CACHE_TTL = 300

client = Client()

def to_shamsi(dt):
    try:
        return jdatetime.datetime.fromgregorian(datetime=dt).strftime("%Y/%m/%d - %H:%M")
    except:
        return dt.strftime("%Y-%m-%d %H:%M")

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def macd_custom(close, fast=48, slow=104, signal=36):
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def rsi(close, period=14):
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def ichimoku_span_b(high, low):
    # ساده‌سازی شده Span B (26 دوره قبل)
    period52_high = high.rolling(52).max()
    period52_low = low.rolling(52).min()
    return (period52_high + period52_low) / 2

def get_klines(symbol: str, interval: str = "4h", limit: int = 1000):
    try:
        klines = client.get_klines(symbol=symbol + "USDT", interval=interval, limit=limit)
        df = pd.DataFrame(klines[:], columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'tb_base', 'tb_quote', 'ignore'
        ])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col])
        return df
    except Exception as e:
        print(f"Binance error: {e}")
        return None

def detect_flat_span_b(df):
    span_b = ichimoku_span_b(df['high'], df['low'])
    recent = span_b.dropna().iloc[-300:]
    levels = []
    i = 0
    while i < len(recent) - 15:
        window = recent.iloc[i:i+15]
        if (window.max() - window.min()) / window.min() < 0.003:  # < 0.3%
            levels.append(round(window.mean(), 6))
            i += 12
        else:
            i += 1
    return list(set(levels))[:3]

def analyze(symbol: str, interval: str = "4h") -> dict:
    cache_key = f"{symbol.upper()}_{interval}"
    now = time.time()

    if cache_key in CACHE and now - CACHE[cache_key][2] < CACHE_TTL:
        return CACHE[cache_key][1]

    df = get_klines(symbol.upper(), interval)
    if df is None or len(df) < 200:
        return {"error": "دیتا دریافت نشد"}

    close = df['close']
    df['EMA26'] = ema(close, 26)
    df['EMA50'] = ema(close, 50)
    df['EMA100'] = ema(close, 100)
    
    macd_line, signal_line, _ = macd_custom(close)
    df['MACD'] = macd_line
    df['MACD_sig'] = signal_line
    
    df['RSI'] = rsi(close)

    c = df.iloc[-1]
    p = df.iloc[-2]

    all_above_100 = (c.EMA26 > c.EMA100 and c.EMA50 > c.EMA100 and c.close > c.EMA100)
    all_below_100 = (c.EMA26 < c.EMA100 and c.EMA50 < c.EMA100 and c.close < c.EMA100)

    cross_up = (c.EMA26 > c.EMA50 > c.EMA100 and p.EMA26 <= p.EMA50) or (c.EMA50 > c.EMA100 and p.EMA50 <= p.EMA100)

    macd_bull = c.MACD > c.MACD_sig and c.MACD > 0
    macd_bear = c.MACD < c.MACD_sig and c.MACD < 0

    levels = detect_flat_span_b(df)

    if all_above_100 and macd_bull and cross_up:
        trend = "قوی صعودی 🔥🔥"
        suggestion = "لانگ قوی"
    elif all_above_100 and macd_bull:
        trend = "صعودی 🟢"
        suggestion = "لانگ یا هولد"
    elif all_below_100 and macd_bear:
        trend = "نزولی 🔴"
        suggestion = "شورت یا صبر"
    elif all_below_100 and macd_bear:
        trend = "قوی نزولی 🛑"
        suggestion = "شورت قوی"
    else:
        trend = "خنثی / رنج ⚪"
        suggestion = "احتیاط"

    rsi_text = f"RSI: {c.RSI:.1f}"
    if c.RSI > 70: rsi_text += " ➜ اشباع خرید ⚠️"
    elif c.RSI < 30: rsi_text += " ➜ اشباع فروش ⚠️"

    result = {
        "symbol": symbol.upper(),
        "price": f"${c.close:,.2f}",
        "trend": trend,
        "suggestion": suggestion,
        "rsi": rsi_text,
        "macd": "صعودی 🟢" if macd_bull else "نزولی 🔴" if macd_bear else "خنثی",
        "key_levels": levels,
        "time": to_shamsi(datetime.now())
    }

    CACHE[cache_key] = (df, result, now)
    return result
