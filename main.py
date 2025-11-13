# main.py
import os
import requests
from datetime import datetime, timedelta, date
from telegram import (
    Update, ReplyKeyboardMarkup, InlineKeyboardMarkup,
    InlineKeyboardButton, Bot, BotCommand
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio
import telegram.error
import psycopg2
from psycopg2.extras import DictCursor

# -------------------------
# تنظیمات محیطی و متغیرها
# -------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
TRON_ADDRESS = os.getenv("TRON_ADDRESS")
INFO_CHANNEL = os.getenv("INFO_CHANNEL")  # chat id مثل -1001234567890
REPORT_CHANNEL = os.getenv("REPORT_CHANNEL")
CMC_API_KEY_1 = os.getenv("CMC_API_KEY_1")
CMC_API_KEY_2 = os.getenv("CMC_API_KEY_2")
CMC_API_KEY_3 = os.getenv("CMC_API_KEY_3")
ADMIN_IDS = os.getenv("ADMIN_IDS")  # مثال: "12345678,87654321"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN تنظیم نشده است.")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL تنظیم نشده است.")
if not INFO_CHANNEL:
    print("هشدار: INFO_CHANNEL تنظیم نشده است. پیام‌های پرداخت به کانال ارسال نخواهند شد.")
if not TRON_ADDRESS:
    print("هشدار: TRON_ADDRESS تنظیم نشده است. پیام پرداخت آدرس را نمایش نخواهد داد.")

# لیست کلیدهای CMC
api_keys = [k.strip() for k in (CMC_API_KEY_1, CMC_API_KEY_2, CMC_API_KEY_3) if k]
current_key_index = 0
current_api_key = api_keys[current_key_index] if api_keys else None

# تبدیل ADMIN_IDS به لیست عددی
if ADMIN_IDS:
    try:
        ADMIN_ID_LIST = [int(x.strip()) for x in ADMIN_IDS.split(",") if x.strip()]
    except Exception:
        ADMIN_ID_LIST = []
        print("فرمت ADMIN_IDS اشتباه است. باید مانند: 12345678,87654321 باشد.")
else:
    ADMIN_ID_LIST = []

# -------------------------
# دیتابیس
# -------------------------
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            last_free_use DATE,
            subscription_expiry TIMESTAMP,
            registered_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            tx_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', -- pending, approved, rejected
            note TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            processed_at TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("✅ دیتابیس آماده و جداول ایجاد شدند.")

# -------------------------
# عملیات اشتراک
# -------------------------
def register_user_if_not_exists(telegram_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE telegram_id = %s", (telegram_id,))
    if not cur.fetchone():
        cur.execute("INSERT INTO users (telegram_id) VALUES (%s)", (telegram_id,))
        conn.commit()
    cur.close()
    conn.close()

def activate_user_subscription(telegram_id: int, days: int = 30):
    """اشتراک را فعال یا تمدید می‌کند و last_free_use را پاک می‌کند."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT subscription_expiry FROM users WHERE telegram_id = %s", (telegram_id,))
    rec = cur.fetchone()
    now = datetime.now()
    if rec and rec["subscription_expiry"] and rec["subscription_expiry"] > now:
        new_expiry = rec["subscription_expiry"] + timedelta(days=days)
    else:
        new_expiry = now + timedelta(days=days)
    cur.execute(
        "UPDATE users SET subscription_expiry = %s, last_free_use = NULL WHERE telegram_id = %s",
        (new_expiry, telegram_id)
    )
    conn.commit()
    cur.close()
    conn.close()
    return new_expiry

def check_subscription_status(telegram_id: int):
    """
    برمی‌گرداند (is_subscribed: bool, days_remaining: int)
    ادمین‌ها همیشه True برگردانده می‌شوند.
    """
    if telegram_id in ADMIN_ID_LIST:
        # ادمین‌ها همیشه اشتراک دارند — مقدار روزها را یک عدد بزرگ برمی‌گردانیم
        return True, 3650

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT subscription_expiry FROM users WHERE telegram_id = %s", (telegram_id,))
    rec = cur.fetchone()
    cur.close()
    conn.close()
    if not rec or not rec["subscription_expiry"]:
        return False, 0
    expiry = rec["subscription_expiry"]
    now = datetime.now()
    if expiry > now:
        return True, (expiry - now).days
    return False, 0

def has_free_use_today(telegram_id: int) -> bool:
    # ادمین‌ها محدودیتی ندارند
    if telegram_id in ADMIN_ID_LIST:
        return False
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT last_free_use FROM users WHERE telegram_id = %s", (telegram_id,))
    rec = cur.fetchone()
    cur.close()
    conn.close()
    if rec and rec["last_free_use"]:
        return rec["last_free_use"] == date.today()
    return False

def record_free_use(telegram_id: int):
    if telegram_id in ADMIN_ID_LIST:
        return
    conn = get_db_connection()
    cur = conn.cursor()
    today = date.today()
    cur.execute("UPDATE users SET last_free_use = %s WHERE telegram_id = %s", (today, telegram_id))
    conn.commit()
    cur.close()
    conn.close()

# -------------------------
# کمکی‌های نمایش
# -------------------------
def safe_number(value, fmt="{:,.2f}"):
    return fmt.format(value) if value is not None else "نامشخص"

# -------------------------
# مدیریت کلیدهای CMC
# -------------------------
async def check_and_select_api_key(bot: Bot):
    global current_api_key, current_key_index
    if not api_keys:
        if REPORT_CHANNEL:
            try:
                await bot.send_message(chat_id=REPORT_CHANNEL, text="⚠️ هیچ کلید CMC تنظیم نشده است.", parse_mode="HTML")
            except telegram.error.TelegramError:
                pass
        return False
    url = "https://pro-api.coinmarketcap.com/v1/key/info"
    for idx, key in enumerate(api_keys):
        headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": key}
        try:
            resp = requests.get(url, headers=headers, timeout=8)
            resp.raise_for_status()
            data = resp.json()
            usage = data.get("data", {}).get("usage", {}).get("current_month", {})
            plan = data.get("data", {}).get("plan", {})
            credits_used = usage.get("credits_used", 0)
            credits_total = plan.get("credit_limit", 10000)
            credits_left = credits_total - credits_used
            if credits_left > 0:
                current_api_key = key
                current_key_index = idx
                if REPORT_CHANNEL:
                    try:
                        await bot.send_message(chat_id=REPORT_CHANNEL, text=f"✅ کلید CMC انتخاب شد: #{idx+1} — باقی: {credits_left:,}")
                    except telegram.error.TelegramError:
                        pass
                return True
        except Exception as e:
            print(f"Error checking CMC key #{idx+1}: {e}")
            continue
    if REPORT_CHANNEL:
        try:
            await bot.send_message(chat_id=REPORT_CHANNEL, text="⚠️ هیچ کلید CMC با کردیت پیدا نشد.", parse_mode="HTML")
        except telegram.error.TelegramError:
            pass
    return False

# -------------------------
# هندلرها
# -------------------------
async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand("start", "شروع ربات"),
        BotCommand("check", "بررسی وضعیت اشتراک"),
        BotCommand("verify", "ارسال هش تراکنش: /verify <tx_hash>"),
    ]
    await bot.set_my_commands(commands)

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    register_user_if_not_exists(user_id)
    subscribed, days_left = check_subscription_status(user_id)

    # منوی کلیدی
    keyboard = [
        ["📊 وضعیت کلی بازار", "📈 اطلاعات ارز"],
        ["📜 اطلاعات تکمیلی", "💎 اشتراک و پرداخت"]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    if user_id in ADMIN_ID_LIST:
        msg = (
            "🔑 خوش آمدی ادمین!\n"
            "تمام قابلیت‌ها برای شما فعال است.\n\n"
            "از منوی زیر استفاده کن:"
        )
        await update.message.reply_text(msg, reply_markup=markup)
        return

    if subscribed:
        await update.message.reply_text(f"✅ اشتراک شما فعال است.\n⏰ حدوداً {days_left} روز تا پایان اشتراک باقی مانده.", reply_markup=markup)
    else:
        tron_msg = TRON_ADDRESS or "آدرس پرداخت تعریف نشده است. لطفاً با ادمین تماس بگیرید."
        msg = (
            "👋 سلام!\n\n"
            "برای فعال‌سازی اشتراک ماهیانه (۵ ترون) لطفاً مبلغ را به آدرس زیر واریز کنید:\n\n"
            f"<code>{tron_msg}</code>\n\n"
            "پس از واریز، هش تراکنش را با فرمت زیر ارسال کنید:\n"
            "<code>/verify TX_HASH</code>\n\n"
            "🔔 تا زمانی که اشتراک فعال نشود می‌توانید روزانه یک بار از بخش «اطلاعات ارز» استفاده کنید. اطلاعات تکمیلی و سایر فیچرها نیاز به اشتراک دارند."
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=markup)

# /check
async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    subscribed, days_left = check_subscription_status(user_id)
    if subscribed:
        await update.message.reply_text(f"🟢 اشتراک فعال است.\n⏰ حدوداً {days_left} روز باقی مانده.")
    else:
        await update.message.reply_text("❌ شما اشتراک فعال ندارید. برای فعال‌سازی /start را بزنید و راهنمای پرداخت را دنبال کنید.")

# /verify <tx_hash>
async def verify_tx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    if not args:
        await update.message.reply_text("❌ لطفاً هش تراکنش را به صورت: /verify <tx_hash> ارسال کنید.")
        return
    tx_hash = args[0].strip()
    # ثبت payment
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO payments (telegram_id, tx_hash, status) VALUES (%s, %s, %s) RETURNING id, created_at", (user_id, tx_hash, 'pending'))
    rec = cur.fetchone()
    conn.commit()
    payment_id = rec["id"]
    created_at = rec["created_at"]
    cur.close()
    conn.close()

    await update.message.reply_text(f"✅ هش تراکنش ثبت شد. شناسه پرداخت: #{payment_id}\nپس از بررسی، نتیجه برای شما پیام داده خواهد شد.")

    # ارسال پیام به INFO_CHANNEL با دکمه‌های تایید/رد (فقط ادمین‌ها میتونن کلیک کنن)
    if INFO_CHANNEL:
        try:
            txt = (
                f"🟨 تراکنش جدید ثبت شد (منتظر بررسی):\n\n"
                f"👤 کاربر: <code>{user_id}</code>\n"
                f"🔗 هش: <code>{tx_hash}</code>\n"
                f"🆔 payment_id: <code>{payment_id}</code>\n"
                f"زمان: {created_at}\n\n"
                "برای تایید یا رد، از دکمه‌های زیر استفاده کنید."
            )
            keyboard = [
                [
                    InlineKeyboardButton("✅ تأیید پرداخت", callback_data=f"admin_pay_approve:{payment_id}"),
                    InlineKeyboardButton("❌ رد پرداخت", callback_data=f"admin_pay_reject:{payment_id}")
                ]
            ]
            await context.bot.send_message(chat_id=INFO_CHANNEL, text=txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        except telegram.error.TelegramError as e:
            print(f"Error sending payment notification to INFO_CHANNEL: {e}")

# Callback برای دکمه‌های تایید/رد در کانال
async def admin_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_who_clicked = query.from_user.id

    # فقط ادمین‌ها مجازند
    if user_who_clicked not in ADMIN_ID_LIST:
        await query.message.reply_text("❌ شما دسترسی ادمین ندارید.")
        return

    data = query.data  # e.g. "admin_pay_approve:45" or "admin_pay_reject:45"
    try:
        action, pid_str = data.split(":")
        payment_id = int(pid_str)
    except Exception:
        await query.edit_message_text("⚠️ فرمت داده نامعتبر.")
        return

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, telegram_id, tx_hash, status FROM payments WHERE id = %s", (payment_id,))
    rec = cur.fetchone()
    if not rec:
        cur.close()
        conn.close()
        await query.edit_message_text(f"⚠️ پرداخت با شناسه #{payment_id} پیدا نشد.")
        return

    if rec["status"] != "pending":
        cur.close()
        conn.close()
        await query.edit_message_text(f"⚠️ این پرداخت قبلاً پردازش شده است (وضعیت: {rec['status']}).")
        return

    payer_id = rec["telegram_id"]
    tx_hash = rec["tx_hash"]

    now = datetime.now()
    if action == "admin_pay_approve":
        # تأیید پرداخت -> فعال‌سازی اشتراک
        new_expiry = activate_user_subscription(payer_id, days=30)
        cur.execute("UPDATE payments SET status = %s, processed_at = %s, note = %s WHERE id = %s",
                    ('approved', now, f"Approved by {user_who_clicked}", payment_id))
        conn.commit()
        cur.close()
        conn.close()

        # ویرایش پیام کانال (غیرفعال‌سازی دکمه‌ها)
        try:
            await query.edit_message_text(f"✅ پرداخت #{payment_id} تأیید شد.\nکاربر: <code>{payer_id}</code>\nتمدید تا: {new_expiry}", parse_mode="HTML")
        except Exception:
            pass

        # اطلاع به کاربر
        try:
            await context.bot.send_message(chat_id=payer_id,
                                           text=f"✅ پرداخت شما مورد تایید قرار گرفت و اشتراک تا {new_expiry.strftime('%Y-%m-%d %H:%M')} فعال شد.")
        except telegram.error.TelegramError:
            print(f"Couldn't send message to user {payer_id} after approve.")
        return

    elif action == "admin_pay_reject":
        # رد پرداخت
        cur.execute("UPDATE payments SET status = %s, processed_at = %s, note = %s WHERE id = %s",
                    ('rejected', now, f"Rejected by {user_who_clicked}", payment_id))
        conn.commit()
        cur.close()
        conn.close()
        try:
            await query.edit_message_text(f"❌ پرداخت #{payment_id} رد شد.\nکاربر: <code>{payer_id}</code>", parse_mode="HTML")
        except Exception:
            pass
        # اطلاع به کاربر
        try:
            await context.bot.send_message(chat_id=payer_id,
                                           text=f"❌ پرداخت شما (payment #{payment_id}) معتبر تشخیص داده نشد و اشتراک فعال نشد. در صورت مشکل با ادمین تماس بگیرید.")
        except telegram.error.TelegramError:
            print(f"Couldn't send message to user {payer_id} after reject.")
        return
    else:
        cur.close()
        conn.close()
        await query.edit_message_text("⚠️ عملیات نامشخص.")
        return

# هندل پیام‌های عمومی (جستجوی ارز و منو)
async def crypto_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_api_key
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # دسته‌بندی منوی دستوری
    if text == "📊 وضعیت کلی بازار":
        await show_global_market(update)
        return
    if text == "💎 اشتراک و پرداخت":
        # نمایش آدرس و راهنمای پرداخت
        tron_msg = TRON_ADDRESS or "آدرس پرداخت تعریف نشده است."
        await update.message.reply_text(
            f"برای فعالسازی اشتراک ماهیانه (۵ ترون)، لطفاً مبلغ را به آدرس زیر واریز کنید:\n\n<code>{tron_msg}</code>\n\n"
            "سپس هش تراکنش را با /verify <TX_HASH> ارسال کنید.",
            parse_mode="HTML"
        )
        return
    if text == "📜 اطلاعات تکمیلی":
        # اگر مشترک نیست -> پیام بده که باید اشتراک بخره
        subscribed, _ = check_subscription_status(user_id)
        if not subscribed:
            await update.message.reply_text("لطفاً اشتراک تهیه کنید تا به این بخش دسترسی داشته باشید.")
            return
        else:
            await update.message.reply_text("برای مشاهده جزئیات یک ارز، نام یا نماد آن را بفرستید.")
            return
    # اگر کاربر خواستار اطلاعات ارز (نماد) است:
    # محدودیت یک بار در روز برای کاربران غیر ادمین و غیر مشترک
    subscribed, _ = check_subscription_status(user_id)
    if not subscribed and has_free_use_today(user_id):
        await update.message.reply_text("⚠️ شما امروز از سهمیه رایگان خود استفاده کرده‌اید. برای دسترسی بیشتر لطفاً اشتراک تهیه کنید.")
        return
    # اگر هنوز از سهمیه استفاده نکرده، ثبت استفاده
    if not subscribed:
        record_free_use(user_id)

    # درخواست به CMC
    if not current_api_key:
        await update.message.reply_text("⚠️ هیچ کلید CoinMarketCap معتبر در دسترس نیست.")
        return

    query = text.strip().lower()
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": current_api_key}
    params = {"symbol": query.upper(), "convert": "USD"}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if "data" not in data or query.upper() not in data["data"]:
            await update.message.reply_text("❌ ارز مورد نظر پیدا نشد. لطفاً نام یا نماد دقیق وارد کنید.")
            return
        result = data["data"][query.upper()]
        name = result["name"]
        symbol = result["symbol"]
        price = result["quote"]["USD"]["price"]
        change_1h = result["quote"]["USD"]["percent_change_1h"]
        change_24h = result["quote"]["USD"]["percent_change_24h"]
        change_7d = result["quote"]["USD"]["percent_change_7d"]
        market_cap = result["quote"]["USD"]["market_cap"]
        volume_24h = result["quote"]["USD"]["volume_24h"]
        num_pairs = result.get("num_market_pairs")
        rank = result.get("cmc_rank")

        msg = f"""🔍 <b>اطلاعات ارز</b>:
🏷️ <b>نام</b>: {name}
💱 <b>نماد</b>: {symbol}
💵 <b>قیمت</b>: ${safe_number(price)}
⏱️ <b>تغییر ۱ ساعته</b>: {safe_number(change_1h, "{:.2f}")}%
📊 <b>تغییر ۲۴ ساعته</b>: {safe_number(change_24h, "{:.2f}")}%
📅 <b>تغییر ۷ روزه</b>: {safe_number(change_7d, "{:.2f}")}%
📈 <b>حجم ۲۴ساعته</b>: ${safe_number(volume_24h, "{:,.0f}")}
💰 <b>ارزش کل بازار</b>: ${safe_number(market_cap, "{:,.0f}")}
🛒 <b>تعداد بازارها</b>: {num_pairs}
🏅 <b>رتبه بازار</b>: #{rank}
"""
        # برای کاربران مشترک دکمه اطلاعات تکمیلی نمایش داده می‌شود
        subscribed, _ = check_subscription_status(user_id)
        keyboard = []
        if subscribed:
            keyboard = [[InlineKeyboardButton("📜 نمایش اطلاعات تکمیلی", callback_data=f"details_{symbol}")]]
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        print(f"Error fetching coin data: {e}")
        await update.message.reply_text("⚠️ خطا در دریافت اطلاعات ارز. لطفاً دوباره تلاش کنید.")

# نمایش اطلاعات تکمیلی (دکمه inline)
async def handle_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    subscribed, _ = check_subscription_status(user_id)
    if not subscribed:
        await query.message.reply_text("لطفاً اشتراک تهیه کنید تا به این بخش دسترسی داشته باشید.")
        return
    # ادامه مشابه قبل: درخواست به CMC برای اطلاعات تکمیلی
    symbol = query.data[len("details_"):]
    if not current_api_key:
        await query.message.reply_text("⚠️ هیچ کلید CoinMarketCap معتبر در دسترس نیست.")
        return
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/info"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": current_api_key}
    params = {"symbol": symbol}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if "data" not in data or symbol.upper() not in data["data"]:
            await query.message.reply_text("❌ اطلاعات تکمیلی پیدا نشد.")
            return
        coin = data["data"][symbol.upper()]
        desc = coin.get("description") or "ناموجود"
        whitepaper = coin.get("urls", {}).get("technical_doc", ["ناموجود"])[0]
        website = coin.get("urls", {}).get("website", ["ناموجود"])[0]
        logo = coin.get("logo", "ناموجود")
        msg = f"📜 <b>اطلاعات تکمیلی {coin.get('name','')}</b>\n\n💬 {desc[:1000]}...\n\n📄 وایت‌پیپر: {whitepaper}\n🌐 وب‌سایت: {website}\n🖼 لوگو: {logo}"
        keyboard = [[InlineKeyboardButton("❌ بستن", callback_data=f"close_details_{symbol}")]]
        await query.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        print(f"Error fetching details: {e}")
        await query.message.reply_text("⚠️ خطا در دریافت اطلاعات تکمیلی.")

async def handle_close_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass

# گزارش مصرف API (optional)
async def send_usage_report_to_channel(bot: Bot):
    global current_api_key, current_key_index
    if not REPORT_CHANNEL or not current_api_key:
        return
    url = "https://pro-api.coinmarketcap.com/v1/key/info"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": current_api_key}
    try:
        resp = requests.get(url, headers=headers, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("data", {}).get("usage", {}).get("current_month", {})
        plan = data.get("data", {}).get("plan", {})
        credits_used = usage.get("credits_used", 0)
        credits_total = plan.get("credit_limit", 10000)
        credits_left = credits_total - credits_used
        plan_name = plan.get("name", "Free")
        msg = f"📊 وضعیت مصرف API:\nپلن: {plan_name}\nکل: {credits_total:,}\nمصرف: {credits_used:,}\nباقی: {credits_left:,}\nزمان: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        await bot.send_message(chat_id=REPORT_CHANNEL, text=msg)
    except Exception as e:
        print(f"Error sending usage report: {e}")

# Main
async def main():
    try:
        print("Initializing bot...")
        init_db()
        app = ApplicationBuilder().token(BOT_TOKEN).build()

        # Command handlers
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("check", check_subscription))
        app.add_handler(CommandHandler("verify", verify_tx))

        # Admin callback handler for approve/reject in INFO_CHANNEL
        app.add_handler(CallbackQueryHandler(admin_payment_callback, pattern=r"^admin_pay_"))

        # Handlers for details and close
        app.add_handler(CallbackQueryHandler(handle_details, pattern=r"^details_"))
        app.add_handler(CallbackQueryHandler(handle_close_details, pattern=r"^close_details_"))

        # Message handler (main)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, crypto_info))

        await set_bot_commands(app.bot)
        await check_and_select_api_key(app.bot)

        print("Bot started.")
        await app.initialize()
        await app.start()

        # start polling with small conflict handling
        retry = 0
        while retry < 3:
            try:
                await app.updater.start_polling()
                break
            except telegram.error.Conflict:
                retry += 1
                await asyncio.sleep(3)
                if retry >= 3:
                    raise

        # scheduler (optional reports)
        scheduler = AsyncIOScheduler()
        scheduler.add_job(send_usage_report_to_channel, "interval", minutes=5, args=[app.bot])
        scheduler.start()

        await asyncio.Event().wait()
    except Exception as e:
        print(f"Error in main: {e}")
        raise
    finally:
        try:
            await app.stop()
            await app.shutdown()
        except Exception:
            pass

if __name__ == "__main__":
    asyncio.run(main())
