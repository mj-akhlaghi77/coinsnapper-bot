# technical_analysis.py

import os
import requests
from datetime import datetime
import re

# تنظیمات TAAPI.IO
TAAPI_SECRET = os.getenv("TAAPI_SECRET")  # حتماً در .env بذار
TAAPI_BASE = "https://api.taapi.io"

# لیست اندیکاتورهای مهم (می‌تونی بعداً اضافه کنی)
INDICATORS = [
    {"indicator": "ema", "period": 50, "id": "EMA50"},
    {"indicator": "ema", "period": 200, "id": "EMA200"},
    {"indicator": "rsi", "period": 14, "id": "RSI"},
    {"indicator": "macd", "id": "MACD"},
    {"indicator": "supertrend", "period": 10, "multiplier": 3, "id": "ST"},
    {"indicator": "bbands2", "period": 20, "id": "BB"},
    {"indicator": "atr", "period": 14, "id": "ATR"},
    {"indicator": "volume", "id": "VOLUME"},
]

def get_technical_data(symbol: str, timeframe: str = "4h"):
    """دریافت همه اندیکاتورها از TAAPI.IO"""
    if not TAAPI_SECRET:
        return None, "کلید TAAPI.IO تنظیم نشده است."

    symbol = symbol.upper() + "/USDT"
    results = {}
    errors = []

    for ind in INDICATORS:
        params = {
            "secret": TAAPI_SECRET,
            "exchange": "binance",
            "symbol": symbol,
            "interval": timeframe,
            **{k: v for k, v in ind.items() if k != "id"}
        }
        try:
            url = f"{TAAPI_BASE}/{ind['indicator']}"
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                results[ind["id"]] = data
            else:
                errors.append(f"{ind['id']}: {resp.status_code}")
        except Exception as e:
            errors.append(f"{ind['id']}: {str(e)}")

    error_msg = "\n".join(errors) if errors else None
    return results, error_msg


def generate_technical_prompt(symbol: str, data: dict) -> str:
    """ساخت پرامپت حرفه‌ای برای GPT-4o"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""
تو یک تحلیلگر تکنیکال حرفه‌ای پراپ تریدینگ هستی. فقط بر اساس داده‌های واقعی زیر یک تحلیل کامل، واضح و قابل اجرا بنویس.
فقط از داده‌های زیر استفاده کن. هیچ حدس نزن.

ارز: {symbol.upper()}/USDT
تایم‌فریم: 4 ساعته
تاریخ: {now}

داده‌ها:
- قیمت فعلی: ${data.get('EMA50', {}).get('value', 'نامشخص'):.2f}
- EMA50: {data.get('EMA50', {}).get('value', 'نامشخص')}
- EMA200: {data.get('EMA200', {}).get('value', 'نامشخص')}
- RSI(14): {data.get('RSI', {}).get('value', 'نامشخص'):.1f}
- MACD: {data.get('MACD', {}).get('macd', 'نامشخص')} (Signal: {data.get('MACD', {}).get('signal', 'نامشخص')})
- Supertrend: {'سبز (صعودی)' if data.get('ST', {}).get('value', 0) > 0 else 'قرمز (نزولی)'}
- باند بولینگر: بالا {data.get('BB', {}).get('upper', 'نامشخص')} | پایین {data.get('BB', {}).get('lower', 'نامشخص')}
- حجم ۲۴h: {data.get('VOLUME', {}).get('value', 'نامشخص'):,.0f}

ساختار خروجی (حتماً دقیقاً همین باشه):
**تحلیل تکنیکال {symbol.upper()} (4h)**

**روند کلی**: صعودی / نزولی / رنج
**وضعیت قیمت نسبت به EMA50 و EMA200**
**وضعیت RSI و احتمال اشباع**
**وضعیت MACD و سیگنال**
**وضعیت Supertrend**
**سطوح کلیدی حمایت و مقاومت** (حداکثر ۳ تا)
**استراتژی پیشنهادی پراپ** (ورود، استاپ، تارگت، R:R)
**ریسک فعلی**: کم / متوسط / زیاد

از ایموجی‌های مرتبط استفاده کن. فقط تحلیل بنویس، نه مقدمه و نه نتیجه‌گیری اضافه.
"""


async def get_technical_analysis(symbol: str, context=None):
    data, error = get_technical_data(symbol)

    if not data or error:
        return f"تحلیل تکنیکال موقتاً در دسترس نیست.\nخطا: {error or 'دریافت داده'}"

    # ساخت پرامپت حرفه‌ای
    prompt = generate_technical_prompt(symbol, data)

    try:
        # استفاده از همون سیستم deep_analysis (با کش و همه چی)
        fake_coin = {
            "symbol": symbol,
            "name": symbol,
            "description": prompt  # اینجا پرامپت تکنیکال رو می‌ذاریم
        }
        from deep_analysis import call_openai_analysis
        raw_analysis = call_openai_analysis(fake_coin, context)

        # بلد کردن عنوان‌ها + فرار کاراکترها
        import re
        def escape_markdown_v2(text: str) -> str:
    """
    فرار دادن همه کاراکترهای رزرو شده در MarkdownV2
    این تابع ۱۰۰٪ همه چیز رو امن می‌کنه
    """
    escape_chars = r"\_*[]()~`>#+-=|{}.!"
    return ''.join('\\' + char if char in escape_chars else char for char in text)


async def get_technical_analysis(symbol: str, context=None):
    data, error = get_technical_data(symbol)

    if not data or error:
        return escape_markdown_v2(f"تحلیل تکنیکال موقتاً در دسترس نیست.\nخطا: {error or 'دریافت داده'}")

    prompt = generate_technical_prompt(symbol, data)

    try:
        fake_coin = {
            "symbol": symbol,
            "name": symbol,
            "description": prompt
        }
        from deep_analysis import call_openai_analysis
        raw_analysis = call_openai_analysis(fake_coin, context)

        # اول همه کاراکترهای مشکل‌دار رو فرار بده
        analysis = escape_markdown_v2(raw_analysis)

        # حالا فقط عنوان‌ها رو بلد کن (با * که امن هست)
        import re
        analysis = re.sub(r'(\d+\.\s*[^\n]+)', r'*\1*', analysis)

        return analysis

    except Exception as e:
        error_text = f"خطا در هوش مصنوعی:\n`{str(e)}`"
        return escape_markdown_v2(error_text)
