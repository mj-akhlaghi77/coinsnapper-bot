# main.py
import os
import requests
import jdatetime
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
# تنظیمات محیطی
# -------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
TRON_ADDRESS = os.getenv("TRON_ADDRESS")
INFO_CHANNEL = os.getenv("INFO_CHANNEL")  # Chat ID مثل -1001234567890
REPORT_CHANNEL = os.getenv("REPORT_CHANNEL")
CMC_API_KEY_1 = os.getenv("CMC_API_KEY_1")
CMC_API_KEY_2 = os.getenv("CMC_API_KEY_2")
CMC_API_KEY_3 = os.getenv("CMC_API_KEY_3")
ADMIN_IDS = os.getenv("ADMIN_IDS")  # رشته: "123,456"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN تنظیم نشده است.")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL تنظیم نشده است.")

# لیست کلیدهای CMC
api_keys = [k.strip() for k in (CMC_API_KEY_1, CMC_API_KEY_2, CMC_API_KEY_3) if k and k.strip()]
current_key_index = 0
current_api_key = api_keys[current_key_index] if api_keys else None

# تبدیل ADMIN_IDS به لیست اعداد (محکم‌کاری)
ADMIN_ID_LIST = []
if ADMIN_IDS:
    try:
        for part in ADMIN_IDS.split(","):
            s = part.strip()
            if s:
                ADMIN_ID_LIST.append(int(s))
    except Exception:
        print("فرمت ADMIN_IDS اشتباه است. مثال صحیح: 12345678,87654321")
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
            status TEXT NOT NULL DEFAULT 'pending',
            note TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            processed_at TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("✅ دیتابیس آماده شد (users, payments).")

# -------------------------
# تاریخ شمسی - فرمت: ۱۴۰۴/۱۱/۲۳ ساعت ۱۴:۳۰
# -------------------------
def to_shamsi(dt: datetime) -> str:
    try:
        jdt = jdatetime.datetime.fromgregorian(datetime=dt)
        return jdt.strftime("%Y/%-m/%-d ساعت %H:%M")
    except Exception:
        # fallback
        return dt.strftime("%Y-%m-%d %H:%M")

