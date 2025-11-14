# deep_analysis.py
import os
import json
import requests
import psycopg2
from psycopg2.extras import DictCursor
from datetime import datetime, timedelta

# تنظیمات
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # یا هر API دیگه
DATABASE_URL = os.getenv("DATABASE_URL")
MODEL = "gpt-4o-mini"  # یا gpt-4o, claude, gemini
CACHE_DAYS = 7  # چند روز کش بشه؟

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)

def init_cache_table():
    """ایجاد جدول کش تحلیل عمیق"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS deep_analysis_cache (
            id SERIAL PRIMARY KEY,
            symbol TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            analysis_text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            expires_at TIMESTAMP NOT NULL
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("جدول کش تحلیل عمیق آماده است.")

def get_cached_analysis(symbol: str) -> str | None:
    """بررسی کش: اگر معتبر بود، متن رو برگردون"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT analysis_text FROM deep_analysis_cache 
            WHERE symbol = %s AND expires_at > NOW()
        """, (symbol.upper(),))
        rec = cur.fetchone()
        cur.close()
        conn.close()
        return rec["analysis_text"] if rec else None
    except Exception as e:
        print(f"خطا در خواندن کش: {e}")
        return None

def save_analysis_to_cache(symbol: str, name: str, analysis: str):
    """ذخیره تحلیل در دیتابیس با انقضا"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        expires_at = datetime.now() + timedelta(days=CACHE_DAYS)
        cur.execute("""
            INSERT INTO deep_analysis_cache (symbol, name, analysis_text, expires_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (symbol) DO UPDATE SET
                name = EXCLUDED.name,
                analysis_text = EXCLUDED.analysis_text,
                expires_at = EXCLUDED.expires_at,
                created_at = NOW()
        """, (symbol.upper(), name, analysis, expires_at))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"خطا در ذخیره کش: {e}")

def call_openai_analysis(coin_data: dict) -> str:
    """فراخوانی ChatGPT فقط وقتی کش نیست"""
    if not OPENAI_API_KEY:
        return "API کلید ChatGPT تنظیم نشده است."

    symbol = coin_data["symbol"]
    name = coin_data["name"]
    price = coin_data.get("price", 0)
    market_cap = coin_data.get("market_cap", 0)
    volume_24h = coin_data.get("volume_24h", 0)
    description = (coin_data.get("description") or "")[:3000]
    website = coin_data.get("website", "ندارد")
    whitepaper = coin_data.get("whitepaper", "ندارد")
    contracts = coin_data.get("contracts", [])

    contract_str = ", ".join([f"{c['network']}: {c['address']}" for c in contracts]) if contracts else "ندارد"

    prompt = f"""
    تو یه تحلیل‌گر حرفه‌ای کریپتو هستی. یه تحلیل **فاندامنتال + آنچین + توکونومیک** کامل، **به فارسی، صمیمی و انسانی** بنویس.

    اطلاعات:
    - نام: {name}
    - نماد: {symbol}
    - قیمت: ${price:,.2f}
    - مارکت کپ: ${market_cap:,.0f}
    - حجم ۲۴ ساعته: ${volume_24h:,.0f}
    - توضیحات: {description}
    - وبسایت: {website}
    - وایت‌پیپر: {whitepaper}
    - قراردادها: {contract_str}

    بخش‌ها:
    1. معرفی پروژه و کاربرد
    2. تیم و اعتبار
    3. توکونومیک (عرضه، توزیع، کاربرد)
    4. آنچین (حجم واقعی، هولدرها)
    5. مزیت رقابتی
    6. ریسک‌ها
    7. جمع‌بندی و چشم‌انداز

    لحن: دوستانه، مثل یه مشاور کریپتو
    طول: ۴۰۰–۶۰۰ کلمه
    ایموجی‌های مناسب
    """

    try:
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 1200
        }
        resp = requests.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers, timeout=40)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"خطا در فراخوانی OpenAI: {e}")
        return f"موقتی در دسترس نیست. بعداً امتحان کن."

def get_deep_analysis(coin_data: dict) -> str:
    """
    اصلی: اول کش → اگر نبود API → ذخیره در کش
    """
    symbol = coin_data["symbol"]

    # ۱. کش رو چک کن
    cached = get_cached_analysis(symbol)
    if cached:
        return f"تحلیل عمیق {coin_data['name']} (از حافظه):\n\n{cached}"

    # ۲. اگر کش نبود، API رو بزن
    print(f"تحلیل جدید برای {symbol} — فراخوانی API...")
    analysis = call_openai_analysis(coin_data)

    # ۳. ذخیره در کش (حتی اگر خطا داد، ذخیره نشه)
    if "خطا" not in analysis and "تنظیم نشده" not in analysis and len(analysis) > 100:
        save_analysis_to_cache(symbol, coin_data["name"], analysis)
        return f"تحلیل عمیق {coin_data['name']} (تازه):\n\n{analysis}"
    else:
        return analysis  # خطا
