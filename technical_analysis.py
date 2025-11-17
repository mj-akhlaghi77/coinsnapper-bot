# technical_analysis.py
import os
import requests
import json
from typing import Tuple, Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv()

TAAPI_SECRET = os.getenv("TAAPI_SECRET")
if not TAAPI_SECRET:
    raise ValueError("TAAPI_SECRET در فایل .env تنظیم نشده است!")

# لیست اندیکاتورهای مورد نیاز
INDICATORS = [
    "ema", "ema", "sma", "sma", "rsi", "macd", "stoch", "bbands", "supertrend", "ichimoku", "adx", "obv"
]
PERIODS = {
    "ema": [50, 200],
    "sma": [50, 200],
    "rsi": [14],
    "macd": [None],
    "stoch": [None],
    "bbands": [None],
    "supertrend": [None],
    "ichimoku": [None],
    "adx": [None],
    "obv": [None]
}

def get_technical_data(symbol: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """دریافت داده‌های تکنیکال از TAAPI.IO"""
    url = "https://api.taapi.io/bulk"
    headers = {"Content-Type": "application/json"}
    
    queries = []
    for indicator in INDICATORS:
        if indicator in ["ema", "sma"]:
            for period in PERIODS[indicator]:
                queries.append({
                    "indicator": indicator,
                    "params": {"period": period},
                    "id": f"{indicator.upper()}{period}"
                })
        else:
            queries.append({
                "indicator": indicator,
                "id": indicator.upper()
            })

    payload = {
        "secret": TAAPI_SECRET,
        "exchange": "binance",
        "symbol": f"{symbol}/USDT",
        "interval": "4h",
        "indicators": queries
    }

    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code != 200:
            return None, f"خطای TAAPI: {response.status_code}"

        data = response.json()
        if "error" in data:
            return None, f"خطای TAAPI: {data['error']}"

        result = {}
        for item in data:
            key = item["id"]
            value = item["result"]["value"] if "value" in item["result"] else item["result"]
            result[key] = {"value": value}

        return result, None

    except requests.exceptions.RequestException as e:
        return None, f"خطا در ارتباط با TAAPI: {str(e)}"
    except Exception as e:
        return None, f"خطای غیرمنتظره: {str(e)}"


def generate_technical_prompt(symbol: str, data: Dict[str, Any]) -> str:
    """ساخت پرامپت حرفه‌ای برای GPT"""
    price = data.get("EMA50", {}).get("value", "نامشخص")
    ema50 = data.get("EMA50", {}).get("value", "نامشخص")
    ema200 = data.get("EMA200", {}).get("value", "نامشخص")
    rsi = data.get("RSI", {}).get("value", "نامشخص")
    macd = data.get("MACD", {}).get("value", {})
    supertrend = data.get("SUPERTREND", {}).get("value", "نامشخص")

    return f"""
نماد: {symbol}/USDT
تایم‌فریم: 4 ساعته

داده‌های فعلی:
- قیمت فعلی: {price}
- EMA50: {ema50}
- EMA200: {ema200}
- RSI(14): {rsi}
- MACD: {macd}
- Supertrend: {supertrend}

لطفاً یک تحلیل تکنیکال حرفه‌ای، کوتاه و دقیق بنویس که شامل این موارد باشد:
1. روند کلی (صعودی/نزولی/رنج)
2. وضعیت قیمت نسبت به EMA50 و EMA200
3. وضعیت RSI و احتمال اشباع خرید/فروش
4. سیگنال MACD
5. وضعیت Supertrend
6. سطوح کلیدی حمایت و مقاومت
7. استراتژی پیشنهادی پراپ (ورود، استاپ، تارگت، R:R)
8. ریسک فعلی

از ایموجی‌های مناسب استفاده کن و خروجی رو تمیز و مرتب بنویس.
فقط تحلیل را بنویس، بدون مقدمه.
"""


def escape_markdown_v2(text: str) -> str:
    """فرار دادن کاراکترهای رزرو شده در MarkdownV2 — بدون دست زدن به * و _"""
    if not text:
        return ""
    escape_chars = r"\_[]()~`>#+-=|{}.!"
    return ''.join('\\' + c if c in escape_chars else c for c in text)


async def get_technical_analysis(symbol: str, context=None) -> str:
    """تابع اصلی — تحلیل تکنیکال کامل با GPT"""
    data, error = get_technical_data(symbol)

    if error or not data:
        return escape_markdown_v2(f"*تحلیل تکنیکال در دسترس نیست*\nخطا: `{error or 'داده دریافت نشد'}`")

    prompt = generate_technical_prompt(symbol, data)

    try:
        fake_coin = {"symbol": symbol, "name": symbol, "description": prompt}
        from deep_analysis import call_openai_analysis
        raw_analysis = call_openai_analysis(fake_coin, context)

        # مرحله ۱: فرار کاراکترهای خطرناک
        safe_text = escape_markdown_v2(raw_analysis)

        # مرحله ۲: تبدیل ** به *
        safe_text = safe_text.replace("**", "*")

        # مرحله ۳: بلد کردن خطوط شماره‌دار (مثل 1. عنوان)
        import re
        lines = safe_text.split('\n')
        final_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and stripped[0].isdigit() and '.' in stripped[:6]:
                line = "*" + line.strip() + "*"
            final_lines.append(line)
        final_text = '\n'.join(final_lines)

        return final_text

    except Exception as e:
        error_msg = f"*خطا در تحلیل تکنیکال*\n`{str(e)[:100]}`"
        return escape_markdown_v2(error_msg)
