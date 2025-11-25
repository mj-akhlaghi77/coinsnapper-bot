# technical_analysis.py - نسخه حرفه‌ای و دقیق بر اساس توضیحات شما
from binance.client import Client
import pandas as pd
from datetime import datetime
import jdatetime

client = Client()

def to_shamsi(dt):
    try:
        return jdatetime.datetime.fromgregorian(datetime=dt).strftime("%Y/%m/%d - %H:%M")
    except:
        return dt.strftime("%Y-%m-%d %H:%M")

def analyze(symbol: str):
    symbol = symbol.upper() + "USDT"
    
    try:
        klines = client.get_klines(symbol=symbol, interval="4h", limit=300)
        if len(klines) < 300:
            return {"error": "داده کافی نیست"}

        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'qav', 'trades', 'tbb', 'tbq', 'ignore'
        ])
        df['close'] = pd.to_numeric(df['close'])
        df['high'] = pd.to_numeric(df['high'])
        df['low'] = pd.to_numeric(df['low'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        # نقطه مرجع: کلوز کندل شماره ۳۰۰ از آخر
        reference_price = df['close'].iloc[0]
        ref_time = df['timestamp'].iloc[0]

        # تنظیمات ZigZag
        depth = 12
        deviation = 0.05
        backstep = 3

        pivots = []
        last_pivot_price = None
        last_pivot_type = None

        for i in range(depth, len(df)):
            high = df['high'].iloc[i]
            low = df['low'].iloc[i]
            close = df['close'].iloc[i]

            is_high = high == df['high'].iloc[i-depth:i+depth+1].max()
            is_low = low == df['low'].iloc[i-depth:i+depth+1].min()

            if not (is_high or is_low):
                continue

            if last_pivot_price is None:
                pivots.append({"price": close, "type": "high" if is_high else "low", "index": i})
                last_pivot_price = close
                last_pivot_type = "high" if is_high else "low"
                continue

            change = (close - last_pivot_price) / last_pivot_price if last_pivot_price else 0

            if (last_pivot_type == "high" and is_low and change <= -deviation) or \
               (last_pivot_type == "low" and is_high and change >= deviation):

                # Backstep
                valid = True
                recent_same = [p for p in pivots[-backstep:] if p["type"] == last_pivot_type]
                for p in recent_same:
                    if (last_pivot_type == "high" and close > p["price"]) or \
                       (last_pivot_type == "low" and close < p["price"]):
                        valid = False
                        break

                if valid:
                    pivots.append({"price": close, "type": "high" if is_high else "low", "index": i})
                    last_pivot_price = close
                    last_pivot_type = "high" if is_high else "low"

        if len(pivots) < 2:
            return {"trend": "سایدوی", "levels": ["هیچ اکستریم معتبری تشکیل نشده"]}

        # آخرین ۵۰ کندل برای تشخیص سایدوی
        last_50_candles = df.iloc[-50:]
        last_pivot_time = df['timestamp'].iloc[pivots[-1]["index"]]

        if (df['timestamp'].iloc[-1] - last_pivot_time).total_seconds() / 3600 > 50 * 4:
            return {"trend": "سایدوی (رنج)", "levels": ["بیش از ۵۰ کندل بدون اکستریم جدید"]}

        # تعیین روند اصلی
        highs = [p["price"] for p in pivots if p["type"] == "high"]
        lows = [p["price"] for p in pivots if p["type"] == "low"]

        # روند صعودی: HH + HL
        # روند نزولی: LH + LL
        trend = "نامشخص"
        final_levels = []

        if len(highs) >= 2 and len(lows) >= 2:
            last_high = highs[-1]
            prev_high = highs[-2]
            last_low = lows[-1]
            prev_low = lows[-2]

            if last_high > prev_high and last_low > prev_low:
                trend = "صعودی"
                # فقط Higher High ها
                current_hh = reference_price
                for h in highs:
                    if h > current_hh:
                        final_levels.append(h)
                        current_hh = h

            elif last_high < prev_high and last_low < prev_low:
                trend = "نزولی"
                # فقط Lower Low ها
                current_ll = reference_price
                for l in lows:
                    if l < current_ll:
                        final_levels.append(l)
                        current_ll = l
            else:
                trend = "سایدوی (تغییر روند)"
                final_levels = [p["price"] for p in pivots[-4:]]  # آخرین ۴ اکستریم

        else:
            trend = "در حال تشکیل"
            final_levels = [p["price"] for p in pivots]

        if not final_levels:
            final_levels = [p["price"] for p in pivots[-3:]]

        # فرمت نهایی
        level_texts = [f"{price:,.8f}".rstrip('0').rstrip('.') for price in final_levels]

        return {
            "symbol": symbol.replace("USDT", ""),
            "trend": trend,
            "reference": f"نقطه شروع: {reference_price:,.8f}",
            "key_levels": level_texts
        }

    except Exception as e:
        return {"error": f"خطا: {str(e)}"}
