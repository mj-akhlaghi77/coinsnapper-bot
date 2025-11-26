# technical_analysis.py - نسخه جدید با الگوریتم ZigZag حرفه‌ای
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
    """
    الگوریتم ZigZag مشابه TradingView
    depth: حداقل تعداد کندل بین دو پیک/ولی
    deviation: حداقل تغییر قیمت (درصد)
    backstep: حداقل تعداد کندل عقب‌نشینی برای اصلاح پیک
    """
    high = df['high'].values
    low = df['low'].values
    close = df['close'].values
    timestamp = df['timestamp'].values

    pivots = []
    last_pivot_idx = 0
    last_pivot_price = close[0]
    last_pivot_type = None  # 'high' یا 'low'

    deviation /= 100.0

    for i in range(1, len(df)):
        if len(pivots) == 0:
            pivots.append((0, close[0], 'none'))
            continue

        # بررسی برای پیک بالا (High Pivot)
        if close[i] > last_pivot_price:
            potential_high = high[i]
            if (potential_high - last_pivot_price) / last_pivot_price >= deviation:
                # بررسی backstep
                valid = True
                for j in range(max(last_pivot_idx + depth, i - backstep), i):
                    if high[j] >= potential_high:
                        valid = False
                        break
                if valid:
                    # حذف پیک‌های قبلی اگر این پیک جدید قوی‌تر باشد
                    while len(pivots) > 1 and pivots[-1][1] < potential_high and (i - pivots[-1][0]) >= depth:
                        pivots.pop()
                    pivots.append((i, potential_high, 'high'))
                    last_pivot_idx = i
                    last_pivot_price = potential_high
                    last_pivot_type = 'high'

        # بررسی برای ولی پایین (Low Pivot)
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
        print(f"Binance error: {e}")
        return None


def analyze(symbol: str, interval: str = "4h") -> dict:
    cache_key = f"{symbol.upper()}_{interval}"
    now = time.time()

    if cache_key in CACHE and now - CACHE[cache_key][2] < CACHE_TTL:
        return CACHE[cache_key][1]

    df = get_klines(symbol.upper(), interval, limit=1000)
    if df is None or len(df) < 300:
        return {"error": "دیتای کافی دریافت نشد"}

    # فقط از ۳۰۰ کندل آخر برای تحلیل استفاده می‌کنیم
    df_recent = df.iloc[-300:].reset_index(drop=True)

    pivots = zig_zag(df_recent, depth=12, deviation=5, backstep=3)

    if len(pivots) < 3:
        trend = "نامشخص (داده کم)"
        suggestion = "صبر کن"
        last_extreme = "نامشخص"
    else:
        # آخرین دو اکستریم مهم
        last_pivot = pivots[-1]
        prev_pivot = pivots[-2]

        last_idx = last_pivot[0]
        last_price = last_pivot[1]
        last_type = last_pivot[2]

        # آیا در ۵۰ کندل آخر اکستریم جدیدی تشکیل شده؟
        if (len(df_recent) - 1 - last_idx) > 50:
            trend = "ساید وی (رنج)"
            suggestion = "احتیاط - بازار در استراحت"
            last_extreme = "هیچ اکستریم جدیدی در ۵۰ کندل اخیر"
        else:
            if last_type == 'high' and last_price > prev_pivot[1]:
                trend = "صعودی قوی"
                suggestion = "لانگ یا هولد"
                last_extreme = f"پیک جدید در ${last_price:,.2f}"
            elif last_type == 'low' and last_price < prev_pivot[1]:
                trend = "نزولی قوی"
                suggestion = "شورت یا صبر"
                last_extreme = f"ولی جدید در ${last_price:,.2f}"
            elif last_type == 'high' and last_price < prev_pivot[1]:
                trend = "صعودی (در حال اصلاح)"
                suggestion = "لانگ با احتیاط"
                last_extreme = f"پیک پایین‌تر از قبلی"
            elif last_type == 'low' and last_price > prev_pivot[1]:
                trend = "نزولی (در حال اصلاح)"
                suggestion = "شورت با احتیاط"
                last_extreme = f"ولی بالاتر از قبلی"
            else:
                trend = "خنثی / رنج"
                suggestion = "احتیاط"
                last_extreme = "نامشخص"

    # RSI ساده برای تکمیل تحلیل
    close = df_recent['close']
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = -delta.where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss
    rsi_val = 100 - (100 / (1 + rs)).iloc[-1]

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
        "macd": "غیرفعال (زیگزاگ فعال)",
        "key_levels": [f"آخرین اکستریم: {last_extreme}"] if 'last_extreme' in locals() else ["نامشخص"],
        "time": to_shamsi(datetime.now()),
        "zigzag_points": len(pivots),
        "last_pivot": last_extreme if 'last_extreme' in locals() else "نامشخص"
    }

    CACHE[cache_key] = (df_recent, result, now)
    return result
