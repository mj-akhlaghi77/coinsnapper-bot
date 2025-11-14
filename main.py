# main.py
# نسخهٔ نهایی: مینیمال، دوستانه، گزارش CMC ساعتی با تاریخ شمسی،
# دکمهٔ وضعیت کلی بازار فقط برای مشترکین، دکمهٔ اشتراک/بررسی اشتراک،
# نمایش اطلاعات تکمیلی برای مشترکین و نمایش کانترکت‌ها (درصورت وجود).

import os
import requests
import jdatetime
from datetime import datetime, timedelta, date
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, Bot, BotCommand
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
INFO_CHANNEL = os.getenv("INFO_CHANNEL")      # مثال: -100123...
REPORT_CHANNEL = os.getenv("REPORT_CHANNEL")  # مثال: -100123...
CMC_API_KEY_1 = os.getenv("CMC_API_KEY_1")
CMC_API_KEY_2 = os.getenv("CMC_API_KEY_2")
CMC_API_KEY_3 = os.getenv("CMC_API_KEY_3")

# پشتیبانی از هر دو نام: ADMIN_IDS یا ADMIN_USER_ID
ADMIN_IDS = os.getenv("ADMIN_IDS") or os.getenv("ADMIN_USER_ID")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN تنظیم نشده است.")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL تنظیم نشده است.")

# لیست کلیدهای CMC
api_keys = [k.strip() for k in (CMC_API_KEY_1, CMC_API_KEY_2, CMC_API_KEY_3) if k and k.strip()]
current_key_index = None
current_api_key = None
if api_keys:
    current_key_index = 0
    current_api_key = api_keys[0]

# تبدیل ADMIN_IDS به لیست اعداد
ADMIN_ID_LIST = []
if ADMIN_IDS:
    try:
        for part in ADMIN_IDS.split(","):
            s = part.strip().replace('"', "").replace("'", "")
            if s:
                ADMIN_ID_LIST.append(int(s))
    except Exception:
        print("⚠️ فرمت ADMIN_IDS اشتباه است. مثال صحیح: 12345678,87654321")
        ADMIN_ID_LIST = []

print("✅ لیست ادمین‌ها:", ADMIN_ID_LIST)

# -------------------------
# دیتابیس
# -------------------------
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # جدول users: افزودن flagged notified_3day برای اطلاع رسانی تمدید
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            last_free_use DATE,
            subscription_expiry TIMESTAMP,
            notified_3day BOOLEAN DEFAULT FALSE,
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
    print("✅ دیتابیس و جداول آماده‌اند.")

# -------------------------
# تاریخ شمسی - فرمت: ۱۴۰۴/۱۱/۲۳ ساعت ۱۴:۳۰
# -------------------------
def to_shamsi(dt: datetime) -> str:
    try:
        jdt = jdatetime.datetime.fromgregorian(datetime=dt)
        # برای حذف صفر پیش‌رو در ماه/روز از %-m %-d استفاده شده است (لینوکس)
        # اگر سیستم مشکل داشت، fallback به فرمت ساده استفاده می‌کنیم
        return jdt.strftime("%Y/%-m/%-d ساعت %H:%M")
    except Exception:
        try:
            jdt = jdatetime.datetime.fromgregorian(datetime=dt)
            return jdt.strftime("%Y/%m/%d ساعت %H:%M")
        except Exception:
            return dt.strftime("%Y-%m-%d %H:%M")

# -------------------------
# مدیریت اشتراک و کاربر
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
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT subscription_expiry FROM users WHERE telegram_id = %s", (telegram_id,))
    rec = cur.fetchone()
    now = datetime.now()
    if rec and rec["subscription_expiry"] and rec["subscription_expiry"] > now:
        new_expiry = rec["subscription_expiry"] + timedelta(days=days)
    else:
        new_expiry = now + timedelta(days=days)
    # وقتی فعال می‌کنیم، نشان اطلاع 3 روز را ریست می‌کنیم (تا برای اشتراک جدید اطلاع ارسال شود)
    cur.execute("UPDATE users SET subscription_expiry = %s, notified_3day = FALSE WHERE telegram_id = %s", (new_expiry, telegram_id))
    conn.commit()
    cur.close()
    conn.close()
    return new_expiry

