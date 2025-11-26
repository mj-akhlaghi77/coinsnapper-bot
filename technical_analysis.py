# technical_analysis.py - نسخه حرفه‌ای با زیگزاگ + نمایش نقاط اکستریم
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

def analyze(symbol: str, interval: str = "4h") -> dict:
    cache_key = f"{symbol.upper()}_{interval}"
    now = time.time()

    if cache_key in CACHE and now - CACHE[cache_key][2] < CACHE_TTL:
        return CACHE[cache_key][1]

    df = get_klines(symbol.upper(), interval, limit=1000)
    if df is None or len(df) < 300:
        return {"error": "دیتای کافی دریافت نشد"}

    df_recent = df.iloc[-300:].reset_index(drop=True)

    # نقطه شروع زیگزاگ: کلوز کندل ۳۰۰ام قبل
    start_price = df_recent.iloc[0]['close']
    start_time = to_shamsi(df_recent.iloc[0]['timestamp'])

    pivots = zig_zag(df_recent, depth=12, deviation=5, backstep=3)

    if len(pivots) < 2:
        trend = "نامشخص (داده کم)"
        suggestion = "صبر کن"
        extreme_points = []
    else:
        # آخرین دو اکستریم برای تشخیص روند
        last_pivot = pivots[-1]
        prev_pivot = pivots[-2]

        last_idx = last_pivot[0]
        last_price = last_pivot[1]
        last_type = last_pivot[2]
        last_time = to_shamsi(df_recent.iloc[last_idx]['timestamp'])

        # تشخیص ساید وی: اگر بیش از ۵۰ کندل از آخرین اکستریم گذشته باشه
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

        # ساخت لیست نقاط اکستریم (فقط از نقطه دوم به بعد، چون نقطه اول مصنوعی است)
        extreme_points = []
        for i, (idx, price, ptype) in enumerate(pivots[1:], start=1):  # از نقطه دوم
            candle_time = to_shamsi(df_recent.iloc[idx]['timestamp'])
            if ptype == 'high':
                extreme_points.append(f"پیک صعودی #{i}: ${price:,.2f} — {candle_time}")
            elif ptype == 'low':
                extreme_points.append(f"ولی نزولی #{i}: ${price:,.2f} — {candle_time}")

    # RSI
    close = df_recent['close']
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = -delta.where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss
    rsi_val = 100 - (100 / (1 + rs)).iloc[-1] if not rs.iloc[-1] == 0 else 50
    rsi_text = f"RSI: {rsi_val:.1f}"
    if rsi_val > 70:
        rsi_text += " (اشباع خرید)"
    elif rsi_val < 30:
        rsi_text += " (اشباع فروش)"

    result = {
        "symbol": symbol.upper(),
        "price": f"${df_recent['close'].iloc[-1]:,.2f}",
        "trend": trend,
        "suggestion": suggestion,
        "rsi": rsi_text,
        "start_point": f"کلوز کندل ۳۰۰ام قبل: ${start_price:,.2f} — {start_time}",
        "extreme_points": extreme_points,
        "total_extremes": len(extreme_points),
        "time": to_shamsi(datetime.now()),
    }

    CACHE[cache_key] = (df_recent, result, now)
    return result
