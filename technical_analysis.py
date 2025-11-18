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

        data = raw.get("data")

        # مهم: گاهی data لیست هست، گاهی دیکشنری!
        results = {}

        if isinstance(data, list):
            # حالت لیست (جدیدترین رفتار TAAPI)
            for item in data:
                if "id" in item and "result" in item:
                    result = item["result"]
                    key_id = item["id"]
                    if isinstance(result, dict):
                        if "value" in result:
                            results[key_id] = result["value"]
                        else:
                            # MACD, BBands و ...
                            for subkey, subval in result.items():
                                results[f"{key_id}_{subkey}"] = subval
                    else:
                        results[key_id] = result
        elif isinstance(data, dict):
            # حالت قدیمی دیکشنری
            for key_id, result in data.items():
                if isinstance(result, dict):
                    if "value" in result:
                        results[key_id] = result["value"]
                    else:
                        for subkey, subval in result.items():
                            results[f"{key_id}_{subkey}"] = subval
                else:
                    results[key_id] = result
        else:
            return None, "خروجی TAAPI نامعتبر بود."

        # مقداردهی پیش‌فرض برای پرامپت
        results.setdefault("rsi", "نامشخص")
        results.setdefault("macd", "نامشخص")
        results.setdefault("macd_signal", "نامشخص")
        results.setdefault("ema50", "نامشخص")
        results.setdefault("ema200", "نامشخص")
        results.setdefault("bbands_middle", "نامشخص")
        results.setdefault("bbands_upper", "نامشخص")
        results.setdefault("bbands_lower", "نامشخص")

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
    تو یک تحلیلگر تکنیکال حرفه‌ای هستی. فقط و فقط تحلیل تکنیکال بده.
    با زبان فارسی، روان، ساده و جذاب برای عموم مردم.
    از داده‌های زیر یک تحلیل کامل تکنیکال بده (تایم‌فریم ۱ ساعته):

    نماد: {symbol}/USDT
    RSI(14): {ta_data.get('rsi', 'نامشخص')}
    MACD: {ta_data.get('macd', 'نامشخص')} (سیگنال: {ta_data.get('macd_signal', 'نامشخص')})
    EMA50: {ta_data.get('ema_50', 'نامشخص')}
    EMA200: {ta_data.get('ema_200', 'نامشخص')}
    باند بولینگر (میانی): {ta_data.get('bbands2_middle', 'نامشخص')}
    قیمت فعلی: نزدیک به این باندها
    Stochastic %K: {ta_data.get('stoch_k', 'نامشخص')}
    ADX: {ta_data.get('adx', 'نامشخص')}
    ATR: {ta_data.get('atr', 'نامشخص')}
    حجم ۲۴ ساعته: {ta_data.get('volume', 'نامشخص')}

    تحلیل رو اینطوری بساز:
    **تحلیل تکنیکال {symbol}/USDT (تایم‌فریم ۱ ساعته)**

    **وضعیت فعلی قیمت**
    - توضیح بده قیمت کجاست نسبت به EMA50 و EMA200 (بالا/پایین/تلاقی)

    **سیگنال‌های مهم**
    - RSI: اشباع خرید/فروش؟ روند خنثی؟
    - MACD: تقاطع صعودی/نزولی؟ هیستوگرام مثبت/منفی؟
    - بولینگر: فشردگی؟ خروج از باند؟

    **قدرت روند و نوسان**
    - ADX: روند قوی داره یا ضعیف؟
    - ATR: نوسان چقدره؟

    **جمع‌بندی تکنیکال**
    - روند کوتاه‌مدت: صعودی / نزولی / رنج
    - سطوح مهم: مقاومت/حمایت (از بولینگر یا EMAها حدس بزن)
    - نکته مهم برای تریدرها

    فقط تحلیل بده. هیچ پیشنهاد خرید/فروش نکن.
    از ایموجی استفاده کن تا جذاب بشه: 🚀📉🔥🧊 و ...
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
        return resp.json()["choices"][0]["message"]["content"].strip()
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
