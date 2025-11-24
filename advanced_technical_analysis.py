# advanced_technical_analysis.py (بازنویسی شده)

import os
import pandas as pd
import numpy as np
from binance.client import Client
from datetime import datetime, timedelta
import ta

# تنظیمات اتصال بایننس (اگر نیاز باشه از env بار میشه)
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
client = Client(BINANCE_API_KEY, BINANCE_API_SECRET) if BINANCE_API_KEY else Client()

# --- کش در حافظه: ۵ دقیقه (مطابق درخواست) ---
_cache = {}
CACHE_MINUTES = 5

def get_klines(symbol: str, interval: str = "30m", limit: int = 1000):
    """
    برمی‌گرداند: DataFrame با ستون‌های timestamp, open, high, low, close, volume
    کش ۵ دقیقه‌ای برای کاهش تماس‌های شبکه.
    """
    cache_key = f"{symbol}_{interval}"
    now_ts = datetime.now().timestamp()
    if cache_key in _cache:
        entry = _cache[cache_key]
        if now_ts - entry["time"] < CACHE_MINUTES * 60:
            return entry["data"].copy()
    try:
        raw = client.get_klines(symbol=symbol + "USDT", interval=interval, limit=limit)
        df = pd.DataFrame(raw, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base', 'taker_buy_quote', 'ignore'
        ])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].apply(pd.to_numeric, errors='coerce')
        df = df[['timestamp','open','high','low','close','volume']].reset_index(drop=True)
        _cache[cache_key] = {"data": df.copy(), "time": now_ts}
        return df
    except Exception as e:
        print(f"خطا در دریافت داده بایننس: {e}")
        return pd.DataFrame(columns=['timestamp','open','high','low','close','volume'])


