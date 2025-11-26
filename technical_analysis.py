# technical_analysis.py - نسخه حرفه‌ای با زیگزاگ + نمایش تمام نقاط اکستریم
import time
import pandas as pd
import numpy as np
from binance.client import Client
from datetime import datetime
import jdatetime

# تنظیمات کش
CACHE = {}
CACHE_TTL = 300  # ۵ دقیقه

client = Client()

def to_shamsi(dt):
    try:
        return jdatetime.datetime.fromgregorian(datetime=dt).strftime("%Y/%m/%d - %H:%M")
    except:
        return dt.strftime("%Y-%m-%d %H:%M")


def zig_zag(df, depth=12, deviation=5, backstep=3):
    high = df['high'].values
    low = df['low'].values
    close = df['close'].values

    pivots = []
    last_pivot_idx = 0
    last_pivot_price = close[0]
    last_pivot_type = None

    deviation /= 100.0

    for i in range(1, len(df)):
        if len(pivots) == 0:
            pivots.append((0, close[0], 'none'))
            continue

        # پیک صعودی
        if close[i] > last_pivot_price:
            potential_high = high[i]
            if (potential_high - last_pivot_price) / last_pivot_price >= deviation:
                valid = True
                for j in range(max(last_pivot_idx + depth, i - backstep), i):
                    if high[j] >= potential_high:
                        valid = False
                        break
                if valid:
                    while len(pivots) > 1 and pivots[-1][1] < potential_high and (i - pivots[-1][0]) >= depth:
                        pivots.pop()
                    pivots.append((i, potential_high, 'high'))
                    last_pivot_idx = i
                    last_pivot_price = potential_high
                    last_pivot_type = 'high'

        # ولی نزولی
        elif close[i] < last_pivot_price:
            potential_low = low[i]
            if (last_pivot_price - potential_low) / last_pivot_price >= deviation:
                valid = True
                for j in range(max(last_pivot_idx + depth, i - backstep), i):
                    if low[j] <= potential_low:
                        valid = False
                        break
                if valid:
                    while len(pivots) > 1 and pivots[-1][1] > potential_low and (i - pivots[-1][0]) >= depth:
                        pivots.pop()
                    pivots.append((i, potential_low, 'low'))
                    last_pivot_idx = i
                    last_pivot_price = potential_low
                    last_pivot_type = 'low'

    return pivots


def get_klines(symbol: str, interval: str = "4h", limit: int = 1000):
    try:
        klines = client.get_klines(symbol=symbol + "USDT", interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'tb_base', 'tb_quote', 'ignore'
        ])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"خطا در دریافت داده از بایننس: {e}")
        return None


def analyze(symbol: str, interval: str = "4h") -> dict:
    cache_key = f"{symbol.upper()}_{interval}"
    now = time.time()

    if cache_key in CACHE and now - CACHE[cache_key][2] < CACHE_TTL:
        return CACHE[cache_key][1]

    df = get_klines(symbol.upper(), interval, limit=1000)
    if df is None or len(df) < 300:
        return {"error": "دیتای کافی دریافت نشد"}

    df_recent = df.iloc[-300:].reset_index(drop=True)

    # نقطه شروع زیگزاگ
    start_price = df_recent.iloc[0]['close']
    start_time = to_shamsi(df_recent.iloc[0]['timestamp'])

    pivots = zig_zag(df_recent, depth=12, deviation=5, backstep=3)

    if len(pivots) < 3:
        trend = "نامشخص (داده کم)"
        suggestion = "صبر کن"
        extreme_points = []
        total_extremes = 0
    else:
        last_pivot = pivots[-1]
        prev_pivot = pivots[-2]
        last_idx = last_pivot[0]
        last_price = last_pivot[1]
        last_type = last_pivot[2]

        # تشخیص ساید وی
        if (len(df_recent) - 1 - last_idx) > 50:
            trend = "ساید وی (رنج)"
            suggestion = "احتیاط - بازار در استراحت"
        elif last_type == 'high' and last_price > prev_pivot[1]:
            trend = "صعودی قوی"
            suggestion = "لانگ یا هولد"
        elif last_type == 'low' and last_price < prev_pivot[1]:
            trend = "نزولی قوی"
            suggestion = "شورت یا صبر"
        elif last_type == 'high' and last_price < prev_pivot[1]:
            trend = "صعودی (در حال اصلاح)"
            suggestion = "لانگ با احتیاط"
        elif last_type == 'low' and last_price > prev_pivot[1]:
            trend = "نزولی (در حال اصلاح)"
            suggestion = "شورت با احتیاط"
        else:
            trend = "خنثی / رنج"
            suggestion = "احتیاط"

        # تمام نقاط اکستریم (از نقطه دوم به بعد)
        extreme_points = []
        for i, (idx, price, ptype) in enumerate(pivots[1:], start=1):
            t = to_shamsi(df_recent.iloc[idx]['timestamp'])
            if ptype == 'high':
                extreme_points.append(f"پیک صعودی #{i}: ${price:,.2f} — {t}")
            elif ptype == 'low':
                extreme_points.append(f"ولی نزولی #{i}: ${price:,.2f} — {t}")

        total_extremes = len(extreme_points)

    # RSI
    close = df_recent['close']
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = -delta.where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss
    rsi_val = 100 - (100 / (1 + rs)).iloc[-1] if len(rs) > 0 and not pd.isna(rs.iloc[-1]) else 50
    rsi_text = f"RSI (14): {rsi_val:.1f}"
    if rsi_val > 70:
        rsi_text += " → اشباع خرید"
    elif rsi_val < 30:
        rsi_text += " → اشباع فروش"

    result = {
        "symbol": symbol.upper(),
        "price": f"${df_recent['close'].iloc[-1]:,.2f}",
        "trend": trend,
        "suggestion": suggestion,
        "rsi": rsi_text,
        "start_point": f"کلوز کندل ۳۰۰ام قبل: ${start_price:,.2f} — {start_time}",
        "extreme_points": extreme_points,
        "total_extremes": total_extremes,
        "time": to_shamsi(datetime.now()),
    }

    CACHE[cache_key] = (df_recent, result, now)
    return result