# -------------------------
# عملیات اشتراک و کاربر
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
    """اشتراک را فعال یا تمدید می‌کند. last_free_use پاک می‌شود."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT subscription_expiry FROM users WHERE telegram_id = %s", (telegram_id,))
    rec = cur.fetchone()
    now = datetime.now()
    if rec and rec["subscription_expiry"] and rec["subscription_expiry"] > now:
        new_expiry = rec["subscription_expiry"] + timedelta(days=days)
    else:
        new_expiry = now + timedelta(days=days)
    cur.execute("UPDATE users SET subscription_expiry = %s, last_free_use = NULL WHERE telegram_id = %s",
                (new_expiry, telegram_id))
    conn.commit()
    cur.close()
    conn.close()
    return new_expiry

def check_subscription_status(telegram_id: int):
    """برمی‌گرداند (is_subscribed: bool, days_remaining: int). ادمین‌ها همیشه True هستند."""
    if telegram_id in ADMIN_ID_LIST:
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
# نمایش و قالب‌بندی
# -------------------------
def safe_number(value, fmt="{:,.2f}"):
    return fmt.format(value) if value is not None else "نامشخص"

# -------------------------
# مدیریت کلیدهای CMC (مثل قبل ولی با timeout)
# -------------------------
async def check_and_select_api_key(bot: Bot):
    global current_api_key, current_key_index
    if not api_keys:
        if REPORT_CHANNEL:
            try:
                await bot.send_message(chat_id=REPORT_CHANNEL, text="⚠️ هیچ کلید CoinMarketCap تنظیم نشده.", parse_mode="HTML")
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
                        await bot.send_message(chat_id=REPORT_CHANNEL,
                                               text=f"✅ کلید CMC انتخاب شد: #{idx+1} — باقی: {credits_left:,}")
                    except telegram.error.TelegramError:
                        pass
                return True
        except Exception as e:
            print(f"Error checking CMC key #{idx+1}: {e}")
            continue
    if REPORT_CHANNEL:
        try:
            await bot.send_message(chat_id=REPORT_CHANNEL, text="⚠️ هیچ کلید CMC با کردیت باقی نمانده.", parse_mode="HTML")
        except telegram.error.TelegramError:
            pass
    return False

# -------------------------
# هندلرها و پیام‌ها (دوستانه)
# -------------------------
async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand("start", "شروع ربات"),
        BotCommand("check", "بررسی اشتراک"),
        BotCommand("verify", "ثبت هش پرداخت: /verify <tx_hash>"),
    ]
    await bot.set_my_commands(commands)

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    register_user_if_not_exists(user_id)
    subscribed, days_left = check_subscription_status(user_id)

    keyboard = [
        ["📊 وضعیت کلی بازار", "📈 اطلاعات ارز"],
        ["📜 اطلاعات تکمیلی", "💎 اشتراک و پرداخت"]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    if user_id in ADMIN_ID_LIST:
        await update.message.reply_text(
            "🔑 سلام ادمین! همه‌چی برای تو بازه — هر وقت خواستی بزن شروع کنیم 😎",
            reply_markup=markup
        )
        return

    if subscribed:
        await update.message.reply_text(
            f"🎉 اشتراک فعاله! حدوداً {days_left} روز تا پایانش مونده. هر چی خواستی بپرس 😉",
            reply_markup=markup
        )
    else:
        tron_msg = TRON_ADDRESS or "هنوز آدرس پرداخت تنظیم نشده. با ادمین تماس بگیر."
        await update.message.reply_text(
            "سلام رفیق 👋\n"
            "برای استفاده از همه‌قابلیت‌ها باید اشتراک ماهیانه (۵ ترون) داشته باشی.\n\n"
            f"مبلغ رو به این آدرس بزن:\n<code>{tron_msg}</code>\n\n"
            "بعد از واریز، هش تراکنش رو با این دستور بفرست:\n<code>/verify TX_HASH</code>\n\n"
            "تا وقتی اشتراک فعال نشه، می‌تونی روزی یک بار اطلاعات یک ارز رو ببینی.",
            parse_mode="HTML",
            reply_markup=markup
        )

    # گزارش به کانال INFO_CHANNEL (اطلاع از استارت)
    if INFO_CHANNEL:
        try:
            await context.bot.send_message(chat_id=INFO_CHANNEL,
                                           text=f"🔔 کاربر <code>{user_id}</code> ربات رو استارت زد.\nاشتراک: {'✅' if subscribed else '❌'}\nزمان: {to_shamsi(datetime.now())}",
                                           parse_mode="HTML")
        except telegram.error.TelegramError:
            pass

# /check
async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    subscribed, days_left = check_subscription_status(user_id)
    if subscribed:
        await update.message.reply_text(f"🟢 اشتراک فعاله — حدوداً {days_left} روز باقیه. لذت ببر! 🎉")
    else:
        await update.message.reply_text("⚠️ فعلاً اشتراک نداری. برای خرید /start رو بزنی راهنمایی می‌کنم.")

# /verify <tx_hash>
async def verify_tx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    if not args:
        await update.message.reply_text("❌ لطفاً هش رو به شکل: /verify <TX_HASH> ارسال کن.")
        return
    tx_hash = args[0].strip()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO payments (telegram_id, tx_hash, status) VALUES (%s, %s, %s) RETURNING id, created_at",
                (user_id, tx_hash, 'pending'))
    rec = cur.fetchone()
    conn.commit()
    payment_id = rec["id"]
    created_at = rec["created_at"]
    cur.close()
    conn.close()

    await update.message.reply_text(f"✅ هش ثبت شد (شناسه #{payment_id}). منتظر بررسی ادمین بمون — زود جواب می‌دم 🙂")

    # ارسال پیام به کانال INFO_CHANNEL با دکمه‌های تایید/رد
    if INFO_CHANNEL:
        try:
            txt = (
                f"🟨 تراکنش جدید ثبت شد\n\n"
                f"👤 کاربر: <code>{user_id}</code>\n"
                f"🔗 هش: <code>{tx_hash}</code>\n"
                f"🆔 payment_id: <code>{payment_id}</code>\n"
                f"زمان: {to_shamsi(created_at)}\n\n"
                "🛠 از دکمه‌ها برای تایید یا رد استفاده کنید."
            )
            keyboard = [
                [
                    InlineKeyboardButton("✅ تأیید پرداخت", callback_data=f"admin_pay_approve:{payment_id}"),
                    InlineKeyboardButton("❌ رد پرداخت", callback_data=f"admin_pay_reject:{payment_id}")
                ]
            ]
            await context.bot.send_message(chat_id=INFO_CHANNEL, text=txt, parse_mode="HTML",
                                           reply_markup=InlineKeyboardMarkup(keyboard))
        except telegram.error.TelegramError as e:
            print(f"Error sending to INFO_CHANNEL: {e}")

# Callback برای دکمه‌های تایید/رد در کانال
async def admin_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    clicker = query.from_user.id

    # فقط ادمین‌ها مجازند
    if clicker not in ADMIN_ID_LIST:
        try:
            await query.message.reply_text("❌ شما دسترسی ادمین نداری.")
        except Exception:
            pass
        return

    data = query.data  # مثل "admin_pay_approve:45"
    if ":" not in data:
        await query.edit_message_text("⚠️ داده نامعتبر.")
        return
    action, pid_str = data.split(":", 1)
    try:
        payment_id = int(pid_str)
    except ValueError:
        await query.edit_message_text("⚠️ payment_id نامعتبر.")
        return

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, telegram_id, tx_hash, status, created_at FROM payments WHERE id = %s", (payment_id,))
    rec = cur.fetchone()
    if not rec:
        cur.close()
        conn.close()
        await query.edit_message_text(f"⚠️ پرداخت #{payment_id} پیدا نشد.")
        return

    if rec["status"] != "pending":
        cur.close()
        conn.close()
        await query.edit_message_text(f"⚠️ این پرداخت قبلاً پردازش شده (وضعیت: {rec['status']}).")
        return

    payer = rec["telegram_id"]
    tx_hash = rec["tx_hash"]
    now = datetime.now()

    if action == "admin_pay_approve":
        # تأیید -> فعال‌سازی اشتراک و اطلاع به کاربر
        new_expiry = activate_user_subscription(payer, days=30)
        cur.execute("UPDATE payments SET status=%s, processed_at=%s, note=%s WHERE id=%s",
                    ('approved', now, f"Approved by {clicker}", payment_id))
        conn.commit()
        cur.close()
        conn.close()

        # ویرایش پیام کانال (غیرفعال کردن دکمه‌ها)
        try:
            await query.edit_message_text(f"✅ پرداخت #{payment_id} تأیید شد.\nکاربر: <code>{payer}</code>\nتمدید تا: {to_shamsi(new_expiry)}",
                                          parse_mode="HTML")
        except Exception:
            pass

        # اطلاع به کاربر
        try:
            await context.bot.send_message(chat_id=payer,
                                           text=f"🎉 تبریک! پرداختت تایید شد و اشتراک تا {to_shamsi(new_expiry)} فعال شد. از ربات لذت ببر 😉")
        except telegram.error.TelegramError:
            print(f"Couldn't notify user {payer} after approve.")
        return

    elif action == "admin_pay_reject":
        # رد پرداخت
        cur.execute("UPDATE payments SET status=%s, processed_at=%s, note=%s WHERE id=%s",
                    ('rejected', now, f"Rejected by {clicker}", payment_id))
        conn.commit()
        cur.close()
        conn.close()

        try:
            await query.edit_message_text(f"❌ پرداخت #{payment_id} رد شد.\nکاربر: <code>{payer}</code>", parse_mode="HTML")
        except Exception:
            pass

        try:
            await context.bot.send_message(chat_id=payer,
                                           text=f"❌ متاسفم؛ پرداخت (#{payment_id}) معتبر نبود و اشتراک فعال نشد. اگر فکر می‌کنی اشتباه شده با ادمین تماس بگیر 🙏")
        except telegram.error.TelegramError:
            print(f"Couldn't notify user {payer} after reject.")
        return
    else:
        cur.close()
        conn.close()
        await query.edit_message_text("⚠️ عملیات نامشخص.")
        return

# -------------------------
# نمایش وضعیت کلی بازار (عملیاتی)
# -------------------------
async def show_global_market(update: Update):
    global current_api_key
    if not current_api_key:
        await update.message.reply_text("⚠️ هنوز کلید CoinMarketCap فعال نشده.")
        return
    url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": current_api_key}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        total_market_cap = data.get("quote", {}).get("USD", {}).get("total_market_cap")
        total_volume_24h = data.get("quote", {}).get("USD", {}).get("total_volume_24h")
        btc_dominance = data.get("btc_dominance")
        active_cryptocurrencies = data.get("active_cryptocurrencies")
        last_updated = data.get("last_updated")
        last_txt = to_shamsi(datetime.fromisoformat(last_updated)) if last_updated else to_shamsi(datetime.now())

        msg = (
            f"🌐 وضعیت کلی بازار:\n\n"
            f"💰 ارزش کل بازار: ${safe_number(total_market_cap, '{:,.0f}')}\n"
            f"📊 حجم ۲۴ساعته: ${safe_number(total_volume_24h, '{:,.0f}')}\n"
            f"🟠 دامیننس بیت‌کوین: {safe_number(btc_dominance, '{:.2f}')}%\n"
            f"🔢 تعداد ارزها: {active_cryptocurrencies}\n"
            f"🕒 آخرین بروزرسانی: {last_txt}"
        )
        await update.message.reply_text(msg)
    except Exception as e:
        print(f"Error show_global_market: {e}")
        await update.message.reply_text("⚠️ خطا در دریافت وضعیت کلی بازار. لطفاً بعداً تلاش کن.")

# گزارش مصرف API (ارسال به REPORT_CHANNEL)
async def send_usage_report_to_channel(bot: Bot):
    global current_api_key, current_key_index
    if not REPORT_CHANNEL or not current_api_key:
        return
    url = "https://pro-api.coinmarketcap.com/v1/key/info"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": current_api_key}
    try:
        resp = requests.get(url, headers=headers, timeout=8)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        usage = data.get("usage", {}).get("current_month", {})
        plan = data.get("plan", {})
        credits_used = usage.get("credits_used", 0)
        credits_total = plan.get("credit_limit", 10000)
        credits_left = credits_total - credits_used
        plan_name = plan.get("name", "Free")
        msg = (
            f"📊 وضعیت مصرف CMC:\n"
            f"پلن: {plan_name}\n"
            f"کل: {credits_total:,}\n"
            f"مصرف‌شده: {credits_used:,}\n"
            f"باقی: {credits_left:,}\n"
            f"کلید فعال: #{current_key_index+1}\n"
            f"زمان: {to_shamsi(datetime.now())}"
        )
        await bot.send_message(chat_id=REPORT_CHANNEL, text=msg)
    except Exception as e:
        print(f"Error send_usage_report: {e}")

# -------------------------
# هندلر پیام‌ها (منوی اصلی و جستجوی ارز)
# -------------------------
async def crypto_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_api_key
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # منوها
    if text == "📊 وضعیت کلی بازار":
        await show_global_market(update)
        return
    if text == "💎 اشتراک و پرداخت":
        tron_msg = TRON_ADDRESS or "آدرس پرداخت هنوز تنظیم نشده."
        await update.message.reply_text(
            f"برای اشتراک ماهیانه (۵ ترون)، مبلغ رو به آدرس زیر بزن:\n\n<code>{tron_msg}</code>\n\n"
            "بعد از واریز هش تراکنش رو با /verify ارسال کن.",
            parse_mode="HTML"
        )
        return
    if text == "📜 اطلاعات تکمیلی":
        subscribed, _ = check_subscription_status(user_id)
        if not subscribed:
            await update.message.reply_text("لطفاً اشتراک تهیه کن تا به این بخش دسترسی داشته باشی 💎")
            return
        else:
            await update.message.reply_text("اسم یا نماد ارز رو بفرست تا جزئیاتشو بیارم.")
            return

    # اگر پیام به‌عنوان نماد ارز است:
    subscribed, _ = check_subscription_status(user_id)
    if not subscribed and has_free_use_today(user_id):
        await update.message.reply_text("⚠️ امروز از سهمیه رایگانت استفاده کردی. برای بیشتر شدن دسترسی اشتراک بگیر 😊")
        return
    if not subscribed:
        record_free_use(user_id)

    if not current_api_key:
        await update.message.reply_text("⚠️ کلید CoinMarketCap فعال نیست. لطفاً بعداً تلاش کن.")
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
            await update.message.reply_text("❌ ارز پیدا نشد — نماد رو دقیق وارد کن.")
            return
        result = data["data"][query.upper()]
        name = result.get("name")
        symbol = result.get("symbol")
        price = result["quote"]["USD"]["price"]
        change_1h = result["quote"]["USD"]["percent_change_1h"]
        change_24h = result["quote"]["USD"]["percent_change_24h"]
        change_7d = result["quote"]["USD"]["percent_change_7d"]
        market_cap = result["quote"]["USD"]["market_cap"]
        volume_24h = result["quote"]["USD"]["volume_24h"]
        num_pairs = result.get("num_market_pairs")
        rank = result.get("cmc_rank")

        msg = (
            f"🔍 اطلاعات {name} ({symbol}):\n\n"
            f"💵 قیمت: ${safe_number(price)}\n"
            f"⏱ تغییر ۱ ساعته: {safe_number(change_1h, '{:.2f}')}%\n"
            f"📊 تغییر ۲۴ ساعته: {safe_number(change_24h, '{:.2f}')}%\n"
            f"📅 تغییر ۷ روزه: {safe_number(change_7d, '{:.2f}')}%\n"
            f"📈 حجم ۲۴ساعته: ${safe_number(volume_24h, '{:,.0f}')}\n"
            f"💰 مارکت کپ: ${safe_number(market_cap, '{:,.0f}')}\n"
            f"🛒 بازارها: {num_pairs}\n"
            f"🏅 رتبه: #{rank}"
        )
        keyboard = []
        if subscribed:
            keyboard = [[InlineKeyboardButton("📜 نمایش اطلاعات تکمیلی", callback_data=f"details_{symbol}")]]
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        print(f"Error fetching coin: {e}")
        await update.message.reply_text("⚠️ خطایی پیش اومد. دوباره امتحان کن.")

# اطلاعات تکمیلی (دکمه)
async def handle_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    subscribed, _ = check_subscription_status(user_id)
    if not subscribed:
        await query.message.reply_text("لطفاً اشتراک تهیه کن تا بتونی این بخش رو ببینی 💎")
        return
    symbol = query.data[len("details_"):]
    if not current_api_key:
        await query.message.reply_text("⚠️ کلید CoinMarketCap فعال نیست.")
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
        desc = coin.get("description") or "نداره"
        whitepaper = coin.get("urls", {}).get("technical_doc", ["ندارد"])[0]
        website = coin.get("urls", {}).get("website", ["ندارد"])[0]
        logo = coin.get("logo", "ندارد")
        msg = f"📜 اطلاعات تکمیلی {coin.get('name','')}\n\n{desc[:1000]}...\n\n📄 وایت‌پیپر: {whitepaper}\n🌐 وب: {website}"
        keyboard = [[InlineKeyboardButton("❌ بستن", callback_data=f"close_details_{symbol}")]]
        await query.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        print(f"Error details: {e}")
        await query.message.reply_text("⚠️ خطا در دریافت اطلاعات تکمیلی.")

async def handle_close_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass

# -------------------------
# main
# -------------------------
async def main():
    try:
        print("راه‌اندازی ربات...")
        init_db()
        app = ApplicationBuilder().token(BOT_TOKEN).build()

        # handlers
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("check", check_subscription))
        app.add_handler(CommandHandler("verify", verify_tx))

        app.add_handler(CallbackQueryHandler(admin_payment_callback, pattern=r"^admin_pay_"))
        app.add_handler(CallbackQueryHandler(handle_details, pattern=r"^details_"))
        app.add_handler(CallbackQueryHandler(handle_close_details, pattern=r"^close_details_"))

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, crypto_info))

        await set_bot_commands(app.bot)
        await check_and_select_api_key(app.bot)

        # start
        await app.initialize()
        await app.start()

        # polling with conflict handling
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

        # scheduler: گزارش مصرف
        scheduler = AsyncIOScheduler()
        scheduler.add_job(send_usage_report_to_channel, "interval", minutes=5, args=[app.bot])
        scheduler.start()

        print("ربات اجرا شد 🎉")
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