def check_subscription_status(telegram_id: int):
    """برمی‌گرداند (is_subscribed: bool, days_remaining: int).
    ادمین‌ها همیشه True برگردانده می‌شوند."""
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

# -------------------------
# نمایش و قالب‌بندی
# -------------------------
def safe_number(value, fmt="{:,.2f}"):
    return fmt.format(value) if value is not None else "نامشخص"

# -------------------------
# مدیریت کلیدهای CMC با قابلیت سوییچ و ارسال هشدار هنگام سوییچ
# -------------------------
async def check_and_select_api_key(bot: Bot):
    global current_api_key, current_key_index
    if not api_keys:
        if REPORT_CHANNEL:
            try:
                await bot.send_message(chat_id=REPORT_CHANNEL, text="⚠️ هیچ کلید CoinMarketCap تنظیم نشده.", parse_mode="HTML")
            except telegram.error.TelegramError:
                pass
        current_api_key = None
        current_key_index = None
        return False

    url = "https://pro-api.coinmarketcap.com/v1/key/info"
    prev_index = current_key_index
    selected = False
    total_checked = 0
    for idx, key in enumerate(api_keys):
        total_checked += 1
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
                selected = True
                break
        except Exception as e:
            print(f"Error checking CMC key #{idx+1}: {e}")
            continue

    # اگر سوییچ شد و prev_index متفاوت بود، هشدار بده
    if prev_index is not None and selected and prev_index != current_key_index and REPORT_CHANNEL:
        try:
            await bot.send_message(chat_id=REPORT_CHANNEL,
                                   text=f"⚠️ کلید CMC تغییر کرد!\n🔑 از کلید #{prev_index+1} به #{current_key_index+1} سوئیچ شد.\n🕒 {to_shamsi(datetime.now())}")
        except telegram.error.TelegramError:
            pass

    return selected

# -------------------------
# گزارش مصرف تمام کلیدها و گزارش کلی (ارسال به REPORT_CHANNEL)
# -------------------------
async def send_usage_report_to_channel(bot: Bot):
    """دو پیام ارسال می‌کند:
       1) وضعیت کلید فعال (با قالب مورد نظر)
       2) گزارش کلی همهٔ کلیدها
       این تابع به صورت scheduled هر 1 ساعت اجرا می‌شود.
    """
    global current_api_key, current_key_index
    if not REPORT_CHANNEL:
        return

    url = "https://pro-api.coinmarketcap.com/v1/key/info"

    total_credits_used = 0
    total_credits_left = 0
    active_keys = 0

    per_key_msgs = []

    for idx, key in enumerate(api_keys):
        headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": key}
        try:
            resp = requests.get(url, headers=headers, timeout=8)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            usage = data.get("usage", {}).get("current_month", {})
            plan = data.get("plan", {})
            credits_used = usage.get("credits_used", 0)
            credits_total = plan.get("credit_limit", 10000)
            plan_name = plan.get("name", "Free")
            credits_left = credits_total - credits_used
            total_credits_used += credits_used
            total_credits_left += credits_left
            if credits_left > 0:
                active_keys += 1
            per_key_msgs.append((idx, plan_name, credits_total, credits_used, credits_left))
        except Exception as e:
            print(f"Error checking key #{idx+1} for usage report: {e}")
            per_key_msgs.append((idx, "Error", 0, 0, 0))
            continue

    # پیام وضعیت کلید فعال (اگر موجود)
    if current_api_key is not None and current_key_index is not None:
        # پیدا کردن جزئیات کلید فعال از per_key_msgs
        detail = None
        for item in per_key_msgs:
            if item[0] == current_key_index:
                detail = item
                break
        if detail:
            plan_name = detail[1]
            credits_total = detail[2]
            credits_used = detail[3]
            credits_left = detail[4]
        else:
            plan_name = "نامشخص"
            credits_total = 0
            credits_used = 0
            credits_left = 0

        msg_active = f"""📊 <b>وضعیت مصرف API کوین‌مارکت‌کپ</b>:
🔹 پلن: {plan_name}
🔸 اعتبارات ماهانه: {credits_total:,}
✅ مصرف‌شده: {credits_used:,}
🟢 باقی‌مانده: {credits_left:,}
🔑 کلید API فعال: شماره {current_key_index + 1} ({current_api_key[-6:]})
🕒 آخرین بروزرسانی: {to_shamsi(datetime.now())}
"""
        try:
            await bot.send_message(chat_id=REPORT_CHANNEL, text=msg_active, parse_mode="HTML")
        except telegram.error.TelegramError:
            pass

    # پیام گزارش کلی
    msg_summary = f"""📋 <b>گزارش کلی API کوین‌مارکت‌کپ</b>:
🔢 تعداد کل کلیدهای API: {len(api_keys)}
🔑 تعداد کلیدهای فعال (با کردیت): {active_keys}
✅ کل کردیت‌های مصرف‌شده: {total_credits_used:,}
🟢 کل کردیت‌های باقی‌مانده: {total_credits_left:,}
🕒 آخرین بروزرسانی: {to_shamsi(datetime.now())}
"""
    try:
        await bot.send_message(chat_id=REPORT_CHANNEL, text=msg_summary, parse_mode="HTML")
    except telegram.error.TelegramError:
        pass

