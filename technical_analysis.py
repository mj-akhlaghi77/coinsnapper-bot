# technical_analysis.py - نسخه نهایی: دقیقاً مثل Display reversal price تریدینگ‌ویو
import time
import pandas as pd
from binance.client import Client
from datetime import datetime
import jdatetime

CACHE = {}
CACHE_TTL = 300  # 5 دقیقه کش
client = Client()

def to_shamsi(dt):
    try:
        return jdatetime.datetime.fromgregorian(datetime=dt).strftime("%Y/%m/%d - %H:%M")
    except:
        return dt.strftime("%Y-%m-%d %H:%M")


def zig_zag(df, depth=12, deviation=5, backstep=3):
    close = df['close'].values
    pivots = []
    last_pivot_idx = 0
    last_pivot_price = close[0]
    direction = 0  # 1 = صعودی، -1 = نزولی

    deviation /= 100.0

    for i in range(1, len(df)):
        current_price = close[i]

        if direction >= 0:  # منتظر پیک صعودی
            if current_price > last_pivot_price:
                potential_high = current_price
                if (potential_high - last_pivot_price) / last_pivot_price >= deviation:
                    valid = all(close[j] <= potential_high for j in range(max(last_pivot_idx + depth, i - backstep), i))
                    if valid:
                        while len(pivots) > 1 and pivots[-1][1] <= potential_high and (i - pivots[-1][0]) >= depth:
                            pivots.pop()
                        pivots.append((i, potential_high, 'high'))
                        last_pivot_idx = i
                        last_pivot_price = potential_high
                        direction = -1

        if direction <= 0:  # منتظر ولی نزولی
            if current_price < last_pivot_price:
                potential_low = current_price
                if (last_pivot_price - potential_low) / last_pivot_price >= deviation:
                    valid = all(close[j] >= potential_low for j in range(max(last_pivot_idx + depth, i - backstep), i))
                    if valid:
                        while len(pivots) > 1 and pivots[-1][1] >= potential_low and (i - pivots[-1][0]) >= depth:
                            pivots.pop()
                        pivots.append((i, potential_low, 'low'))
                        last_pivot_idx = i
                        last_pivot_price = potential_low
                        direction = 1

    return pivots


def get_klines(symbol: str, interval: str = "4h", limit: int = 1000):
    try:
        klines = client.get_klines(symbol=symbol + "USDT", interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'tb_base', 'tb_quote', 'ignore'
        ])
        df['close'] = pd.to_numeric(df['close'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df[['timestamp', 'close']]
    except Exception as e:
        print(f"خطا در دریافت دیتا از بایننس: {e}")
        return None


def analyze(symbol: str, interval: str = "4h") -> dict:
    cache_key = f"{symbol.upper()}_{interval}"
    now = time.time()

    if cache_key in CACHE and now - CACHE[cache_key][2] < CACHE_TTL:
        return CACHE[cache_key][1]

    df = get_klines(symbol.upper(), interval, limit=1000)
    if df is None or len(df) < 300:
        return {"error": "دیتا کافی نیست"}

    df_recent = df.iloc[-300:].reset_index(drop=True)

    # نقطه شروع: کلوز کندل ۳۰۰ام قبل
    start_price = df_recent.iloc[0]['close']
    start_time = to_shamsi(df_recent.iloc[0]['timestamp'])

    pivots = zig_zag(df_recent, depth=12, deviation=5, backstep=3)

    # تمام نقاط زیگزاگ (فقط قیمت کلوز کندل چرخش)
    reversal_prices = []
    for i, (idx, price, ptype) in enumerate(pivots[1:], start=1):  # از نقطه دوم
        t = to_shamsi(df_recent.iloc[idx]['timestamp'])
        arrow = "Up" if ptype == 'high' else "Down"
        reversal_prices.append(f"{arrow} نقطه #{i}: ${price:,.2f} — {t}")

    # تشخیص روند
    if len(reversal_prices) < 2:
        trend = "نامشخص"
        suggestion = "صبر کن"
    else:
        last_two = reversal_prices[-2:]
        last_is_up = "Up" in last_two[-1]
        prev_is_down = "Down" in last_two[-2] if len(last_two) > 1 else False
        if last_is_up and prev_is_down:
            trend = "صعودی قوی"
            suggestion = "لانگ یا هولد"
        elif not last_is_up and "Up" in last_two[-2]:
            trend = "نزولی قوی"
            suggestion = "شورت یا صبر"
        else:
            trend = "رنج / ساید وی"
            suggestion = "احتیاط"

    result = {
        "symbol": symbol.upper(),
        "price": f"${df_recent['close'].iloc[-1]:,.2f}",
        "trend": trend,
        "suggestion": suggestion,
        "start_point": f"شروع زیگزاگ: ${start_price:,.2f} — {start_time}",
        "reversal_prices": reversal_prices[::-1],  # جدید → قدیم
        "total_points": len(reversal_prices),
        "time": to_shamsi(datetime.now()),
    }

    # خط درست شده
    CACHE[cache_key] = (df_recent, result, now)
    return result
