# technical_analysis.py
import os
import requests
import psycopg2
from psycopg2.extras import DictCursor
from datetime import datetime, timedelta

# تنظیمات
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TAAPI_SECRET = os.getenv("TAAPI_SECRET")  # کلید Runflare
DATABASE_URL = os.getenv("DATABASE_URL")
MODEL = "gpt-4o-mini"  # ارزون‌تر و سریع‌تر برای تحلیل تکنیکال
CACHE_MINUTES_TECH = 15  # کش ۱۵ دقیقه‌ای برای تحلیل تکنیکال

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)

def init_tech_cache_table():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS technical_analysis_cache (
            id SERIAL PRIMARY KEY,
            symbol TEXT UNIQUE NOT NULL,
            analysis_text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            expires_at TIMESTAMP NOT NULL
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

def get_cached_tech_analysis(symbol: str) -> str | None:
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT analysis_text FROM technical_analysis_cache 
            WHERE symbol = %s AND expires_at > NOW()
        """, (symbol.upper(),))
        rec = cur.fetchone()
        cur.close()
        conn.close()
        return rec["analysis_text"] if rec else None
    except Exception as e:
        print(f"خطا در خواندن کش تکنیکال: {e}")
        return None

def save_tech_analysis_to_cache(symbol: str, analysis: str):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        expires_at = datetime.now() + timedelta(minutes=CACHE_MINUTES_TECH)
        cur.execute("""
            INSERT INTO technical_analysis_cache (symbol, analysis_text, expires_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (symbol) DO UPDATE SET
                analysis_text = EXCLUDED.analysis_text,
                expires_at = EXCLUDED.expires_at,
                created_at = NOW()
        """, (symbol.upper(), analysis, expires_at))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"خطا در ذخیره کش تکنیکال: {e}")

def get_taapi_data(symbol: str):
    if not TAAPI_SECRET:
        return None, "کلید TAAPI.IO تنظیم نشده است."

    symbol = symbol.upper()

    # فقط کوین‌های رایگان
    if symbol not in ["BTC", "ETH", "XRP", "LTC", "XMR"]:
        return None, f"در پلن رایگان فقط BTC، ETH، XRP، LTC و XMR پشتیبانی می‌شن.\n{symbol} فعلاً در دسترس نیست."

    construct = {
        "exchange": "binance",
        "symbol": f"{symbol}/USDT",
        "interval": "1h",
        "indicators": [
            {"id": "rsi", "indicator": "rsi", "period": 14},
            {"id": "macd", "indicator": "macd"},
            {"id": "ema50", "indicator": "ema", "period": 50},
            {"id": "ema200", "indicator": "ema", "period": 200},
            {"id": "bbands", "indicator": "bbands2", "period": 20},
            {"id": "stoch", "indicator": "stoch"},
            {"id": "adx", "indicator": "adx"},
            {"id": "atr", "indicator": "atr"},
            {"id": "volume", "indicator": "volume"}
        ]
    }

    url = "https://api.taapi.io/bulk"
    payload = {
        "secret": TAAPI_SECRET,
        "construct": construct
    }

    try:
        resp = requests.post(url, json=payload, timeout=20)
        resp.raise_for_status()
        raw = resp.json()

        # بررسی خطا
        if "error" in raw:
            return None, f"خطا در TAAPI: {raw['error']}"

        data = raw.get("data", [])

        # حالا فقط و فقط حالت لیست (رفتار جدید TAAPI از ۲۰۲۵ به بعد)
        if not isinstance(data, list):
            return None, "خروجی TAAPI نامعتبر بود (باید لیست باشه!)."

        results = {}

        for item in data:
            key_id = item.get("id")
            result = item.get("result")

            if key_id is None or result is None:
                continue

            if isinstance(result, dict):
                if "value" in result:  # RSI, EMA, ADX و ...
                    results[key_id] = result["value"]
                else:
                    # MACD, BBands, Stoch و ...
                    for subkey, subval in result.items():
                        results[f"{key_id}_{subkey}".lower()] = subval
            else:
                results[key_id] = result

        # مقداردهی پیش‌فرض برای پرامپت (کلیدها رو دقیقاً مثل پرامپت تنظیم کردیم)
        results.setdefault("rsi", None)
        results.setdefault("macd_macd", None)
        results.setdefault("macd_signal", None)
        results.setdefault("ema50", None)
        results.setdefault("ema200", None)
        results.setdefault("bbands_middle", None)
        results.setdefault("bbands_upper", None)
        results.setdefault("bbands_lower", None)
        results.setdefault("stoch_k", None)
        results.setdefault("adx", None)
        results.setdefault("atr", None)
        results.setdefault("volume", None)

        return results, None

    except requests.exceptions.HTTPError as e:
        try:
            err = e.response.json()
            msg = err.get("error") or str(err)
        except:
            msg = e.response.text
        return None, f"خطا در TAAPI: {e.response.status_code} - {msg}"
    except Exception as e:
        return None, f"خطا در ارتباط با TAAPI: {str(e)}"
        
def generate_technical_analysis(symbol: str, ta_data: dict) -> str:
    if not OPENAI_API_KEY:
        return "کلید ChatGPT تنظیم نشده است."

    prompt = f"""
    تو یک تحلیلگر تکنیکال حرفه‌ای هستی. فقط تحلیل تکنیکال بده و هیچ پیشنهاد خرید/فروش نکن.
    زبان: فارسی روان و ساده برای همه مردم.
    تایم‌فریم: ۱ ساعته

    داده‌ها:
    نماد: {symbol}/USDT
    RSI(14): {ta_data.get('rsi', 'نامشخص')}
    MACD: {ta_data.get('macd_macd', 'نامشخص')} | سیگنال: {ta_data.get('macd_signal', 'نامشخص')}
    EMA50: {ta_data.get('ema50', 'نامشخص')}
    EMA200: {ta_data.get('ema200', 'نامشخص')}
    باند بولینگر میانی: {ta_data.get('bbands_middle', 'نامشخص')}
    باند بولینگر بالا: {ta_data.get('bbands_upper', 'نامشخص')}
    باند بولینگر پایین: {ta_data.get('bbands_lower', 'نامشخص')}
    Stochastic %K: {ta_data.get('stoch_k', 'نامشخص')}
    ADX: {ta_data.get('adx', 'نامشخص')}
    ATR: {ta_data.get('atr', 'نامشخص')}
    حجم ۲۴ ساعته: {ta_data.get('volume', 'نامشخص')}

    قوانین خیلی مهم:
    - هیچ کلمه‌ای را بولد نکن، از ** یا * یا # یا markdown استفاده نکن.
    - عنوان‌ها را کاملاً ساده و بدون هیچ علامتی بنویس (مثلاً: تحلیل تکنیکال BTC/USDT)
    - قبل از هر عبارت انگلیسی که شرپع کننده جمله هست لطفا معادل فارسی آن را بنویس 
    - برای این کار از این کاراکتر استفاده کن: ‌ (این کاراکتر نامرئی است و بین حروف فارسی و انگلیسی می‌ذاری تا جهت متن خراب نشود)

    قالب خروجی دقیقاً این باشد:

    تحلیل تکنیکال {symbol}/USDT (تایم‌فریم ۱ ساعته)

    وضعیت فعلی قیمت
    قیمت الان نسبت به EMA50 و EMA۲۰۰ کجاست؟ (بالا، پایین، نزدیک، تلاقی و ...)

    سیگنال‌های مهم
    • RSI در چه محدوده‌ایه؟ اشباع خرید یا فروش داره؟
    • MACD چه سیگنالی می‌ده؟ تقاطع صعودی یا نزولی؟ هیستوگرام مثبت یا منفی؟
    • باندهای بولینگر فشرده شدن یا قیمت از باند خارج شده؟

    قدرت روند و نوسان
    • ADX نشون‌دهنده روند قوی یا ضعیفه؟
    • ATR می‌گه نوسان بازار چقدره؟ کم یا زیاد؟

    جمع‌بندی
    روند کوتاه‌مدت فعلاً صعودی / نزولی / رنج به نظر می‌رسه.
    سطوح مهم حمایت و مقاومت نزدیک: از باند بولینگر و EMAها حدس بزن.
    نکته مهم برای تریدرها: ...

    فقط متن ساده بده، ایموجی هم اگه خواستی کم استفاده کن که متن تمیز بمونه.
    """

    try:
        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.6,
            "max_tokens": 900
        }
        resp = requests.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers, timeout=40)
        resp.raise_for_status()
        
        analysis = resp.json()["choices"][0]["message"]["content"].strip()
        
        # حذف کامل هر نوع markdown احتمالی (حتی اگه GPT یادش رفته باشه!)
        cleanup_map = str.maketrans({
            '*': '', '**': '', '_': '', '#': '', '`': '', '[': '', ']': '', '(': '', ')': '',
            '<': '', '>': '', '~': ''
        })
        analysis = analysis.translate(cleanup_map)
        
        # حذف خطوط خالی اضافی
        lines = [line.rstrip() for line in analysis.split('\n') if line.strip()]
        analysis = '\n'.join(lines)
        
        return analysis
    except Exception as e:
        return f"خطا در تولید تحلیل تکنیکال: {str(e)}"

def get_technical_analysis(symbol: str) -> str:
    symbol = symbol.upper()

    # اول کش
    cached = get_cached_tech_analysis(symbol)
    if cached:
        return f"تحلیل تکنیکال {symbol}/USDT (از کش - بروز هر ۱۵ دقیقه)\n\n{cached}"

    # اگر کش نبود → داده از TAAPI
    ta_data, error = get_taapi_data(symbol)
    if error:
        return error

    if not ta_data:
        return "داده تکنیکال دریافت نشد. شاید ارز پشتیبانی نمیشه (فقط BTC, ETH, XRP, LTC, XMR در پلن رایگان)."

    # تولید تحلیل با GPT
    analysis = generate_technical_analysis(symbol, ta_data)

    # ذخیره در کش
    if len(analysis) > 100 and "خطا" not in analysis:
        save_tech_analysis_to_cache(symbol, analysis)

    return f"تحلیل تکنیکال {symbol}/USDT (تازه)\n\n{analysis}"