# -------------------------
# هندلرها و پیام‌های دوستانه
# -------------------------
async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand("start", "شروع ربات"),
        BotCommand("check", "بررسی اشتراک"),
        BotCommand("verify", "ثبت هش پرداخت: /verify <tx_hash>"),
    ]
    await bot.set_my_commands(commands)

# /start (مینیمال، دستورالعمل ارسال نماد)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    register_user_if_not_exists(user_id)
    subscribed, days_left = check_subscription_status(user_id)

    # پیام دوستانه مینیمال
    msg = "سلام! 👋\nاسم یا نماد یه ارز رو بفرست (مثلاً BTC یا بیت‌کوین) تا اطلاعاتشو برات بیارم."
    # دکمه‌ها: وضعیت کلی بازار فقط برای مشترکین/ادمین‌ها؛ و دکمهٔ اشتراک یا بررسی اشتراک
    buttons = []
    if subscribed:
        # دکمه وضعیت کلی بازار
        buttons.append([InlineKeyboardButton("📊 وضعیت کلی بازار", callback_data="global_market")])
        buttons.append([InlineKeyboardButton("🔍 بررسی اشتراک", callback_data="check_subscription")])
    else:
        # فقط دکمه اشتراک و پرداخت
        buttons.append([InlineKeyboardButton("💎 اشتراک و پرداخت", callback_data="subscribe")])

    try:
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        await update.message.reply_text(msg)

    # گزارش به کانال INFO_CHANNEL
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
        await update.message.reply_text(f"🟢 اشتراک فعاله — حدوداً {days_left} روز باقیه. ❤️")
    else:
        await update.message.reply_text("❌ اشتراک فعالی نداری. برای اطلاعات پرداخت /start رو بزن یا از دکمهٔ اشتراک استفاده کن.")

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
    now = datetime.now()

    if action == "admin_pay_approve":
        new_expiry = activate_user_subscription(payer, days=30)
        cur.execute("UPDATE payments SET status=%s, processed_at=%s, note=%s WHERE id=%s",
                    ('approved', now, f"Approved by {clicker}", payment_id))
        conn.commit()
        cur.close()
        conn.close()

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

