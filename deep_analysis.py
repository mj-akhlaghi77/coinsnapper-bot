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
MODEL = "gpt-4o"  # یا gpt-4o, claude, gemini
CACHE_DAYS = 1  # چند روز کش بشه؟

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
    change_1h = coin_data.get("change_1h", 0)
    change_24h = coin_data.get("change_24h", 0)
    circulating_supply = coin_data.get("circulating_supply", 0)
    total_supply = coin_data.get("total_supply", 0)
    max_supply = coin_data.get("max_supply", 0)
    rank = coin_data.get("rank", 0)
    description = (coin_data.get("description") or "")[:3000]
    website = coin_data.get("website", "ندارد")
    whitepaper = coin_data.get("whitepaper", "ندارد")
    contracts = coin_data.get("contracts", [])

    contract_str = ", ".join([f"{c['network']}: {c['address']}" for c in contracts]) if contracts else "ندارد"

   prompt = f""
   «تو یک تحلیلگر حرفه‌ای بازار کریپتو هستی. هر زمان یک ارز دیجیتال از تو خواسته شد، با توجه به اطلاعات که در انتها بهت داده میشه لطفاً بر اساس ساختار زیر یک معرفی کامل، روان، ساده و قابل فهم در حد 350 تا 450 کلمه ارائه بده. همچنین  قبل از استفاده از نام نماد از کلمه رمزارز استفاده کن متن باید کاملاً منظم و بخش‌بندی‌شده باشد و از لحن نیمه رسمی استفاده شود تا برای عموم و عوام جذاب باشد همچنین انتهای جملات با فعل تمام شوند و هیچ‌گونه پیشنهاد خرید یا فروش نداشته باشد.
۱. معرفی کوتاه
در این بخش توضیح بده: نام پروژه و توکن، سال تأسیس، فرد یا تیم سازنده، هدف پروژه، مشکلی که حل می‌کند و اینکه در چه دسته‌بندی قرار می‌گیرد. یک تصویر کلی و سریع از پروژه ارائه بده.
۲. فاندامنتال
در این بخش فقط موارد اصلی و ضروری را توضیح بده: تعداد کل توکن‌ها، میزان عرضه در گردش، محدود یا نامحدود بودن عرضه، کاربردهای اصلی توکن مثل پرداخت، کارمزد شبکه، حاکمیت یا استیکینگ. توضیح‌ها باید واضح، روان و کوتاه باشند.
۳. مزایا و معایب و ریسک‌ها
نقاط قوت پروژه را بیان کن، سپس نقاط ضعف و ریسک‌های اصلی را توضیح بده. اگر لازم بود به چند رقیب مهم اشاره کن تا دید بهتری داده شود.
۴. نقشه راه
به دستاوردهای مهم گذشته و برنامه‌های اصلی آینده اشاره کن و بگو پروژه تا امروز چقدر به وعده‌هایش عمل کرده است.
۵. جمع‌بندی
یک جمع‌بندی کاملاً بی‌طرفانه ارائه بده که بر اساس اطلاعات بالا یک دید کلی و سریع از وضعیت فعلی پروژه بدهد. هیچ نتیجه‌گیری سرمایه‌گذاری نکن.»
    اطلاعات:
    - نام: {name}
    - نماد: {symbol}
    - قیمت: ${price:,.2f}
    - مارکت کپ: ${market_cap:,.0f}
    - حجم ۲۴ ساعته: ${volume_24h:,.0f}
    - تغییر ۱ ساعته: {change_1h:,.0f}
    - تغییر ۲۴ ساعته: {change_24h:,.0f}
    - عرضه درگردش: {circulating_supply:,.0f}
    - عرضه کل بازار: {total_supply:,.0f}
    - عرضه نهایی: {max_supply:,.0f}
    - رتبه بازار: {rank:,.0f}
    - توضیحات: {description}
    - وبسایت: {website}
    - وایت‌پیپر: {whitepaper}
    - قراردادها: {contract_str}

    ""

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