# --- سنکو اسپن B با بازه 1000 و تشخیص فلت حداقل 15 کندل ---
def ichimoku_senkou_span_b(df, period=1000, shift=26):
    """
    محاسبه Senkou Span B با دوره‌ی پیش‌فرض 1000 (مطابق خواست شما).
    نتیجه به اندازه‌ی shift شیفت داده می‌شود تا مانند ایچیموکو قرار گیرد.
    """
    if len(df) < period:
        # اگر داده کمتر است از کل طول استفاده کن (بیشینه تا طول موجود)
        period = max(2, len(df)//2)
    span_b = (df['high'].rolling(period).max() + df['low'].rolling(period).min()) / 2
    return span_b.shift(shift)

def is_flat(series: pd.Series, window: int = 15, tol: float = 1e-6):
    """
    تشخیص فلت بودن یک سری: اگر تغییرات مطلق بین مقادیر در یک پنجره از
    window کندل کمتر از tol باشد، آن پنجره را فلت فرض می‌کنیم.
    خروجی: سری بولی که نشان می‌دهد در هر ایندکس آیا قبلاً window کندل فلت بوده یا نه.
    """
    if series.isna().all():
        return pd.Series(False, index=series.index)
    # مقدار اختلاف نسبی یا مطلق — از اختلاف نسبی بر اساس میانگین استفاده می‌کنیم
    diff = series.diff().abs()
    # برای جلوگیری از تقسیم بر صفر:
    denom = series.rolling(window).mean().abs().replace(0, np.nan)
    rel = diff / denom
    rel = rel.fillna(0)
    flat_mask = rel.rolling(window, min_periods=1).max() < tol
    return flat_mask.fillna(False)


# --- زیگزاگ مقاوم‌تر (پشتیبانی از depth, deviation(٪), backstep) ---
def zigzag(df, depth=12, deviation=5, backstep=3):
    """
    پیوت‌های زیگزاگ را به صورت [(index, 'high'|'low'), ...] برمی‌گرداند.
    منطق:
      - اگر یک high محلی (بزرگترین در بازه depth اطراف) پیدا شد و
        از آخرین پیوت بیش از backstep کندل فاصله داشته باشد و
        نسبت به آخرین پیوت مطابق deviation % تغییر کرده باشد -> پیوت ثبت می‌شود.
    """
    pivots = []
    last_idx = None
    last_type = None
    dev = deviation / 100.0

    n = len(df)
    for i in range(depth, n - depth):
        window_high = df['high'].iloc[i - depth: i + depth + 1]
        window_low  = df['low'].iloc[i - depth: i + depth + 1]
        is_high = df['high'].iat[i] == window_high.max()
        is_low  = df['low'].iat[i] == window_low.min()

        if is_high:
            if last_idx is None or (i - last_idx) >= backstep:
                if last_type is None:
                    pivots.append((i, 'high'))
                    last_idx, last_type = i, 'high'
                else:
                    # بررسی deviation نسبت به مقدار آخرین پیوت
                    last_price = df['high'].iat[last_idx] if last_type == 'high' else df['low'].iat[last_idx]
                    if df['high'].iat[i] > last_price * (1 + dev):
                        pivots.append((i, 'high'))
                        last_idx, last_type = i, 'high'
        elif is_low:
            if last_idx is None or (i - last_idx) >= backstep:
                if last_type is None:
                    pivots.append((i, 'low'))
                    last_idx, last_type = i, 'low'
                else:
                    last_price = df['low'].iat[last_idx] if last_type == 'low' else df['high'].iat[last_idx]
                    if df['low'].iat[i] < last_price * (1 - dev):
                        pivots.append((i, 'low'))
                        last_idx, last_type = i, 'low'
    # ensure last pivot appended (if exists)
    return pivots


# --- دایورجنس (منطق ساده و ایمن) ---
def detect_divergence(price_vals, indicator_vals, kind='regular'):
    """
    ورودی‌ها می‌توانند لیست اندیس‌ها یا مقادیر باشند؛ ما مقادیر می‌پذیریم.
    kind: 'regular' یا 'hidden'
    خروجی: True/False
    منطق ساده:
      - regular: if price makes higher-high but indicator lower-high (یا عکس برای پایین‌ها)
      - hidden: price makes lower-high but indicator higher-high (یا عکس)
    """
    if len(price_vals) < 2 or len(indicator_vals) < 2:
        return False
    p1, p2 = price_vals[-2], price_vals[-1]
    i1, i2 = indicator_vals[-2], indicator_vals[-1]
    try:
        if kind == 'regular':
            return (p2 > p1 and i2 < i1) or (p2 < p1 and i2 > i1)
        else:
            return (p2 < p1 and i2 > i1) or (p2 > p1 and i2 < i1)
    except Exception:
        return False


# --- تحلیل کلی (تابع اصلی که main.py صدا می‌زند) ---
def advanced_technical_analysis(symbol: str, interval: str = "30m") -> str:
    """
    خروجی متن فارسی برای ارسال به تلگرام.
    تحلیل در تایم‌فریم interval انجام می‌شود (پیش‌فرض 30m).
    """
    symbol = symbol.upper()
    df = get_klines(symbol, interval=interval, limit=1200)
    if df.empty or len(df) < 300:
        return f"دادهٔ کافی برای {symbol} در تایم‌فریم {interval} موجود نیست (حداقل ۳۰۰ کندل لازم است)."

    close = df['close']

    # --- مووینگ‌ها ---
    df['ema20'] = ta.trend.ema_indicator(close, window=20)
    df['ema50'] = ta.trend.ema_indicator(close, window=50)
    df['ema100'] = ta.trend.ema_indicator(close, window=100)

    # --- مکدی‌ها (استفاده از macd_diff برای histogram جهت تشخیص فاز) ---
    macd_4x = ta.trend.MACD(close, window_slow=104, window_fast=48, window_sign=36)
    macd_def = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    macd_small = ta.trend.MACD(close, window_slow=6, window_fast=3, window_sign=2)

    df['macd_4x'] = macd_4x.macd()
    df['macd_4x_hist'] = macd_4x.macd_diff()
    df['macd_def'] = macd_def.macd()
    df['macd_def_hist'] = macd_def.macd_diff()
    df['macd_small'] = macd_small.macd()
    df['macd_small_hist'] = macd_small.macd_diff()

    # --- RSI و استوکاستیک (K و D) ---
    df['rsi'] = ta.momentum.rsi(close, window=14)
    stoch = ta.momentum.StochasticOscillator(df['high'], df['low'], close, window=5, smooth_window=3)
    df['stoch_k'] = stoch.stoch()          # %K
    # ta library may not expose %D directly; محاسبهٔ دستی برای D:
    df['stoch_d'] = df['stoch_k'].rolling(3, min_periods=1).mean()

    # --- senkou span B و flat detection ---
    df['senkou_b'] = ichimoku_senkou_span_b(df, period=1000, shift=26)
    df['senkou_b_flat'] = is_flat(df['senkou_b'], window=15, tol=1e-5)

    # --- زیگزاگها ---
    zz1 = zigzag(df, depth=12, deviation=5, backstep=3)
    zz2 = zigzag(df, depth=5, deviation=5, backstep=3)

    if len(zz1) < 2:
        return f"زیگزاگ (عمومی) برای {symbol} کافی نیست — نیاز به حداقل ۲ پیوت."

    # استخراج آخرین های و لوها به صورت امن
    highs_idx = [i for i, t in zz1 if t == 'high']
    lows_idx  = [i for i, t in zz1 if t == 'low']
    last_high = df['high'].iat[highs_idx[-1]] if highs_idx else None
    prev_high = df['high'].iat[highs_idx[-2]] if len(highs_idx) >= 2 else None
    last_low  = df['low'].iat[lows_idx[-1]] if lows_idx else None
    prev_low  = df['low'].iat[lows_idx[-2]] if len(lows_idx) >= 2 else None

    current_price = float(close.iat[-1])

    # تعیین جهت کلی روند بر اساس EMA100 و macd_4x_hist (فاز)
    ema100 = df['ema100'].iat[-1]
    macd4_hist = df['macd_4x_hist'].iat[-1]
    if np.isnan(ema100) or np.isnan(macd4_hist):
        trend_direction = "نامشخص"
    else:
        if current_price > ema100 and macd4_hist > 0:
            trend_direction = "صعودی"
        elif current_price < ema100 and macd4_hist < 0:
            trend_direction = "نزولی"
        else:
            trend_direction = "بی‌طرف/در حال انتقال"

    # تشخیص سایدوی: اگر بعد از آخرین پیوت اصلی (zz1) تا انتهای داده پیوت جدیدی در ۵۰ کندل ایجاد نشده => در سایدوی
    last_pivot_idx = zz1[-1][0]
    if (len(df) - 1) - last_pivot_idx >= 50:
        sideway = "در حال تشکیل سایدوی"
    else:
        sideway = "خارج از سایدوی"

    # --- دایورجنس‌ها ---
    # برای RSI در های/لوهای زیگزاگ اصلی
    rsi_peak_vals = [df['rsi'].iat[i] for i, t in zz1 if (t == 'high' and not pd.isna(df['rsi'].iat[i])) or (t == 'low' and not pd.isna(df['rsi'].iat[i]))]
    price_peak_vals = [df['high'].iat[i] if t == 'high' else df['low'].iat[i] for i, t in zz1]
    # دایورجنس RSI در سقف‌ها/کف‌ها (منطبق با تعریف شما — فقط در سقف‌ها برای روند صعودی و در کف‌ها برای روند نزولی)
    rsi_div = detect_divergence(price_peak_vals, rsi_peak_vals, kind='regular')

    # هیدن دایورجنس مکدی کوچک (دوزیگزاگ دوم برای دقت بیشتر)
    small_price_vals = [df['low'].iat[i] if t == 'low' else df['high'].iat[i] for i, t in zz2]
    small_macd_vals = [df['macd_small_hist'].iat[i] for i, t in zz2]
    macd_hidden = detect_divergence(small_price_vals, small_macd_vals, kind='hidden')

    # استوکاستیک برای پیدا کردن نقاط دقیق برگشت (ترکیب با macd_small_hidden)
    stoch_signal = False
    # اگر macd کوچک هیدن داده و استوکاستیک در منطقه اشباع و divergence محلی وجود داشته باشد
    if macd_hidden:
        # بررسی آخرین استوکاستیک
        if df['stoch_k'].iat[-1] is not None and not pd.isna(df['stoch_k'].iat[-1]):
            if df['stoch_k'].iat[-1] < 20 or df['stoch_k'].iat[-1] > 80:
                stoch_signal = True

    # ساخت خروجی متنی
    lines = []
    lines.append(f"تحلیل تکنیکال پیشرفته {symbol}/USDT (تایم‌فریم {interval})")
    lines.append("وضعیت کلی بازار:")
    lines.append(f"روند اصلی: {trend_direction}")
    lines.append(f"وضعیت فعلی: {sideway}")
    lines.append(f"قیمت فعلی: {current_price:,.6f}")

    lines.append("")
    lines.append("سیاست زیگزاگ + سنکو اسپن B:")
    if last_high is not None:
        lines.append(f"آخرین های (زیگزاگ اصلی): {last_high:,.6f}")
    if last_low is not None:
        lines.append(f"آخرین لو (زیگزاگ اصلی): {last_low:,.6f}")
    if df['senkou_b'].iat[-1] and df['senkou_b_flat'].iat[-1]:
        lines.append("سنکو اسپن B: فلت (سطح معتبر)")
    else:
        lines.append("سنکو اسپن B: بدون لِوِل فلت اخیر")

    lines.append("")
    lines.append("دایورجنس‌ها و سیگنال‌ها:")
    lines.append(f"دایورجنس RSI: {'دیده شد' if rsi_div else 'ندیدیم'}")
    lines.append(f"هیدن دایورجنس مکدی کوچک: {'دیده شد' if macd_hidden else 'ندیدیم'}")
    if stoch_signal:
        lines.append("استوکاستیک: شرایط برگشت (اشباع) مشاهده شد — ترکیب با هیدن مکدی کوچک تایید می‌کند")
    else:
        lines.append("استوکاستیک: سیگنال قوی برگشتی دیده نشد")

    # توصیهٔ کلی (سطحی؛ فقط بر اساس قوانین توصیف‌شده — نه سیگنال معاملاتی مطلق)
    lines.append("")
    if trend_direction == "صعودی" and current_price > df['ema50'].iat[-1]:
        lines.append("جمع‌بندی: در روند صعودی قرار داریم — نگاه بلندمدت صعودی")
        lines.append("تارگت: بالای آخرین های (سطح‌های بعدی مطابق سنکو و زیگزاگ)")
    elif trend_direction == "نزولی" and current_price < df['ema50'].iat[-1]:
        lines.append("جمع‌بندی: در روند نزولی قرار داریم — مراقب شکست حمایتها")
        lines.append("تارگت: زیر آخرین لو (سطح‌های بعدی مطابق سنکو و زیگزاگ)")
    else:
        lines.append("جمع‌بندی: بازار در انتقال یا اصلاح است — منتظر تأیید بیشتر (مکدی ۴ برابر + کراس مووینگ + عبور قیمت از آخرین های/لو) باشیم")

    return "\n".join(lines)