# نمایش وضعیت کلی بازار (برای مشترکین)
async def show_global_market_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # این تابع هم برای callback دکمه inline و هم برای دستور استفاده می‌شود
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
    else:
        # fallback
        user_id = update.effective_user.id

    subscribed, _ = check_subscription_status(user_id)
    if not subscribed:
        # اگر کاربر اشتراک ندارد، پیام کوتاه بده
        try:
            if query:
                await query.message.reply_text("لطفاً اشتراک تهیه کن تا وضعیت کلی بازار رو ببینی.")
            else:
                await update.message.reply_text("لطفاً اشتراک تهیه کن تا وضعیت کلی بازار رو ببینی.")
        except Exception:
            pass
        return

    # اگر مشترک است، اطلاعات کلی بازار را بفرست
    global current_api_key
    if not current_api_key:
        try:
            if query:
                await query.message.reply_text("⚠️ کلید CoinMarketCap فعال نیست. لطفاً بعداً تلاش کن.")
            else:
                await update.message.reply_text("⚠️ کلید CoinMarketCap فعال نیست. لطفاً بعداً تلاش کن.")
        except Exception:
            pass
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
        if query:
            await query.message.reply_text(msg)
        else:
            await update.message.reply_text(msg)
    except Exception as e:
        print(f"Error show_global_market: {e}")
        if query:
            await query.message.reply_text("⚠️ خطا در دریافت وضعیت کلی بازار. لطفاً بعداً تلاش کن.")
        else:
            await update.message.reply_text("⚠️ خطا در دریافت وضعیت کلی بازار. لطفاً بعداً تلاش کن.")

# اطلاعات تکمیلی (callback)
async def handle_details_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    subscribed, _ = check_subscription_status(user_id)
    symbol = query.data[len("details_"):]
    if not subscribed:
        await query.message.reply_text("😅 برای دیدن اطلاعات تکمیلی باید اشتراک داشته باشی. برای خرید /start رو بزن یا از دکمهٔ اشتراک استفاده کن.")
        return

    # درخواست اطلاعات تکمیلی از CMC
    if not current_api_key:
        await query.message.reply_text("⚠️ کلید CoinMarketCap فعلاً فعال نیست.")
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

        # استخراج فیلدهای مهم از پاسخ: description, technical_doc, website, logo
        desc = coin.get("description") or "ندارد"
        whitepaper = coin.get("urls", {}).get("technical_doc", ["ندارد"])[0]
        website = coin.get("urls", {}).get("website", ["ندارد"])[0]
        logo = coin.get("logo", "ندارد")

        # استخراج کانترکت‌ها (اگر موجود باشد)
        # CoinMarketCap ممکن است اطلاعات قرارداد را در چند فیلد داشته باشد (contracts, platform, urls.explorer)
        contracts_info = []
        # 1) مستقیم contracts
        if coin.get("contracts"):
            try:
                for c in coin.get("contracts"):
                    addr = c.get("contract_address") or c.get("address") or None
                    network = c.get("platform") or c.get("name") or None
                    if addr:
                        contracts_info.append(f"{network or 'network'}: {addr}")
            except Exception:
                pass
        # 2) برخی پاسخ‌ها ممکن است platform داشته باشند
        if coin.get("platform"):
            try:
                platform = coin.get("platform")
                addr = platform.get("token_address") or platform.get("contract_address") or None
                if addr:
                    network = platform.get("name") or platform.get("symbol") or "network"
                    contracts_info.append(f"{network}: {addr}")
            except Exception:
                pass
        # 3) به عنوان fallback، از urls.explorer استفاده می‌کنیم (ممکن است لینک‌های حاوی آدرس باشند)
        explorers = coin.get("urls", {}).get("explorer", []) if coin.get("urls") else []
        for ex in explorers:
            if ex and "tx/" not in ex and "address" in ex or len(ex) > 20:
                # اضافه می‌کنیم به لیست اما این ممکن است دقیق نباشد
                contracts_info.append(f"explorer: {ex}")

        contract_text = "\n".join(contracts_info) if contracts_info else "اطلاعات قرارداد در CMC موجود نیست."

        msg = f"📜 اطلاعات تکمیلی {coin.get('name','')}\n\n💬 {desc[:1200]}...\n\n📄 وایت‌پیپر: {whitepaper}\n🌐 وب: {website}\n\n🧾 قراردادها:\n{contract_text}"
        keyboard = [[InlineKeyboardButton("❌ بستن", callback_data=f"close_details_{symbol}")]]
        await query.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        print(f"Error details: {e}")
        await query.message.reply_text("⚠️ خطا در دریافت اطلاعات تکمیلی.")

