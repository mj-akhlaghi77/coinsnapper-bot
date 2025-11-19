# technical_analysis.py
import time
import pandas as pd
import pandas_ta as ta
from binance.client import Client
from datetime import datetime
import jdatetime

# کش در مموری (۵ دقیقه)
CACHE = {}
CACHE_TTL = 300  # ثانیه

client = Client()  # عمومی، نیازی به کلید نیست

def to_shamsi(dt):
    try:
        return jdatetime.datetime.fromgregorian(datetime=dt).strftime("%Y/%m/%d - %H:%M")
    except:
        return dt.strftime("%Y-%m-%d %H:%M")

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

def detect_flat_span_b(df: pd.DataFrame):
    ichi = df.ta.ichimoku()
    if ichi is None or len(ichi) < 2:
        return []
    span_b = ichi[1]['ISS_26'].dropna()
    levels = []
    i = len(span_b) - 300 if len(span_b) > 300 else 0
    while i < len(span_b) - 10:
        window = span_b.iloc[i:i+15]
        if (window.max() - window.min()) / window.min() < 0.003:  # کمتر از 0.3%
            levels.append(round(window.mean(), 6))
            i += 12
        else:
            i += 1
    return list(set(levels))[:3]  # سطوح منحصر به فرد

def analyze(symbol: str, interval: str = "4h") -> dict:
    cache_key = f"{symbol.upper()}_{interval}"
    now = time.time()

    # کش
    if cache_key in CACHE and now - CACHE[cache_key][2] < CACHE_TTL:
        return CACHE[cache_key][1]

    df = get_klines(symbol.upper(), interval)
    if df is None or len(df) < 200:
        return {"error": "دیتا از بایننس دریافت نشد"}

    # اندیکاتورها
    df['EMA26'] = ta.ema(df['close'], 26)
    df['EMA50'] = ta.ema(df['close'], 50)
    df['EMA100'] = ta.ema(df['close'], 100)
    
    macd = df.ta.macd(fast=48, slow=104, signal=36)
    df['MACD'] = macd['MACD_48_104_36']
    df['MACD_sig'] = macd['MACDs_48_104_36']
    
    df['RSI'] = ta.rsi(df['close'], length=14)

    c = df.iloc[-1]   # کندل آخر
    p = df.iloc[-2]   # کندل قبلی

    # کراس‌های صعودی
    cross_up_26_50 = c.EMA26 > c.EMA50 and p.EMA26 <= p.EMA50
    cross_up_50_100 = c.EMA50 > c.EMA100 and p.EMA50 <= p.EMA100
    cross_up_26_100 = c.EMA26 > c.EMA100 and p.EMA26 <= p.EMA100

    # همه بالای EMA100؟
    all_above_100 = (c.EMA26 > c.EMA100 and c.EMA50 > c.EMA100 and c.close > c.EMA100)
    all_below_100 = (c.EMA26 < c.EMA100 and c.EMA50 < c.EMA100 and c.close < c.EMA100)

    # تأیید MACD
    macd_bull = c.MACD > c.MACD_sig and c.MACD > 0
    macd_bear = c.MACD < c.MACD_sig and c.MACD < 0

    levels = detect_flat_span_b(df)

    # تصمیم نهایی روند
    if all_above_100 and macd_bull and (cross_up_26_50 or cross_up_50_100 or cross_up_26_100):
        trend = "قوی صعودی 🔥🔥"
        suggestion = "لانگ قوی"
    elif all_above_100 and macd_bull:
        trend = "صعودی 🟢"
        suggestion = "لانگ یا هولد"
    elif all_below_100 and macd_bear:
        trend = "نزولی 🔴"
        suggestion = "شورت یا احتیاط"
    elif all_below_100 and macd_bear:
        trend = "قوی نزولی 🛑"
        suggestion = "شورت قوی"
    else:
        trend = "خنثی / رنج ⚪"
        suggestion = "صبر کن - رنج"

    # هشدار RSI
    rsi_text = f"RSI: {c.RSI:.1f}"
    if c.RSI > 70:
        rsi_text += " ➜ اشباع خرید ⚠️"
    elif c.RSI < 30:
        rsi_text += " ➜ اشباع فروش ⚠️"

    result = {
        "symbol": symbol.upper(),
        "price": f"${c.close:,.2f}",
        "trend": trend,
        "suggestion": suggestion,
        "rsi": rsi_text,
        "macd": "صعودی 🟢" if macd_bull else "نزولی 🔴" if macd_bear else "خنثی ⚪",
        "key_levels": levels,
        "time": to_shamsi(datetime.now())
    }

    CACHE[cache_key] = (df, result, now)
    return result
