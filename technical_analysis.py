# technical_analysis.py - نسخه کاملاً سفارشی برای شما
from binance.client import Client
import pandas as pd

client = Client()

def analyze(symbol: str):
    symbol = symbol.upper() + "USDT"

    try:
        klines = client.get_klines(symbol=symbol, interval="4h", limit=1000)
        if len(klines) < 500:
            return {"error": "داده کافی نیست"}

        # فقط ۵۰۰ کندل آخر
        df = pd.DataFrame(klines[-500:], columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'qav', 'trades', 'tbb', 'tbq', 'ignore'
        ])
        df['close'] = pd.to_numeric(df['close'])
        df['high'] = pd.to_numeric(df['high'])
        df['low'] = pd.to_numeric(df['low'])

        # نقطه مرجع: کلوز کندل شماره ۳۰۰ از آخر (یعنی ایندکس ۲۰۰ در دیتافریم ۵۰۰ تایی)
        reference_price = df['close'].iloc[200]  # کندل ۳۰۰ام از آخر

        # تنظیمات ZigZag
        depth = 12
        deviation = 0.05  # 5%
        backstep = 3

        pivots = []
        last_pivot_price = None
        last_pivot_type = None

        for i in range(depth, len(df) - depth):
            high = df['high'].iloc[i]
            low = df['low'].iloc[i]
            close = df['close'].iloc[i]

            is_high = high == df['high'].iloc[i-depth:i+depth+1].max()
            is_low = low == df['low'].iloc[i-depth:i+depth+1].min()

            if not (is_high or is_low):
                continue

            if last_pivot_price is None:
                # اولین پیوت بعد از کندل ۳۰۰
                pivots.append(close)
                last_pivot_price = close
                last_pivot_type = "high" if is_high else "low"
                continue

            change = (close - last_pivot_price) / last_pivot_price

            if (last_pivot_type == "high" and is_low and change <= -deviation) or \
               (last_pivot_type == "low" and is_high and change >= deviation):

                # Backstep چک
                valid = True
                recent_same = [p for p in pivots[-backstep:] if 
                              (last_pivot_type == "high" and p > close) or 
                              (last_pivot_type == "low" and p < close)]
                if recent_same:
                    valid = False

                if valid:
                    pivots.append(close)
                    last_pivot_price = close
                    last_pivot_type = "high" if is_high else "low"

        if len(pivots) < 2:
            return {"error": "اکستریم کافی تشکیل نشده"}

        # تعیین روند بر اساس اولین اکستریم نسبت به کندل ۳۰۰
        first_extreme = pivots[0]

        if first_extreme > reference_price:
            # روند صعودی → فقط Higher High ها
            filtered = []
            current_hh = reference_price
            for price in pivots:
                if price > current_hh:
                    filtered.append(price)
                    current_hh = price
        else:
            # روند نزولی → فقط Lower Low ها
            filtered = []
            current_ll = reference_price
            for price in pivots:
                if price < current_ll:
                    filtered.append(price)
                    current_ll = price

        if not filtered:
            return {"error": "روند مشخصی تشکیل نشده"}

        # خروجی نهایی: فقط قیمت‌ها، هر خط یکی
        key_levels = [f"{price:,.8f}".rstrip('0').rstrip('.') for price in filtered]

        return {
            "symbol": symbol.replace("USDT", ""),
            "key_levels": key_levels  # فقط لیست قیمت‌ها
        }

    except Exception as e:
        return {"error": f"خطا: {str(e)}"}
