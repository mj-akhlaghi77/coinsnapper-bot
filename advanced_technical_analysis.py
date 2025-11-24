# advanced_technical_analysis.py
import os
import pandas as pd
import numpy as np
from binance.client import Client
from binance.exceptions import BinanceAPIException
import ta
from datetime import datetime
import psycopg2
from psycopg2.extras import DictCursor

# تنظیمات
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
DATABASE_URL = os.getenv("DATABASE_URL")

client = Client(BINANCE_API_KEY, BINANCE_API_SECRET) if BINANCE_API_KEY else Client()

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)

# کش ساده در حافظه (برای ۱۰ دقیقه)
_cache = {}
CACHE_MINUTES = 10

def get_klines(symbol: str, interval: str = "1h", limit: int = 1000):
    cache_key = f"{symbol}_{interval}"
    if cache_key in _cache:
        if datetime.now().timestamp() - _cache[cache_key]["time"] < CACHE_MINUTES * 60:
            return _cache[cache_key]["data"]
    
    try:
        klines = client.get_klines(symbol=symbol + "USDT", interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base', 'taker_buy_quote', 'ignore'
        ])
        df['close'] = pd.to_numeric(df['close'])
        df['high'] = pd.to_numeric(df['high'])
        df['low'] = pd.to_numeric(df['low'])
        df['open'] = pd.to_numeric(df['open'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        _cache[cache_key] = {"data": df, "time": datetime.now().timestamp()}
        return df
    except Exception as e:
        print(f"خطا در دریافت داده بایننس: {e}")
        return pd.DataFrame()

# --- اندیکاتورها ---
def zigzag(df, depth=12, deviation=5, backstep=3):
    zig = []
    last_pivot = None
    direction = 0
    for i in range(depth, len(df) - depth):
        high_idx = df['high'][i-depth:i+depth].idxmax()
        low_idx = df['low'][i-depth:i+depth].idxmin()
        
        if high_idx == i and (last_pivot is None or df['high'][i] > df['high'][last_pivot] * (1 + deviation/100)):
            if direction == -1:
                zig.append((last_pivot, 'low'))
            last_pivot = i
            direction = 1
        elif low_idx == i and (last_pivot is None or df['low'][i] < df['low'][last_pivot] * (1 - deviation/100)):
            if direction == 1:
                zig.append((last_pivot, 'high'))
            last_pivot = i
            direction = -1
    if last_pivot is not None:
        zig.append((last_pivot, 'high' if direction == 1 else 'low'))
    return zig

def ichimoku_senkou_span_b(df, period=52):
    span_b = (df['high'].rolling(period).max() + df['low'].rolling(period).min()) / 2
    return span_b.shift(26)

def is_flat(series, window=15, threshold=0.0005):
    return series.pct_change(window).abs().lt(threshold).rolling(window).sum() >= window

# --- دایورجنس ---
def detect_divergence(price_peaks, indicator_peaks, type='regular'):
    if len(price_peaks) < 2 or len(indicator_peaks) < 2:
        return False
    p1, p2 = price_peaks[-2], price_peaks[-1]
    i1, i2 = indicator_peaks[-2], indicator_peaks[-1]
    if type == 'regular':
        return (p2 > p1 and i2 < i1) or (p2 < p1 and i2 > i1)
    else:  # hidden
        return (p2 < p1 and i2 > i1) or (p2 > p1 and i2 < i1)

# --- تحلیل اصلی ---
def advanced_technical_analysis(symbol: str) -> str:
    symbol = symbol.upper()
    df = get_klines(symbol, interval="5m", limit=1000)  # ۵ دقیقه
    if df.empty or len(df) < 300:
        return f"داده کافی برای {symbol} وجود ندارد."

    close = df['close']
    
    # اندیکاتورها
    df['ema20'] = ta.trend.EMAIndicator(close, window=20).ema_indicator()
    df['ema50'] = ta.trend.EMAIndicator(close, window=50).ema_indicator()
    df['ema100'] = ta.trend.EMAIndicator(close, window=100).ema_indicator()
    
    macd_4x = ta.trend.MACD(close, window_slow=104, window_fast=48, window_sign=36)
    macd_default = ta.trend.MACD(close)
    macd_small = ta.trend.MACD(close, window_fast=3, window_slow=6, window_sign=2)
    
    df['macd_4x'] = macd_4x.macd()
    df['macd_def'] = macd_default.macd()
    df['macd_small'] = macd_small.macd()
    
    df['rsi'] = ta.momentum.RSIIndicator(close, window=14).rsi()
    df['stoch_k'] = ta.momentum.StochasticOscillator(df['high'], df['low'], close, window=5, smooth_window=3).stoch()
    
    df['senkou_b'] = ichimoku_senkou_span_b(df)
    df['senkou_b_flat'] = is_flat(df['senkou_b'])

    # زیگزاگ‌ها
    zz1 = zigzag(df, depth=12, deviation=5, backstep=3)
    zz2 = zigzag(df, depth=5, deviation=5, backstep=3)

    if len(zz1) < 3:
        return "زیگزاگ کافی تشکیل نشده."

    # آخرین های و لو
    last_high = max([df['high'].iloc[i] for i, t in zz1 if t == 'high'][-2:], default=0)
    last_low = min([df['low'].iloc[i] for i, t in zz1 if t == 'low'][-2:], default=float('inf'))
    
    current_price = close.iloc[-1]
    trend_direction = "صعودی" if df['ema100'].iloc[-1] < current_price and df['macd_4x'].iloc[-1] > 0 else "نزولی"
    
    # تشخیص سایدوی
    recent_zz = [i for i, _ in zz1[-10:]]
    if len(recent_zz) >= 2:
        if max(recent_zz) - min(recent_zz) < 50:
            sideway = "در حال تشکیل سایدوی"
        else:
            sideway = "خارج از سایدوی"
    else:
        sideway = "در روند"

    # دایورجنس‌ها
    rsi_div = "هیدن دایورجنس RSI دیده شد" if detect_divergence(
        [df['high'].iloc[i] for i, t in zz1 if t == 'high'],
        [df['rsi'].iloc[i] for i, t in zz1 if t == 'high']
    ) else "بدون دایورجنس RSI"
    
    macd_hidden = "هیدن دایورجنس مکدی کوچک" if detect_divergence(
        [df['low'].iloc[i] for i, t in zz2 if t == 'low'],
        [df['macd_small'].iloc[i] for i, t in zz2 if t == 'low'], 'hidden'
    ) else ""

    # خروجی نهایی
    analysis = f"""تحلیل تکنیکال پیشرفته {symbol}/تتر (تایم‌فریم ۱ ساعته)

وضعیت کلی بازار
روند اصلی: {trend_direction}
وضعیت فعلی: {sideway}
قیمت فعلی: {current_price:,.2f}

سطوح کلیدی (زیگزاگ + سنکو اسپن B)
آخرین های: {last_high:,.2f}
آخرین لو: {last_low:,.2f}
لِوِل مهم بعدی: {'سنکو اسپن B فلت' if df['senkou_b_flat'].iloc[-1] else 'بدون لِوِل فلت'}

سیگنال‌های مهم
{macd_hidden if macd_hidden else 'بدون هیدن دایورجنس'}
{rsi_div}
{'احتمال اتمام اصلاح و ادامه روند' if macd_hidden else 'بدون سیگنال برگشتی قوی'}

جمع‌بندی
در حال حاضر {'در روند صعودی قوی' if trend_direction == 'صعودی' and current_price > df['ema50'].iloc[-1] else 'در اصلاح یا سایدوی'}
تارگت بعدی: {'بالای آخرین های' if trend_direction == 'صعودی' else 'زیر آخرین لو'}
حمایت/مقاومت مهم: سنکو اسپن B یا زیگزاگ"""

    # پاکسازی نهایی
    analysis = analysis.replace('**', '').replace('*', '').replace('_', '').replace('#', '')
    lines = [line.strip() for line in analysis.split('\n') if line.strip()]
    return '\n'.join(lines)
