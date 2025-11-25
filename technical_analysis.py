# technical_analysis.py
import time
import pandas as pd
import numpy as np
from binance.client import Client
from datetime import datetime
import jdatetime

# تنظیمات ZigZag
ZIGZAG_DEPTH = 12
ZIGZAG_DEVIATION = 5  # درصد
ZIGZAG_BACKSTEP = 3

client = Client()  # نیازی به کلید نیست برای داده‌های عمومی

def to_shamsi(dt):
    try:
        return jdatetime.datetime.fromgregorian(datetime=dt).strftime("%Y/%m/%d - %H:%M")
    except:
        return dt.strftime("%Y-%m-%d %H:%M")

def fetch_klines(symbol: str, interval: str = "4h", limit: int = 1000):
    """دریافت ۱۰۰۰ کندل ۴ ساعته از بایننس"""
    try:
        klines = client.get_klines(
            symbol=symbol + "USDT",
            interval=interval,
            limit=limit
        )
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base', 'taker_buy_quote', 'ignore'
        ])
        df['close'] = pd.to_numeric(df['close'])
        df['high'] = pd.to_numeric(df['high'])
        df['low'] = pd.to_numeric(df['low'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df[['timestamp', 'open', 'high', 'low', 'close']].tail(500).reset_index(drop=True)
    except Exception as e:
        print(f"خطا در دریافت داده بایننس: {e}")
        return None

def zigzag(df: pd.DataFrame):
    """الگوریتم ZigZag با تنظیمات مشخص"""
    high_points = []
    low_points = []
    last_pivot = None
    direction = 0  # 1 = بالا، -1 = پایین، 0 = شروع

    deviation = ZIGZAG_DEVIATION / 100

    for i in range(ZIGZAG_DEPTH, len(df) - ZIGZAG_DEPTH):
        high = df['high'].iloc[i]
        low = df['low'].iloc[i]
        close = df['close'].iloc[i]
        time = df['timestamp'].iloc[i]

        # بررسی High جدید
        is_high = all(high >= df['high'].iloc[i - ZIGZAG_DEPTH:i + ZIGZAG_DEPTH + 1])
        # بررسی Low جدید
        is_low = all(low <= df['low'].iloc[i - ZIGZAG_DEPTH:i + ZIGZAG_DEPTH + 1])

        if is_high or is_low:
            if last_pivot is None:
                # اولین پیوت
                if is_high:
                    high_points.append((time, close, i))
                    last_pivot = ("high", high, close, i)
                    direction = -1
                elif is_low:
                    low_points.append((time, close, i))
                    last_pivot = ("low", low, close, i)
                    direction = 1
                continue

            last_type, last_price, last_close, last_idx = last_pivot

            # محاسبه تغییر درصد از آخرین پیوت
            change = (close - last_close) / last_close if last_close > 0 else 0

            # اگر جهت مخالف و تغییر کافی داشت
            if (direction == 1 and is_low and change <= -deviation) or \
               (direction == -1 and is_high and change >= deviation):

                # بررسی Backstep
                skip = False
                if len(high_points) > 0 and last_type == "high":
                    for t, c, idx in reversed(high_points[-ZIGZAG_BACKSTEP:]):
                        if idx > last_idx and c > close:
                            skip = True
                            break
                elif len(low_points) > 0 and last_type == "low":
                    for t, c, idx in reversed(low_points[-ZIGZAG_BACKSTEP:]):
                        if idx > last_idx and c < close:
                            skip = True
                            break

                if not skip:
                    if is_high:
                        high_points.append((time, close, i))
                        last_pivot = ("high", high, close, i)
                        direction = -1
                    else:
                        low_points.append((time, close, i))
                        last_pivot = ("low", low, close, i)
                        direction = 1

    # ترکیب نقاط
    points = []
    for t, c, i in high_points:
        points.append({"time": t, "price": c, "type": "high", "index": i})
    for t, c, i in low_points:
        points.append({"time": t, "price": c, "type": "low", "index": i})

    points.sort(key=lambda x: x["index"])
    return points

def analyze(symbol: str):
    """تابع اصلی تحلیل تکنیکال - فراخوانی شده از main.py"""
    symbol = symbol.upper()

    # دریافت قیمت فعلی
    try:
        ticker = client.get_symbol_ticker(symbol=symbol + "USDT")
        current_price = float(ticker["price"])
    except:
        current_price = 0.0

    df = fetch_klines(symbol)
    if df is None or len(df) < 100:
        return {"error": "داده کافی دریافت نشد"}

    points = zigzag(df)

    if len(points) < 2:
        key_levels = ["تحلیل ZigZag: نقاط کافی شناسایی نشد."]
    else:
        # آخرین High و Low
        last_high = None
        last_low = None
        for p in reversed(points):
            if p["type"] == "high" and last_high is None:
                last_high = p
            elif p["type"] == "low" and last_low is None:
                last_low = p
            if last_high and last_low:
                break

        key_levels = []
        for i, p in enumerate(points):
            emoji = "High" if p["type"] == "high" else "Low"
            label = "آخرین High" if p is last_high else "آخرین Low" if p is last_low else f"اکستریم {i+1}"
            key_levels.append(f"{emoji} {label}: <b>{p['price']:,.8f}</b> در {to_shamsi(p['time'])}")

        # شروع از کندل ۵۰۰ام (اولین نقطه ممکن)
        start_price = df.iloc[0]['close']
        trend = "صعودی" if points and points[-1]["type"] == "high" else "نزولی" if points else "نامشخص"
        if len(points) >= 2:
            if points[-2]["type"] == "low" and points[-1]["type"] == "high":
                trend = "صعودی (Higher High)"
            elif points[-2]["type"] == "high" and points[-1]["type"] == "low":
                trend = "نزولی (Lower Low)"

        suggestion = "در حال تشکیل موج جدید" if len(points) < 3 else "منتظر شکست سطوح کلیدی باش"

    return {
        "symbol": symbol,
        "price": f"${current_price:,.8f}" if current_price else "نامشخص",
        "trend": trend,
        "suggestion": suggestion,
        "rsi": "در دسترس نیست (در نسخه بعدی)",
        "macd": "در دسترس نیست (در نسخه بعدی)",
        "key_levels": key_levels,
        "time": to_shamsی(datetime.now()),
        "points_count": len(points)
    }
