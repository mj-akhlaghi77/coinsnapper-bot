# اضافه کن به technical_analysis.py
import pandas as pd
import numpy as np
from datetime import datetime

def _find_local_extrema(prices, depth):
    """
    local extrema: نقطه‌ای که در بازه depth قبل و بعد خود بیشترین/کمترین باشد.
    برمی‌گرداند لیستی از تاپل‌ها: (index, price, 'high'|'low')
    """
    n = len(prices)
    extrema = []
    for i in range(depth, n - depth):
        window = prices[i - depth: i + depth + 1]
        val = prices[i]
        if val == max(window):
            extrema.append((i, val, 'high'))
        elif val == min(window):
            extrema.append((i, val, 'low'))
    return extrema

def _apply_backstep_and_deviation(extrema, backstep, deviation_pct):
    """
    حذف یا ادغام اکستریماهای نزدیک (backstep) و اعمال حداقل deviation در درصد
    خروجی: فهرست فیلترشده‌ی اکستریما به ترتیب زمانی
    """
    if not extrema:
        return []

    filtered = []
    for idx, price, typ in extrema:
        if not filtered:
            filtered.append([idx, price, typ])
            continue

        last_idx, last_price, last_typ = filtered[-1]
        # اگر هم‌نوع و نزدیک‌تر از backstep باشند: نگه دار قوی‌تر (برای high => بزرگتر، برای low => کوچکتر)
        if typ == last_typ and abs(idx - last_idx) <= backstep:
            if typ == 'high':
                if price > last_price:
                    filtered[-1] = [idx, price, typ]
            else:  # low
                if price < last_price:
                    filtered[-1] = [idx, price, typ]
            continue

        # اگر اختلاف قیمت کمتر از deviation_pct نسبت به آخرین اکستریم باشه، نادیده بگیر
        rel_change = abs(price - last_price) / last_price * 100.0 if last_price != 0 else 100.0
        if rel_change < deviation_pct:
            # در صورتی که نوع متفاوت است، ممکنه بخواهیم جایگزین کنیم (اما برای سادگی نادیده می‌گیریم)
            # این شرط باعث میشه اکستریماهای خیلی کوچک حذف بشن
            continue

        filtered.append([idx, price, typ])

    # تبدیل به tuples
    return [(i, p, t) for i, p, t in filtered]

def analyze_from_df(df, depth=12, deviation=5, backstep=3):
    """
    ورودی: df با ستون 'close' و ایندکس زمانی یا شماره‌ای (صعودی)
    خروجی: dict شامل {'trend': 'صعودی'|'نزولی'|'سایدوی', 'extrema': [...], 'reason': '...'}
    منطق:
      - اکستریما با پنجره depth پیدا میشه
      - پس از اعمال backstep و deviation، اگر آخرین high نسبت به قبلی high بالاتر باشه و آخرین low نسبت به قبلی low بالاتر باشه => صعودی
      - اگر برعکس باشه => نزولی
      - اگر در 50 کندل آخر هیچ اکستریمی که بالاتر از آخرین high یا پایین‌تر از آخرین low باشه تشکیل نشده باشه => سایدوی
    """
    prices = df['close'].values
    n = len(prices)
    if n < depth * 2 + 1:
        return {"trend": "نامشخص", "extrema": [], "reason": "دیتای کافی نیست"}

    raw_ext = _find_local_extrema(prices, depth)
    ext = _apply_backstep_and_deviation(raw_ext, backstep, deviation)

    # جدا کردن highs و lows
    highs = [e for e in ext if e[2] == 'high']
    lows = [e for e in ext if e[2] == 'low']

    trend = "نامشخص"
    reason = ""

    # قاعده صعودی / نزولی ساده:
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]:
            trend = "قوی صعودی"
            reason = "higher highs و higher lows تشکیل شده‌اند"
        elif highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]:
            trend = "قوی نزولی"
            reason = "lower highs و lower lows تشکیل شده‌اند"
        else:
            trend = "نامشخص"
            reason = "الگوی highs/lows صریح نیست"
    else:
        trend = "نامشخص"
        reason = "تعداد اکستریماها کافی نیست"

    # قاعده سایدوی: اگر طی 50 کندل اخیر هیچ اکستریمی که بالاتر از آخرین high یا پایین‌تر از آخرین low باشه شکل نگرفته باشه => سایدوی
    last_index = n - 1
    window_start_idx = max(0, n - 50)
    # اکستریماهایی که در 50 کندل آخر قرار دارند:
    recent_ext = [e for e in ext if e[0] >= window_start_idx]
    is_side = True
    if highs:
        last_high_price = highs[-1][1]
        # آیا در recent_ext high ای بالاتر از last_high_price هست؟
        for i, p, t in recent_ext:
            if t == 'high' and p > last_high_price:
                is_side = False
                break
    if lows and is_side:
        last_low_price = lows[-1][1]
        for i, p, t in recent_ext:
            if t == 'low' and p < last_low_price:
                is_side = False
                break

    if is_side and recent_ext:
        # اگر واقعاً در 50 کندل اخیر هیچ اکستریمی تغییر‌دهنده نبوده => سایدوی
        trend = "سایدوی"
        reason = "طی ۵۰ کندل اخیر اکستریم جدید صعودی یا نزولی تشکیل نشد"

    return {"trend": trend, "extrema": ext, "reason": reason}