# حذف پیام جزئیات
async def handle_close_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass

# Handler کلی برای callbackهای منو (global market / subscribe / check_subscription)
async def inline_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "global_market":
        await show_global_market_callback(update, context)
        return
    if data == "subscribe":
        tron_msg = TRON_ADDRESS or "آدرس پرداخت هنوز تنظیم نشده."
        await query.message.reply_text(
            f"برای اشتراک ماهیانه (۵ ترون)، مبلغ رو به این آدرس واریز کن:\n\n<code>{tron_msg}</code>\n\n"
            "سپس هش تراکنش رو با /verify <TX_HASH> ارسال کن.",
            parse_mode="HTML"
        )
        return
    if data == "check_subscription":
        subscribed, days_left = check_subscription_status(user_id)
        if subscribed:
            await query.message.reply_text(f"🟢 اشتراک فعاله — حدوداً {days_left} روز باقیه. 🎉")
        else:
            await query.message.reply_text("❌ اشتراک فعال نداری. از دکمهٔ اشتراک استفاده کن یا /start را بزنی.")
        return

# -------------------------
# هندل پیام متن اصلی: کاربر نام یا نماد ارز را می‌فرستد
# -------------------------
async def crypto_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_api_key
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # اگر کاربر /start زده، باید handled باشد؛ اینجا فرض می‌کنیم نماد است
    register_user_if_not_exists(user_id)
    subscribed, _ = check_subscription_status(user_id)

    if not current_api_key:
        await update.message.reply_text("⚠️ کلید CoinMarketCap فعال نیست. لطفاً بعداً تلاش کن.")
        return

    query_symbol = text.strip().lower()
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": current_api_key}
    params = {"symbol": query_symbol.upper(), "convert": "USD"}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        if "data" not in data or query_symbol.upper() not in data["data"]:
            await update.message.reply_text("❌ ارز پیدا نشد — لطفاً نام یا نماد دقیق وارد کن.")
            return

        result = data["data"][query_symbol.upper()]
        name = result.get("name")
        symbol = result.get("symbol")
        price = result["quote"]["USD"]["price"]
        change_1h = result["quote"]["USD"].get("percent_change_1h")
        change_24h = result["quote"]["USD"].get("percent_change_24h")
        change_7d = result["quote"]["USD"].get("percent_change_7d")
        market_cap = result["quote"]["USD"].get("market_cap")
        volume_24h = result["quote"]["USD"].get("volume_24h")
        num_pairs = result.get("num_market_pairs")
        rank = result.get("cmc_rank")

        # پیام اصلی (اطلاعات پایه) — بدون محدودیت برای غیر مشترکین
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

        # دکمه اطلاعات تکمیلی همیشه نمایش داده می‌شود؛ در هنگام کلیک بررسی اشتراک انجام می‌شود
        keyboard = [[InlineKeyboardButton("📜 اطلاعات تکمیلی", callback_data=f"details_{symbol}")]]
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        print(f"Error fetching coin: {e}")
        await update.message.reply_text("⚠️ یه خطایی پیش اومد — دوباره امتحان کن.")

