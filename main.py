# main_fixed.py
# نسخه نهایی نهایی شده: منوی پایین (ReplyKeyboard)، پیام‌های دوستانه، تاریخ شمسی،
# مدیریت ادمین (ADMIN_IDS یا ADMIN_USER_ID)، گزارش مصرف CMC ساعتی، تایید پرداخت از کانال،
# نمایش قراردادها به صورت Network: 0x... (بدون نمایش لینک‌های explorer کامل).
#
# توجه: قبل از اجرا requirements.txt شامل موارد زیر باشد:
# python-telegram-bot==20.3
# requests
# psycopg2-binary
# apscheduler
# jdatetime

import os
import re
import requests
import jdatetime
from datetime import datetime, timedelta, date
from telegram import (
    Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, Bot, BotCommand
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
INFO_CHANNEL = os.getenv("INFO_CHANNEL")      # -100...
REPORT_CHANNEL = os.getenv("REPORT_CHANNEL")  # -100...
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
        return jdt.strftime("%Y/%m/%d ساعت %H:%M")
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
    cur.execute("UPDATE users SET subscription_expiry = %s, notified_3day = FALSE WHERE telegram_id = %s", (new_expiry, telegram_id))
    conn.commit()
    cur.close()
    conn.close()
    return new_expiry

def check_subscription_status(telegram_id: int):
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
# قالب‌بندی و کمکی‌ها
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
                selected = True
                break
        except Exception as e:
            print(f"Error checking CMC key #{idx+1}: {e}")
            continue

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
\"\"\"
        try:
            await bot.send_message(chat_id=REPORT_CHANNEL, text=msg_active, parse_mode=\"HTML\")
        except telegram.error.TelegramError:
            pass

    # پیام گزارش کلی
   msg_summary = f"""📋 <b>گزارش کلی API کوین‌مارکت‌کپ</b>:
🔢 تعداد کل کلیدهای API: {len(api_keys)}
🔑 تعداد کلیدهای فعال (با کردیت): {active_keys}
✅ کل کردیت‌های مصرف‌شده: {total_credits_used:,}
🟢 کل کردیت‌های باقی‌مانده: {total_credits_left:,}
🕒 آخرین بروزرسانی: {to_shamsi(datetime.now())}
\"\"\"
    try:
        await bot.send_message(chat_id=REPORT_CHANNEL, text=msg_summary, parse_mode=\"HTML\")
    except telegram.error.TelegramError:
        pass

# -------------------------
# هندلرها و پیام‌های دوستانه (مینیمال)
# -------------------------
async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand("start", "شروع ربات"),
        BotCommand("check", "بررسی اشتراک"),
        BotCommand("verify", "ثبت هش پرداخت: /verify <tx_hash>"),
    ]
    await bot.set_my_commands(commands)

# منوی پایین پایدار
def build_reply_keyboard(subscribed: bool):
    if subscribed:
        keys = [["📊 وضعیت کلی بازار", "🔍 بررسی اشتراک"]]
    else:
        keys = [["💎 اشتراک و پرداخت"]]
    return ReplyKeyboardMarkup(keys, resize_keyboard=True)

# /start (مینیمال، بدون دکمه‌های inline زیر پیام)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    register_user_if_not_exists(user_id)
    subscribed, days_left = check_subscription_status(user_id)

    msg = "سلام! 👋\nاسم یا نماد یه ارز رو بفرست (مثلاً BTC یا بیت‌کوین) تا اطلاعات پایه‌شو برات بیارم."
    reply_markup = build_reply_keyboard(subscribed)
    await update.message.reply_text(msg, reply_markup=reply_markup)

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
        await update.message.reply_text(f"🟢 اشتراک فعاله — حدوداً {days_left} روز باقیه. ❤️", reply_markup=build_reply_keyboard(True))
    else:
        await update.message.reply_text("❌ اشتراک فعال نداری. برای خرید /start رو بزن یا از دکمهٔ اشتراک استفاده کن.", reply_markup=build_reply_keyboard(False))

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

    await update.message.reply_text(f"✅ هش ثبت شد (شناسه #{payment_id}). منتظر بررسی ادمین بمون — زود جواب می‌دم 🙂", reply_markup=build_reply_keyboard(check_subscription_status(user_id)[0]))

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

    data = query.data
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

        try:
            await context.bot.send_message(chat_id=payer,
                                           text=f"🎉 تبریک! پرداختت تایید شد و اشتراک تا {to_shamsi(new_expiry)} فعال شد. از ربات لذت ببر 😉",
                                           reply_markup=build_reply_keyboard(True))
        except telegram.error.TeleGramError:
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
                                           text=f"❌ متاسفم؛ پرداخت (#{payment_id}) معتبر نبود و اشتراک فعال نشد. اگر فکر می‌کنی اشتباه شده با ادمین تماس بگیر 🙏",
                                           reply_markup=build_reply_keyboard(False))
        except telegram.error.TeleGramError:
            print(f"Couldn't notify user {payer} after reject.")
        return
    else:
        cur.close()
        conn.close()
        await query.edit_message_text("⚠️ عملیات نامشخص.")
        return

# نمایش وضعیت کلی بازار (برای مشترکین) - این تابع به صورت پیام متنی هم فراخوانی می‌شود
async def show_global_market(update_or_query, context=None):
    # update_or_query ممکن است Update یا CallbackQuery باشد
    try:
        # تعیین user_id و متد ارسال پیام
        if hasattr(update_or_query, "effective_user"):
            # این حالت Update است (پیام متنی)
            update = update_or_query
            user_id = update.effective_user.id
            send = lambda text: update.message.reply_text(text)
        else:
            # این حالت CallbackQuery است
            query = update_or_query
            user_id = query.from_user.id
            send = lambda text: query.message.reply_text(text)

        subscribed, _ = check_subscription_status(user_id)
        if not subscribed:
            send("لطفاً اشتراک تهیه کن تا وضعیت کلی بازار رو ببینی.")
            return

        global current_api_key
        if not current_api_key:
            send("⚠️ کلید CoinMarketCap فعال نیست. لطفاً بعداً تلاش کن.")
            return

        url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
        headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": current_api_key}
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
        send(msg)
    except Exception as e:
        print(f"Error show_global_market: {e}")
        try:
            if hasattr(update_or_query, "effective_user"):
                await update_or_query.message.reply_text("⚠️ خطا در دریافت وضعیت کلی بازار. لطفاً بعداً تلاش کن.")
            else:
                await update_or_query.message.reply_text("⚠️ خطا در دریافت وضعیت کلی بازار. لطفاً بعداً تلاش کن.")
        except Exception:
            pass

# استخراج قراردادها (فقط آدرس‌های واقعی 0x... و نام شبکه)
ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")

def extract_contracts_from_coin(coin: dict):
    contracts_set = []
    try:
        # 1) از فیلد contracts (معمول‌ترین محل)
        for c in coin.get("contracts", []) or []:
            addr = None
            # ممکن است ساختارهای متفاوتی وجود داشته باشد
            if isinstance(c, dict):
                addr = c.get("contract_address") or c.get("address") or c.get("token_address")
                network = c.get("platform") or c.get("chain") or c.get("name") or c.get("network")
                # اگر platform خودش یک دیکشنری باشد، نام شبکه را استخراج کنید
                if isinstance(network, dict):
                    network = network.get("name") or network.get("symbol")
                if addr and ADDRESS_RE.match(addr):
                    label = f\"{network or 'network'}: {addr}\"
                    contracts_set.append(label)
        # 2) از فیلد platform مستقیم (برخی پاسخ‌ها اینجا آدرس دارند)
        platform = coin.get("platform")
        if platform and isinstance(platform, dict):
            addr = platform.get("token_address") or platform.get("contract_address")
            network = platform.get("name") or platform.get("symbol")
            if addr and ADDRESS_RE.match(addr):
                contracts_set.append(f\"{network or 'network'}: {addr}\")
        # 3) از urls.explorer در صورتی که در URLها آدرس 0x وجود داشته باشد، آن آدرس را استخراج کن
        explorers = []
        try:
            explorers = coin.get("urls", {}).get("explorer", []) or []
        except Exception:
            explorers = []
        for ex in explorers:
            if not ex or not isinstance(ex, str):
                continue
            found = ADDRESS_RE.search(ex)
            if found:
                addr = found.group(0)
                # تلاش برای تعیین شبکه از URL (heuristic)
                network = None
                if "etherscan" in ex:
                    network = "Ethereum"
                elif "polygonscan" in ex or "matic" in ex:
                    network = "Polygon"
                elif "bscscan" in ex or "binance" in ex:
                    network = "BSC"
                elif "solscan" in ex:
                    network = "Solana"
                else:
                    network = "explorer"
                contracts_set.append(f\"{network}: {addr}\")
    except Exception as e:
        print(f\"Error extracting contracts: {e}\")
    # پاکسازی تکراری‌ها و مرتب‌سازی
    final = []
    for item in contracts_set:
        if item not in final:
            final.append(item)
    return final

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

        desc = coin.get("description") or "ندارد"
        whitepaper = coin.get("urls", {}).get("technical_doc", ["ندارد"])[0]
        website = coin.get("urls", {}).get("website", ["ندارد"])[0]
        logo = coin.get("logo", "ندارد")

        contracts = extract_contracts_from_coin(coin)
        contract_text = "\n".join(contracts) if contracts else "اطلاعات قرارداد در CoinMarketCap موجود نیست."

        msg = f\"📜 اطلاعات تکمیلی {coin.get('name','')}\n\n💬 {desc[:1200]}...\n\n📄 وایت‌پیپر: {whitepaper}\n🌐 وب: {website}\n\n🧾 قراردادها:\n{contract_text}"
        keyboard = [[InlineKeyboardButton("❌ بستن", callback_data=f"close_details_{symbol}")]]
        await query.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        print(f\"Error details: {e}\")
        await query.message.reply_text("⚠️ خطا در دریافت اطلاعات تکمیلی.")

# حذف پیام جزئیات
async def handle_close_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass

# هندل پیام متنی اصلی: کاربر نام یا نماد ارز را می‌فرستد یا از منوی پایین استفاده می‌کند
async def crypto_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_api_key
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # مدیریت کلیدهای متنی منو
    if text == "📊 وضعیت کلی بازار":
        await show_global_market(update, context)
        return
    if text == "🔍 بررسی اشتراک":
        subscribed, days_left = check_subscription_status(user_id)
        if subscribed:
            await update.message.reply_text(f"🟢 اشتراک فعاله — حدوداً {days_left} روز باقیه.", reply_markup=build_reply_keyboard(True))
        else:
            await update.message.reply_text("❌ اشتراک فعال نداری. از دکمهٔ اشتراک استفاده کن یا /start رو بزن.", reply_markup=build_reply_keyboard(False))
        return
    if text == "💎 اشتراک و پرداخت":
        tron_msg = TRON_ADDRESS or "آدرس پرداخت هنوز تنظیم نشده."
        await update.message.reply_text(
            f"برای اشتراک ماهیانه (۵ ترون)، مبلغ رو به آدرس زیر واریز کن:\n\n<code>{tron_msg}</code>\n\n"
            "سپس هش تراکنش رو با /verify <TX_HASH> ارسال کن.",
            parse_mode="HTML",
            reply_markup=build_reply_keyboard(False)
        )
        return

    # در اینجا فرض می‌کنیم پیام نماد ارز است
    register_user_if_not_exists(user_id)
    subscribed, _ = check_subscription_status(user_id)

    if not current_api_key:
        await update.message.reply_text("⚠️ کلید CoinMarketCap فعال نیست. لطفاً بعداً تلاش کن.", reply_markup=build_reply_keyboard(subscribed))
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
            await update.message.reply_text("❌ ارز پیدا نشد — لطفاً نام یا نماد دقیق وارد کن.", reply_markup=build_reply_keyboard(subscribed))
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

        keyboard = [[InlineKeyboardButton("📜 اطلاعات تکمیلی", callback_data=f"details_{symbol}")]]
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        print(f"Error fetching coin: {e}")
        await update.message.reply_text("⚠️ یه خطایی پیش اومد — دوباره امتحان کن.", reply_markup=build_reply_keyboard(subscribed))

# -------------------------
# نوتیفیکیشن تمدید (3 روز مانده) — فقط یک‌بار برای هر اشتراک
# -------------------------
def check_and_notify_renewals():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        now = datetime.now()
        cur.execute(\"\"\"
            SELECT telegram_id, subscription_expiry FROM users
            WHERE subscription_expiry IS NOT NULL
              AND subscription_expiry > %s
              AND notified_3day = FALSE
        \"\"\", (now,))
        rows = cur.fetchall()
        to_notify = []
        for r in rows:
            tid = r[\"telegram_id\"] if isinstance(r, dict) else r[0]
            exp = r[\"subscription_expiry\"] if isinstance(r, dict) else r[1]
            days_left = (exp - now).days if exp else None
            if days_left == 3:
                to_notify.append((tid, exp))
        for tid, exp in to_notify:
            try:
                cur.execute(\"UPDATE users SET notified_3day = TRUE WHERE telegram_id = %s\", (tid,))
                conn.commit()
            except Exception as e:
                print(f\"Error marking notified for {tid}: {e}\")
        cur.close()
        conn.close()
    except Exception as e:
        print(f\"Error in check_and_notify_renewals: {e}\")

async def send_pending_renewal_notifications(bot: Bot):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(\"SELECT telegram_id, subscription_expiry FROM users WHERE notified_3day = TRUE\")
        rows = cur.fetchall()
        for r in rows:
            tid = r[\"telegram_id\"] if isinstance(r, dict) else r[0]
            exp = r[\"subscription_expiry\"] if isinstance(r, dict) else r[1]
            now = datetime.now()
            if exp and 0 <= (exp - now).days <= 3:
                try:
                    await bot.send_message(chat_id=tid, text=f\"⏳ فقط ۳ روز تا پایان اشتراک‌ت مونده! برای تمدید /start رو بزن یا از دکمهٔ اشتراک استفاده کن ❤️\", reply_markup=build_reply_keyboard(False))
                except telegram.error.TeleGramError:
                    pass
        cur.close()
        conn.close()
    except Exception as e:
        print(f\"Error in send_pending_renewal_notifications: {e}\")

# -------------------------
# راه‌اندازی اصلی و scheduler
# -------------------------
async def main():
    try:
        print(\"راه‌اندازی ربات...\")
        init_db()
        app = ApplicationBuilder().token(BOT_TOKEN).build()

        # handlers
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("check", check_subscription))
        app.add_handler(CommandHandler("verify", verify_tx))

        app.add_handler(CallbackQueryHandler(admin_payment_callback, pattern=r\"^admin_pay_\"))
        app.add_handler(CallbackQueryHandler(handle_details_callback, pattern=r\"^details_\"))
        app.add_handler(CallbackQueryHandler(handle_close_details, pattern=r\"^close_details_\"))

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, crypto_info))

        await set_bot_commands(app.bot)

        await check_and_select_api_key(app.bot)

        await app.initialize()
        await app.start()

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

        scheduler = AsyncIOScheduler()
        scheduler.add_job(send_usage_report_to_channel, "interval", hours=1, args=[app.bot])
        scheduler.add_job(check_and_notify_renewals, "interval", days=1)
        scheduler.add_job(lambda: asyncio.create_task(send_pending_renewal_notifications(app.bot)), "interval", days=1)
        scheduler.add_job(lambda: asyncio.create_task(check_and_select_api_key(app.bot)), "interval", hours=6)

        scheduler.start()

        print(\"ربات اجرا شد 🎉\")
        await asyncio.Event().wait()
    except Exception as e:
        print(f\"Error in main: {e}\")
        raise
    finally:
        try:
            await app.stop()
            await app.shutdown()
        except Exception:
            pass

if __name__ == \"__main__\":
    asyncio.run(main())