# -------------------------
# نوتیفیکیشن تمدید (3 روز مانده) — فقط یک‌بار برای هر اشتراک
# -------------------------
def check_and_notify_renewals():
    """این تابع توسط scheduler هر روز اجرا می‌شود و به کاربرانی که دقیقاً 3 روز تا پایان اشتراک دارند پیام می‌دهد (یک‌بار)."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        now = datetime.now()
        target = now + timedelta(days=3)
        # انتخاب کاربرانی که بین target و target+1 day قرار ندارند، اما expiry در محدوده target روز قرار دارد
        cur.execute("""
            SELECT telegram_id, subscription_expiry FROM users
            WHERE subscription_expiry IS NOT NULL
              AND subscription_expiry > %s
              AND notified_3day = FALSE
        """, (now,))
        rows = cur.fetchall()
        to_notify = []
        for r in rows:
            tid = r["telegram_id"]
            exp = r["subscription_expiry"]
            days_left = (exp - now).days
            if days_left == 3:
                to_notify.append((tid, exp))
        # ارسال پیام‌ها و علامت‌گذاری notified_3day
        for tid, exp in to_notify:
            try:
                # ارسال پیام از طریق بوت (نمی‌توان اینجا مستقیم بوت را استفاده کرد).
                # ما یک پیام در REPORT_CHANNEL یا INFO_CHANNEL قرار می‌دهیم و همچنین مستقیماً به کاربر پیام می‌فرستیم
                # اما چون این تابع sync است، ارسال پیام async از طریق scheduler باید از بیرون انجام شود.
                # بنابراین این تابع تنها لیست را برمی‌گرداند یا می‌توانید آن را با یک نسخه async جایگزین کنید.
                # برای سادگی: این تابع مقدارهایی را در DB علامت می‌زند و یک رکورد برای ارسال پیام توسط job async می‌سازد.
                cur.execute("UPDATE users SET notified_3day = TRUE WHERE telegram_id = %s", (tid,))
                conn.commit()
            except Exception as e:
                print(f"Error marking notified for {tid}: {e}")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error in check_and_notify_renewals: {e}")

async def send_pending_renewal_notifications(bot: Bot):
    """این تابع async اجرا می‌شود تا پیام‌های واقعی را به کاربرانی که notified_3day=True فرستاده شود.
    پس از ارسال، ستون notified_3day را روی TRUE نگه می‌دارد (تا دوباره ارسال نشود).
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT telegram_id, subscription_expiry FROM users WHERE notified_3day = TRUE")
        rows = cur.fetchall()
        for r in rows:
            tid = r["telegram_id"]
            exp = r["subscription_expiry"]
            # فقط پیام را ارسال می‌کنیم اگر expiry در آینده و دقیقا حدود 3 روز باشد (برای جلوگیری از ارسال‌های قدیمی)
            now = datetime.now()
            if exp and 0 <= (exp - now).days <= 3:
                try:
                    await bot.send_message(chat_id=tid, text=f"⏳ فقط ۳ روز تا پایان اشتراک شما مونده! برای تمدید /start رو بزن یا از دکمهٔ اشتراک استفاده کن ❤️")
                except telegram.error.TelegramError:
                    pass
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error in send_pending_renewal_notifications: {e}")

# -------------------------
# راه‌اندازی اصلی و scheduler
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
        app.add_handler(CallbackQueryHandler(handle_details_callback, pattern=r"^details_"))
        app.add_handler(CallbackQueryHandler(handle_close_details, pattern=r"^close_details_"))
        app.add_handler(CallbackQueryHandler(inline_menu_callback, pattern=r"^(global_market|subscribe|check_subscription)$"))

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, crypto_info))

        await set_bot_commands(app.bot)

        # انتخاب کلید CMC اولیه و ارسال هشدار سوییچ در صورت نیاز
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

        # scheduler
        scheduler = AsyncIOScheduler()
        # گزارش مصرف هر 1 ساعت
        scheduler.add_job(send_usage_report_to_channel, "interval", hours=1, args=[app.bot])
        # چک و نشانه گذاری کاربران برای نوتیفیکیشن 3 روزه (هر روز یکبار)
        scheduler.add_job(check_and_notify_renewals, "interval", days=1)
        # ارسال واقعی نوتیفیکیشن‌های 3 روزه (هر روز اجرا شود و پیام‌ها را ارسال کند)
        scheduler.add_job(lambda: asyncio.create_task(send_pending_renewal_notifications(app.bot)), "interval", days=1)
        # به‌علاوه، هر 6 ساعت کلیدها را بررسی می‌کنیم تا در صورت لزوم سوییچ کنیم و هشدار بفرستیم
        scheduler.add_job(lambda: asyncio.create_task(check_and_select_api_key(app.bot)), "interval", hours=6)

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
